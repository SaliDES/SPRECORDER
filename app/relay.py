#!/usr/bin/env python3
"""Small self-contained Stripchat HLS resolver/proxy for SPRECORDER.
Uses Python stdlib only; no ChanTrail process/container dependency."""
import base64, hashlib, json, logging, os, re, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit, parse_qs, urlencode
from urllib.request import Request, urlopen

LOG=logging.getLogger('sprecorder.relay')
DATA=os.environ.get('SPREC_DATA_DIR','/data')
KEYS_FILE=os.path.join(DATA,'config','mouflon_keys.json')
HOST=os.environ.get('SPREC_RELAY_HOST','0.0.0.0')
PORT=int(os.environ.get('SPREC_RELAY_PORT','3030'))
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36'
HEADERS={'User-Agent':UA,'Accept':'*/*','Origin':'https://stripchat.com','Referer':'https://stripchat.com/'}
CACHE={}; CACHE_LOCK=threading.Lock(); CACHE_TTL=20

def fetch(url, timeout=12, headers=None):
    req=Request(url,headers={**HEADERS,**(headers or {})})
    with urlopen(req,timeout=timeout) as r:
        return r.status, r.headers, r.read(), r.geturl()

def resolve_stream(model):
    now=time.time()
    with CACHE_LOCK:
        item=CACHE.get(model)
        if item and now-item[0]<CACHE_TTL: return item[1]
    uid_url='https://stripchat.com/api/front/users/user-ids/'+quote(model,safe='')
    _,_,raw,_=fetch(uid_url)
    uid_data=json.loads(raw.decode('utf-8','replace'))
    uid=uid_data.get('id') if isinstance(uid_data,dict) else None
    if not uid: raise RuntimeError('Impossible de résoudre l’identifiant Stripchat du modèle')
    cam_url=f'https://stripchat.com/api/front/v2/models/{quote(str(uid),safe="")}/cam?uniq={int(now*1000)}'
    _,_,raw,_=fetch(cam_url,headers={'X-Requested-With':'XMLHttpRequest','Referer':f'https://stripchat.com/{quote(model,safe="")}'})
    data=json.loads(raw.decode('utf-8','replace'))
    user=(data.get('user') or {}).get('user') or {}
    cam=data.get('cam') or {}
    if not user.get('isLive') or user.get('status')!='public' or not cam.get('streamName'):
        raise RuntimeError('Modèle hors ligne, privé ou flux indisponible')
    sid=str(cam['streamName'])
    # The edge host redirects to the currently assigned b-hls shard.
    url=f'https://edge-hls.doppiocdn.com/hls/{sid}/master/{sid}_auto.m3u8'
    with CACHE_LOCK: CACHE[model]=(now,url)
    return url

def load_keys():
    try:
        with open(KEYS_FILE,encoding='utf-8') as f: d=json.load(f)
        k=d.get('keys') or d.get('pkey2pdkey') or {}
        return k if isinstance(k,dict) else {}
    except Exception as e:
        LOG.warning('Lecture clés Mouflon impossible: %s',e); return {}

def decrypt_token(token,pdkey):
    # Mouflon v2: reverse the token, base64 decode, XOR with SHA256(pdkey).
    try:
        enc=token[::-1]
        enc += '='*((4-len(enc)%4)%4)
        raw=base64.b64decode(enc,validate=False)
        key=hashlib.sha256(pdkey.encode()).digest()
        plain=bytes(b ^ key[i%len(key)] for i,b in enumerate(raw))
        return plain.decode('utf-8')
    except Exception:
        return None

def normalize_segment_url(url):
    """Translate media-hls/<b-hls-shard>/... to the matching shard host/HLS path."""
    p=urlsplit(url)
    m=re.match(r'^/((?:b|edge)-hls-\d+)/(.+)$',p.path)
    if p.hostname and p.hostname.startswith('media-hls.') and m:
        shard,rest=m.groups()
        host=shard+'.'+p.hostname.split('.',1)[1]
        return urlunsplit((p.scheme,host,'/hls/'+rest,p.query,p.fragment))
    return url

def rewrite_playlist(text, base_url, local_base, model):
    keys=load_keys(); lines=text.splitlines(); out=[]
    pkey=''
    for ln in lines:
        mm=re.match(r'\s*#EXT-X-MOUFLON:PSCH:v2:([^\s,]+)',ln,re.I)
        if mm: pkey=mm.group(1)
    pdkey=keys.get(pkey)
    def make_local(target):
        target=normalize_segment_url(target)
        u=urlsplit(target); q=parse_qs(u.query,keep_blank_values=True)
        q['psch']=['v2']
        if pkey: q['pkey']=[pkey]
        query=urlencode({k:v[-1] for k,v in q.items()})
        target=urlunsplit((u.scheme,u.netloc,u.path,query,u.fragment))
        return local_base+'/?url='+quote(target,safe='')
    pending_uri=None
    for ln in lines:
        s=ln.strip()
        if s.upper().startswith('#EXT-X-MOUFLON:PSCH'):
            continue
        if s.upper().startswith('#EXT-X-MOUFLON:URI:'):
            pending_uri=s.split(':',2)[-1].strip()
            mt=re.search(r'_(\d+)_([^_]+)_(\d+)',pending_uri)
            if mt and pdkey:
                decoded=decrypt_token(mt.group(2),pdkey)
                if decoded:
                    pending_uri=pending_uri.replace('_'+mt.group(2)+'_','_'+decoded+'_',1)
            pending_uri=urljoin(base_url,pending_uri)
            continue
        if not s:
            out.append(ln); continue
        if s.startswith('#'):
            # URI attributes (KEY, MAP, PART, etc.)
            def attr(m):
                u=urljoin(base_url,m.group(1)); return 'URI="'+make_local(u)+'"'
            ln=re.sub(r'URI="([^"]+)"',attr,ln)
            out.append(ln); continue
        target=pending_uri or urljoin(base_url,s)
        pending_uri=None
        out.append(make_local(target))
    return '\n'.join(out)+'\n'

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,fmt,*args): LOG.info(fmt,*args)
    def send_bytes(self,status,body,ctype='application/octet-stream'):
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p=urlsplit(self.path); q=parse_qs(p.query)
        try:
            if p.path=='/health': return self.send_bytes(200,b'{"relay":"ok"}','application/json')
            if p.path.startswith('/hls/') or p.path.startswith('/stream/'):
                prefix='/hls/' if p.path.startswith('/hls/') else '/stream/'
                model=unquote(p.path[len(prefix):]).strip('/')
                if not model or '/' in model: return self.send_bytes(400,b'Invalid model')
                upstream=resolve_stream(model)
            elif p.path=='/' and q.get('url'):
                upstream=unquote(q['url'][0])
                host=urlsplit(upstream).hostname or ''
                if not (host.endswith('doppiocdn.com') or host.endswith('doppiocdn.live') or host.endswith('doppiocdn.net') or host.endswith('doppiocdn.org') or host.endswith('doppiocdn.media')):
                    return self.send_bytes(403,b'Upstream host refused')
            else: return self.send_bytes(404,b'Not found')
            status,headers,body,final_url=fetch(upstream,headers={'Accept-Encoding':'identity'})
            ctype=headers.get('Content-Type','')
            is_playlist=upstream.lower().split('?',1)[0].endswith('.m3u8') or b'#EXTM3U' in body[:100]
            if is_playlist:
                text=body.decode('utf-8','replace')
                # Preserve the host/port used by this client (NAS IP in browser, localhost in FFmpeg).
                local=f'http://{self.headers.get("Host", "127.0.0.1:"+str(self.server.server_address[1]))}'
                rewritten=rewrite_playlist(text,upstream,local, '')
                return self.send_bytes(200,rewritten.encode(), 'application/vnd.apple.mpegurl')
            return self.send_bytes(status,body,ctype or 'application/octet-stream')
        except Exception as e:
            LOG.warning('Relay request failed for %s: %s',self.path,e)
            return self.send_bytes(502,('Relay error: '+str(e)).encode(),'text/plain; charset=utf-8')

_server=None
def start():
    global _server
    if _server: return _server
    _server=ThreadingHTTPServer((HOST,PORT),Handler)
    threading.Thread(target=_server.serve_forever,daemon=True,name='sprecorder-hls-relay').start()
    LOG.info('Relay HLS autonome actif sur %s:%s',HOST,PORT)
    return _server
