import importlib.util
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLAYER = ROOT / "app" / "player.py"


def load_player(data_dir, ffmpeg):
    os.environ["SPREC_DATA_DIR"] = str(data_dir)
    os.environ["SPREC_FFMPEG"] = str(ffmpeg)
    os.environ["SPREC_FFMPEG_RECONNECT_DELAY"] = "0.05"
    os.environ["SPREC_FFMPEG_MAX_RECONNECTS"] = "0"
    spec = importlib.util.spec_from_file_location("sprec_player_test", PLAYER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.load_models()
    return mod


class RobustRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sprecorder-test-"))
        self.fake = self.tmp / "fake_ffmpeg.py"
        self.fake.write_text("""#!/usr/bin/env python3
import sys,time,signal
if '-version' in sys.argv:
    print('ffmpeg version test')
    raise SystemExit(0)
stop=False
def h(*a):
    global stop
    stop=True
signal.signal(signal.SIGINT,h)
for _ in range(100000):
    if stop: break
    sys.stdout.buffer.write(b'\\x47'+b'X'*187)
    sys.stdout.buffer.flush()
    time.sleep(0.005)
""")
        self.fake.chmod(0o755)
        self.mod = load_player(self.tmp / "data", self.fake)
        self.mod.models = {"manual": {}, "auto": {}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_part_grows_before_recording(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        time.sleep(1.0)
        r = self.mod.recordings["A"]
        self.assertEqual(r["state"], "RECORDING")
        self.assertGreater(r["bytes_written"], 0)
        self.assertGreater(Path(r["ts"]).stat().st_size, 0)
        self.mod.stop_record("A")
        self.assertTrue(Path(r["final"]).exists())
        self.assertFalse(Path(r["ts"]).exists())

    def test_two_models_are_independent(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.models["manual"]["B"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        self.mod.start_record("B")
        time.sleep(0.4)
        self.assertEqual(len(self.mod.recordings), 2)
        self.assertNotEqual(self.mod.recordings["A"]["ts"], self.mod.recordings["B"]["ts"])
        self.mod.stop_record("A")
        self.mod.stop_record("B")


    def test_reconnect_after_ffmpeg_exit(self):
        reconnect = self.tmp / "fake_ffmpeg_reconnect.py"
        reconnect.write_text("""#!/usr/bin/env python3
import sys,time
if '-version' in sys.argv:
    print('ffmpeg version test')
    raise SystemExit(0)
for _ in range(15):
    sys.stdout.buffer.write(b'\x47'+b'Y'*187)
    sys.stdout.buffer.flush()
    time.sleep(0.005)
raise SystemExit(1)
""")
        reconnect.chmod(0o755)
        self.mod.FFMPEG = str(reconnect)
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        time.sleep(1.5)
        r = self.mod.recordings["A"]
        self.assertGreaterEqual(r["attempts"], 2)
        self.assertGreaterEqual(r["reconnects"], 1)
        self.assertGreater(r["bytes_written"], 0)
        part_size = Path(r["ts"]).stat().st_size
        self.assertEqual(part_size, r["bytes_written"])
        self.mod.stop_record("A")
        self.assertTrue(Path(r["final"]).exists())

    def test_same_model_does_not_start_duplicate(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        with self.assertRaises(RuntimeError):
            self.mod.start_record("A")
        self.mod.stop_record("A")

    def test_recovery_creates_recovered_file(self):
        # Build a small valid TS using the system ffmpeg if available.
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not installed")
        src = self.tmp / "data" / "recordings" / "sample.ts.part"
        src.parent.mkdir(parents=True, exist_ok=True)
        os.system("ffmpeg -hide_banner -loglevel error -f lavfi -i testsrc=size=160x120:rate=5 -t 1 -c:v libx264 -preset ultrafast -f mpegts %s" % src)
        self.mod.FFMPEG = shutil.which("ffmpeg")
        recovered = self.mod.recover_partials()
        self.assertTrue(any(x.endswith(".recovered.ts") for x in recovered))


if __name__ == "__main__":
    unittest.main()
