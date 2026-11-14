import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from bulk_convert import BulkConversion, progress_time, BusyError


def sample_ts(target):
    subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
                    '-f','lavfi','-i','testsrc=duration=5:size=320x180:rate=15',
                    '-c:v','mpeg4','-q:v','4','-f','mpegts',str(target)],
                   check=True,timeout=40)
    old = time.time()-3600
    os.utime(target,(old,old))


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.new = self.base/'new'; self.new.mkdir()
        self.old = self.base/'old'; self.old.mkdir()
        self.v3 = self.base/'v3'; self.v3.mkdir()
        self.batch = BulkConversion(self.new,self.old,self.v3,self.base/'state',settle_seconds=180)

    def tearDown(self):self.tmp.cleanup()

    def wait(self):
        if self.batch.thread:self.batch.thread.join(timeout=50)
        status=self.batch.status()
        self.assertFalse(status['active'])
        return status

    def test_legacy_recovered_ts_is_imported_without_deleting_original(self):
        folder=self.old/'ModelA';folder.mkdir()
        src=folder/'ModelA_20260101_010101.ts.recovered.ts';sample_ts(src)
        self.assertIn('history/ModelA/'+src.name,self.batch.scan())
        self.batch.start_all()
        result=self.wait()
        output=self.new/'imports/history/ModelA/ModelA_20260101_010101.ts.recovered.mp4'
        self.assertEqual(result['converted'],1)
        self.assertEqual(result['completed'],1)
        self.assertAlmostEqual(result['percent'],100)
        self.assertTrue(src.exists(), 'Original in history must survive')
        self.assertTrue(output.is_file())
        self.assertGreater(output.stat().st_size,4000)
        self.assertTrue(self.batch.already_done('history/ModelA/'+src.name))
        self.assertEqual(self.batch.scan(),[])

    def test_new_ts_converts_and_only_then_removes_source(self):
        folder=self.new/'ModelB';folder.mkdir()
        src=folder/'ModelB_20260101_020202.ts';sample_ts(src)
        self.batch.start_one('new/ModelB/'+src.name)
        result=self.wait()
        self.assertEqual(result['converted'],1)
        self.assertFalse(src.exists())
        self.assertTrue(src.with_suffix('.mp4').is_file())

    def test_corrupt_ts_is_never_deleted(self):
        src=self.old/'broken.recovered.ts'
        src.write_bytes(b'bad video'*2000)
        old=time.time()-3600;os.utime(src,(old,old))
        self.batch.start_one('history/'+src.name)
        result=self.wait()
        self.assertEqual(result['failed'],1)
        self.assertTrue(src.exists())
        self.assertFalse(self.batch.output('history',Path(src.name)).exists())
        self.assertTrue(result['errors'])

    def test_new_or_in_progress_ts_not_processed(self):
        live=self.new/'ModelC_20260101_030303.ts';sample_ts(live)
        os.utime(live,None)
        self.assertEqual(self.batch.scan(),[])
        self.batch.start_one('new/'+live.name)
        result=self.wait()
        self.assertEqual(result['skipped'],1)
        self.assertTrue(live.exists())

    def test_refuse_symlink_and_internal_fragments(self):
        src=self.old/'original.ts';sample_ts(src)
        (self.new/'alias.ts').symlink_to(src)
        with self.assertRaises(ValueError):self.batch.source('new/alias.ts')
        work=self.new/'.work_20260101_000000';work.mkdir()
        (work/'chunk_00000000.ts').write_bytes(b'hi')
        self.assertFalse(any('chunk_' in x for x in self.batch.scan()))

    def test_no_overwrite_mp4(self):
        src=self.old/'test.ts';sample_ts(src)
        dest=self.batch.output('history',Path(src.name));dest.parent.mkdir(parents=True)
        dest.write_bytes(b'KEEP_ME')
        self.batch.start_one('history/test.ts')
        result=self.wait()
        self.assertEqual(result['skipped'],1)
        self.assertEqual(dest.read_bytes(),b'KEEP_ME')
        self.assertTrue(src.exists())

    def test_restart_after_crash_discards_only_stale_partial_mp4(self):
        source=self.old/'Recovered_20260101_040404.recovered.ts';sample_ts(source)
        target=self.batch.output('history',Path(source.name))
        target.parent.mkdir(parents=True,exist_ok=True)
        partial=target.with_name(target.name+'.sprec-partial')
        partial.write_bytes(b'incomplete MP4 from a previous crash')
        self.batch.start_one('history/'+source.name)
        result=self.wait()
        self.assertEqual(result['converted'],1)
        self.assertFalse(partial.exists())
        self.assertTrue(source.exists())
        self.assertTrue(target.is_file())

    def test_parse_ffmpeg_progress(self):
        self.assertAlmostEqual(progress_time('00:01:32.500000'),92.5)
        self.assertEqual(progress_time('N/A'),0)


if __name__=='__main__':unittest.main()
