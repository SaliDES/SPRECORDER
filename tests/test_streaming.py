import base64
import hashlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import streaming


class StreamTests(unittest.TestCase):
    def test_model_validation(self):
        self.assertEqual(streaming.model_name('Model_one'),'Model_one')
        for s in ('http://x','../../etc/passwd','a',''):
            with self.assertRaises(ValueError):streaming.model_name(s)

    def test_master_preferred_matching_key_not_last(self):
        master='''#EXTM3U
#EXT-X-MOUFLON:PSCH:v2:alpha
#EXT-X-MOUFLON:PSCH:v2:beta
#EXT-X-STREAM-INF:BANDWIDTH=400,RESOLUTION=426x240
https://media-hls.doppiocdn.com/b-hls-14/123_240p.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900,RESOLUTION=1280x720
https://media-hls.doppiocdn.com/b-hls-14/123_720p.m3u8
'''
        url,height=streaming.select_variant(master,'https://edge-hls.doppiocdn.com/m.m3u8',{'alpha':'x'},0)
        self.assertEqual(height,720)
        self.assertEqual(streaming.parse_qs(streaming.urlsplit(url).query)['pkey'],['alpha'])
        url,height=streaming.select_variant(master,'https://edge-hls.doppiocdn.com/m.m3u8',{'beta':'x'},480)
        self.assertEqual(height,240)
        self.assertEqual(streaming.parse_qs(streaming.urlsplit(url).query)['pkey'],['beta'])

    def test_master_without_mouflon_stays_without_artificial_psch(self):
        master='#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=20,RESOLUTION=426x240\nhttps://media-hls.doppiocdn.com/z.m3u8\n'
        url,_=streaming.select_variant(master,'https://edge-hls.doppiocdn.com/m.m3u8',{},0)
        self.assertNotIn('psch=',url)
        self.assertNotIn('pkey=',url)

    def test_mouflon_token_with_slash(self):
        pdkey='example-secret'
        clear='XYZAbCdDef' # non-sensitive synthetic fixture
        raw=bytes(x ^ hashlib.sha256(pdkey.encode()).digest()[i%32] for i,x in enumerate(clear.encode()))
        token=base64.b64encode(raw).decode().rstrip('=')[::-1]
        uri='https://media-hls.doppiocdn.com/b-hls-14/model_720p_h264_'+token+'_1700000000.mp4'
        self.assertIn(clear,streaming.decrypt_segment_url(uri,pdkey))

    def test_media_mouflon_and_discontinuity(self):
        media='''#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:27
#EXT-X-MOUFLON:PSCH:v2:ABC
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.0,
#EXT-X-MOUFLON:URI:https://media-hls.doppiocdn.com/b-hls-14/segment_one_2000.mp4
fake-filler.mp4
#EXTINF:2.0,
second.mp4
#EXT-X-ENDLIST
'''
        segs,ended,delay=streaming.parse_media(media,'https://media-hls.doppiocdn.com/b-hls-14/720.m3u8',{})
        self.assertTrue(ended);self.assertEqual(delay,2)
        self.assertEqual([s.seq for s in segs],[27,28])
        self.assertTrue(segs[0].url.endswith('segment_one_2000.mp4'))
        self.assertTrue(segs[0].init_url.endswith('init.mp4'))

    def test_cdn_rejects_localhost(self):
        for value in ('http://127.0.0.1/foo','https://localhost/a','https://doppiocdn.com.evil/a','file:///etc/passwd'):
            self.assertFalse(streaming.allowed_cdn(value))

    def test_api_resolution(self):
        broadcasts={'item':{'streamName':'abc123'}}
        cam={'user':{'user':{'id':12345,'status':'public','isLive':True,'viewersCount':10}},'cam':{'streamName':'abc123'}}
        with patch.object(streaming,'json_api',side_effect=[broadcasts,cam]):
            info=streaming.get_stream_info('model_here')
        self.assertEqual(info['stream_id'],'12345')
        self.assertTrue(info['public'])

if __name__=='__main__': unittest.main()
