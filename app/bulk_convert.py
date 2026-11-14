"""Asynchronous, crash-safe bulk TS/.recovered.ts remux for the SPRECORDER library.

Historical mounts are always read-only. Their MP4 copies go under recordings/imports/.
New recordings are remuxed in place; source TS is deleted only after validation.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


def probe(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode:
        raise RuntimeError("ffprobe : " + result.stderr[-300:])
    data = json.loads(result.stdout)
    if not any(s.get("codec_type") == "video" for s in data.get("streams", [])):
        raise ValueError("Aucune piste vidéo")
    duration = float(data.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Durée vidéo invalide")
    return duration


def progress_time(raw: str) -> float:
    """Parse ffmpeg progress out_time (HH:MM:SS.microseconds)."""
    try:
        hours, minutes, seconds = raw.split(":")
        return float(hours) * 3600 + float(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return 0.0


class BusyError(ValueError):
    pass


class BulkConversion:
    def __init__(self, recordings: Path, history: Path, history_v3: Path, state: Path,
                 *, gate: threading.Lock | None = None, settle_seconds: int = 180):
        self.roots = {"new": recordings.resolve(), "history": history.resolve(),
                      "history-v3": history_v3.resolve()}
        self.state = state
        self.gate = gate or threading.Lock()
        self.settle_seconds = settle_seconds
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.data = dict(active=False, total=0, completed=0, converted=0, skipped=0,
                         failed=0, percent=0.0, file_percent=0.0, current_file="",
                         total_bytes=0, completed_bytes=0, message="Aucune conversion groupée lancée",
                         errors=[], started_at=None, finished_at=None)
        self._restore()

    def _restore(self):
        try:
            previous = json.loads((self.state / "batch.json").read_text())
            if isinstance(previous, dict):
                self.data.update({k: v for k, v in previous.items() if k in self.data})
                if self.data["active"]:
                    self.data["active"] = False
                    self.data["message"] = "Conversion interrompue. Relancer pour reprendre les TS restants."
        except (OSError, ValueError):
            pass

    def _persist_locked(self):
        try:
            self.state.mkdir(parents=True, exist_ok=True)
            tmp = self.state / "batch.json.tmp"
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
            os.replace(tmp, self.state / "batch.json")
        except OSError:
            pass

    def status(self) -> dict:
        with self.lock:
            return dict(self.data, errors=list(self.data["errors"]))

    @staticmethod
    def is_ts(path: Path) -> bool:
        return path.name.lower().endswith(".ts") and ".part" not in path.name.lower()

    def source(self, key: str) -> tuple[str, Path, Path]:
        if not isinstance(key, str) or len(key) > 4096 or "/" not in key:
            raise ValueError("Chemin de vidéo invalide")
        label, rel = key.split("/", 1)
        if label not in self.roots or not rel or "\\" in rel:
            raise ValueError("Source inconnue")
        root = self.roots[label]
        if not root.is_dir():
            raise ValueError("Source absente")
        candidate = root / rel
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        # Refuse symlinks, including intermediary directory links.
        if resolved != candidate.absolute() or not resolved.is_file() or not self.is_ts(resolved):
            raise ValueError("Fichier TS non autorisé")
        if any(x.startswith(".") or x.lower() in
               ("ts_fragment", "ts_fragments", "chunks", "tmp", "temp", "fragments")
               for x in resolved.relative_to(root).parts):
            raise ValueError("Fragment en cours de capture")
        if (resolved.parent / "playlist.m3u8").exists():
            raise ValueError("Session HLS en cours")
        return label, resolved, resolved.relative_to(root)

    def output(self, label: str, relative: Path) -> Path:
        # .recovered.ts and .ts are both accepted, without collisions/overwrites.
        base = relative.name[:-3] + ".mp4"
        if label == "new":
            return self.roots["new"] / relative.parent / base
        return self.roots["new"] / "imports" / label / relative.parent / base

    def already_done(self, key: str) -> bool:
        try:
            label, _, rel = self.source(key)
            return self.output(label, rel).is_file()
        except (ValueError, OSError):
            return False

    def eligible(self, key: str) -> bool:
        try:
            label, source, rel = self.source(key)
            info = source.stat()
            return (info.st_size > 4096 and time.time() - info.st_mtime >= self.settle_seconds
                    and not self.output(label, rel).exists())
        except (ValueError, OSError):
            return False

    def scan(self) -> list[str]:
        found: list[str] = []
        for label, root in self.roots.items():
            if not root.is_dir():
                continue
            for path in root.rglob("*.ts"):
                try:
                    key = label + "/" + str(path.relative_to(root))
                    # New imported MP4s are never .ts; no accidental recursion loop.
                    if self.eligible(key):
                        found.append(key)
                except (ValueError, OSError):
                    continue
        return sorted(set(found))

    def start_all(self) -> dict:
        with self.lock:
            if self.data["active"]:
                raise BusyError("Une conversion est déjà en cours")
            # Reserve the worker *before* doing a potentially slow recursive scan.
            self.data.update(active=True, message="Recherche des fichiers TS…")
        worker = threading.Thread(target=self._run_scan, daemon=True, name="sprec-bulk-convert")
        self.thread = worker
        worker.start()
        return self.status()

    def start_one(self, key: str) -> dict:
        # Validate user-selected file before starting background work.
        self.source(key)
        with self.lock:
            if self.data["active"]:
                raise BusyError("Une conversion est déjà en cours")
            self.data.update(active=True, message="Préparation de la conversion…")
        self.thread = threading.Thread(target=self._run, args=([key],), daemon=True,
                                       name="sprec-single-convert")
        self.thread.start()
        return self.status()

    def _run_scan(self):
        try:
            self._run(self.scan())
        except Exception as exc:
            with self.lock:
                self.data.update(active=False, message=f"Échec du scan : {exc}",
                                 finished_at=time.time())
                self._persist_locked()

    def _run(self, keys: list[str]):
        sizes: list[int] = []
        for key in keys:
            try:
                _, source, _ = self.source(key)
                sizes.append(source.stat().st_size)
            except (OSError, ValueError):
                sizes.append(0)
        total_bytes = sum(sizes)
        with self.lock:
            self.data.update(active=True, total=len(keys), completed=0, converted=0,
                             skipped=0, failed=0, percent=0.0, file_percent=0.0,
                             total_bytes=total_bytes, completed_bytes=0, current_file="",
                             errors=[], started_at=time.time(), finished_at=None,
                             message=f"{len(keys)} fichier(s) à examiner")
            self._persist_locked()
        processed_bytes = 0
        for idx, (key, weight) in enumerate(zip(keys, sizes)):
            def update(value: float) -> None:
                with self.lock:
                    self.data["file_percent"] = round(value * 100, 1)
                    self.data["percent"] = round(100 * (processed_bytes + weight * value)
                                                  / max(1, total_bytes), 1)
            with self.lock:
                self.data["current_file"] = key
                self.data["message"] = f"Conversion {idx+1}/{len(keys)} : {key}"
                self.data["file_percent"] = 0.0
            try:
                outcome = self._convert(key, update)
                with self.lock:
                    self.data["converted" if outcome == "converted" else "skipped"] += 1
            except Exception as exc:
                with self.lock:
                    self.data["failed"] += 1
                    self.data["errors"] = (self.data["errors"] +
                                           [{"file": key, "message": str(exc)}])[-30:]
            processed_bytes += weight
            with self.lock:
                self.data["completed"] += 1
                self.data["completed_bytes"] = processed_bytes
                self.data["percent"] = round(100 * processed_bytes / max(1, total_bytes), 1)
                self.data["file_percent"] = 100.0
                self._persist_locked()
        with self.lock:
            self.data.update(active=False, percent=100.0, current_file="", file_percent=0.0,
                             message=f"Terminé : {self.data['converted']} MP4, "
                                     f"{self.data['skipped']} ignoré(s), {self.data['failed']} erreur(s)",
                             finished_at=time.time())
            self._persist_locked()

    def _convert(self, key: str, on_progress: Callable[[float], None]) -> str:
        with self.gate:
            try:
                label, source, rel = self.source(key)
            except (OSError, ValueError):
                # A concurrent automatic conversion may have already removed it.
                return "skipped"
            original = source.stat()
            if original.st_size <= 4096 or time.time()-original.st_mtime < self.settle_seconds:
                return "skipped"  # No partial/actively written TS.
            target = self.output(label, rel)
            if target.exists():
                return "skipped"  # Never overwrite a previous MP4.
            target.parent.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(target.parent).free < int(original.st_size * 1.12) + 64*1024*1024:
                raise RuntimeError("Espace libre insuffisant pour créer le MP4 sans risque")
            duration = probe(source)
            temp = target.with_name(target.name + ".sprec-partial")
            # Restarting after a crash: discard only our own temporary MP4,
            # never an original TS or an already-published MP4.
            if temp.is_symlink():
                raise RuntimeError("Lien symbolique de conversion interdit")
            if temp.exists():
                if not temp.is_file():
                    raise RuntimeError("Chemin temporaire inattendu")
                temp.unlink()
            cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-fflags", "+genpts", "-i", str(source), "-map", "0:v:0", "-map", "0:a?",
                   "-dn", "-sn", "-c", "copy", "-movflags", "+faststart", "-f", "mp4",
                   "-stats_period", "0.5", "-progress", "pipe:1", str(temp)]
            try:
                with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, bufsize=1) as proc:
                    last_error = []
                    for raw in proc.stdout:
                        line = raw.strip()
                        if line.startswith("out_time="):
                            on_progress(min(0.99, max(0.0, progress_time(line.split("=", 1)[1]) / duration)))
                        elif line and not ("=" in line and line.split("=", 1)[0] in
                                           {"frame", "fps", "bitrate", "total_size", "out_time_us",
                                            "out_time_ms", "dup_frames", "drop_frames", "speed", "progress"}):
                            last_error.append(line)
                            last_error = last_error[-12:]
                    code = proc.wait()
                    if code != 0:
                        raise RuntimeError("FFmpeg a échoué : " + " | ".join(last_error)[-450:])
                if not temp.is_file() or temp.stat().st_size < 4096:
                    raise RuntimeError("MP4 incomplet ou vide")
                output_duration = probe(temp)
                if duration > 5 and output_duration < duration * .90:
                    raise RuntimeError(f"MP4 tronqué ({output_duration:.1f}s au lieu de {duration:.1f}s)")
                newstat = source.stat()
                if (newstat.st_ino, newstat.st_size, newstat.st_mtime_ns) != (
                        original.st_ino, original.st_size, original.st_mtime_ns):
                    raise RuntimeError("Le TS a changé pendant la conversion : original conservé")
                with temp.open("rb") as output_file:
                    os.fsync(output_file.fileno())
                # Atomic publish, fails rather than overwriting a pre-existing MP4.
                os.link(temp, target)
                temp.unlink()
                if label == "new":
                    source.unlink()   # Successful, validated conversion only.
                on_progress(1.0)
                return "converted"
            finally:
                # Failed/aborted partial is not a playable MP4 and is safe to remove.
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
