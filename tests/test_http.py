import json,sys,threading,unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen, Request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server

class FakeEngine:
    quality=0
    def __init__(self):self.models=[]
    def status(self):return {'version':'4.0.0-independent','monitored':[],'recordings':[],'events':[]}
    def list_files(self):return []
    def add_model(self,model,auto):self.models.append((model,auto))

class HTTPTests(unittest.TestCase):
    def setUp(self):
        server.API.engine=FakeEngine()
        self.srv=ThreadingHTTPServer(('127.0.0.1',0),server.API)
        self.t=threading.Thread(target=self.srv.serve_forever,daemon=True);self.t.start()
        self.base=f'http://127.0.0.1:{self.srv.server_port}'
    def tearDown(self):self.srv.shutdown();self.srv.server_close();self.t.join()
    def test_health_status_ui_same_port(self):
        for path in ('/health','/api/status'):
            with urlopen(self.base+path) as resp:self.assertEqual(resp.status,200)
        with urlopen(self.base+'/') as resp:
            self.assertIn(b'Enregistrement automatique',resp.read())
    def test_post_models(self):
        req=Request(self.base+'/api/model/add',data=b'{"model":"Test123","auto":true}',headers={'Content-Type':'application/json'},method='POST')
        with urlopen(req) as resp:self.assertEqual(resp.status,200)
        self.assertEqual(server.API.engine.models,[('Test123',True)])
    def test_cross_origin_forbidden(self):
        req=Request(self.base+'/api/model/add',data=b'{"model":"Test123","auto":true}',headers={'Origin':'https://attack.example'},method='POST')
        with self.assertRaises(HTTPError) as context:urlopen(req)
        self.assertEqual(context.exception.code,403)
        self.assertFalse(server.API.engine.models)

if __name__=='__main__': unittest.main()
