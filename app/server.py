"""Single-port HTTP UI, API, authenticated-by-origin controls and HLS preview proxy."""
from __future__ import annotations
import json
import logging
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urljoin, urlsplit

try:
    from . import streaming
    from .engine import Engine, KEYS_FILE
except ImportError:
    import streaming
    from engine import Engine, KEYS_FILE

LOG=logging.getLogger("sprecorder.http")
PORT=int(os.getenv("PORT", "8080"))
STATIC=Path(__file__).resolve().parent/"static"


class API(BaseHTTPRequestHandler):
    engine:Engine
    tokens:dict[str,tuple[float,str]]={}
    token_lock=threading.Lock()
    server_version="SPRECORDER/4.0"

    def log_message(self,fmt,*args):
        LOG.info("%s %s",self.address_string(),fmt%args)

    def send_data(self,data:bytes,ctype="application/octet-stream",status=200,extra=None):
        self.send_response(status)
        self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("X-Frame-Options","DENY")
        for key,value in (extra or {}).items(): self.send_header(key,value)
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError): pass

    def respond(self,obj,status=200):
        self.send_data(json.dumps(obj,ensure_ascii=False).encode(),"application/json; charset=utf-8",status)

    def fail(self,e,status=400): self.respond({"error":str(e)},status)

    def do_GET(self):
        path=urlsplit(self.path).path
        try:
            if path=="/health": return self.respond({"ok":True,"version":"4.0.0-independent"})
            if path=="/api/status": return self.respond(self.engine.status())
            if path=="/api/files": return self.respond({"files":self.engine.list_files()})
            if path.startswith("/media/"):
                from urllib.parse import unquote
                return self.video(unquote(path[len("/media/"):]))
            if path.startswith("/preview/"):
                return self.preview(path)
            if path.startswith("/proxy/"):
                return self.proxy(path[len("/proxy/"):])
            if path in ("/","/index.html"):
                return self.static("index.html","text/html; charset=utf-8")
            if path=="/hls.min.js": return self.static("hls.min.js","application/javascript; charset=utf-8")
            return self.fail("Page inexistante",404)
        except Exception as exc:
            LOG.exception("GET %s failed",path)
            return self.fail(str(exc),502)

    def static(self,name,content_type):
        p=STATIC/name
        if not p.is_file(): return self.fail("Asset absent: "+name,404)
        self.send_data(p.read_bytes(),content_type,extra={"Content-Security-Policy":"default-src 'self'; img-src 'self' data: https:; media-src 'self' blob:; connect-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"})

    def origin_ok(self):
        # Browsers attach Origin to POST; reject cross-site requests.
        origin=self.headers.get("Origin")
        if not origin: return True # CLI and same-site clients
        try:
            host=urlsplit(origin).netloc
            return host==self.headers.get("Host") and urlsplit(origin).scheme in ("http","https")
        except ValueError:
            return False

    def do_POST(self):
        path=urlsplit(self.path).path
        if not self.origin_ok(): return self.fail("Origine refusée",403)
        try:
            n=int(self.headers.get("Content-Length","0"))
            if n<0 or n>4096: return self.fail("Requête trop volumineuse",413)
            data=json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data,dict): raise ValueError("Objet JSON attendu")
            model=data.get("model","")
            if path=="/api/model/add":
                self.engine.add_model(model,data.get("auto",False)); return self.respond({"ok":True})
            if path=="/api/model/auto":
                self.engine.set_auto(model,data.get("auto",False)); return self.respond({"ok":True})
            if path=="/api/model/remove":
                self.engine.remove_model(model); return self.respond({"ok":True})
            if path=="/api/record/start":
                return self.respond({"ok":True,"job":self.engine.start_record(model)})
            if path=="/api/record/stop":
                self.engine.stop_record(model); return self.respond({"ok":True})
            if path=="/api/settings":
                self.engine.set_quality(int(data.get("quality",0))); return self.respond({"ok":True})
            if path=="/api/convert":
                rel=str(data.get("path", ""))
                if not rel.startswith("new/"): return self.fail("Conversion de l'historique interdite",403)
                p=self.engine.file_path(rel)
                if p.suffix.lower()!=".ts": raise ValueError("Fichier TS attendu")
                done=self.engine.converter.convert(p,bypass_age_for_test=False)
                return self.respond({"ok":done,"message":"Conversion lancée et terminée" if done else "TS conservé : voir journal"})
            return self.fail("Route inconnue",404)
        except (ValueError,KeyError,OSError) as exc:
            return self.fail(exc,400)
        except Exception as exc:
            LOG.exception("POST %s failed",path)
            return self.fail(exc,500)

    def token(self,url):
        if not streaming.allowed_cdn(url): raise ValueError("CDN non autorisé")
        token=secrets.token_urlsafe(20)
        with self.token_lock:
            # Avoid unbounded growth, evict expired tokens.
            now=time.time()
            if len(self.tokens)>2000:
                self.tokens={k:v for k,v in self.tokens.items() if v[0]>now}
            self.tokens[token]=(now+120,url)
        return "/proxy/"+token

    def preview(self,path):
        parts=path.strip("/").split("/")
        if len(parts)!=3 or parts[0]!="preview" or parts[2]!="index.m3u8": return self.fail("URL HLS invalide",404)
        model=streaming.model_name(parts[1])
        info=streaming.get_stream_info(model)
        if not info["public"]: return self.fail("Flux public indisponible",404)
        master,master_url=streaming.fetch_master(info["stream_id"])
        variant,_=streaming.select_variant(master,master_url,streaming.load_keys(KEYS_FILE),self.engine.quality)
        return self.render_playlist(variant)

    def render_playlist(self,url):
        raw,final,_=streaming.fetch(url)
        text=raw.decode("utf-8","replace")
        keys=streaming.load_keys(KEYS_FILE)
        # Reuse the *same* Mouflon parser as the recording engine, not a separate ad hoc decrypter.
        segs,_,_=streaming.parse_media(text,final,keys)
        seq=iter(segs)
        output=[]
        pending=None
        for ln in text.splitlines():
            l=ln.strip()
            if l.startswith("#EXT-X-MOUFLON:URI:"):
                pending=l
                continue
            if l.startswith("#EXT-X-MOUFLON:PSCH:"):
                continue
            if l.startswith("#EXT-X-MAP:") or l.startswith("#EXT-X-KEY:"):
                def replace_uri(m):
                    upstream=urljoin(final,m.group(1))
                    return 'URI="'+self.token(upstream)+'"'
                output.append(re.sub(r'URI="([^"]+)"',replace_uri,ln))
            elif l and not l.startswith("#"):
                try: segment=next(seq)
                except StopIteration: return self.fail("HLS incohérent",502)
                output.append(self.token(segment.url))
            else: output.append(ln)
        return self.send_data(("\n".join(output)+"\n").encode(),"application/vnd.apple.mpegurl")

    def proxy(self,token):
        if not re.fullmatch(r"[A-Za-z0-9_-]{15,80}",token): return self.fail("Jeton invalide",404)
        with self.token_lock:
            entry=self.tokens.get(token)
        if not entry or entry[0]<time.time(): return self.fail("Segment HLS expiré",404)
        raw,_,ctype=streaming.fetch(entry[1])
        return self.send_data(raw,ctype or "video/mp4")

    def video(self,rel):
        p=self.engine.file_path(rel)
        size=p.stat().st_size
        start,end=0,size-1
        rng=self.headers.get("Range")
        if rng:
            m=re.fullmatch(r"bytes=(\d*)-(\d*)",rng.strip())
            if not m or (not m.group(1) and not m.group(2)):
                return self.fail("Range invalide",416)
            if m.group(1):
                start=int(m.group(1))
                if m.group(2): end=min(end,int(m.group(2)))
            else:
                start=max(0,size-int(m.group(2)))
            if start>=size or start>end:return self.fail("Range hors limites",416)
        headers={"Accept-Ranges":"bytes"}
        if rng: headers["Content-Range"]=f"bytes {start}-{end}/{size}"
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type","video/mp4" if p.suffix==".mp4" else "video/MP2T")
        self.send_header("Content-Length",str(end-start+1))
        for k,v in headers.items(): self.send_header(k,v)
        self.end_headers()
        try:
            with p.open("rb") as f:
                f.seek(start)
                left=end-start+1
                while left>0:
                    blob=f.read(min(1024*1024,left))
                    if not blob:break
                    self.wfile.write(blob)
                    left-=len(blob)
        except (BrokenPipeError,ConnectionResetError):pass


def main():
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    API.engine=Engine()
    server=ThreadingHTTPServer(("0.0.0.0",PORT),API)
    import signal
    def stop(signum,frame):
        API.engine.shutdown()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    LOG.info("SPRECORDER v4 one UI: http://0.0.0.0:%s",PORT)
    try: server.serve_forever(poll_interval=.5)
    finally:
        API.engine.shutdown(); server.server_close()

if __name__=="__main__": main()
