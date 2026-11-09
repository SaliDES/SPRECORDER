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
    os.environ["SPREC_FFPROBE"] = ""  # deterministic fake-FFmpeg conversion test
    spec = importlib.util.spec_from_file_location("sprec_player_test", PLAYER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.load_models()
    return mod


class RobustRecorderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sprecorder-test-"))
        self.fake = self.tmp / "fake_ffmpeg.py"
        self.fake.write_text("""#!/bin/sh
case " $* " in
  *" -version "*) echo "ffmpeg version test"; exit 0 ;;
  *" -f mp4 "*) for last do :; done; printf '0000ftypisomxxxxxxxxxxxxxxxx' > "$last"; exit 0 ;;
esac
trap 'exit 0' INT TERM
while :; do printf 'G%187s' ''; sleep 0.005; done
""")
        self.fake.chmod(0o755)
        self.mod = load_player(self.tmp / "data", self.fake)
        self.mod.models = {"manual": {}, "auto": {}}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_part_grows_before_recording(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        time.sleep(1.5)
        r = self.mod.recordings["A"]
        self.assertEqual(r["state"], "RECORDING")
        self.assertGreater(r["bytes_written"], 0)
        self.assertGreater(Path(r["ts"]).stat().st_size, 0)
        self.mod.stop_record("A")
        mp4 = Path(r["final"]).with_suffix(".mp4")
        self.assertTrue(mp4.exists())
        self.assertGreater(mp4.stat().st_size, 0)
        self.assertFalse(Path(r["final"]).exists())
        self.assertFalse(Path(r["ts"]).exists())

    def test_two_models_are_independent(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.models["manual"]["B"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        self.mod.start_record("B")
        time.sleep(1.5)
        self.assertEqual(len(self.mod.recordings), 2)
        self.assertNotEqual(self.mod.recordings["A"]["ts"], self.mod.recordings["B"]["ts"])
        self.mod.stop_record("A")
        self.mod.stop_record("B")


    def test_reconnect_after_ffmpeg_exit(self):
        reconnect = self.tmp / "fake_ffmpeg_reconnect.py"
        reconnect.write_text("""#!/bin/sh
case " $* " in
  *" -version "*) echo "ffmpeg version test"; exit 0 ;;
  *" -f mp4 "*) for last do :; done; printf '0000ftypisomxxxxxxxxxxxxxxxx' > "$last"; exit 0 ;;
esac
i=0
while [ "$i" -lt 15 ]; do printf 'G%187s' ''; i=$((i+1)); sleep 0.005; done
exit 1
""")
        reconnect.chmod(0o755)
        self.mod.FFMPEG = str(reconnect)
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        time.sleep(3.0)
        r = self.mod.recordings["A"]
        self.assertGreaterEqual(r["attempts"], 2)
        self.assertGreaterEqual(r["reconnects"], 1)
        self.assertGreater(r["bytes_written"], 0)
        part_size = Path(r["ts"]).stat().st_size
        self.assertGreaterEqual(part_size, r["bytes_written"])
        self.assertLess(part_size-r["bytes_written"], 65536)
        self.mod.stop_record("A")
        mp4 = Path(r["final"]).with_suffix(".mp4")
        self.assertTrue(mp4.exists())
        self.assertFalse(Path(r["final"]).exists())

    def test_same_model_does_not_start_duplicate(self):
        self.mod.models["manual"]["A"] = {"avatar": "", "online": True}
        self.mod.start_record("A")
        with self.assertRaises(RuntimeError):
            self.mod.start_record("A")
        self.mod.stop_record("A")

    def test_library_lists_ts_and_manual_conversion_keeps_source(self):
        recdir = Path(self.tmp / "data" / "recordings")
        recdir.mkdir(parents=True, exist_ok=True)
        ts = recdir / "older_show.ts"
        ts.write_bytes(b"synthetic transport stream")
        listed = self.mod.list_recordings()
        self.assertTrue(any(x["name"] == ts.name for x in listed))
        mp4 = ts.with_suffix(".mp4")
        log_path = Path(self.tmp / "data" / "logs" / "manual-convert.log")
        with log_path.open("w") as log:
            ok = self.mod._convert_ts_to_mp4(str(ts), str(mp4), log, delete_source=False)
        self.assertTrue(ok)
        self.assertTrue(ts.exists(), "manual conversion must preserve the TS by default")
        self.assertTrue(mp4.exists())
        self.assertGreater(mp4.stat().st_size, 0)

    def test_library_rejects_path_traversal(self):
        with self.assertRaises(ValueError):
            self.mod._safe_recording_path("../models.json")

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
