import importlib.util
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'app' / 'sprecorder.py'
spec = importlib.util.spec_from_file_location('sprecorder', MODULE)
import sys
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


def fixture(dest):
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
           '-f', 'lavfi', '-i', 'testsrc=size=160x90:rate=12',
           '-f', 'lavfi', '-i', 'sine=frequency=500:sample_rate=22050',
           '-t', '3', '-threads', '1', '-c:v', 'libx264',
           '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-f', 'mpegts', str(dest)]
    subprocess.run(cmd, check=True, timeout=40)


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sprecorder-v3-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'recordings'
        self.root.mkdir()
        self.conv = m.Converter(self.root, Path(self.tmp.name) / 'state',
                                settle=60, minimum=1024, scan_seconds=5)
        self.sub = self.root / 'test_model'
        self.sub.mkdir()
        self.src = self.sub / 'test_model_20261113_120000.ts'

    def test_success_remux_and_remove_ts(self):
        fixture(self.src)
        self.assertTrue(self.conv.convert(self.src, bypass_age_for_test=True))
        dest = self.src.with_suffix('.mp4')
        self.assertTrue(dest.exists())
        self.assertFalse(self.src.exists())
        self.assertGreater(m.duration(m.media_info(dest)), 2)

    def test_bad_ts_preserved(self):
        self.src.write_bytes(b'x' * 100000)
        self.assertFalse(self.conv.convert(self.src, bypass_age_for_test=True))
        self.assertTrue(self.src.exists())
        self.assertFalse(self.src.with_suffix('.mp4').exists())

    def test_existing_mp4_never_overwritten(self):
        fixture(self.src)
        target = self.src.with_suffix('.mp4')
        target.write_bytes(b'unique-marker')
        self.assertFalse(self.conv.convert(self.src, bypass_age_for_test=True))
        self.assertEqual(target.read_bytes(), b'unique-marker')
        self.assertTrue(self.src.exists())

    def test_partial_segments_ignored(self):
        fixture(self.src)
        stamp = time.time() - 600
        os.utime(self.src, (stamp, stamp))
        session = self.root / 'test_model' / 'test_model_20261113_120000'
        session.mkdir()
        (session/'playlist.m3u8').write_text('#EXTM3U')
        fragment = session/'test_model_20261113_120000.ts'
        fixture(fragment)
        os.utime(fragment, (stamp, stamp))
        self.assertFalse(self.conv.eligible(fragment))
        self.assertFalse(self.conv.convert(fragment, bypass_age_for_test=True))
        self.assertFalse(self.conv.eligible(self.src))  # first observation
        self.conv.seen[str(self.src)].first_seen -= 10
        self.assertTrue(self.conv.eligible(self.src))   # stable second observation

    def test_non_final_name_skipped(self):
        source = self.sub / 'segment_0001.ts'
        source.write_bytes(b'x' * 100000)
        self.assertFalse(self.conv.convert(source, bypass_age_for_test=True))

if __name__ == '__main__':
    unittest.main()
