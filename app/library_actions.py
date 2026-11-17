"""Favorite recordings, on-demand disk stats, and guarded per-file deletion.

All library roots are read-only by default except SPRECORDER's own recordings.
To delete archived files, the admin must explicitly map separate writable mounts.
Nothing is deleted automatically by this module.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path


class LibraryActions:
    def __init__(self, roots: dict[str, Path], state: Path,
                 writable_roots: dict[str, Path | None],
                 *, gate: threading.Lock, min_age_seconds: int = 30):
        self.roots = {label: Path(path).resolve() for label, path in roots.items()}
        self.writable_roots = {}
        for label, path in writable_roots.items():
            self.writable_roots[label] = Path(path).resolve() if path is not None and Path(path).is_dir() else None
        self.writable_roots['new'] = self.roots['new']
        self.state = Path(state)
        self.path = self.state / 'favorites.json'
        self.gate = gate
        self.min_age_seconds = max(0, int(min_age_seconds))
        self.lock = threading.RLock()
        self.favorites: set[str] = set()
        self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(raw, dict) and isinstance(raw.get('favorites'), list):
                self.favorites = {s for s in raw['favorites'] if isinstance(s, str) and len(s) < 4096}
        except (OSError, ValueError):
            pass

    def _save(self):
        self.state.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.json.tmp')
        try:
            temp.write_text(json.dumps({'favorites': sorted(self.favorites)}, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(temp, self.path)
        finally:
            # Crash-safe, no lost favorites file if writing fails.
            if temp.exists():
                temp.unlink()

    @staticmethod
    def canonical(key: str) -> str:
        """Store favorites by *recording*, not file extension.

        A converted historic TS and its imported MP4 share the same star.
        Likewise a new .ts/.recovered.ts and its .mp4 share one star.
        """
        if key.startswith('new/imports/history/'):
            key = 'history/' + key[len('new/imports/history/'):]
        elif key.startswith('new/imports/history-v3/'):
            key = 'history-v3/' + key[len('new/imports/history-v3/'):]
        if key.lower().endswith('.mp4') or key.lower().endswith('.ts'):
            return key.rsplit('.', 1)[0]
        raise ValueError('Enregistrement non pris en charge')

    @staticmethod
    def _safe_key(key: str) -> tuple[str, Path]:
        if not isinstance(key, str) or not key or len(key) > 4096 or '/' not in key or '\\' in key or '\x00' in key:
            raise ValueError('Chemin invalide')
        label, subpath = key.split('/', 1)
        rel = Path(subpath)
        if label not in ('new', 'history', 'history-v3') or not subpath or rel.is_absolute():
            raise ValueError('Bibliothèque inconnue')
        if any(part in ('', '.', '..') or part.startswith('.') or part.lower() in
               ('ts_fragment', 'ts_fragments', 'chunks', 'tmp', 'temp', 'fragments') for part in rel.parts):
            raise ValueError('Fichier temporaire non autorisé')
        name = rel.name.lower()
        if not (name.endswith('.mp4') or name.endswith('.ts')) or any(s in name for s in ('.sprec-partial', '.merging', '.part')):
            raise ValueError('Seuls les fichiers vidéo terminés sont autorisés')
        return label, rel

    def locate(self, key: str) -> tuple[str, Path, Path]:
        label, rel = self._safe_key(key)
        root = self.roots[label]
        candidate = root / rel
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        # Ensures no symlink in any path component. No hidden work fragments.
        if resolved != candidate.absolute() or candidate.is_symlink() or not resolved.is_file():
            raise ValueError('Fichier hors bibliothèque')
        if (resolved.parent / 'playlist.m3u8').exists():
            raise ValueError('Session HLS potentiellement active')
        return label, rel, resolved

    def is_favorite(self, key: str) -> bool:
        with self.lock:
            return self.canonical(key) in self.favorites

    def count(self) -> int:
        with self.lock:
            return len(self.favorites)

    def set_favorite(self, key: str, enabled: bool) -> bool:
        # Must exist in the library. Prevent arbitrary saved keys.
        self.locate(key)
        canonical = self.canonical(key)
        if not isinstance(enabled, bool):
            raise ValueError('Valeur de favori invalide')
        with self.lock:
            if enabled: self.favorites.add(canonical)
            else: self.favorites.discard(canonical)
            self._save()
            return canonical in self.favorites

    def disk_stats(self) -> dict:
        result = {}
        for label, root in self.roots.items():
            try:
                if root.is_dir():
                    usage = shutil.disk_usage(root)
                    result[label] = {'total': usage.total, 'used': usage.used,
                                     'free': usage.free,
                                     'percent': round(100 * usage.used / usage.total, 1) if usage.total else 0}
            except OSError:
                pass
        return result

    def can_delete(self, key: str) -> bool:
        label, _ = self._safe_key(key)
        return self.writable_roots.get(label) is not None

    def _writable(self, label: str, rel: Path, read_source: Path) -> Path:
        root = self.writable_roots.get(label)
        if root is None or not root.is_dir():
            raise PermissionError('Suppression de cet historique impossible : montage de nettoyage TrueNAS absent')
        candidate = root / rel
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        if resolved != candidate.absolute() or candidate.is_symlink() or not resolved.is_file():
            raise ValueError('Chemin de suppression non autorisé')
        a, b = read_source.stat(), resolved.stat()
        if (a.st_dev, a.st_ino) != (b.st_dev, b.st_ino):
            raise ValueError('Les montages lecture et écriture ne correspondent pas')
        return resolved

    def delete_one(self, key: str, confirmation: str, *, busy: bool,
                   active_models: set[str] | None = None,
                   converting: str | None = None) -> dict:
        """Delete exactly one chosen media file, never its MP4/TS counterpart.

        Locks out bulk/cleanup and per-file FFmpeg conversion, checks file
        age/identity right before unlink. A filename confirmation is mandatory.
        """
        label, rel, source = self.locate(key)
        if confirmation != 'SUPPRIMER ' + key:
            raise ValueError('Confirmation de suppression incorrecte')
        if busy:
            raise ValueError('Une conversion ou un nettoyage est en cours : réessaie après la fin')
        if label == 'new' and converting and converting in (str(rel), key):
            raise ValueError('Conversion de ce fichier en cours')
        if label == 'new' and active_models and any(rel.name.startswith(model + '_') for model in active_models):
            raise ValueError('Attends la fin de la capture de ce modèle avant de supprimer sa vidéo')
        if not self.gate.acquire(blocking=False):
            raise ValueError('Un traitement vidéo est en cours : réessaie plus tard')
        try:
            # All validation is repeated while the shared processing gate is held.
            label, rel, source = self.locate(key)
            before = source.stat()
            if time.time() - before.st_mtime < self.min_age_seconds:
                raise ValueError('Fichier trop récent : attends la fin de son écriture')
            writable = self._writable(label, rel, source)
            current = source.stat()
            second = writable.stat()
            fingerprint = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
            if fingerprint(before) != fingerprint(current) or fingerprint(before) != fingerprint(second):
                raise ValueError('Fichier modifié durant la vérification')
            # Guard against last-minute replacement by a symlink or alternate inode.
            self.locate(key)
            self._writable(label, rel, source)
            writable.unlink()
        finally:
            self.gate.release()
        return {'deleted': key, 'bytes': before.st_size}

    def prune_favorite_if_orphaned(self, key: str, existing_keys: list[str]):
        canonical = self.canonical(key)
        if not any(self.canonical(p) == canonical for p in existing_keys):
            with self.lock:
                if canonical in self.favorites:
                    self.favorites.remove(canonical)
                    self._save()
