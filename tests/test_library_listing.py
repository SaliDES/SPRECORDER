"""Regression: temporary TS files must not break the whole library endpoint."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import engine


class LibraryListingRegressionTests(unittest.TestCase):
    def test_temporary_fragments_are_ignored_and_finished_videos_visible(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            new, history, h3 = (base / label for label in ('recordings', 'history', 'history-v3'))
            for root in (new, history, h3):
                root.mkdir()

            def write(root, name):
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b'test media')
                return p

            write(new, 'Freya_20261118_100000.ts')
            write(new, 'Freya_20261118_100000.mp4')
            write(new, 'Freya_20261118_105000.recovered.ts')
            write(history, 'Archived_20261026_213928.ts')
            write(h3, 'OldV3_20261001_120000.ts.recovered.ts')
            # Intermediate FFmpeg artifacts and legacy fragment directories.
            write(new, 'CurrentlyRecording_20261118_103000.merging.ts')
            write(new, 'Other.part.ts')
            write(new, 'Bad.sprec-partial.ts')
            write(new, '.hidden.ts')
            write(new, '.work/fragment.ts')
            write(new, 'ts_fragments/chunk_0001.ts')
            write(history, 'segments/playlist.m3u8')
            write(history, 'segments/chunk_0001.ts')
            with patch.object(engine, 'RECORDINGS', new), \
                 patch.object(engine, 'STATE', base / 'state'), \
                 patch.object(engine, 'CONFIG', base / 'config'), \
                 patch.object(engine, 'HISTORY', history), \
                 patch.object(engine, 'HISTORY_V3', h3), \
                 patch.object(engine.Engine, '_startup', lambda self: None), \
                 patch.object(engine.Engine, '_convert_loop', lambda self: None):
                app = engine.Engine()
                try:
                    result = app.list_files()
                    paths = {r['path'] for r in result}
                    self.assertEqual(paths, {
                        'new/Freya_20261118_100000.ts',
                        'new/Freya_20261118_100000.mp4',
                        'new/Freya_20261118_105000.recovered.ts',
                        'history/Archived_20261026_213928.ts',
                        'history-v3/OldV3_20261001_120000.ts.recovered.ts',
                    })
                    self.assertTrue(all('favorite' in item and 'deletable' in item for item in result))
                    app.set_favorite('new/Freya_20261118_100000.ts', True)
                    by_path = {r['path']: r for r in app.list_files()}
                    self.assertTrue(by_path['new/Freya_20261118_100000.ts']['favorite'])
                    self.assertTrue(by_path['new/Freya_20261118_100000.mp4']['favorite'])
                finally:
                    app.shutdown()


if __name__ == '__main__':
    unittest.main()
