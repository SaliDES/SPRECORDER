import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import engine, streaming


def make_sample(root):
    cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc=duration=7:size=320x180:rate=15',
         '-c:v','libx264','-pix_fmt','yuv420p','-g','30','-sc_threshold','0','-hls_time','2','-hls_list_size','0',
         '-hls_segment_type','fmp4','-hls_fmp4_init_filename','init.mp4','-f','hls',str(root/'live.m3u8')]
    subprocess.run(cmd,check=True,timeout=25)
    return (root/'init.mp4').read_bytes(), sorted(root.glob('live*.m4s'))


class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.temp.name)
        cls.init,cls.segs=make_sample(cls.root)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_actual_remux_each_fmp4_and_final_mp4(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)
            work=d/'.work_20261114_000000';work.mkdir()
            with (work/'playlist.m3u8').open('w') as f:
                f.write('#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n')
                for n,seg in enumerate(self.segs):
                    data=engine.remux_fmp4(self.init,seg.read_bytes())
                    self.assertGreater(len(data),1000)
                    (work/f'chunk_{n:08d}.ts').write_bytes(data)
                    if n:f.write('#EXT-X-DISCONTINUITY\n')
                    f.write(f'#EXTINF:2.000,\nchunk_{n:08d}.ts\n')
                f.write('#EXT-X-ENDLIST\n')
            ts=engine.merge_session(work,d/'Model_20261114_000000.ts')
            conv=engine.Converter(d,d/'state',minimum=1024,settle=60)
            self.assertTrue(conv.convert(ts,bypass_age_for_test=True))
            self.assertTrue((d/'Model_20261114_000000.mp4').exists())
            self.assertFalse(ts.exists())

    def test_bad_ts_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)
            ts=d/'Bad_20261114_000000.ts'
            ts.write_bytes(b'invalid'*20000)
            conv=engine.Converter(d,d/'state',minimum=1024,settle=60)
            self.assertFalse(conv.convert(ts,bypass_age_for_test=True))
            self.assertTrue(ts.exists())

    def test_mocked_capture_job_to_mp4(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(engine,'RECORDINGS',root/'recordings'),patch.object(engine,'STATE',root/'state'),patch.object(engine,'CONFIG',root/'config'),patch.object(engine,'KEYS_FILE',root/'config'/'keys.json'):
                with patch.object(streaming,'get_stream_info',return_value={'stream_id':'12345','public':True,'live':True}),\
                     patch.object(streaming,'fetch_master',return_value=('#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2,RESOLUTION=320x180\nhttps://media-hls.doppiocdn.com/720.m3u8\n','https://edge-hls.doppiocdn.com/master.m3u8')):
                    fake='https://media-hls.doppiocdn.com'
                    playlist='#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n#EXT-X-MAP:URI="'+fake+'/init.mp4"\n'
                    for n in range(len(self.segs)):
                        playlist+=f'#EXTINF:2,\n{fake}/seg{n}.mp4\n'
                    playlist+='#EXT-X-ENDLIST\n'
                    blobs={f'{fake}/init.mp4':self.init,f'{fake}/720.m3u8':playlist.encode()}
                    for n,s in enumerate(self.segs):blobs[f'{fake}/seg{n}.mp4']=s.read_bytes()
                    def stub_fetch(url,**kwargs):return blobs[url],url,'video/mp4'
                    with patch.object(streaming,'fetch',side_effect=stub_fetch):
                        app=engine.Engine()
                        app.start_record('ModelDemo')
                        deadline=time.time()+20
                        while app.jobs and time.time()<deadline:time.sleep(.1)
                        app.shutdown()
                        videos=list((root/'recordings').rglob('*.mp4'))
                        self.assertEqual(len(videos),1,app.status()['events'])
                        self.assertFalse(list((root/'recordings').rglob('*.ts')))

if __name__=='__main__': unittest.main()
