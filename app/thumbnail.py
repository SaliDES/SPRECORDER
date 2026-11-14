"""On-demand FFmpeg poster frames for MP4 and TS, cached on persistent storage.

Keeps recording files read-only (including previous generations mounted read-only).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path


class Thumbnailer:
    def __init__(self, cache: Path):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.sem = threading.BoundedSemaphore(2)
        self.lock = threading.RLock()
        self.key_locks: dict[str, threading.Lock] = {}

    def get(self, video: Path) -> bytes:
        if video.suffix.lower() not in ('.mp4','.ts') or not video.is_file():
            raise ValueError('Source vidéo invalide')
        st = video.stat()
        # A changed recording produces a different cache entry.
        digest = hashlib.sha256(f'{video}:{st.st_size}:{st.st_mtime_ns}'.encode()).hexdigest()
        cached = self.cache / (digest + '.jpg')
        if cached.is_file() and cached.stat().st_size > 800:
            return cached.read_bytes()
        marker = self.cache / (digest + '.fail')
        if marker.exists() and time.time() - marker.stat().st_mtime < 600:
            raise RuntimeError('Miniature indisponible pour ce fichier')
        with self.lock:
            key_lock = self.key_locks.setdefault(digest, threading.Lock())
        with key_lock:
            if cached.is_file() and cached.stat().st_size > 800:
                return cached.read_bytes()
            with self.sem:
                tmp = self.cache / (digest + f'.{threading.get_ident()}.tmp.jpg')
                try:
                    # Input-side seek is fast for long files. Retry near the beginning
                    # for short recordings and damaged starts.
                    filter_graph = ('scale=480:270:force_original_aspect_ratio=decrease,'
                                    'pad=480:270:(ow-iw)/2:(oh-ih)/2:color=0x0e1624')
                    for offset in ('2', '0'):
                        cmd = ['ffmpeg','-nostdin','-hide_banner','-loglevel','error',
                               '-ss',offset,'-i',str(video),'-an','-sn',
                               '-vf',filter_graph,'-frames:v','1',
                               '-q:v','5','-y',str(tmp)]
                        try:
                            proc = subprocess.run(cmd, capture_output=True, timeout=22)
                            if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 800:
                                os.replace(tmp,cached)
                                marker.unlink(missing_ok=True)
                                return cached.read_bytes()
                        except (subprocess.TimeoutExpired, OSError):
                            pass
                        tmp.unlink(missing_ok=True)
                    marker.touch()
                    raise RuntimeError('Impossible de lire une image de cette vidéo')
                finally:
                    tmp.unlink(missing_ok=True)
