import base64, hashlib, importlib.util, os, tempfile, unittest
from urllib.parse import unquote
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sprec_relay_test',ROOT/'app'/'relay.py')
relay=importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)

class RelayTests(unittest.TestCase):
    def test_mouflon_v2_decrypt_roundtrip(self):
        key='test-pdkey'
        plain='segment-token-123'
        h=hashlib.sha256(key.encode()).digest()
        x=bytes(b ^ h[i%len(h)] for i,b in enumerate(plain.encode()))
        token=base64.b64encode(x).decode().rstrip('=')[::-1]
        self.assertEqual(relay.decrypt_token(token,key),plain)

    def test_media_hls_host_and_path_normalization(self):
        src='https://media-hls.doppiocdn.live/b-hls-34/261139248/chunk.mp4'
        self.assertEqual(relay.normalize_segment_url(src),'https://b-hls-34.doppiocdn.live/hls/261139248/chunk.mp4')

    def test_playlist_rewrite_removes_mouflon_tags_and_proxies_segment(self):
        text='#EXTM3U\n#EXT-X-MOUFLON:PSCH:v2:pkey1\n#EXTINF:2,\n#EXT-X-MOUFLON:URI:https://media-hls.doppiocdn.live/b-hls-34/42/42_1_token_123.mp4\nmedia.mp4\n'
        old=relay.load_keys
        relay.load_keys=lambda:{'pkey1':'pdkey1'}
        try:
            out=relay.rewrite_playlist(text,'https://b-hls-34.doppiocdn.live/hls/42/42.m3u8','http://nas:13031','model')
        finally:
            relay.load_keys=old
        self.assertNotIn('#EXT-X-MOUFLON',out)
        self.assertIn('http://nas:13031/?url=',out)
        decoded=unquote(out)
        self.assertIn('pkey=pkey1',decoded)
        self.assertIn('psch=v2',decoded)

if __name__=='__main__': unittest.main()
