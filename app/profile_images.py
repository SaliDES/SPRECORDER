"""Restricted profile-photo fetcher, using Stripchat referer and a disk cache.

Photos are fetched only from allowlisted HTTPS CDN domains. The browser uses
same-origin /avatar/<model>, avoiding third-party hotlink restrictions.
"""
from __future__ import annotations
import hashlib
import os
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

DOMAINS = ('stripchat.com', 'strpst.com', 'strpst.net', 'doppiocdn.net',
           'doppiocdn.com', 'stripcdn.com')
TYPES = {'image/jpeg','image/png','image/webp','image/avif','image/gif'}
MAX_BYTES = 5 * 1024 * 1024


def allowed_photo(url):
    try:
        parts=urlsplit(url)
        host=(parts.hostname or '').lower()
        return (parts.scheme == 'https' and parts.username is None and parts.password is None
                and parts.port in (None,443)
                and any(host == item or host.endswith('.'+item) for item in DOMAINS))
    except ValueError:
        return False


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination=urljoin(req.full_url,newurl)
        if not allowed_photo(destination):
            raise ValueError('Redirection d’image non autorisée')
        return super().redirect_request(req,fp,code,msg,headers,destination)


class ProfileImages:
    def __init__(self,root:Path):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock()
        self.locks={}

    def get(self,url:str):
        if not allowed_photo(url):
            raise ValueError('URL de profil non autorisée')
        digest=hashlib.sha256(url.encode()).hexdigest()
        data_file=self.root/(digest+'.image')
        type_file=self.root/(digest+'.type')
        with self.lock:
            lock=self.locks.setdefault(digest,threading.Lock())
        with lock:
            if data_file.is_file() and type_file.is_file() and time.time()-data_file.stat().st_mtime<3600:
                return data_file.read_bytes(),type_file.read_text()
            headers={'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/151.0 Safari/537.36',
                     'Referer':'https://stripchat.com/','Accept':'image/avif,image/webp,image/jpeg,image/png,image/gif;q=0.8'}
            opener=build_opener(SafeRedirect())
            with opener.open(Request(url,headers=headers),timeout=12) as response:
                final=response.geturl()
                if not allowed_photo(final):
                    raise ValueError('Redirection d’image non autorisée')
                content_type=response.headers.get('Content-Type','').split(';',1)[0].lower().strip()
                if content_type not in TYPES:
                    raise ValueError('Contenu non image')
                raw=response.read(MAX_BYTES+1)
                if len(raw)>MAX_BYTES or len(raw)<100:
                    raise ValueError('Taille d’image incorrecte')
            temp=self.root/(digest+'.tmp')
            temp.write_bytes(raw)
            os.replace(temp,data_file)
            type_file.write_text(content_type)
            return raw,content_type
