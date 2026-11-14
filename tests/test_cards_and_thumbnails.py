import sys, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import engine, streaming, thumbnail, profile_images


class ModelCardBackendTests(unittest.TestCase):
    def test_profile_photo_proxy_rejects_other_domains(self):
        self.assertTrue(profile_images.allowed_photo('https://img.strpst.com/model.jpg'))
        self.assertFalse(profile_images.allowed_photo('https://evil.example/image.jpg'))
        self.assertFalse(profile_images.allowed_photo('http://img.strpst.com/model.jpg'))
        self.assertFalse(profile_images.allowed_photo('https://img.strpst.com.attacker.tld/pic.png'))

    def test_profile_photo_sanitization_and_fallback(self):
        self.assertEqual(streaming.profile_image({'user':{'user':{'avatarUrl':'https://img.strpst.com/pic.jpg','previewUrl':'https://img.doppiocdn.net/snapshot.jpg'}}}), 'https://img.strpst.com/pic.jpg')
        self.assertEqual(streaming.profile_image({'user':{'user':{'previewUrl':'https://img.doppiocdn.net/thumb.webp'}}}), 'https://img.doppiocdn.net/thumb.webp')
        self.assertEqual(streaming.profile_image({'user':{'user':{'avatarUrl':'http://localhost/private'}}}), '')
        self.assertEqual(streaming.profile_image({'user':{'user':{'avatarUrl':'https://doppiocdn.net.attack.example/img'}}}), '')

    def test_added_models_have_auto_disabled_and_preserve_existing_preference(self):
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp)
            with patch.object(engine,'RECORDINGS',d/'recordings'),patch.object(engine,'STATE',d/'state'),patch.object(engine,'CONFIG',d/'config'),\
                 patch.object(engine.Engine,'_startup',lambda self: None),patch.object(engine.Engine,'_convert_loop',lambda self: None),\
                 patch.object(engine.Engine,'_refresh_one',lambda self,model: None):
                app=engine.Engine()
                app.add_model('ModelA')
                app.add_model('ModelB')
                self.assertEqual(len(app.status()['monitored']),2)
                self.assertFalse(any(x['auto'] for x in app.status()['monitored']))
                app.set_auto('ModelA',True)
                app.add_model('ModelA',False)
                self.assertTrue(dict((x['model'],x['auto']) for x in app.status()['monitored'])['ModelA'])
                app.shutdown()


class LibraryThumbnailsTests(unittest.TestCase):
    def test_jpeg_generated_from_video_and_cached(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            video=base/'sample.mp4'
            cmd=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y',
                 '-f','lavfi','-i','testsrc=size=320x180:rate=12:duration=3',
                 '-c:v','mpeg4','-q:v','6',str(video)]
            subprocess.run(cmd,check=True,timeout=30)
            thumbs=thumbnail.Thumbnailer(base/'cache')
            first=thumbs.get(video)
            self.assertGreater(len(first),800)
            self.assertEqual(first[:3],b'\xff\xd8\xff')
            self.assertEqual(thumbs.get(video),first)
            self.assertEqual(len(list((base/'cache').glob('*.jpg'))),1)

    def test_unsupported_extension_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp); f=base/'private.txt';f.write_text('secret')
            with self.assertRaises(ValueError):thumbnail.Thumbnailer(base/'cache').get(f)

if __name__=='__main__': unittest.main()
