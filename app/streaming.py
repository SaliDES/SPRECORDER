"""SPRECORDER 4: independent HTTP/HLS reader and Mouflon URL handling.

Independent Python implementation following the public HLS workflow documented in
ChanTrail/StripchatRecorder (GPL-3.0), especially streaming/stripchat.rs and
recording/hls.rs. See SOURCE-NOTES.md. No upstream executable or image is used.
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import logging
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger("sprecorder.hls")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0 Safari/537.36"
CDN_TLDS = ("doppiocdn.com", "doppiocdn.org", "doppiocdn.live", "doppiocdn.net")
PSCH = re.compile(r"^#EXT-X-MOUFLON:PSCH:([^:\s]+):([^\s,]+)", re.I)
TOKEN = re.compile(r"_([^_]+)_(\d+(?:_part\d+)?)\.mp4(?:[?#].*)?$", re.I)
MEDIA_SEQ = re.compile(r"^#EXT-X-MEDIA-SEQUENCE:(\d+)")


def allowed_cdn(url: str) -> bool:
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    return (p.scheme == "https" and p.username is None and p.password is None
            and p.port in (None, 443) and any(host == d or host.endswith("."+d)
                                                     for d in CDN_TLDS))


def load_keys(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        pairs = data.get("pkey2pdkey") or data.get("keys") or data
        if isinstance(pairs, list):
            pairs = {x.get("pkey"): x.get("pdkey") for x in pairs if isinstance(x, dict)}
        return {k: v for k, v in pairs.items() if isinstance(k,str) and isinstance(v,str)} if isinstance(pairs,dict) else {}
    except (OSError, ValueError) as exc:
        LOG.debug("Keys unavailable: %s", exc)
        return {}


def fetch(url: str, *, timeout: int = 18, api: bool = False) -> tuple[bytes, str, str]:
    if not api and not allowed_cdn(url):
        raise ValueError("CDN non autorisé: " + url[:100])
    if api and not (url.startswith("https://stripchat.com/api/front/") or
                    url.startswith("https://www.stripchat.com/api/front/")):
        raise ValueError("API URL non autorisée")
    headers = {"User-Agent": UA, "Referer": "https://stripchat.com/", "Accept": "*/*",
               "Accept-Language": "en-US,en;q=0.6", "Accept-Encoding": "identity"}
    def read_one(candidate: str):
        req = Request(candidate, headers=headers)
        with urlopen(req, timeout=timeout) as r:
            final = r.geturl()
            if not api and not allowed_cdn(final):
                raise ValueError("Redirection CDN non autorisée")
            if api and not (final.startswith("https://stripchat.com/") or
                            final.startswith("https://www.stripchat.com/")):
                raise ValueError("Redirection API non autorisée")
            blob = r.read(64*1024*1024+1)
            if len(blob)>64*1024*1024:
                raise ValueError("Segment trop volumineux (>64 Mio)")
            return blob, final, r.headers.get("Content-Type", "")
    try:
        return read_one(url)
    except (HTTPError,URLError,socket.timeout) as first:
        # CDN may serve an identical path on a different TLD. Never invent a
        # different shard or rewrite /b-hls-N/... into /hls/... (old 2.2 bug).
        if not api and urlsplit(url).hostname and urlsplit(url).hostname.startswith("media-hls."):
            domain = next((d for d in CDN_TLDS if d in urlsplit(url).hostname),None)
            if domain:
                errors=[]
                pool = concurrent.futures.ThreadPoolExecutor(max_workers=3)
                try:
                    pending=[pool.submit(read_one,url.replace(domain,other,1))
                             for other in CDN_TLDS if other!=domain]
                    for task in concurrent.futures.as_completed(pending):
                        try: return task.result()
                        except Exception as exc: errors.append(str(exc))
                finally:
                    pool.shutdown(wait=False, cancel_futures=True)
                LOG.debug("CDN alternatives failed: %s",errors)
        if isinstance(first,HTTPError):
            raise RuntimeError(f"HTTP {first.code} vers {urlsplit(url).hostname}{urlsplit(url).path}") from first
        raise RuntimeError(f"Réseau {urlsplit(url).hostname}: {first}") from first


def json_api(url: str) -> dict:
    raw, _, _ = fetch(url, api=True)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Réponse API incorrecte")
    return data


def model_name(model: str) -> str:
    model = model.strip().lstrip("@").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{2,80}", model):
        raise ValueError("Nom de modèle invalide")
    return model



def profile_image(data: dict, model: str = "") -> str:
    """Select a public profile photo from API responses without guessing user URLs.

    Preview/snapshot is a fallback when the API exposes no separate avatar.
    """
    userblock = (data.get("user") or {}) if isinstance(data, dict) else {}
    user = (userblock.get("user") or {}) if isinstance(userblock, dict) else {}
    cam = data.get("cam") or {}
    profile = user.get("profile") or {} if isinstance(user, dict) else {}
    candidates = []
    for obj in (user, profile, userblock, cam, data):
        if not isinstance(obj, dict):
            continue
        for name in ("avatarUrl", "avatarURL", "avatar", "avatar_url", "profileImageUrl",
                     "profilePictureUrl", "profileImage", "profilePicture", "imageUrl",
                     "photoUrl", "pictureUrl", "photo", "image", "previewUrl", "snapshotUrl"):
            value = obj.get(name)
            if isinstance(value, dict):
                value = value.get("url") or value.get("original") or value.get("medium")
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
    for value in candidates:
        if value.startswith("//"):
            value = "https:" + value
        elif value.startswith("/") and not value.startswith("//"):
            value = "https://stripchat.com" + value
        try:
            url = urlsplit(value)
            host = (url.hostname or "").lower()
            suffixes = ("stripchat.com", "strpst.com", "strpst.net", "doppiocdn.net",
                        "doppiocdn.com", "stripcdn.com")
            if (url.scheme == "https" and url.username is None and url.password is None
                and url.port in (None, 443)
                and any(host == domain or host.endswith("." + domain) for domain in suffixes)):
                return value
        except ValueError:
            continue
    return ""

def get_stream_info(model: str) -> dict:
    model = model_name(model)
    b = json_api(f"https://stripchat.com/api/front/v1/broadcasts/{model}")
    sid = str((b.get("item") or {}).get("streamName") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", sid):
        raise ValueError("Flux absent du point d'accès broadcasts")
    info = json_api(f"https://stripchat.com/api/front/v2/models/{sid}/cam")
    user = (info.get("user") or {}).get("user") or {}
    cam = info.get("cam") or {}
    status = user.get("status") or "unknown"
    live = user.get("isLive") is True
    user_id = user.get("id")
    if not isinstance(user_id, int) and not (isinstance(user_id,str) and user_id.isdigit()):
        user_id = cam.get("streamName") or sid
    user_id = str(user_id)
    if not user_id.isdigit():
        raise ValueError("Identifiant de flux numérique manquant")
    return {"model": model, "stream_id": user_id, "live": live, "public": live and status == "public",
            "status": status, "viewers": user.get("viewersCount") or 0,
            "thumbnail": user.get("previewUrl") or "", "avatar": profile_image(info, model)}


def fetch_master(stream_id: str) -> tuple[str, str]:
    if not str(stream_id).isdigit():
        raise ValueError("stream_id invalide")
    def read(tld: str):
        url = f"https://edge-hls.{tld}/hls/{stream_id}/master/{stream_id}_auto.m3u8"
        raw, final, _ = fetch(url)
        text = raw.decode("utf-8", "replace")
        if "#EXTM3U" not in text[:30]:
            raise RuntimeError("Playlist master vide")
        return text, final
    errors = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    try:
        tasks = [pool.submit(read, d) for d in CDN_TLDS]
        for task in concurrent.futures.as_completed(tasks):
            try:
                return task.result()
            except Exception as exc:
                errors.append(str(exc))
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    raise RuntimeError("Master HLS indisponible: " + "; ".join(errors)[:250])


def append_params(url: str, params: dict[str,str]) -> str:
    p = urlsplit(url)
    qs = parse_qs(p.query, keep_blank_values=True)
    for k,v in params.items():
        qs[k] = [v]
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(qs,doseq=True),p.fragment))


def select_variant(master: str, master_url: str, keys: dict, preferred: int = 0) -> tuple[str,int]:
    variants = []
    pairs = []
    bw = height = None
    for raw in master.splitlines():
        line = raw.strip()
        m = PSCH.match(line)
        if m:
            pairs.append((m.group(1),m.group(2)))
        if line.startswith("#EXT-X-STREAM-INF:"):
            mb = re.search(r"BANDWIDTH=(\d+)",line)
            mh = re.search(r"RESOLUTION=\d+x(\d+)",line)
            bw = int(mb.group(1)) if mb else 0
            height = int(mh.group(1)) if mh else 0
        elif bw is not None and line and not line.startswith("#"):
            variants.append((bw,height,urljoin(master_url,line)))
            bw = height = None
    if not variants:
        raise ValueError("Aucune qualité HLS disponible")
    if preferred:
        lower = sorted((v for v in variants if v[1] and v[1] <= preferred),key=lambda x:(x[1],x[0]),reverse=True)
        higher = sorted((v for v in variants if v[1] and v[1] > preferred),key=lambda x:x[1])
        selected = (lower or higher or sorted(variants,reverse=True))[0]
    else:
        selected = max(variants,key=lambda x:x[0])
    url = selected[2]
    if pairs:
        scheme,pkey = next(((sch,pk) for sch,pk in pairs if pk in keys),pairs[0])
        url = append_params(url,{"psch":scheme,"pkey":pkey})
    if not allowed_cdn(url):
        raise ValueError("URL de qualité non autorisée")
    return url, selected[1]


def decrypt_segment_url(url: str, pdkey: str) -> str:
    m = TOKEN.search(url)
    if not m:
        return url
    token = m.group(1)
    encoded = token[::-1]
    encoded += "="*(-len(encoded)%4)
    try:
        encrypted = base64.b64decode(encoded,validate=True)
        digest = hashlib.sha256(pdkey.encode()).digest()
        clear = bytes(v ^ digest[i%32] for i,v in enumerate(encrypted)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return url
    # Preserve valid characters used by Stripchat's token; reject control bytes
    # and traversal. Domain remains constrained to the allow-listed CDN host.
    if not clear or len(clear)>200 or ".." in clear or any(ord(c)<32 for c in clear):
        return url
    return url[:m.start(1)] + clear + url[m.end(1):]


@dataclass(frozen=True)
class Segment:
    seq: int
    url: str
    seconds: float
    init_url: str | None


def parse_media(text: str, media_url: str, keys: dict) -> tuple[list[Segment], bool, float]:
    """Support #EXT-X-MOUFLON:URI in preference to decoy URI as ChanTrail does."""
    if not text.lstrip().startswith("#EXTM3U"):
        raise ValueError("M3U8 manquant")
    start = 0
    current_pdkey = None
    init_url = None
    seconds = 0.
    pending_mouflon = None
    target_duration = 2.
    segments = []
    ended = False
    for raw in text.splitlines():
        line = raw.strip()
        sm = MEDIA_SEQ.match(line)
        if sm: start = int(sm.group(1))
    seq = start
    for raw in text.splitlines():
        line = raw.strip()
        m = PSCH.match(line)
        if m:
            current_pdkey = keys.get(m.group(2))
        elif line.startswith("#EXT-X-TARGETDURATION:"):
            try: target_duration = float(line.split(":",1)[1])
            except ValueError: pass
        elif line.startswith("#EXT-X-MAP:"):
            m = re.search(r'URI="([^"]+)"',line)
            if m:
                init_url = urljoin(media_url,m.group(1))
        elif line.startswith("#EXT-X-MOUFLON:URI:"):
            pending_mouflon = line[len("#EXT-X-MOUFLON:URI:"):]
        elif line.startswith("#EXTINF:"):
            try: seconds = float(line.split(":",1)[1].split(",",1)[0])
            except ValueError: seconds = 2.
        elif line == "#EXT-X-ENDLIST":
            ended = True
        elif line and not line.startswith("#"):
            target = line
            if pending_mouflon:
                target = pending_mouflon
                if not target.startswith(("http://","https://","//")):
                    target = "https://"+target.lstrip("/")
                if target.startswith("//"):
                    target = "https:"+target
                if current_pdkey:
                    target = decrypt_segment_url(target,current_pdkey)
            absolute = urljoin(media_url,target)
            if not allowed_cdn(absolute):
                raise ValueError("Segment HLS hors CDN autorisé")
            segments.append(Segment(seq,absolute,max(.1,seconds),init_url))
            seq += 1
            seconds = 0.
            pending_mouflon = None
    return segments, ended, max(1.,target_duration)
