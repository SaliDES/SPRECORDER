#!/usr/bin/env python3
import json, os, signal, subprocess, threading, time, re, platform, shutil, traceback, select
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen

HOST=os.environ.get('SPREC_HOST','0.0.0.0'); PORT=int(os.environ.get('SPREC_PORT','8080')); BACKEND=os.environ.get('SPREC_BACKEND','http://127.0.0.1:3030')
FFMPEG=os.environ.get('SPREC_FFMPEG') or 'ffmpeg'
ROOT=os.path.dirname(os.path.abspath(__file__)); DATA_DIR=os.environ.get('SPREC_DATA_DIR',os.path.join(ROOT,'data')); os.makedirs(DATA_DIR,exist_ok=True)
RECORDINGS=os.path.join(DATA_DIR,'recordings'); os.makedirs(RECORDINGS,exist_ok=True)
LOGS=os.path.join(DATA_DIR,'logs'); os.makedirs(LOGS,exist_ok=True)
MODELS_FILE=os.path.join(DATA_DIR,'models.json')
lock=threading.RLock(); recordings={}; models={}; status_cache={}; last_results={}
STARTUP_TIMEOUT=float(os.environ.get('SPREC_RECORDING_START_TIMEOUT','30'))
STALL_TIMEOUT=float(os.environ.get('SPREC_FFMPEG_STALL_TIMEOUT','45'))
RECONNECT_DELAY=float(os.environ.get('SPREC_FFMPEG_RECONNECT_DELAY','5'))
MAX_RECONNECTS=int(os.environ.get('SPREC_FFMPEG_MAX_RECONNECTS','0'))  # 0 = unlimited while recording
CHUNK_SIZE=64*1024
FFMPEG_ERROR_RE=re.compile(r'(?:error|failed|corrupt|non-existing PPS|decode_slice_header|no frame|DTS .*out of order|invalid|I/O error|Connection .* failed)',re.I)

PAGE=r'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPRECORDER</title><script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.min.js"></script><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#eee;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{max-width:1450px;margin:24px auto;padding:0 18px}.top{display:flex;align-items:center;justify-content:space-between}.tabs{display:flex;gap:8px;margin:22px 0 18px}.tab{padding:10px 16px;border:1px solid #333842;background:#191c23;color:#bbb;border-radius:9px;cursor:pointer;font-weight:700}.tab.active{background:#3498db;color:#fff;border-color:#3498db}.panel{display:none}.panel.active{display:block}.top h1{margin:0}.add{display:flex;gap:8px;margin:14px 0}.add input{flex:1;min-width:220px;padding:12px;border-radius:9px;border:1px solid #3a3d45;background:#1b1e25;color:#fff;font-size:15px}.add button,.small{padding:10px 14px;border:0;border-radius:8px;cursor:pointer;font-weight:600}.add button{background:#2ecc71}.summary{color:#aaa;margin:8px 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:18px}.autoGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.card{background:#171a21;border:1px solid #2c3039;border-radius:14px;overflow:hidden;box-shadow:0 5px 18px #0004}.head{display:flex;align-items:center;gap:12px;padding:12px 14px}.avatar{width:52px;height:52px;border-radius:50%;object-fit:cover;background:#292d35;border:2px solid #363b46}.name{font-weight:700;font-size:17px}.state{font-size:13px;color:#999;margin-top:3px}.state.live{color:#2ecc71}.spacer{flex:1}.remove{background:#2a2e36;color:#bbb}.videoWrap{position:relative;background:#000}.videoWrap video{display:block;width:100%;aspect-ratio:16/9;background:#000}.overlay{position:absolute;left:10px;top:10px;background:#000b;padding:5px 8px;border-radius:7px;font-size:12px}.controls{display:flex;gap:8px;flex-wrap:wrap;padding:12px 14px}.monitor{background:#3498db;color:white}.record{background:#e67e22;color:#fff}.record.active{background:#c0392b}.autoRec{background:#8e44ad;color:#fff}.meta{padding:0 14px 12px;color:#8d929d;font-size:12px;min-height:18px}.autoCard{padding:15px}.autoControls{display:flex;gap:8px;align-items:center;margin-top:14px}.autoDesc{color:#9ca1ab;font-size:13px;line-height:1.45;margin-top:8px}.empty{padding:50px 20px;text-align:center;color:#777;border:1px dashed #343842;border-radius:12px}.hint{color:#777;font-size:12px;margin-top:14px}.sectionTitle{font-size:20px;font-weight:700}.sectionDesc{color:#999;margin:6px 0 16px}@media(max-width:520px){.grid,.autoGrid{grid-template-columns:1fr}.card{min-width:0}}
.libraryGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}.libraryCard{background:#171a21;border:1px solid #2c3039;border-radius:12px;padding:14px}.libraryCard video{width:100%;aspect-ratio:16/9;background:#000;margin-top:10px}.libraryMeta{color:#999;font-size:12px;margin:8px 0}.libraryActions{display:flex;gap:8px;flex-wrap:wrap}.libraryActions button{padding:8px 11px;border:0;border-radius:7px;cursor:pointer;font-weight:600}.playBtn{background:#3498db;color:#fff}.convertBtn{background:#8e44ad;color:#fff}.deleteBtn{background:#733;color:#fff}@media(max-width:520px){.libraryGrid{grid-template-columns:1fr}}\n</style></head><body><main><div class="top"><h1>SPRECORDER</h1><div id="summary" class="summary"></div></div><div class="tabs"><button class="small" style="background:#444;color:#fff" onclick="recoverPartials()">Récupérer les .ts.part</button><button class="tab active" data-tab="manual" onclick="switchTab('manual')">Monitoring</button><button class="tab" data-tab="auto" onclick="switchTab('auto')">Enregistrement automatique</button><button class="tab" data-tab="library" onclick="switchTab('library');refreshLibrary()">Bibliothèque</button></div>
<section id="manualPanel" class="panel active"><div class="sectionTitle">Monitoring manuel</div><div class="sectionDesc">Ajoute tes modèles, surveille chaque live indépendamment et lance ou arrête l'enregistrement quand tu le souhaites.</div><div class="add"><input id="newModel" placeholder="Ajouter un modèle, ex: MasonxHer" autocomplete="off"><button onclick="addModel('manual')">+ Ajouter</button></div><div id="manualGrid" class="grid"></div><div id="manualEmpty" class="empty">Ajoute un ou plusieurs modèles pour commencer.</div></section>
<section id="autoPanel" class="panel"><div class="sectionTitle">Enregistrement automatique</div><div class="sectionDesc">Les modèles placés ici sont surveillés en permanence. Dès qu'un modèle passe en ligne, SPRECORDER démarre automatiquement l'enregistrement. Lorsqu'il repasse hors ligne, le MP4 est finalisé.</div><div class="add"><input id="newAutoModel" placeholder="Ajouter un modèle à enregistrer automatiquement" autocomplete="off"><button onclick="addModel('auto')">+ Ajouter</button></div><div id="autoGrid" class="autoGrid"></div><div id="autoEmpty" class="empty">Aucun modèle en enregistrement automatique.</div></section>
<section id="libraryPanel" class="panel"><div class="sectionTitle">Bibliothèque</div><div class="sectionDesc">Retrouve tes enregistrements terminés. Les fichiers TS existants peuvent être convertis en MP4.</div><div class="libraryActions" style="margin-bottom:14px"><button class="playBtn" onclick="refreshLibrary()">Actualiser</button></div><div id="libraryGrid" class="libraryGrid"></div><div id="libraryEmpty" class="empty" style="display:none">Aucun enregistrement disponible.</div></section>\n <div class="hint">Le backend original ChanTrail reste responsable du relay, du HLS et de la résolution Stripchat. Les deux listes sont indépendantes.</div></main><script>
const manualGrid=document.getElementById('manualGrid'),autoGrid=document.getElementById('autoGrid'),manualEmpty=document.getElementById('manualEmpty'),autoEmpty=document.getElementById('autoEmpty'),summary=document.getElementById('summary'),newModel=document.getElementById('newModel'),newAutoModel=document.getElementById('newAutoModel');const players={};let data={manual:{},auto:{}};
[newModel,newAutoModel].forEach((i,idx)=>i.addEventListener('keydown',e=>{if(e.key==='Enter')addModel(idx?'auto':'manual')}));
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function switchTab(t){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));['manual','auto','library'].forEach(x=>document.getElementById(x+'Panel').classList.toggle('active',t===x))}
function addModel(kind){const input=kind==='auto'?newAutoModel:newModel,m=input.value.trim();if(!m)return;fetch('/models/add?kind='+kind+'&model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);input.value='';refreshStatus()}).catch(e=>alert(e.message))}
function removeModel(kind,m){if(!confirm('Supprimer '+m+' de cette liste ?'))return;if(kind==='manual')stopMonitor(m);fetch('/models/remove?kind='+kind+'&model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);refreshStatus()}).catch(e=>alert(e.message))}
function monitor(m){if(players[m]){stopMonitor(m);return}const video=document.querySelector('[data-video="'+CSS.escape(m)+'"]');if(!video||!window.mpegts||!mpegts.isSupported()){alert('Le lecteur mpegts.js n’est pas disponible.');return}const url=(location.protocol==='https:'?'https://':'http://')+location.hostname+':3030/stream/'+encodeURIComponent(m);const p=mpegts.createPlayer({type:'mpegts',isLive:true,url:url},{enableWorker:false,liveBufferLatencyChasing:true,liveBufferLatencyMaxLatency:1.5,liveBufferLatencyMinRemain:.3});p.on(mpegts.Events.ERROR,(t,d)=>setMeta(m,'Erreur lecteur: '+t+' / '+d));p.attachMediaElement(video);p.load();p.play().catch(()=>{});players[m]=p;setMeta(m,'Monitoring actif');updateControls(m)}
function stopMonitor(m){const p=players[m];if(p){try{p.pause()}catch(e){}try{p.destroy()}catch(e){}delete players[m]}const v=document.querySelector('[data-video="'+CSS.escape(m)+'"]');if(v){v.removeAttribute('src');try{v.load()}catch(e){}}setMeta(m,'Monitoring arrêté');updateControls(m)}
function setMeta(m,s){const el=document.querySelector('[data-meta="'+CSS.escape(m)+'"]');if(el)el.textContent=s}
function updateControls(m){const btn=document.querySelector('[data-monitor="'+CSS.escape(m)+'"]');if(btn)btn.textContent=players[m]?'Arrêter le monitoring':'Monitorer le live'}
function recoverPartials(){fetch('/record/recover').then(r=>r.json()).then(j=>alert(j.recovered&&j.recovered.length?'Récupérés : '+j.recovered.join(', '):'Aucun .ts.part récupérable.')).catch(e=>alert(e.message))}
function rec(m){fetch('/record/toggle?model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);return refreshStatus()}).catch(e=>alert(e.message))}
function renderManual(){const names=Object.keys(data.manual||{});manualEmpty.style.display=names.length?'none':'block';manualGrid.innerHTML=names.map(m=>{const x=data.manual[m],live=x.online,rec=x.recording,mon=!!players[m];return `<section class="card"><div class="head"><img class="avatar" src="${esc(x.avatar||'')}" onerror="this.style.visibility='hidden'"><div><div class="name">${esc(m)}</div><div class="state ${live?'live':''}" data-state="${esc(m)}">${live?'● EN LIGNE':'○ HORS LIGNE'}${rec?' · ENREGISTREMENT':''}</div></div><div class="spacer"></div><button class="small remove" onclick="removeModel('manual','${esc(m)}')">Supprimer</button></div><div class="videoWrap"><video data-video="${esc(m)}" controls autoplay muted playsinline></video><div class="overlay" data-overlay="${esc(m)}" style="display:${live?'block':'none'}">LIVE</div></div><div class="controls"><button class="small monitor" data-monitor="${esc(m)}" onclick="monitor('${esc(m)}')">${mon?'Arrêter le monitoring':'Monitorer le live'}</button><button class="small record ${rec?'active':''}" data-record="${esc(m)}" onclick="rec('${esc(m)}')">${rec?'Arrêter l’enregistrement':'Enregistrer maintenant'}</button></div><div class="meta" data-meta="${esc(m)}">${x.recording_error?'Erreur: '+esc(x.recording_error):rec?'RECORDING · '+esc(formatBytes(x.bytes))+' · '+esc(formatDuration(x.duration))+' · reconnexions '+esc(x.reconnections):mon?'Monitoring actif':'Prêt.'}</div></section>`}).join('')}
function renderAuto(){const names=Object.keys(data.auto||{});autoEmpty.style.display=names.length?'none':'block';autoGrid.innerHTML=names.map(m=>{const x=data.auto[m],live=x.online,rec=x.recording;return `<section class="card autoCard"><div class="head" style="padding:0"><img class="avatar" src="${esc(x.avatar||'')}" onerror="this.style.visibility='hidden'"><div><div class="name">${esc(m)}</div><div class="state ${live?'live':''}" data-autostate="${esc(m)}">${live?'● EN LIGNE':'○ HORS LIGNE'}</div></div><div class="spacer"></div><button class="small remove" onclick="removeModel('auto','${esc(m)}')">Supprimer</button></div><div class="autoDesc">${live?(rec?'Enregistrement automatique en cours.':'En ligne — démarrage automatique.'):'Hors ligne — en attente du prochain live.'}</div><div class="autoControls"><button class="small autoRec" onclick="rec('${esc(m)}')">${rec?'Arrêter maintenant':'Enregistrer maintenant'}</button></div><div class="meta" data-autometa="${esc(m)}">${rec?'Fichier: '+esc(x.file):'Surveillance automatique active.'}</div></section>`}).join('')}
function refreshLibrary(){fetch('/library').then(r=>r.json()).then(j=>renderLibrary(j.items||[])).catch(e=>alert('Bibliothèque: '+e.message))}
 function renderLibrary(items){const grid=document.getElementById('libraryGrid'),empty=document.getElementById('libraryEmpty');empty.style.display=items.length?'none':'block';grid.innerHTML=items.map(x=>{const name=esc(x.name),url='/library/file/'+encodeURIComponent(x.name),date=new Date(x.modified*1000).toLocaleString();return `<article class="libraryCard"><strong>${name}</strong><div class="libraryMeta">${formatBytes(x.size)} · ${date}${x.active?' · En cours':''}</div>${x.type==='mp4'?`<video controls preload="metadata" src="${url}"></video>`:''}<div class="libraryActions" style="margin-top:10px">${x.type==='ts'&&!x.active?`<button class="convertBtn" data-convert="${name}">Convertir en MP4</button>`:''}<button class="deleteBtn" data-delete="${name}" ${x.active?'disabled':''}>Supprimer</button></div></article>`}).join('')}
 document.getElementById('libraryGrid').addEventListener('click',e=>{const c=e.target.closest('[data-convert]'),d=e.target.closest('[data-delete]');if(c)convertLibrary(c.dataset.convert);if(d&&!d.disabled)deleteLibrary(d.dataset.delete)});
 function convertLibrary(name){if(!confirm('Convertir '+name+' en MP4 ? Le TS ne sera supprimé qu’après validation du MP4.'))return;fetch('/library/convert?file='+encodeURIComponent(name),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);refreshLibrary()}).catch(e=>alert(e.message))}
 function deleteLibrary(name){if(!confirm('Supprimer définitivement '+name+' ?'))return;fetch('/library/delete?file='+encodeURIComponent(name),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);refreshLibrary()}).catch(e=>alert(e.message))}
function formatBytes(n){n=Number(n||0);const u=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<4){n/=1024;i++}return n.toFixed(i?1:0)+' '+u[i]}
function formatDuration(n){n=Math.max(0,Math.floor(Number(n||0)));const h=String(Math.floor(n/3600)).padStart(2,'0'),m=String(Math.floor(n%3600/60)).padStart(2,'0'),s=String(n%60).padStart(2,'0');return h+':'+m+':'+s}
function updateSummary(){const a=Object.keys(data.manual||{}).length,b=Object.keys(data.auto||{}).length;summary.textContent=(a+b)+' modèle'+(a+b>1?'s':'')+' · '+a+' monitoring · '+b+' auto'}
function updateStatuses(){Object.keys(data.manual||{}).forEach(m=>{const x=data.manual[m],state=document.querySelector('[data-state="'+CSS.escape(m)+'"]');if(state){state.className='state '+(x.online?'live':'');state.textContent=(x.online?'● EN LIGNE':'○ HORS LIGNE')+(x.recording?' · ENREGISTREMENT':'')}const ov=document.querySelector('[data-overlay="'+CSS.escape(m)+'"]');if(ov)ov.style.display=x.online?'block':'none';updateControls(m);const b=document.querySelector('[data-record="'+CSS.escape(m)+'"]');if(b){b.textContent=x.recording?'Arrêter l’enregistrement':'Enregistrer maintenant';b.className='small record '+(x.recording?'active':'')}const meta=document.querySelector('[data-meta="'+CSS.escape(m)+'"]');if(meta){if(x.recording)meta.textContent='RECORDING · '+formatBytes(x.bytes)+' · '+formatDuration(x.duration)+' · reconnexions '+x.reconnections+' · erreurs FFmpeg '+x.ffmpeg_errors;else if(x.recording_error)meta.textContent='Erreur: '+x.recording_error;else if(x.recording_state==='RECONNECTING')meta.textContent='Reconnexion… '+x.reconnections+' · '+formatBytes(x.bytes);}});Object.keys(data.auto||{}).forEach(m=>{const x=data.auto[m],state=document.querySelector('[data-autostate="'+CSS.escape(m)+'"]');if(state){state.className='state '+(x.online?'live':'');state.textContent=x.online?'● EN LIGNE':'○ HORS LIGNE'}const meta=document.querySelector('[data-autometa="'+CSS.escape(m)+'"]');if(meta)meta.textContent=x.recording?'RECORDING · '+formatBytes(x.bytes)+' · '+formatDuration(x.duration)+' · reconnexions '+x.reconnections+' · erreurs FFmpeg '+x.ffmpeg_errors:(x.recording_error?'Erreur: '+x.recording_error:'Surveillance automatique active.')})}
async function refreshStatus(){try{const r=await fetch('/models/status?ts='+Date.now(),{cache:'no-store'}),j=await r.json(),next=j.models||{manual:{},auto:{}};const oldManual=Object.keys(data.manual||{}).sort().join('\n'),oldAuto=Object.keys(data.auto||{}).sort().join('\n'),newManual=Object.keys(next.manual||{}).sort().join('\n'),newAuto=Object.keys(next.auto||{}).sort().join('\n');data=next;if(oldManual!==newManual||oldAuto!==newAuto){renderManual();renderAuto()}updateSummary();updateStatuses()}catch(e){}}
refreshStatus();setInterval(refreshStatus,5000);
</script></body></html>
'''

def load_models():
    global models
    try:
        with open(MODELS_FILE,'r',encoding='utf-8') as f: raw=json.load(f)
    except Exception: raw={}
    if isinstance(raw,dict) and ('manual' in raw or 'auto' in raw):
        models={'manual':raw.get('manual') if isinstance(raw.get('manual'),dict) else {},'auto':raw.get('auto') if isinstance(raw.get('auto'),dict) else {}}
    elif isinstance(raw,dict):
        manual={}; auto={}
        for m,x in raw.items():
            x=x if isinstance(x,dict) else {}
            (auto if x.get('auto') else manual)[m]={k:v for k,v in x.items() if k!='auto'}
        models={'manual':manual,'auto':auto}
    else: models={'manual':{},'auto':{}}
    for kind in ('manual','auto'):
        for m in list(models[kind]):
            if not isinstance(models[kind][m],dict): models[kind][m]={}
            models[kind][m].setdefault('avatar',''); models[kind][m].setdefault('online',False)

def save_models():
    tmp=MODELS_FILE+'.tmp'
    with open(tmp,'w',encoding='utf-8') as f: json.dump(models,f,ensure_ascii=False,indent=2)
    os.replace(tmp,MODELS_FILE)

def safe_model(m):
    m=m.strip()
    if not m or len(m)>120 or any(c in m for c in '/\\\x00\r\n'): raise ValueError('Nom de modèle invalide')
    return m

def kind_name(kind):
    if kind not in ('manual','auto'): raise ValueError('Liste inconnue')
    return kind

def all_entries():
    for kind in ('manual','auto'):
        for m in list(models[kind]): yield kind,m

def fetch_stripchat(model):
    """Get live state + avatar with a few fallbacks.

    Stripchat has changed the public API recently; the username/cam endpoint can
    return 403 outside a browser session.  We therefore try the legacy endpoint
    with browser-like headers, then resolve username -> model id, and finally
    inspect the public profile HTML for the live flags.  Avatar extraction is
    kept independent because it already works reliably.
    """
    base_headers={
        'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0 Safari/537.36',
        'Accept':'application/json, text/plain, */*',
        'Accept-Language':'fr-FR,fr;q=0.9,en;q=0.8',
        'Referer':f'https://stripchat.com/{quote(model,safe="")}',
        'Origin':'https://stripchat.com',
        'X-Requested-With':'XMLHttpRequest',
        'Cache-Control':'no-cache',
    }
    online=False; avatar=''; api_ok=False

    def inspect_obj(obj):
        nonlocal online, avatar, api_ok
        if not isinstance(obj,dict): return
        api_ok=True
        user=obj.get('user')
        if isinstance(user,dict):
            u=user.get('user') if isinstance(user.get('user'),dict) else user
            if isinstance(u,dict):
                status=str(u.get('status','')).lower()
                if status in ('public','live','online'): online=True
                if u.get('isLive') is True: online=True
                for k in ('avatarUrl','avatar','profilePicture','profilePictureUrl'):
                    if isinstance(u.get(k),str) and u[k].startswith('http'): avatar=u[k]; break
        cam=obj.get('cam')
        if isinstance(cam,dict):
            if cam.get('isCamAvailable') is True and (cam.get('streamName') or cam.get('isCamActive') is True): online=True
            if cam.get('isCamActive') is True: online=True
            for k in ('avatarUrl','avatar','profilePicture','profilePictureUrl'):
                if isinstance(cam.get(k),str) and cam[k].startswith('http'): avatar=cam[k]; break

    # 1) Current/legacy public cam endpoint.
    try:
        u=f'https://stripchat.com/api/front/v2/models/username/{quote(model,safe="")}/cam?uniq={int(time.time()*1000)}'
        req=Request(u,headers=base_headers)
        with urlopen(req,timeout=8) as r: obj=json.loads(r.read().decode('utf-8','replace'))
        inspect_obj(obj)
    except Exception:
        pass

    # 2) Newer username -> model id resolver, then /models/{id}/cam.
    if not api_ok or not online:
        try:
            u=f'https://stripchat.com/api/front/users/user-ids/{quote(model,safe="")}?uniq={int(time.time()*1000)}'
            req=Request(u,headers=base_headers)
            with urlopen(req,timeout=8) as r: obj=json.loads(r.read().decode('utf-8','replace'))
            mid=None
            if isinstance(obj,dict):
                for container in (obj, obj.get('user'), obj.get('data')):
                    if isinstance(container,dict):
                        for k in ('id','userId','modelId'):
                            if container.get(k) is not None: mid=container[k]; break
                    if mid is not None: break
            if mid is not None:
                u=f'https://stripchat.com/api/front/v2/models/{quote(str(mid),safe="")}/cam?uniq={int(time.time()*1000)}'
                req=Request(u,headers=base_headers)
                with urlopen(req,timeout=8) as r: obj=json.loads(r.read().decode('utf-8','replace'))
                inspect_obj(obj)
        except Exception:
            pass

    # 3) Public profile HTML fallback. This also keeps avatar working if the API is blocked.
    try:
        req=Request('https://stripchat.com/'+quote(model,safe=''),headers={**base_headers,'Accept':'text/html,application/xhtml+xml'})
        with urlopen(req,timeout=10) as r: html=r.read().decode('utf-8','replace')
        if re.search(r'"isLive"\s*:\s*true',html,re.I) or re.search(r'"isCamActive"\s*:\s*true',html,re.I): online=True
        if re.search(r'"status"\s*:\s*"public"',html,re.I) and re.search(r'"isCamAvailable"\s*:\s*true',html,re.I): online=True
        # Avoid a broad 'status=public' match: it is only considered live together
        # with an explicit live/cam-available flag.
        mm=re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',html,re.I)
        if not mm: mm=re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',html,re.I)
        if mm and mm.group(1).startswith('http'): avatar=mm.group(1).replace('&amp;','&')
    except Exception:
        pass

    return online,avatar


def monitor_loop():
    while True:
        entries=list(all_entries())
        for kind,m in entries:
            try: online,avatar=fetch_stripchat(m)
            except Exception as e: print('[MONITOR] status failed for',m,e); continue
            with lock:
                x=models.get(kind,{}).get(m)
                if not x: continue
                was=x.get('online',False); x['online']=online
                if avatar: x['avatar']=avatar
            if kind=='auto':
                if online and not was and m not in recordings:
                    try: start_record(m)
                    except Exception as e: print('[MONITOR] auto record failed for',m,e)
                elif not online and was and m in recordings:
                    try: stop_record(m)
                    except Exception as e: print('[MONITOR] auto stop failed for',m,e)
        with lock:
            try: save_models()
            except Exception as e: print('[MONITOR] save failed:',e)
        time.sleep(10)

def snapshot():
    with lock:
        out={'manual':{},'auto':{}}
        for kind in ('manual','auto'):
            for m,x in models[kind].items():
                r=recordings.get(m)
                last=last_results.get(m,{})
                item={**x,'recording':bool(r and r.get('connected')),'recording_state':r.get('state') if r else last.get('state','IDLE'),
                      'file':r.get('file') if r and r.get('file') else last.get('file'),'recording_error':r.get('error') if r else last.get('error'),
                      'recording_reason':r.get('reason') if r else last.get('reason'),'bytes':r.get('bytes_written',last.get('bytes',0)) if r else last.get('bytes',0),
                      'duration':max(0,time.time()-r.get('started',time.time())) if r else last.get('duration',0),
                      'reconnections':r.get('reconnects',last.get('reconnections',0)) if r else last.get('reconnections',0),
                      'ffmpeg_errors':r.get('ffmpeg_errors',last.get('ffmpeg_errors',0)) if r else last.get('ffmpeg_errors',0)}
                out[kind][m]=item
        return out

def _stream_url(model):
    return BACKEND.rstrip('/') + '/stream/' + quote(model, safe='')

def _human_bytes(n):
    n=float(n or 0)
    for unit in ('B','KB','MB','GB','TB'):
        if n < 1024 or unit == 'TB': return f'{n:.1f} {unit}'
        n /= 1024

def _human_duration(seconds):
    seconds=max(0,int(seconds or 0)); h,rem=divmod(seconds,3600); m,sec=divmod(rem,60)
    return f'{h:02d}:{m:02d}:{sec:02d}'

def _log_ffmpeg_line(rec, line):
    line=line.rstrip('\n')
    if not line: return
    rec['log'].write(line+'\n')
    if FFMPEG_ERROR_RE.search(line):
        rec['ffmpeg_errors'] += 1
        rec['last_ffmpeg_error']=line[-500:]
    rec['last_stderr']=line[-500:]

def _terminate_process(proc):
    if proc is None: return
    if proc.poll() is None:
        try: proc.send_signal(signal.SIGINT)
        except Exception:
            try: proc.terminate()
            except Exception: pass
        try: proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try: proc.kill()
            except Exception: pass
            try: proc.wait(timeout=5)
            except Exception: pass

def _build_ffmpeg_cmd(model, url, output='pipe:1'):
    # Keep stream copy. discardcorrupt skips damaged packets instead of making one bad packet fatal.
    # genpts lets FFmpeg synthesize missing PTS; avoid_negative_ts keeps the TS timeline sane.
    return [FFMPEG,'-hide_banner','-loglevel','warning','-nostdin','-y',
            '-user_agent','Mozilla/5.0 SPRECORDER/1.0',
            '-headers','Accept: video/mp2t,*/*;q=0.8\r\nOrigin: http://127.0.0.1:8080\r\n',
            '-rw_timeout','15000000','-reconnect','1','-reconnect_streamed','1','-reconnect_delay_max','5',
            '-fflags','+discardcorrupt+genpts',
            '-err_detect','ignore_err',
            '-i',url,
            '-map','0',
            '-c','copy',
            '-avoid_negative_ts','make_non_negative',
            '-muxpreload','0','-muxdelay','0',
            '-f','mpegts',output]

def _start_ffmpeg(model, rec):
    url=rec['url']
    cmd=_build_ffmpeg_cmd(model,url,'pipe:1')
    rec['log'].write(f"[SPRECORDER] FFmpeg command attempt={rec['attempts']+1}: {' '.join(cmd)}\n")
    try:
        proc=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    except Exception as e:
        rec['error']=f'Impossible de lancer FFmpeg: {e}'
        return None
    rec['proc']=proc
    rec['attempts'] += 1
    return proc

def _read_stderr(proc, rec):
    try:
        while True:
            raw=proc.stderr.readline()
            if not raw: break
            _log_ffmpeg_line(rec,raw.decode('utf-8','replace'))
    except Exception as e:
        with lock: rec['log'].write(f'[SPRECORDER] stderr reader error: {e}\n')


def _safe_recording_path(name):
    raw_name=name or ''
    name=os.path.basename(raw_name)
    if raw_name!=name or not name or name in ('.','..') or '/' in name or '\\' in name or '\x00' in name:
        raise ValueError('Nom de fichier invalide')
    path=os.path.realpath(os.path.join(RECORDINGS,name))
    if os.path.dirname(path)!=os.path.realpath(RECORDINGS):
        raise ValueError('Chemin de fichier invalide')
    if not name.lower().endswith(('.ts','.mp4')):
        raise ValueError('Format non pris en charge')
    return path

def _convert_ts_to_mp4(ts_path, log=None, delete_source=True):
    """Remux TS to MP4, validate the result, then optionally remove the TS."""
    ts_path=os.path.realpath(ts_path)
    if os.path.dirname(ts_path)!=os.path.realpath(RECORDINGS) or not ts_path.lower().endswith('.ts'):
        raise ValueError('Source TS invalide')
    if not os.path.isfile(ts_path) or os.path.getsize(ts_path)<=0:
        raise RuntimeError('Fichier TS absent ou vide')
    mp4_path=ts_path[:-3]+'.mp4'
    if os.path.exists(mp4_path):
        raise RuntimeError('Le MP4 existe déjà; le TS est conservé')
    tmp_path=mp4_path+'.tmp'
    try:
        cmd=[FFMPEG,'-nostdin','-hide_banner','-loglevel','error','-y','-i',ts_path,'-map','0','-c','copy','-movflags','+faststart','-f','mp4',tmp_path]
        with open(os.devnull,'w') as null:
            result=subprocess.run(cmd,stdout=null,stderr=subprocess.PIPE,text=True,timeout=3600)
        if result.returncode!=0:
            raise RuntimeError((result.stderr or 'FFmpeg a échoué').strip()[-1200:])
        if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path)<=0:
            raise RuntimeError('Le MP4 temporaire est vide')
        probe=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:stream=codec_type','-of','json',tmp_path],
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=60)
        if probe.returncode!=0:
            raise RuntimeError('Validation ffprobe impossible: '+(probe.stderr or '').strip()[-800:])
        info=json.loads(probe.stdout or '{}')
        streams=info.get('streams') or []
        duration=float((info.get('format') or {}).get('duration') or 0)
        if not any(x.get('codec_type')=='video' for x in streams) or duration<=0:
            raise RuntimeError('Validation MP4 échouée: piste vidéo ou durée absente')
        os.replace(tmp_path,mp4_path)
        if delete_source:
            os.remove(ts_path)
        if log:
            log.write(f'[CONVERSION] OK {os.path.basename(ts_path)} -> {os.path.basename(mp4_path)} duration={duration:.2f}s\\n')
        return os.path.basename(mp4_path)
    except Exception as e:
        try:
            if os.path.exists(tmp_path): os.remove(tmp_path)
        except OSError: pass
        if log:
            try: log.write(f'[CONVERSION] FAILED {os.path.basename(ts_path)}: {e}; source TS kept\\n')
            except Exception: pass
        raise

def library_items():
    items=[]
    active=set()
    with lock:
        for rec in recordings.values():
            for key in ('ts','final'):
                if rec.get(key): active.add(os.path.realpath(rec[key]))
    for name in sorted(os.listdir(RECORDINGS),key=lambda x: os.path.getmtime(os.path.join(RECORDINGS,x)) if os.path.exists(os.path.join(RECORDINGS,x)) else 0,reverse=True):
        if name.endswith('.part') or not name.lower().endswith(('.ts','.mp4')): continue
        try:
            path=_safe_recording_path(name)
            if not os.path.isfile(path): continue
            items.append({'name':name,'size':os.path.getsize(path),'modified':int(os.path.getmtime(path)),
                          'type':name.rsplit('.',1)[-1].lower(),'active':os.path.realpath(path) in active})
        except (OSError,ValueError): continue
    return items

def _record_worker(model):
    with lock: rec=recordings.get(model)
    if not rec: return
    part=rec['ts']
    try:
        with open(part,'ab',buffering=0) as out:
            while True:
                with lock:
                    if rec['stop_requested']:
                        break
                    attempts=rec['attempts']
                if MAX_RECONNECTS and rec['reconnects'] >= MAX_RECONNECTS:
                    rec['reason']='Nombre maximal de reconnexions atteint'
                    break
                proc=_start_ffmpeg(model,rec)
                if proc is None:
                    with lock: rec['state']='RECONNECTING'; rec['connected']=False
                    time.sleep(RECONNECT_DELAY); rec['reconnects'] += 1
                    continue
                stderr_thread=threading.Thread(target=_read_stderr,args=(proc,rec),daemon=True)
                stderr_thread.start()
                first_data_deadline=time.monotonic()+STARTUP_TIMEOUT
                last_data=time.monotonic()
                got_data=False
                while True:
                    with lock:
                        stopping=rec['stop_requested']
                    if stopping:
                        _terminate_process(proc); break
                    try:
                        ready,_,_=select.select([proc.stdout],[],[],0.5)
                        chunk=proc.stdout.read(CHUNK_SIZE) if ready else b''
                    except Exception as e:
                        with lock: rec['last_ffmpeg_error']=str(e); rec['ffmpeg_errors']+=1
                        chunk=b''
                    now=time.monotonic()
                    if chunk:
                        out.write(chunk); out.flush()
                        with lock:
                            rec['bytes_written'] += len(chunk)
                            rec['last_data']=time.time()
                            if not rec['connected']:
                                rec['connected']=True
                                rec['state']='RECORDING'
                                rec['first_data_at']=time.time()
                                rec['log'].write('[SPRECORDER] RECORDING confirmed: FFmpeg produced data.\n')
                        got_data=True; last_data=now
                        if time.time()-rec.get('last_stats_log',rec['started']) >= 30:
                            with lock:
                                rec['last_stats_log']=time.time()
                                rec['log'].write(f"[SPRECORDER] stats bytes={rec['bytes_written']} duration={_human_duration(time.time()-rec['started'])} reconnects={rec['reconnects']} ffmpeg_errors={rec['ffmpeg_errors']}\n")
                        continue
                    rc=proc.poll()
                    if rc is not None: break
                    if not got_data and now >= first_data_deadline:
                        with lock:
                            rec['reason']='FFmpeg démarré mais aucune donnée reçue dans le délai de démarrage'
                            rec['log'].write('[SPRECORDER] Startup timeout: no output data. Reconnecting.\n')
                        _terminate_process(proc); break
                    if got_data and now-last_data >= STALL_TIMEOUT:
                        with lock:
                            rec['reason']=f'Flux FFmpeg bloqué depuis {STALL_TIMEOUT:.0f}s'
                            rec['log'].write(f'[SPRECORDER] Stall detected after {STALL_TIMEOUT:.0f}s without data. Reconnecting.\n')
                        _terminate_process(proc); break
                    time.sleep(0.2)
                rc=proc.poll()
                stderr_thread.join(timeout=2)
                try: proc.stdout.close()
                except Exception: pass
                try: proc.stderr.close()
                except Exception: pass
                with lock:
                    rec['proc']=None
                    if rec['stop_requested']:
                        rec['reason']=rec.get('reason') or 'Arrêt demandé par l’utilisateur'
                        break
                    rec['connected']=False
                    rec['state']='RECONNECTING'
                    rec['last_exit_code']=rc
                    rec['reconnects'] += 1
                    reason=rec.get('reason') or f'FFmpeg s’est arrêté (code {rc})'
                    rec['reason']=reason
                    rec['log'].write(f'[SPRECORDER] FFmpeg disconnected: {reason}; reconnect #{rec["reconnects"]}.\n')
                time.sleep(RECONNECT_DELAY)
        # Worker only reaches here after requested stop or terminal condition.
        with lock:
            normal=rec['stop_requested'] and rec['bytes_written']>0
            rec['connected']=False
            rec['state']='FINALIZING' if normal else 'ERROR'
            if not normal and not rec.get('reason'): rec['reason']='Enregistrement interrompu sans arrêt normal'
        if normal:
            os.replace(part,rec['final'])
            try:
                converted=_convert_ts_to_mp4(rec['final'],log=rec['log'],delete_source=True)
                with lock:
                    rec['file']=converted
                    rec['converted_file']=converted
                    rec['reason']='Arrêt normal; MP4 validé automatiquement'
            except Exception as conversion_error:
                with lock:
                    rec['file']=os.path.basename(rec['final'])
                    rec['conversion_error']=str(conversion_error)
                    rec['reason']='Arrêt normal; conversion MP4 échouée, TS conservé'
            with lock:
                rec['state']='IDLE'; rec['finished_at']=time.time()
                rec['log'].write(f"[SPRECORDER] NORMAL STOP bytes={rec['bytes_written']} duration={_human_duration(time.time()-rec['started'])} -> {rec['file']}\n")
        else:
            with lock:
                rec['error']=rec.get('reason') or 'FFmpeg arrêté; .ts.part conservé'
                rec['state']='ERROR'
                rec['log'].write(f"[SPRECORDER] FAILED bytes={rec['bytes_written']} duration={_human_duration(time.time()-rec['started'])} reason={rec['error']} -- keeping {part}\n")
    except Exception as e:
        with lock:
            rec['error']=f'Erreur recorder: {e}'
            rec['reason']=rec['error']
            rec['state']='ERROR'; rec['connected']=False
            rec['log'].write('[SPRECORDER] Worker exception: '+traceback.format_exc()+'\n')
    finally:
        try: rec['log'].flush(); rec['log'].close()
        except Exception: pass
        with lock:
            rec['proc']=None
            # Keep failed/reconnecting records visible; remove only after successful finalization.
            if rec.get('state') in ('IDLE','ERROR'):
                last_results[model]={
                    'state':rec.get('state'),'file':rec.get('file'),'error':rec.get('error'),'reason':rec.get('reason'),'conversion_error':rec.get('conversion_error'),
                    'bytes':rec.get('bytes_written',0),'duration':max(0,time.time()-rec.get('started',time.time())),
                    'reconnections':rec.get('reconnects',0),'ffmpeg_errors':rec.get('ffmpeg_errors',0)
                }
                recordings.pop(model,None)

def start_record(model):
    model=safe_model(model)
    with lock:
        existing=recordings.get(model)
        if existing and existing.get('state') not in ('ERROR','IDLE'):
            raise RuntimeError('Cet enregistrement est déjà en cours ou en reconnexion')
        if existing: recordings.pop(model,None)
    url=_stream_url(model)
    stamp=time.strftime('%Y%m%d_%H%M%S')
    base=f'{model}_{stamp}'
    ts=os.path.join(RECORDINGS,base+'.ts.part')
    final=os.path.join(RECORDINGS,base+'.ts')
    logpath=os.path.join(LOGS,'ffmpeg_'+stamp+'_'+re.sub(r'[^A-Za-z0-9_-]','_',model)+'.log')
    try:
        test=os.path.join(RECORDINGS,'.sprecorder_write_test')
        with open(test,'ab') as f: f.write(b'')
        os.remove(test)
    except OSError as e:
        raise RuntimeError(f'Le conteneur ne peut pas écrire dans {RECORDINGS}: {e}')
    log=open(logpath,'w',buffering=1)
    log.write(f'[SPRECORDER] model={model} url={url}\n')
    log.write(f'[SPRECORDER] options=discardcorrupt,genpts,ignore_err,stream_copy\n')
    rec={'proc':None,'ts':ts,'final':final,'file':None,'log':log,'logpath':logpath,'model':model,'url':url,
         'started':time.time(),'bytes_written':0,'reconnects':0,'attempts':0,'ffmpeg_errors':0,'last_data':None,
         'last_ffmpeg_error':None,'last_stderr':None,'connected':False,'state':'CONNECTING','error':None,
         'reason':'En attente des premières données FFmpeg','stop_requested':False}
    with lock:
        last_results.pop(model,None)
        recordings[model]=rec
    threading.Thread(target=_record_worker,args=(model,),name=f'recorder-{model}',daemon=True).start()
    return base+'.ts'

def stop_record(model):
    model=safe_model(model)
    with lock:
        rec=recordings.get(model)
        if not rec: raise RuntimeError('Aucun enregistrement en cours pour '+model)
        rec['stop_requested']=True
        rec['reason']='Arrêt demandé par l’utilisateur'
        proc=rec.get('proc')
    if proc: _terminate_process(proc)
    deadline=time.time()+35
    while time.time()<deadline:
        with lock:
            if model not in recordings: return rec.get('file') or os.path.basename(rec['final'])
            state=rec.get('state')
            if state=='ERROR': raise RuntimeError(rec.get('error') or 'Enregistrement interrompu; .ts.part conservé')
        time.sleep(.25)
    raise RuntimeError('Délai dépassé lors de l’arrêt; le .ts.part a été conservé')

def recover_partials():
    recovered=[]
    for name in os.listdir(RECORDINGS):
        if not name.endswith('.ts.part'): continue
        src=os.path.join(RECORDINGS,name)
        if not os.path.isfile(src) or os.path.getsize(src)<=0: continue
        # We only recover/remux bytes that FFmpeg can parse; damaged H264 remains unrecoverable.
        dst=src[:-5]+'.recovered.ts'
        logpath=os.path.join(LOGS,'recovery_'+re.sub(r'[^A-Za-z0-9_.-]','_',name)+'.log')
        cmd=[FFMPEG,'-hide_banner','-loglevel','warning','-y','-fflags','+discardcorrupt+genpts','-err_detect','ignore_err','-i',src,'-map','0','-c','copy','-avoid_negative_ts','make_non_negative','-f','mpegts',dst]
        with open(logpath,'w',buffering=1) as log:
            log.write(f'[RECOVERY] source={src} size={os.path.getsize(src)}\n')
            try:
                rc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=log,timeout=120).returncode
            except subprocess.TimeoutExpired:
                log.write('[RECOVERY] timeout after 120s; keeping source .ts.part\n')
                rc=-124
        if rc==0 and os.path.exists(dst) and os.path.getsize(dst)>0:
            recovered.append(os.path.basename(dst))
    return recovered

class Handler(BaseHTTPRequestHandler):
    def js(self,code,data):
        b=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/','/index.html'):
            b=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if path=='/health':self.js(200,{'player':'ok','ffmpeg':FFMPEG,'recordings_dir':RECORDINGS});return
        if path=='/record/recover':self.js(200,{'ok':True,'recovered':recover_partials()});return
        if path=='/models/status':self.js(200,{'models':snapshot()});return
        if path=='/library':self.js(200,{'items':library_items()});return
        if path.startswith('/library/file/'):
            name=unquote(path[len('/library/file/'):])
            try: file_path=_safe_recording_path(name)
            except ValueError: self.send_error(400);return
            if not name.lower().endswith('.mp4') or not os.path.isfile(file_path): self.send_error(404);return
            size=os.path.getsize(file_path); start=0; end=size-1; status=200
            rng=self.headers.get('Range')
            if rng:
                match=re.match(r'bytes=(\d*)-(\d*)$',rng.strip())
                if not match: self.send_error(416);return
                if match.group(1): start=int(match.group(1))
                if match.group(2): end=int(match.group(2))
                if not match.group(1) and match.group(2):
                    length=int(match.group(2)); start=max(0,size-length); end=size-1
                if start>=size or end<start: self.send_error(416);return
                end=min(end,size-1); status=206
            length=end-start+1
            self.send_response(status);self.send_header('Content-Type','video/mp4');self.send_header('Accept-Ranges','bytes')
            self.send_header('Content-Length',str(length));self.send_header('Cache-Control','no-store')
            if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with open(file_path,'rb') as f:
                f.seek(start); remaining=length
                while remaining:
                    chunk=f.read(min(64*1024,remaining))
                    if not chunk:break
                    self.wfile.write(chunk);remaining-=len(chunk)
            return
        self.send_error(404)
    def do_POST(self):
        path=urlparse(self.path).path; q=parse_qs(urlparse(self.path).query); m=q.get('model',[''])[0]; kind=q.get('kind',['manual'])[0]
        try:
            if path=='/library/convert':
                name=q.get('file',[''])[0]; src=_safe_recording_path(name)
                if name.lower().endswith('.mp4'): raise ValueError('Ce fichier est déjà un MP4')
                with lock:
                    if any(os.path.realpath(r.get(k,''))==src for r in recordings.values() for k in ('ts','final') if r.get(k)):
                        raise RuntimeError('Impossible de convertir un enregistrement en cours')
                result=_convert_ts_to_mp4(src,delete_source=True)
                self.js(200,{'ok':True,'file':result});return
            if path=='/library/delete':
                name=q.get('file',[''])[0]; target=_safe_recording_path(name)
                with lock:
                    if any(os.path.realpath(r.get(k,''))==target for r in recordings.values() for k in ('ts','final') if r.get(k)):
                        raise RuntimeError('Impossible de supprimer un enregistrement en cours')
                if not os.path.isfile(target): raise FileNotFoundError('Fichier introuvable')
                os.remove(target);self.js(200,{'ok':True});return
            if path=='/models/add':
                kind=kind_name(kind); m=safe_model(m)
                with lock:
                    if m not in models[kind]: models[kind][m]={'avatar':'','online':False};save_models()
                online,avatar=fetch_stripchat(m)
                with lock:
                    if m in models[kind]: models[kind][m].update({'online':online,'avatar':avatar or models[kind][m].get('avatar','')});save_models()
                if kind=='auto' and online and m not in recordings: start_record(m)
                self.js(200,{'ok':True});return
            if path=='/models/remove':
                kind=kind_name(kind); m=safe_model(m)
                if kind=='auto' and m in recordings: stop_record(m)
                with lock: models[kind].pop(m,None);save_models()
                self.js(200,{'ok':True});return
            if path=='/record/toggle':
                m=safe_model(m)
                if m in recordings: f=stop_record(m); self.js(200,{'ok':True,'recording':False,'file':f})
                else: f=start_record(m); self.js(200,{'ok':True,'recording':True,'file':f})
                return
            self.send_error(404)
        except Exception as e:self.js(409,{'ok':False,'error':str(e)})
    def log_message(self,fmt,*args):print('[PLAYER] '+fmt%args)

if __name__=='__main__':
    load_models()
    try:
        test=os.path.join(RECORDINGS,'.sprecorder_write_test')
        with open(test,'ab') as f: f.write(b'')
        os.remove(test)
        print(f'[STARTUP] Recording directory writable: {RECORDINGS}')
    except OSError as e:
        print(f'[STARTUP] WARNING: recording directory is not writable: {e}')
    print(f'[STARTUP] platform={platform.platform()} machine={platform.machine()}')
    print(f'[STARTUP] ffmpeg={shutil.which(FFMPEG) or FFMPEG}')
    print(f'[STARTUP] LD_PRELOAD={os.environ.get("LD_PRELOAD","")!r}')
    try:
        v=subprocess.run([FFMPEG,'-hide_banner','-version'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=10)
        print('[STARTUP] ffmpeg_version='+v.stdout.splitlines()[0] if v.stdout else '[STARTUP] ffmpeg_version=unknown')
        if v.returncode!=0: print('[STARTUP] WARNING: ffmpeg -version failed')
    except Exception as e: print('[STARTUP] WARNING: ffmpeg preflight failed:',e)
    # Never block HTTP startup on recovery of an old .ts.part. Recovery runs in
    # the background and each FFmpeg recovery job has a hard timeout.
    def background_recovery():
        try:
            recovered=recover_partials()
            if recovered: print('[RECOVERY] recovered partials: '+', '.join(recovered), flush=True)
            else: print('[RECOVERY] startup scan complete; no recovered partials', flush=True)
        except Exception as e:
            print('[RECOVERY] scan failed:',e, flush=True)
    threading.Thread(target=background_recovery,daemon=True,name='recovery').start()
    threading.Thread(target=monitor_loop,daemon=True,name='monitor').start()
    print(f'SPRECORDER player: http://{HOST}:{PORT}', flush=True)
    print(f'FFmpeg: {FFMPEG}', flush=True)
    print(f'Recordings: {RECORDINGS}', flush=True)
    try:
        server=ThreadingHTTPServer((HOST,PORT),Handler)
        print(f'[HTTP] listening on {HOST}:{PORT}', flush=True)
        server.serve_forever()
    except Exception:
        print('[HTTP] FATAL SERVER ERROR', flush=True)
        traceback.print_exc()
        raise
