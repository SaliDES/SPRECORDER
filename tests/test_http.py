import json,sys,threading,unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen, Request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import server

class FakeBulk:
    def __init__(self):self.requests=[]
    def start_all(self):self.requests.append('all');return {'active':True,'total':0}
    def start_one(self,key):self.requests.append(key);return {'active':True,'total':1}

class FakeEngine:
    quality=0
    def __init__(self):self.models=[];self.bulk=FakeBulk()
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
    def test_batch_convert_and_historical_file_routes_are_async(self):
        for endpoint, body in [('/api/convert/all',b'{}'),
                               ('/api/convert',b'{"path":"history/Model.ts.recovered.ts"}')]:
            request=Request(self.base+endpoint,data=body,
                            headers={'Content-Type':'application/json'},method='POST')
            with urlopen(request) as response:
                self.assertEqual(response.status,202)
                self.assertTrue(json.load(response)['ok'])
        self.assertEqual(server.API.engine.bulk.requests,
                         ['all','history/Model.ts.recovered.ts'])

    def test_cross_origin_forbidden(self):
        req=Request(self.base+'/api/model/add',data=b'{"model":"Test123","auto":true}',headers={'Origin':'https://attack.example'},method='POST')
        with self.assertRaises(HTTPError) as context:urlopen(req)
        self.assertEqual(context.exception.code,403)
        self.assertFalse(server.API.engine.models)

if __name__=='__main__': unittest.main()
