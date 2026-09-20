#!/usr/bin/env python3
import json, os, signal, subprocess, threading, time, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from urllib.request import Request, urlopen

HOST=os.environ.get('SPREC_HOST','0.0.0.0'); PORT=int(os.environ.get('SPREC_PORT','8080')); BACKEND=os.environ.get('SPREC_BACKEND','http://127.0.0.1:3030')
FFMPEG=os.environ.get('SPREC_FFMPEG') or 'ffmpeg'
ROOT=os.path.dirname(os.path.abspath(__file__)); DATA_DIR=os.environ.get('SPREC_DATA_DIR',os.path.join(ROOT,'data')); os.makedirs(DATA_DIR,exist_ok=True)
RECORDINGS=os.path.join(DATA_DIR,'recordings'); os.makedirs(RECORDINGS,exist_ok=True)
MODELS_FILE=os.path.join(DATA_DIR,'models.json')
lock=threading.RLock(); recordings={}; models={}; status_cache={}

PAGE=r'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPRECORDER</title><script src="https://cdn.jsdelivr.net/npm/mpegts.js@1.8.0/dist/mpegts.min.js"></script><style>
*{box-sizing:border-box}body{margin:0;background:#0f1115;color:#eee;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif}main{max-width:1450px;margin:24px auto;padding:0 18px}.top{display:flex;align-items:center;justify-content:space-between}.tabs{display:flex;gap:8px;margin:22px 0 18px}.tab{padding:10px 16px;border:1px solid #333842;background:#191c23;color:#bbb;border-radius:9px;cursor:pointer;font-weight:700}.tab.active{background:#3498db;color:#fff;border-color:#3498db}.panel{display:none}.panel.active{display:block}.top h1{margin:0}.add{display:flex;gap:8px;margin:14px 0}.add input{flex:1;min-width:220px;padding:12px;border-radius:9px;border:1px solid #3a3d45;background:#1b1e25;color:#fff;font-size:15px}.add button,.small{padding:10px 14px;border:0;border-radius:8px;cursor:pointer;font-weight:600}.add button{background:#2ecc71}.summary{color:#aaa;margin:8px 0 18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(430px,1fr));gap:18px}.autoGrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.card{background:#171a21;border:1px solid #2c3039;border-radius:14px;overflow:hidden;box-shadow:0 5px 18px #0004}.head{display:flex;align-items:center;gap:12px;padding:12px 14px}.avatar{width:52px;height:52px;border-radius:50%;object-fit:cover;background:#292d35;border:2px solid #363b46}.name{font-weight:700;font-size:17px}.state{font-size:13px;color:#999;margin-top:3px}.state.live{color:#2ecc71}.spacer{flex:1}.remove{background:#2a2e36;color:#bbb}.videoWrap{position:relative;background:#000}.videoWrap video{display:block;width:100%;aspect-ratio:16/9;background:#000}.overlay{position:absolute;left:10px;top:10px;background:#000b;padding:5px 8px;border-radius:7px;font-size:12px}.controls{display:flex;gap:8px;flex-wrap:wrap;padding:12px 14px}.monitor{background:#3498db;color:white}.record{background:#e67e22;color:#fff}.record.active{background:#c0392b}.autoRec{background:#8e44ad;color:#fff}.meta{padding:0 14px 12px;color:#8d929d;font-size:12px;min-height:18px}.autoCard{padding:15px}.autoControls{display:flex;gap:8px;align-items:center;margin-top:14px}.autoDesc{color:#9ca1ab;font-size:13px;line-height:1.45;margin-top:8px}.empty{padding:50px 20px;text-align:center;color:#777;border:1px dashed #343842;border-radius:12px}.hint{color:#777;font-size:12px;margin-top:14px}.sectionTitle{font-size:20px;font-weight:700}.sectionDesc{color:#999;margin:6px 0 16px}@media(max-width:520px){.grid,.autoGrid{grid-template-columns:1fr}.card{min-width:0}}
</style></head><body><main><div class="top"><h1>SPRECORDER</h1><div id="summary" class="summary"></div></div><div class="tabs"><button class="tab active" data-tab="manual" onclick="switchTab('manual')">Monitoring</button><button class="tab" data-tab="auto" onclick="switchTab('auto')">Enregistrement automatique</button></div>
<section id="manualPanel" class="panel active"><div class="sectionTitle">Monitoring manuel</div><div class="sectionDesc">Ajoute tes modèles, surveille chaque live indépendamment et lance ou arrête l'enregistrement quand tu le souhaites.</div><div class="add"><input id="newModel" placeholder="Ajouter un modèle, ex: MasonxHer" autocomplete="off"><button onclick="addModel('manual')">+ Ajouter</button></div><div id="manualGrid" class="grid"></div><div id="manualEmpty" class="empty">Ajoute un ou plusieurs modèles pour commencer.</div></section>
<section id="autoPanel" class="panel"><div class="sectionTitle">Enregistrement automatique</div><div class="sectionDesc">Les modèles placés ici sont surveillés en permanence. Dès qu'un modèle passe en ligne, SPRECORDER démarre automatiquement l'enregistrement. Lorsqu'il repasse hors ligne, le MP4 est finalisé.</div><div class="add"><input id="newAutoModel" placeholder="Ajouter un modèle à enregistrer automatiquement" autocomplete="off"><button onclick="addModel('auto')">+ Ajouter</button></div><div id="autoGrid" class="autoGrid"></div><div id="autoEmpty" class="empty">Aucun modèle en enregistrement automatique.</div></section>
<div class="hint">Le backend original ChanTrail reste responsable du relay, du HLS et de la résolution Stripchat. Les deux listes sont indépendantes.</div></main><script>
const manualGrid=document.getElementById('manualGrid'),autoGrid=document.getElementById('autoGrid'),manualEmpty=document.getElementById('manualEmpty'),autoEmpty=document.getElementById('autoEmpty'),summary=document.getElementById('summary'),newModel=document.getElementById('newModel'),newAutoModel=document.getElementById('newAutoModel');const players={};let data={manual:{},auto:{}};
[newModel,newAutoModel].forEach((i,idx)=>i.addEventListener('keydown',e=>{if(e.key==='Enter')addModel(idx?'auto':'manual')}));
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function switchTab(t){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('active',b.dataset.tab===t));document.getElementById('manualPanel').classList.toggle('active',t==='manual');document.getElementById('autoPanel').classList.toggle('active',t==='auto')}
function addModel(kind){const input=kind==='auto'?newAutoModel:newModel,m=input.value.trim();if(!m)return;fetch('/models/add?kind='+kind+'&model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);input.value='';refreshStatus()}).catch(e=>alert(e.message))}
function removeModel(kind,m){if(!confirm('Supprimer '+m+' de cette liste ?'))return;if(kind==='manual')stopMonitor(m);fetch('/models/remove?kind='+kind+'&model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);refreshStatus()}).catch(e=>alert(e.message))}
function monitor(m){if(players[m]){stopMonitor(m);return}const video=document.querySelector('[data-video="'+CSS.escape(m)+'"]');if(!video||!window.mpegts||!mpegts.isSupported()){alert('Le lecteur mpegts.js n’est pas disponible.');return}const url=(location.protocol==='https:'?'https://':'http://')+location.hostname+':3030/stream/'+encodeURIComponent(m);const p=mpegts.createPlayer({type:'mpegts',isLive:true,url:url},{enableWorker:false,liveBufferLatencyChasing:true,liveBufferLatencyMaxLatency:1.5,liveBufferLatencyMinRemain:.3});p.on(mpegts.Events.ERROR,(t,d)=>setMeta(m,'Erreur lecteur: '+t+' / '+d));p.attachMediaElement(video);p.load();p.play().catch(()=>{});players[m]=p;setMeta(m,'Monitoring actif');updateControls(m)}
function stopMonitor(m){const p=players[m];if(p){try{p.pause()}catch(e){}try{p.destroy()}catch(e){}delete players[m]}const v=document.querySelector('[data-video="'+CSS.escape(m)+'"]');if(v){v.removeAttribute('src');try{v.load()}catch(e){}}setMeta(m,'Monitoring arrêté');updateControls(m)}
function setMeta(m,s){const el=document.querySelector('[data-meta="'+CSS.escape(m)+'"]');if(el)el.textContent=s}
function updateControls(m){const btn=document.querySelector('[data-monitor="'+CSS.escape(m)+'"]');if(btn)btn.textContent=players[m]?'Arrêter le monitoring':'Monitorer le live'}
function rec(m){fetch('/record/toggle?model='+encodeURIComponent(m),{method:'POST'}).then(r=>r.json()).then(j=>{if(!j.ok)throw Error(j.error);return refreshStatus()}).catch(e=>alert(e.message))}
function renderManual(){const names=Object.keys(data.manual||{});manualEmpty.style.display=names.length?'none':'block';manualGrid.innerHTML=names.map(m=>{const x=data.manual[m],live=x.online,rec=x.recording,mon=!!players[m];return `<section class="card"><div class="head"><img class="avatar" src="${esc(x.avatar||'')}" onerror="this.style.visibility='hidden'"><div><div class="name">${esc(m)}</div><div class="state ${live?'live':''}" data-state="${esc(m)}">${live?'● EN LIGNE':'○ HORS LIGNE'}${rec?' · ENREGISTREMENT':''}</div></div><div class="spacer"></div><button class="small remove" onclick="removeModel('manual','${esc(m)}')">Supprimer</button></div><div class="videoWrap"><video data-video="${esc(m)}" controls autoplay muted playsinline></video><div class="overlay" data-overlay="${esc(m)}" style="display:${live?'block':'none'}">LIVE</div></div><div class="controls"><button class="small monitor" data-monitor="${esc(m)}" onclick="monitor('${esc(m)}')">${mon?'Arrêter le monitoring':'Monitorer le live'}</button><button class="small record ${rec?'active':''}" data-record="${esc(m)}" onclick="rec('${esc(m)}')">${rec?'Arrêter l’enregistrement':'Enregistrer maintenant'}</button></div><div class="meta" data-meta="${esc(m)}">${rec?'Fichier: '+esc(x.file):mon?'Monitoring actif':'Prêt.'}</div></section>`}).join('')}
function renderAuto(){const names=Object.keys(data.auto||{});autoEmpty.style.display=names.length?'none':'block';autoGrid.innerHTML=names.map(m=>{const x=data.auto[m],live=x.online,rec=x.recording;return `<section class="card autoCard"><div class="head" style="padding:0"><img class="avatar" src="${esc(x.avatar||'')}" onerror="this.style.visibility='hidden'"><div><div class="name">${esc(m)}</div><div class="state ${live?'live':''}" data-autostate="${esc(m)}">${live?'● EN LIGNE':'○ HORS LIGNE'}</div></div><div class="spacer"></div><button class="small remove" onclick="removeModel('auto','${esc(m)}')">Supprimer</button></div><div class="autoDesc">${live?(rec?'Enregistrement automatique en cours.':'En ligne — démarrage automatique.'):'Hors ligne — en attente du prochain live.'}</div><div class="autoControls"><button class="small autoRec" onclick="rec('${esc(m)}')">${rec?'Arrêter maintenant':'Enregistrer maintenant'}</button></div><div class="meta" data-autometa="${esc(m)}">${rec?'Fichier: '+esc(x.file):'Surveillance automatique active.'}</div></section>`}).join('')}
function updateSummary(){const a=Object.keys(data.manual||{}).length,b=Object.keys(data.auto||{}).length;summary.textContent=(a+b)+' modèle'+(a+b>1?'s':'')+' · '+a+' monitoring · '+b+' auto'}
function updateStatuses(){Object.keys(data.manual||{}).forEach(m=>{const x=data.manual[m],state=document.querySelector('[data-state="'+CSS.escape(m)+'"]');if(state){state.className='state '+(x.online?'live':'');state.textContent=(x.online?'● EN LIGNE':'○ HORS LIGNE')+(x.recording?' · ENREGISTREMENT':'')}const ov=document.querySelector('[data-overlay="'+CSS.escape(m)+'"]');if(ov)ov.style.display=x.online?'block':'none';updateControls(m);const b=document.querySelector('[data-record="'+CSS.escape(m)+'"]');if(b){b.textContent=x.recording?'Arrêter l’enregistrement':'Enregistrer maintenant';b.className='small record '+(x.recording?'active':'')}});Object.keys(data.auto||{}).forEach(m=>{const x=data.auto[m],state=document.querySelector('[data-autostate="'+CSS.escape(m)+'"]');if(state){state.className='state '+(x.online?'live':'');state.textContent=x.online?'● EN LIGNE':'○ HORS LIGNE'}const meta=document.querySelector('[data-autometa="'+CSS.escape(m)+'"]');if(meta)meta.textContent=x.recording?'Fichier: '+x.file:'Surveillance automatique active.'})}
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
                r=recordings.get(m); out[kind][m]={**x,'recording':bool(r),'file':r['file'] if r else None}
        return out

def start_record(model):
    model=safe_model(model)
    with lock:
        if model in recordings: raise RuntimeError('Cet enregistrement est déjà en cours')
        stamp=time.strftime('%Y%m%d_%H%M%S'); base=f'{model}_{stamp}'; ts=os.path.join(RECORDINGS,base+'.ts.part'); final=os.path.join(RECORDINGS,base+'.mp4'); logpath=os.path.join(RECORDINGS,'ffmpeg_'+stamp+'_'+re.sub(r'[^A-Za-z0-9_-]','_',model)+'.log')
        url=BACKEND+'/stream/'+quote(model,safe=''); log=open(logpath,'w')
        cmd=[FFMPEG,'-hide_banner','-loglevel','warning','-y','-i',url,'-map','0','-c','copy','-f','mpegts',ts]
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log)
        recordings[model]={'proc':proc,'ts':ts,'final':final,'file':base+'.mp4','log':log,'logpath':logpath,'model':model}
        return base+'.mp4'

def stop_record(model):
    model=safe_model(model)
    with lock:
        rec=recordings.pop(model,None)
        if not rec: raise RuntimeError('Aucun enregistrement en cours pour '+model)
    p=rec['proc']
    try:
        if p.poll() is None:
            try:p.send_signal(signal.SIGINT)
            except Exception:p.terminate()
            try:p.wait(timeout=25)
            except subprocess.TimeoutExpired:p.kill();p.wait()
    finally: rec['log'].close()
    if not os.path.exists(rec['ts']) or os.path.getsize(rec['ts'])==0: raise RuntimeError('Le flux enregistré est vide. Consulte '+rec['logpath'])
    cmd=[FFMPEG,'-hide_banner','-loglevel','warning','-y','-i',rec['ts'],'-c','copy','-movflags','+faststart',rec['final']]
    with open(rec['logpath'],'a') as log: rc=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=log).returncode
    if rc!=0 or not os.path.exists(rec['final']) or os.path.getsize(rec['final'])==0: raise RuntimeError('Conversion MP4 échouée. Consulte '+rec['logpath'])
    try:os.remove(rec['ts'])
    except OSError:pass
    return rec['file']

class Handler(BaseHTTPRequestHandler):
    def js(self,code,data):
        b=json.dumps(data,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/','/index.html'):
            b=PAGE.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b);return
        if path=='/health':self.js(200,{'player':'ok'});return
        if path=='/models/status':self.js(200,{'models':snapshot()});return
        self.send_error(404)
    def do_POST(self):
        path=urlparse(self.path).path; q=parse_qs(urlparse(self.path).query); m=q.get('model',[''])[0]; kind=q.get('kind',['manual'])[0]
        try:
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
    load_models(); threading.Thread(target=monitor_loop,daemon=True).start()
    print(f'SPRECORDER player: http://{HOST}:{PORT}');print(f'FFmpeg: {FFMPEG}');print(f'Recordings: {RECORDINGS}')
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
