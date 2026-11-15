"""Real FFmpeg cleanup tests: old files need a separately mounted write path."""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from bulk_convert import BulkConversion
from cleanup import CleanupConverted, CONFIRMATION


def ts(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=duration=5:size=160x90:rate=12",
                    "-c:v", "mpeg4", "-q:v", "4", "-f", "mpegts", str(path)],
                   check=True, timeout=40)
    old = time.time() - 3600
    os.utime(path, (old, old))


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.base = Path(self.t.name)
        self.new = self.base / "new"
        self.old = self.base / "old"
        self.v3 = self.base / "v3"
        for root in (self.new, self.old, self.v3): root.mkdir()
        self.bulk = BulkConversion(self.new, self.old, self.v3, self.base / "state", settle_seconds=180)
        self.cleanup = CleanupConverted(self.bulk, self.base / "state")

    def tearDown(self):self.t.cleanup()

    def wait(self, worker):
        worker.thread.join(timeout=50)
        self.assertFalse(worker.status()["active"])
        return worker.status()

    def convert(self, file: Path, label: str):
        key = label + "/" + str(file.relative_to(self.old if label == "history" else self.new))
        self.bulk.start_one(key)
        self.assertEqual(self.wait(self.bulk)["converted"],1)
        return key

    def test_historical_ts_cannot_be_deleted_without_explicit_write_mount(self):
        src = self.old / "model" / "model.ts.recovered.ts"; ts(src)
        self.convert(src,"history")
        self.assertEqual(self.cleanup.preview()["files"],0)
        self.assertTrue(src.exists())

    def test_legacy_recovered_ts_deleted_after_mp4_verified(self):
        src = self.old / "model" / "model.ts.recovered.ts"; ts(src)
        key = self.convert(src,"history")
        target = self.bulk.output("history",Path("model/model.ts.recovered.ts"))
        self.assertTrue(target.exists())
        self.cleanup = CleanupConverted(self.bulk,self.base / "state",{"history": self.old})
        preview=self.cleanup.preview()
        self.assertEqual(preview["files"],1)
        self.assertGreater(preview["bytes"],4096)
        with self.assertRaises(ValueError):self.cleanup.start("OUI")
        self.assertTrue(src.exists())
        self.cleanup.start(CONFIRMATION)
        result=self.wait(self.cleanup)
        self.assertEqual(result["deleted"],1)
        self.assertGreater(result["freed_bytes"],0)
        self.assertFalse(src.exists())
        self.assertTrue(target.exists(),"MP4 preserved")

    def test_corrupted_mp4_cannot_authorize_deletion(self):
        src=self.old/"model.recovered.ts";ts(src)
        dst=self.bulk.output("history",Path(src.name));dst.parent.mkdir(parents=True)
        dst.write_bytes(b"INVALID"*1000)
        self.cleanup=CleanupConverted(self.bulk,self.base/"state",{"history":self.old})
        self.assertEqual(self.cleanup.preview()["files"],1)  # heuristic preview only
        self.cleanup.start(CONFIRMATION)
        result=self.wait(self.cleanup)
        self.assertEqual(result["deleted"],0)
        self.assertTrue(src.exists())
        self.assertEqual(dst.read_bytes(),b"INVALID"*1000)

    def test_no_mp4_no_deletion(self):
        src=self.old/"unfinished.ts";ts(src)
        self.cleanup=CleanupConverted(self.bulk,self.base/"state",{"history":self.old})
        self.assertEqual(self.cleanup.preview()["files"],0)
        self.cleanup.start(CONFIRMATION)
        self.assertEqual(self.wait(self.cleanup)["deleted"],0)
        self.assertTrue(src.exists())

    def test_active_recent_ts_remains(self):
        src=self.old/"recent.ts";ts(src)
        dst=self.bulk.output("history",Path(src.name));dst.parent.mkdir(parents=True)
        subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error", "-y",
                        "-i", str(src), "-c", "copy", str(dst)],check=True)
        os.utime(src,None)
        self.cleanup=CleanupConverted(self.bulk,self.base/"state",{"history":self.old})
        self.assertEqual(self.cleanup.preview()["files"],0)
        self.assertTrue(src.exists())

    def test_same_name_but_wrong_mp4_duration_preserves_ts(self):
        src=self.old/"full.ts";ts(src)
        dst=self.bulk.output("history",Path(src.name));dst.parent.mkdir(parents=True)
        subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error", "-y",
                        "-f","lavfi","-i","testsrc=duration=1:size=160x90:rate=12",
                        "-c:v","mpeg4",str(dst)],check=True)
        self.cleanup=CleanupConverted(self.bulk,self.base/"state",{"history":self.old})
        self.cleanup.start(CONFIRMATION)
        result=self.wait(self.cleanup)
        self.assertEqual(result["deleted"],0)
        self.assertTrue(src.exists())

    def test_new_ts_with_verified_mp4_cleaned(self):
        src=self.new/"old_recording.ts";ts(src)
        dest=src.with_suffix(".mp4")
        subprocess.run(["ffmpeg","-nostdin","-hide_banner","-loglevel","error", "-y",
                        "-i",str(src),"-c","copy",str(dest)],check=True)
        self.cleanup.start(CONFIRMATION)
        self.assertEqual(self.wait(self.cleanup)["deleted"],1)
        self.assertFalse(src.exists());self.assertTrue(dest.exists())

if __name__ == '__main__': unittest.main()
