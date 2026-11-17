"""v4.4: favorite persistence, free space and safe manual deletion."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from library_actions import LibraryActions


class LibraryActionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.new = self.root / 'new'
        self.history = self.root / 'old'
        self.h3 = self.root / 'old3'
        self.state = self.root / 'state'
        for p in (self.new, self.history, self.h3, self.state): p.mkdir()
        self.gate = threading.Lock()

    def actions(self, historic=False, age=30):
        return LibraryActions(
            {'new': self.new, 'history': self.history, 'history-v3': self.h3}, self.state,
            {'history': self.history if historic else None,
             'history-v3': self.h3 if historic else None}, gate=self.gate,
            min_age_seconds=age)

    def media(self, root, path, size=15000, age=3600):
        p = root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'X' * size)
        os.utime(p, (time.time() - age, time.time() - age))
        return p

    def test_favorites_persist_and_migrate_to_mp4(self):
        actions = self.actions()
        self.media(self.new, 'model/Hello_123.recovered.ts')
        self.assertTrue(actions.set_favorite('new/model/Hello_123.recovered.ts', True))
        new_engine = self.actions()
        self.assertTrue(new_engine.is_favorite('new/model/Hello_123.recovered.mp4'))
        self.assertTrue(new_engine.set_favorite('new/model/Hello_123.recovered.ts', False) is False)
        self.assertFalse(self.actions().is_favorite('new/model/Hello_123.recovered.mp4'))

    def test_historical_imported_mp4_shares_same_favorite(self):
        self.media(self.history, 'Foo_20260101_010101.ts')
        self.media(self.new, 'imports/history/Foo_20260101_010101.mp4')
        actions = self.actions()
        actions.set_favorite('history/Foo_20260101_010101.ts', True)
        self.assertTrue(actions.is_favorite('new/imports/history/Foo_20260101_010101.mp4'))
        self.assertTrue(self.actions().is_favorite('history/Foo_20260101_010101.ts'))

    def test_single_delete_ts_without_deleting_mp4(self):
        actions = self.actions()
        ts = self.media(self.new, 'Model_20260101_111111.ts')
        mp4 = self.media(self.new, 'Model_20260101_111111.mp4')
        key = 'new/' + ts.name
        res = actions.delete_one(key, 'SUPPRIMER ' + key, busy=False)
        self.assertEqual(res['bytes'], 15000)
        self.assertFalse(ts.exists())
        self.assertTrue(mp4.exists())

    def test_delete_mp4_without_touching_ts(self):
        actions = self.actions()
        ts = self.media(self.new, 'Video_20260101_111111.recovered.ts')
        mp4 = self.media(self.new, 'Video_20260101_111111.recovered.mp4')
        key = 'new/' + mp4.name
        actions.delete_one(key, 'SUPPRIMER ' + key, busy=False)
        self.assertFalse(mp4.exists())
        self.assertTrue(ts.exists())

    def test_legacy_delete_needs_explicit_mount(self):
        ts = self.media(self.history, 'Old_20260101_111111.recovered.ts')
        key = 'history/' + ts.name
        a = self.actions()
        with self.assertRaises(PermissionError):
            a.delete_one(key, 'SUPPRIMER ' + key, busy=False)
        self.assertTrue(ts.exists())
        self.actions(historic=True).delete_one(key, 'SUPPRIMER ' + key, busy=False)
        self.assertFalse(ts.exists())

    def test_no_invalid_confirmation(self):
        ts = self.media(self.new, 'Keep_20260101_111111.ts')
        with self.assertRaisesRegex(ValueError, 'Confirmation'):
            self.actions().delete_one('new/' + ts.name, 'oui', busy=False)
        self.assertTrue(ts.exists())

    def test_protects_active_busy_and_conversion_lock(self):
        ts = self.media(self.new, 'Model_20260101_111111.ts')
        key = 'new/' + ts.name
        a = self.actions()
        for kwargs in ({'busy': True}, {'busy':False,'active_models':{'Model'}},
                       {'busy':False,'converting':ts.name}):
            with self.assertRaises(ValueError):
                a.delete_one(key,'SUPPRIMER '+key,**kwargs)
            self.assertTrue(ts.exists())
        self.gate.acquire()
        try:
            with self.assertRaisesRegex(ValueError, 'traitement vidéo'):
                a.delete_one(key,'SUPPRIMER '+key,busy=False)
        finally:
            self.gate.release()
        self.assertTrue(ts.exists())

    def test_protects_recent_files(self):
        ts = self.media(self.new, 'Model_20260101_111111.ts',age=2)
        key = 'new/' + ts.name
        with self.assertRaisesRegex(ValueError,'trop récent'):
            self.actions().delete_one(key,'SUPPRIMER '+key,busy=False)
        self.assertTrue(ts.exists())

    def test_symlinks_traversal_and_hidden_fragments_refused(self):
        a = self.actions(historic=True)
        other = self.media(self.root,'private.ts')
        (self.new/'bad.ts').symlink_to(other)
        self.media(self.new,'.work_x/private.ts')
        for key in ('new/../private.ts','new/bad.ts','new/.work_x/private.ts',
                    'new/part.sprec-partial.mp4','history/../../private.ts'):
            with self.assertRaises((ValueError,FileNotFoundError)):
                a.locate(key)
        self.assertTrue(other.exists())

    def test_disk_stats_and_favorite_orphan_prune(self):
        actions = self.actions()
        source = self.media(self.new,'File_20260101_111111.ts')
        key = 'new/' + source.name
        actions.set_favorite(key, True)
        d = actions.disk_stats()
        self.assertGreater(d['new']['total'],0)
        self.assertGreaterEqual(d['new']['free'],0)
        actions.prune_favorite_if_orphaned(key, ['new/File_20260101_111111.mp4'])
        self.assertTrue(actions.is_favorite(key))
        actions.prune_favorite_if_orphaned(key, [])
        self.assertFalse(self.actions().is_favorite(key))


if __name__ == '__main__':
    unittest.main()
