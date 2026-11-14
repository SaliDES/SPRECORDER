"""SPRECORDER v4: safe TS to MP4 conversion, no upstream service."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

VERSION = "4.0.0-independent"
RECORDINGS = Path(os.getenv("SPREC_RECORDINGS", "/recordings"))
STATE = Path(os.getenv("SPREC_STATE", "/state"))
HISTORY = Path(os.getenv("SPREC_HISTORY", "/history"))
PORT = int(os.getenv("SPREC_PORT", "8080"))
ENGINE_PORT = int(os.getenv("SPREC_ENGINE_PORT", "13032"))
SCAN_SECONDS = max(5, int(os.getenv("SPREC_SCAN_SECONDS", "30")))
SETTLE_SECONDS = max(60, int(os.getenv("SPREC_SETTLE_SECONDS", "180")))
MIN_TS_BYTES = max(1024, int(os.getenv("SPREC_MIN_TS_BYTES", "4096")))
RETRY_SECONDS = 1800
CHUNK = 1024 * 1024
LOG = logging.getLogger("sprecorder")
STOP = threading.Event()


def media_info(path: Path) -> dict:
    """Inspect an actual media file; does not decode/re-encode its video."""
    cmd = ["ffprobe", "-v", "error", "-show_entries",
           "format=duration:stream=codec_type,codec_name", "-of", "json", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode:
        raise RuntimeError("ffprobe: " + proc.stderr[-500:])
    result = json.loads(proc.stdout)
    if not any(s.get("codec_type") == "video" for s in result.get("streams", [])):
        raise ValueError("Aucune piste vidéo reconnue")
    return result


def duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        return 0


def is_valid_media(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 4_096 and duration(media_info(path)) > 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return False


@dataclass
class Observation:
    stamp: tuple[int, int, int]
    first_seen: float
    checks: int = 1


class Converter:
    def __init__(self, root: Path, state: Path, *, settle: int = SETTLE_SECONDS,
                 minimum: int = MIN_TS_BYTES, scan_seconds: int = SCAN_SECONDS):
        self.root = root.resolve()
        self.state = state
        self.settle = settle
        self.minimum = minimum
        self.scan_seconds = scan_seconds
        self.seen: dict[str, Observation] = {}
        self.retry_after: dict[str, float] = {}
        self.mu = threading.Lock()
        self.info = {"version": VERSION, "active": None, "last_success": None,
                     "last_error": None, "converted": 0, "events": []}

    def event(self, kind: str, message: str):
        item = {"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": kind, "message": str(message)}
        with self.mu:
            self.info["events"] = (self.info["events"] + [item])[-30:]
            if kind == "error":
                self.info["last_error"] = item
            elif kind == "success":
                self.info["last_success"] = item
                self.info["converted"] += 1
            self._save_locked()
        (LOG.error if kind == "error" else LOG.info)("%s: %s", kind, message)

    def _save_locked(self):
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            tmp = self.state / "status.json.tmp"
            tmp.write_text(json.dumps(self.info, ensure_ascii=False, indent=2))
            os.replace(tmp, self.state / "status.json")
        except OSError as exc:
            LOG.warning("Impossible d'écrire le statut : %s", exc)

    def status(self):
        with self.mu:
            return json.loads(json.dumps(self.info))

    def eligible(self, file: Path, now: float | None = None) -> bool:
        """Only finalized-looking TS files; never touch partials or ongoing writes."""
        now = time.time() if now is None else now
        if file.suffix.lower() != ".ts" or file.is_symlink():
            return False
        if any(part.startswith(".") or part.lower() in
               {"ts_fragment", "ts_fragments", "chunks", "tmp", "temp", "fragments"}
               for part in file.relative_to(self.root).parts[:-1]):
            return False
        if file.name.startswith(".") or ".part" in file.name.lower():
            return False
        # ChanTrail writes intermediate .ts chunks inside a session folder.
        # Only files merged into its final model_YYYYMMDD_HHMMSS.ts naming qualify.
        if not re.search(r"_\d{8}_\d{6}\.ts$", file.name, re.I):
            return False
        if (file.parent / "playlist.m3u8").exists():
            return False
        try:
            stat = file.stat()
        except OSError:
            return False
        if stat.st_size < self.minimum or now - stat.st_mtime < self.settle:
            return False
        if file.with_suffix(".mp4").exists():
            return False  # Never overwrite a user's MP4.
        key = str(file)
        if self.retry_after.get(key, 0) > now:
            return False
        stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        old = self.seen.get(key)
        if old is None or old.stamp != stamp:
            self.seen[key] = Observation(stamp, now)
            return False
        old.checks += 1
        return old.checks >= 2 and now - old.first_seen >= self.scan_seconds * 0.75

    def convert(self, source: Path, *, bypass_age_for_test: bool = False) -> bool:
        """Remux atomically; never remove TS on any failure or file mutation."""
        try:
            if source.is_symlink() or source.suffix.lower() != ".ts":
                return False
            if not re.search(r"_\d{8}_\d{6}\.ts$", source.name, re.I):
                return False
            if (source.parent / "playlist.m3u8").exists():
                return False
            source = source.resolve(strict=True)
            source.relative_to(self.root)
            before = source.stat()
            if before.st_size < self.minimum:
                return False
            if not bypass_age_for_test and time.time() - before.st_mtime < self.settle:
                return False
            dest = source.with_suffix(".mp4")
            # Destination is never overwritten. A leftover temp is safe to retry.
            if dest.exists():
                return False
            tmp = source.with_name(source.stem + ".sprec-partial.mp4")
            free = shutil.disk_usage(source.parent).free
            required = int(before.st_size * 1.12) + 64 * 1024 * 1024
            if free < required:
                raise RuntimeError("Espace insuffisant pour convertir sans risquer le TS")
            original = media_info(source)
            with self.mu:
                self.info["active"] = str(source.relative_to(self.root))
                self._save_locked()
            self.event("start", str(source.relative_to(self.root)))
            cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning", "-y",
                   "-fflags", "+genpts", "-i", str(source),
                   "-map", "0:v:0", "-map", "0:a?", "-dn", "-sn",
                   "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(tmp)]
            with subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  text=True) as proc:
                _, err = proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError(f"FFmpeg code {proc.returncode}: {err[-600:]}")
            if not tmp.exists() or tmp.stat().st_size < 4_096:
                raise RuntimeError("MP4 absent ou anormalement petit")
            converted = media_info(tmp)
            old_duration, new_duration = duration(original), duration(converted)
            if new_duration <= 0 or (old_duration > 5 and new_duration < old_duration * 0.90):
                raise RuntimeError(f"MP4 incomplet (durées TS={old_duration:.1f}s MP4={new_duration:.1f}s)")
            after = source.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns):
                raise RuntimeError("Le TS a changé pendant la conversion : suppression annulée")
            # Flush output and atomically publish without overwriting an existing MP4.
            with tmp.open("rb") as out:
                os.fsync(out.fileno())
            os.link(tmp, dest)  # EEXIST => keep TS, never overwrite existing MP4.
            tmp.unlink()
            source.unlink()   # Only after verified MP4 publication and stable TS.
            self.event("success", f"{dest.relative_to(self.root)} ({dest.stat().st_size} octets)")
            return True
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            self.event("error", f"{source.name}: {exc}")
            self.retry_after[str(source)] = time.time() + RETRY_SECONDS
            return False
        finally:
            with self.mu:
                self.info["active"] = None
                self._save_locked()

    def scan_once(self):
        live_keys = set()
        try:
            files = list(self.root.rglob("*.ts"))
        except OSError as exc:
            self.event("error", f"Scan impossible : {exc}")
            return
        for source in files:
            key = str(source)
            live_keys.add(key)
            if STOP.is_set():
                break
            if self.eligible(source):
                self.convert(source)
        # Avoid permanent growth of observation state.
        self.seen = {k: v for k, v in self.seen.items() if k in live_keys}
        self.retry_after = {k: t for k, t in self.retry_after.items()
                            if k in live_keys and t > time.time()}

    def run(self):
        self.event("ready", "Surveillance des TS terminés démarrée")
        while not STOP.is_set():
            self.scan_once()
            STOP.wait(self.scan_seconds)


