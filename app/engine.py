"""SPRECORDER recording and monitoring engine (single service)."""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

try:
    from . import streaming
    from .converter import Converter
except ImportError:
    import streaming
    from converter import Converter

LOG = logging.getLogger("sprecorder.engine")
DATA = Path(os.getenv("SPREC_DATA_DIR", "/data"))
RECORDINGS = DATA / "recordings"
STATE = DATA / "state"
CONFIG = DATA / "config"
HISTORY = Path(os.getenv("SPREC_HISTORY", "/history"))
HISTORY_V3 = Path(os.getenv("SPREC_HISTORY_V3", "/history-v3"))
KEYS_FILE = CONFIG / "mouflon_keys.json"
POLL_SECONDS = max(10,int(os.getenv("SPREC_MONITOR_SECONDS", "45")))
STALL_SECONDS = max(90,int(os.getenv("SPREC_STALL_SECONDS", "600")))


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding="utf-8")
    os.replace(tmp,path)


def ffprobe(path: Path):
    proc = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration:stream=codec_type", "-of","json",str(path)],capture_output=True,text=True,timeout=60)
    if proc.returncode: raise RuntimeError(proc.stderr[-300:])
    info=json.loads(proc.stdout)
    if not any(s.get("codec_type")=="video" for s in info.get("streams",[])):
        raise RuntimeError("Pas de flux vidéo lisible")
    return info


def remux_fmp4(init: bytes, fragment: bytes) -> bytes:
    proc = subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error","-fflags","+genpts",
                           "-i","pipe:0","-map","0:v:0","-map","0:a?","-c","copy","-f","mpegts","pipe:1"],
                          input=init+fragment,capture_output=True,timeout=50)
    if proc.returncode or len(proc.stdout) < 188*5:
        raise RuntimeError("fMP4→TS: "+proc.stderr.decode("utf8","replace")[-500:])
    return proc.stdout


def merge_session(session_dir: Path, dest: Path) -> Path:
    """Safely produce TS from local HLS chunks; leave source fragments on failures."""
    pl = session_dir / "playlist.m3u8"
    if not pl.exists() or not list(session_dir.glob("chunk_*.ts")):
        raise RuntimeError("Aucun fragment TS à fusionner")
    tmp=dest.with_suffix(".merging.ts")
    if tmp.exists(): tmp.unlink()
    proc = subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","warning", "-y",
                           "-protocol_whitelist","file,crypto,data", "-allowed_extensions","ALL", "-i",str(pl),
                           "-map","0:v:0","-map","0:a?","-c","copy","-f","mpegts",str(tmp)],
                          capture_output=True,text=True,timeout=3600)
    if proc.returncode or not tmp.is_file() or tmp.stat().st_size < 32_768:
        raise RuntimeError("Fusion TS: "+proc.stderr[-500:])
    ffprobe(tmp)
    if dest.exists():
        raise RuntimeError("Le TS de destination existe déjà")
    os.replace(tmp,dest)
    return dest


def safe_public_name(name):
    return streaming.model_name(name)


@dataclass
class Recording:
    model: str
    stop: threading.Event = field(default_factory=threading.Event)
    start: float = field(default_factory=time.time)
    bytes: int = 0
    segments: int = 0
    quality: int = 0
    status: str = "démarrage"
    last_error: str = ""
    thread: threading.Thread | None = None

    def snapshot(self):
        return {"model":self.model,"start":self.start,"bytes":self.bytes,
                "segments":self.segments,"quality":self.quality,"status":self.status,
                "last_error":self.last_error}


class Engine:
    def __init__(self):
        for d in (RECORDINGS,STATE,CONFIG): d.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.jobs:dict[str,Recording]={}
        self.events=deque(maxlen=100)
        self.monitored:dict[str,dict]={}
        self.infos:dict[str,dict]={}
        self.quality=0
        self.running=threading.Event()
        self.running.set()
        self.converter=Converter(RECORDINGS, STATE / "conversions",settle=180,scan_seconds=30)
        self._load()
        self.log("ready","SPRECORDER v4 démarré (moteur HLS intégré)")
        threading.Thread(target=self._startup,daemon=True,name="sprec-startup").start()
        threading.Thread(target=self._convert_loop,daemon=True,name="sprec-convert").start()

    def _load(self):
        try:
            data=json.loads((STATE/"settings.json").read_text())
            self.monitored={safe_public_name(k):v for k,v in data.get("monitored",{}).items() if isinstance(v,dict)}
            self.quality=int(data.get("quality",0))
        except (OSError,ValueError,TypeError): pass

    def _save(self):
        with self.lock:
            atomic_json(STATE/"settings.json",{"monitored":self.monitored,"quality":self.quality})

    def log(self,kind,msg):
        row={"at":datetime.now().strftime("%d/%m %H:%M:%S"),"kind":kind,"message":str(msg)}
        with self.lock:
            self.events.append(row)
        getattr(LOG,"error" if kind=="error" else "info")("%s: %s",kind,msg)

    def status(self):
        with self.lock:
            return {"version":"4.0.0-independent","recordings":[j.snapshot() for j in self.jobs.values()],
                    "monitored":[{"model":k,"auto":v.get("auto",False),"info":self.infos.get(k)} for k,v in sorted(self.monitored.items())],
                    "quality":self.quality,"events":list(self.events)[-30:],
                    "conversion":self.converter.status()}

    def add_model(self,model:str,auto:bool=False):
        model=safe_public_name(model)
        with self.lock:
            self.monitored[model]={"auto":bool(auto)}
            self._save()
        self.log("monitor",f"Modèle ajouté: {model}")

    def set_auto(self,model:str,auto:bool):
        model=safe_public_name(model)
        with self.lock:
            if model not in self.monitored: raise KeyError("Modèle non suivi")
            self.monitored[model]["auto"]=bool(auto)
            self._save()
        self.log("monitor",f"Auto {model}: {'activé' if auto else 'désactivé'}")

    def remove_model(self,model:str):
        model=safe_public_name(model)
        with self.lock:
            self.monitored.pop(model,None)
            self.infos.pop(model,None)
            self._save()
        self.log("monitor",f"Modèle retiré: {model}")

    def set_quality(self,quality:int):
        if quality not in (0,240,480,720,1080): raise ValueError("Qualité invalide")
        with self.lock:
            self.quality=quality
            self._save()

    def start_record(self,model:str):
        model=safe_public_name(model)
        with self.lock:
            if model in self.jobs: raise ValueError("Enregistrement déjà actif")
            job=Recording(model)
            self.jobs[model]=job
            job.thread=threading.Thread(target=self._record,args=(job,),daemon=True,name="record-"+model)
            job.thread.start()
        self.log("record",f"Lancement de {model}")
        return job.snapshot()

    def stop_record(self,model:str):
        model=safe_public_name(model)
        with self.lock:
            job=self.jobs.get(model)
            if not job: raise KeyError("Pas d'enregistrement actif")
            job.status="arrêt demandé"
            job.stop.set()
            if model in self.monitored and self.monitored[model].get("auto"):
                self.monitored[model]["auto"]=False
                self._save()
        self.log("record",f"Arrêt demandé: {model}")

    def _startup(self):
        """Recover interrupted chunk sessions *before* starting auto monitoring."""
        try:
            self._recover_pending()
        except Exception as exc:
            self.log("warning", f"Récupération de démarrage: {exc}")
        if self.running.is_set():
            threading.Thread(target=self._monitor_loop,daemon=True,name="sprec-monitor").start()

    def _recover_pending(self):
        for folder in RECORDINGS.glob("*/.work_????????_??????"):
            if not self.running.is_set(): break
            if not folder.is_dir() or folder.is_symlink(): continue
            model=folder.parent.name
            if not re.fullmatch(r"[A-Za-z0-9_-]{2,80}", model): continue
            when=folder.name.removeprefix(".work_")
            target=folder.parent/(f"{model}_{when}.ts")
            if target.with_suffix(".mp4").exists():
                continue
            if target.exists():
                # Existing TS may be a completed merge awaiting conversion.
                continue
            pl=folder/"playlist.m3u8"
            if not pl.exists() or not list(folder.glob("chunk_*.ts")):continue
            self.log("recover", f"Récupération des fragments : {folder.relative_to(RECORDINGS)}")
            try:
                content=pl.read_text(encoding="utf-8")
                if not content.rstrip().endswith("#EXT-X-ENDLIST"):
                    with pl.open("a") as out:out.write("#EXT-X-ENDLIST\n")
                ts=merge_session(folder,target)
                if self.converter.convert(ts,bypass_age_for_test=True):
                    shutil.rmtree(folder)
                    self.log("success", f"Enregistrement récupéré : {ts.with_suffix('.mp4').name}")
                else:
                    self.log("warning", f"Récupération TS réussie, conversion MP4 à reprendre : {ts.name}")
            except Exception as exc:
                self.log("error", f"Récupération {folder.name}: {exc} (fragments conservés)")

    def _monitor_loop(self):
        while self.running.is_set():
            with self.lock:
                models=[(m,opts.copy()) for m,opts in self.monitored.items()]
            for model,opts in models:
                if not self.running.is_set(): break
                try:
                    info=streaming.get_stream_info(model)
                    with self.lock: self.infos[model]=info
                    if info["public"] and opts.get("auto"):
                        with self.lock: already=model in self.jobs
                        if not already: self.start_record(model)
                except Exception as exc:
                    with self.lock: self.infos[model]={"live":None,"status":"erreur réseau","error":str(exc)}
                    self.log("warning",f"Surveillance {model}: {exc}")
            for _ in range(POLL_SECONDS):
                if not self.running.is_set(): break
                time.sleep(1)

    def _convert_loop(self):
        while self.running.is_set():
            try: self.converter.scan_once()
            except Exception as exc: self.log("error",f"Conversion : {exc}")
            for _ in range(30):
                if not self.running.is_set(): break
                time.sleep(1)

    def _record(self,job:Recording):
        model=job.model
        session=None
        try:
            info=streaming.get_stream_info(model)
            if not info["public"]:
                raise RuntimeError("Modèle hors ligne ou show non public")
            master,url=streaming.fetch_master(info["stream_id"])
            variant,quality=streaming.select_variant(master,url,streaming.load_keys(KEYS_FILE),self.quality)
            job.quality=quality
            stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
            outputdir=RECORDINGS/model
            outputdir.mkdir(parents=True,exist_ok=True)
            session=outputdir/(".work_"+stamp)
            session.mkdir(exist_ok=False)
            target=outputdir/f"{model}_{stamp}.ts"
            job.status="enregistrement"
            self.log("record",f"{model} enregistrement {quality}p")
            known=set()
            init_path=None
            init_data=b""
            last_ok=time.monotonic()
            failures=0
            index=0
            while self.running.is_set() and not job.stop.is_set():
                try:
                    raw,final,_=streaming.fetch(variant)
                    text=raw.decode("utf-8","replace")
                    if "#EXT-X-MOUFLON-ADVERT" in text:
                        raise RuntimeError("Publicité HLS : attente du direct réel")
                    segments,ended,delay=streaming.parse_media(text,final,streaming.load_keys(KEYS_FILE))
                    new=[seg for seg in segments if (seg.seq,seg.url) not in known]
                    # On startup avoid older playback-buffer content.
                    if index == 0 and len(new) > 3: new=new[-3:]
                    for seg in new:
                        if job.stop.is_set() or not self.running.is_set(): break
                        if seg.init_url and seg.init_url!=init_path:
                            init_data,_,_=streaming.fetch(seg.init_url)
                            init_path=seg.init_url
                        rawseg,_,_=streaming.fetch(seg.url)
                        if len(rawseg)<1000: raise RuntimeError("Segment CDN vide")
                        ts=remux_fmp4(init_data,rawseg)
                        part=session/f"chunk_{index:08d}.ts.part"
                        part.write_bytes(ts)
                        final_chunk=session/f"chunk_{index:08d}.ts"
                        os.replace(part,final_chunk)
                        if index == 0:
                            (session/"playlist.m3u8").write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n",encoding="utf-8")
                        with (session/"playlist.m3u8").open("a") as f:
                            if index: f.write("#EXT-X-DISCONTINUITY\n")
                            f.write(f"#EXTINF:{seg.seconds:.3f},\n{final_chunk.name}\n")
                        known.add((seg.seq,seg.url)); index+=1
                        job.segments=index; job.bytes+=len(ts)
                        last_ok=time.monotonic(); failures=0
                    if ended: self.log("record",f"{model} fin de la playlist"); break
                    if time.monotonic()-last_ok > STALL_SECONDS:
                        raise RuntimeError(f"Aucun nouveau fragment depuis {STALL_SECONDS}s")
                    job.stop.wait(min(5,max(1,delay/2)))
                except Exception as exc:
                    failures+=1
                    job.last_error=str(exc)
                    self.log("warning",f"{model} : {exc}")
                    if failures>=3:
                        # Recover from sharded CDN changes (b-hls-14 -> b-hls-16, etc.).
                        try:
                            updated=streaming.get_stream_info(model)
                            if not updated["public"]:
                                self.log("record",f"{model} est hors ligne; finalisation")
                                break
                            master,url=streaming.fetch_master(updated["stream_id"])
                            variant,job.quality=streaming.select_variant(master,url,streaming.load_keys(KEYS_FILE),self.quality)
                        except Exception as retry_exc:
                            self.log("warning",f"Rafraîchissement {model}: {retry_exc}")
                        failures=0
                    if time.monotonic()-last_ok > STALL_SECONDS:
                        raise RuntimeError(f"Enregistrement bloqué > {STALL_SECONDS}s: {exc}")
                    job.stop.wait(5)
            job.status="fusion TS"
            if job.segments:
                with (session/"playlist.m3u8").open("a") as f: f.write("#EXT-X-ENDLIST\n")
                ts=merge_session(session,target)
                self.log("record",f"TS enregistré: {ts.relative_to(RECORDINGS)}")
                job.status="conversion MP4"
                if self.converter.convert(ts,bypass_age_for_test=True):
                    shutil.rmtree(session) # Finished TS+MP4 proven, source TS removed safely.
                    self.log("success",f"MP4 enregistré: {ts.with_suffix('.mp4').relative_to(RECORDINGS)}")
                else:
                    self.log("warning",f"Conversion MP4 impossible: TS conservé ({ts.name})")
            else:
                self.log("warning",f"{model}: zéro fragment; aucun fichier généré")
            job.status="terminé"
        except Exception as exc:
            job.status="échec"
            job.last_error=str(exc)
            self.log("error",f"{model}: {exc}" + (f" (fragments conservés: {session})" if session else ""))
        finally:
            with self.lock: self.jobs.pop(model,None)

    def list_files(self):
        roots=[("new",RECORDINGS)]
        if HISTORY.is_dir(): roots.append(("history",HISTORY))
        if HISTORY_V3.is_dir(): roots.append(("history-v3",HISTORY_V3))
        out=[]
        for source,root in roots:
            root=root.resolve()
            try:
                for ext in ("*.mp4","*.ts"):
                    for p in root.rglob(ext):
                        if len(out)>=2000: break
                        if p.is_symlink() or not p.is_file() or any(x.startswith('.') for x in p.relative_to(root).parts): continue
                        rel=p.relative_to(root)
                        st=p.stat()
                        out.append({"name":p.name,"path":source+"/"+str(rel),"source":source,
                                    "size":st.st_size,"modified":int(st.st_mtime),"type":p.suffix[1:]})
            except OSError as exc:
                self.log("warning",f"Bibliothèque {source}: {exc}")
        return sorted(out,key=lambda x:x['modified'],reverse=True)

    def file_path(self,relative:str):
        if relative.startswith("new/"): root=RECORDINGS.resolve(); rel=relative[4:]
        elif relative.startswith("history/") and HISTORY.is_dir(): root=HISTORY.resolve(); rel=relative[8:]
        elif relative.startswith("history-v3/") and HISTORY_V3.is_dir(): root=HISTORY_V3.resolve(); rel=relative[11:]
        else: raise ValueError("Origine invalide")
        candidate=root/rel
        p=candidate.resolve(strict=True)
        p.relative_to(root)
        if p!=candidate.absolute() or p.suffix.lower() not in (".mp4",".ts") or not p.is_file():
            raise ValueError("Fichier invalide")
        return p

    def shutdown(self):
        self.running.clear()
        with self.lock:
            jobs=list(self.jobs.values())
            for job in jobs: job.stop.set()
        # Docker stop_grace_period gives finishing and MP4 conversion time.
        # If killed during conversion, the .ts and .work fragments remain intact.
        for job in jobs:
            if job.thread and job.thread is not threading.current_thread():
                job.thread.join(timeout=95)
