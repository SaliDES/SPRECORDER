"""Opt-in, verified deletion of TS sources after successful MP4 conversion.

No automatic deletion of historical files. Read-only history mounts stay read-only;
separate explicit writable mounts are required to remove archived originals.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path

try:
    from .bulk_convert import BulkConversion, BusyError
except ImportError:
    from bulk_convert import BulkConversion, BusyError


CONFIRMATION = "SUPPRIMER LES TS"


class CleanupConverted:
    def __init__(self, bulk: BulkConversion, state: Path,
                 writable_roots: dict[str, Path | None] | None = None,
                 *, gate: threading.Lock | None = None, min_age: int = 600):
        self.bulk = bulk
        self.state = state
        self.gate = gate or bulk.gate
        self.min_age = max(600, int(min_age))
        self.writable_roots = {"new": bulk.roots["new"]}
        for label in ("history", "history-v3"):
            path = (writable_roots or {}).get(label)
            # A historical archive is never writable for cleanup unless the
            # operator explicitly provides a separate dedicated mount.
            self.writable_roots[label] = path.resolve() if path is not None and path.is_dir() else None
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.data = {"active": False, "total": 0, "processed": 0,
                     "deleted": 0, "skipped": 0, "failed": 0,
                     "freed_bytes": 0, "potential_bytes": 0, "current": "",
                     "message": "Aucun nettoyage lancé", "errors": [],
                     "started_at": None, "finished_at": None}
        self._restore()

    def _restore(self):
        try:
            data = json.loads((self.state / "cleanup.json").read_text())
            if isinstance(data, dict):
                self.data.update({k: v for k, v in data.items() if k in self.data})
                if self.data["active"]:
                    self.data["active"] = False
                    self.data["message"] = "Nettoyage interrompu : relancer pour réexaminer les fichiers"
        except (OSError, ValueError):
            pass

    def _save_locked(self):
        self.state.mkdir(parents=True, exist_ok=True)
        temporary = self.state / "cleanup.json.tmp"
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        os.replace(temporary, self.state / "cleanup.json")

    def status(self):
        with self.lock:
            return {**self.data, "errors": list(self.data["errors"])}

    def _writable_path(self, label: str, rel: Path, source: Path) -> Path:
        root = self.writable_roots.get(label)
        if root is None:
            raise PermissionError("Accès d'écriture historique non configuré (mettre à jour le YAML TrueNAS)")
        if not root.is_dir():
            raise PermissionError("Dossier de nettoyage introuvable")
        candidate = root / rel
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if resolved != candidate.absolute() or not resolved.is_file() or resolved.is_symlink():
            raise ValueError("Chemin de nettoyage non autorisé")
        source_stat, writable_stat = source.stat(), resolved.stat()
        if (source_stat.st_dev, source_stat.st_ino) != (writable_stat.st_dev, writable_stat.st_ino):
            raise ValueError("Les montages lecture et nettoyage ne pointent pas vers le même fichier")
        return resolved

    def _candidate(self, key: str, *, need_write: bool = True):
        label, source, rel = self.bulk.source(key)
        st = source.stat()
        if st.st_size <= 4096 or time.time() - st.st_mtime < self.min_age:
            raise ValueError("TS trop récent ou incomplet")
        target = self.bulk.output(label, rel)
        if not target.is_file() or target.is_symlink():
            raise ValueError("Aucun MP4 converti correspondant")
        # Refuse symlinks in target parents, including redirected imports directories.
        safe_root = self.bulk.roots["new"]
        resolved_target = target.resolve(strict=True)
        resolved_target.relative_to(safe_root)
        if resolved_target != target.absolute():
            raise ValueError("MP4 cible non autorisé")
        dest = self._writable_path(label, rel, source) if need_write else None
        return label, source, target, dest, st

    def _scan(self):
        candidates = []
        for label, root in self.bulk.roots.items():
            if self.writable_roots.get(label) is None or not root.is_dir():
                continue
            for path in root.rglob("*.ts"):
                try:
                    key = label + "/" + str(path.relative_to(root))
                    _, _, _, _, stat = self._candidate(key, need_write=True)
                    candidates.append((key, stat.st_size))
                except (OSError, ValueError):
                    continue
        return sorted(set(candidates))

    def preview(self):
        with self.lock:
            if self.data["active"]:
                raise BusyError("Un nettoyage est déjà en cours")
        candidates = self._scan()
        return {"files": len(candidates), "bytes": sum(n for _, n in candidates),
                "examples": [key for key, _ in candidates[:8]],
                "by_source": {label: sum(1 for key, _ in candidates if key.startswith(label + "/"))
                              for label in self.bulk.roots},
                "archives_writable": {label: self.writable_roots.get(label) is not None
                                      for label in ("history", "history-v3")},
                "note": "Estimation : chaque MP4 sera vérifié avec ffprobe avant suppression."}

    def start(self, confirmation: str):
        if confirmation != CONFIRMATION:
            raise ValueError("Confirmation incorrecte")
        with self.lock:
            if self.data["active"]:
                raise BusyError("Un nettoyage est déjà en cours")
            if self.bulk.status()["active"]:
                raise BusyError("Une conversion groupée est en cours : attends la fin")
            self.data.update(active=True, message="Recherche des TS convertis…",
                             started_at=time.time(), finished_at=None,
                             total=0, processed=0, deleted=0, skipped=0, failed=0,
                             freed_bytes=0, potential_bytes=0, errors=[], current="")
            self._save_locked()
        self.thread = threading.Thread(target=self._worker, name="sprec-cleanup-converted", daemon=True)
        self.thread.start()
        return self.status()

    def _worker(self):
        try:
            candidates = self._scan()
            with self.lock:
                self.data.update(total=len(candidates), potential_bytes=sum(s for _, s in candidates),
                                 message=f"Vérification de {len(candidates)} TS converti(s)")
                self._save_locked()
            for key, _ in candidates:
                with self.lock:
                    self.data["current"] = key
                try:
                    # Shared FFmpeg/conversion gate; do not race bulk or automatic conversion.
                    with self.gate:
                        self._delete_verified(key)
                    with self.lock:
                        self.data["deleted"] += 1
                except (ValueError, OSError) as exc:
                    with self.lock:
                        self.data["skipped"] += 1
                        self.data["errors"] = (self.data["errors"] + [{"file": key, "message": str(exc)}])[-20:]
                except Exception as exc:
                    with self.lock:
                        self.data["failed"] += 1
                        self.data["errors"] = (self.data["errors"] + [{"file": key, "message": str(exc)}])[-20:]
                with self.lock:
                    self.data["processed"] += 1
                    self._save_locked()
        except Exception as exc:
            with self.lock:
                self.data["failed"] += 1
                self.data["errors"].append({"file": "scan", "message": str(exc)})
        finally:
            with self.lock:
                self.data.update(active=False, current="", finished_at=time.time(),
                                 message=(f"Nettoyage terminé : {self.data['deleted']} TS supprimé(s), "
                                          f"{self.data['skipped']} ignoré(s), {self.data['failed']} erreur(s)"))
                self._save_locked()

    @staticmethod
    def _inspect(path: Path):
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height,channels", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=90,
        )
        if result.returncode:
            raise ValueError("FFprobe ne peut pas lire le fichier : " + result.stderr[-250:])
        info = json.loads(result.stdout)
        duration = float(info.get("format", {}).get("duration") or 0)
        streams = [(s.get("codec_type"), s.get("codec_name"), s.get("width"),
                    s.get("height"), s.get("channels")) for s in info.get("streams", [])
                   if s.get("codec_type") in ("video", "audio")]
        if duration <= 0 or not any(s[0] == "video" for s in streams):
            raise ValueError("Durée ou piste vidéo invalide")
        return duration, streams

    def _delete_verified(self, key: str):
        # Recheck source, target and writable mount *immediately* before deletion.
        label, source, target, writable, before = self._candidate(key, need_write=True)
        target_before = target.stat()
        if target_before.st_size < 4096:
            raise ValueError("MP4 trop petit pour être validé")
        original_duration, source_video = self._inspect(source)
        mp4_duration, mp4_video = self._inspect(target)
        if source_video != mp4_video:
            raise ValueError("Pistes audio/vidéo TS et MP4 incompatibles")
        if original_duration > 5 and not (original_duration * 0.90 <= mp4_duration <= original_duration * 1.10):
            raise ValueError(f"Durées TS/MP4 différentes : {original_duration:.1f}s / {mp4_duration:.1f}s")
        if original_duration <= 5 and abs(original_duration - mp4_duration) > 1.5:
            raise ValueError("Durées TS/MP4 différentes")
        with target.open("rb") as f:
            if b"ftyp" not in f.read(32):
                raise ValueError("En-tête MP4 invalide")
        after = source.stat()
        now_writable = writable.stat()
        target_after = target.stat()
        # A changed inode or file size means the source may be in use.
        signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if signature(before) != signature(after) or signature(before) != signature(now_writable):
            raise ValueError("TS modifié pendant la vérification, conservation de l'original")
        if signature(target_before) != signature(target_after):
            raise ValueError("MP4 modifié pendant la vérification")
        if time.time() - after.st_mtime < self.min_age:
            raise ValueError("TS modifié récemment")
        # Re-resolve all paths, refusing symlinks or mount changes.
        self._candidate(key, need_write=True)
        writable.unlink()
        with self.lock:
            self.data["freed_bytes"] += before.st_size
