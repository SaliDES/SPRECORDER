import importlib.util
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'app' / 'sprecorder.py'
spec = importlib.util.spec_from_file_location('sprecorder_http', MODULE)
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sprec-library-')
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name) / 'recordings'
        root.mkdir()
        old = Path(self.tmp.name) / 'historical'
        old.mkdir()
        self.src = old / 'archived_20261101_100000.mp4'
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
                        '-f', 'lavfi', '-i', 'color=c=red:s=160x90:r=10', '-t', '2',
                        '-threads', '1', '-c:v', 'libx264', str(self.src)], check=True, timeout=30)
        m.HISTORY = old
        m.Handler.converter = m.Converter(root, Path(self.tmp.name) / 'state')
        m.Handler.index_file = MODULE.with_name('index.html')
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = 'http://127.0.0.1:%d' % self.server.server_port

    def test_history_and_ranges(self):
        data = urllib.request.urlopen(self.url + '/api/files', timeout=5).read().decode()
        self.assertIn('history/archived_20261101_100000.mp4', data)
        req = urllib.request.Request(
            self.url + '/video?path=history/archived_20261101_100000.mp4',
            headers={'Range': 'bytes=0-7'})
        with urllib.request.urlopen(req, timeout=5) as res:
            self.assertEqual(res.status, 206)
            self.assertEqual(len(res.read()), 8)
            self.assertTrue(res.headers.get('Content-Range', '').startswith('bytes 0-7/'))

    def test_reject_traversal(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url + '/video?path=new/../../etc/passwd', timeout=5)
        self.assertEqual(ctx.exception.code, 404)

if __name__ == '__main__':
    unittest.main()
