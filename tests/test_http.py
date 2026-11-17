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

class FakeCleanup:
    def __init__(self):self.requests=[]
    def status(self):return {"active":False}
    def preview(self):return {"files":0,"bytes":0}
    def start(self,confirmation):
        from cleanup import CONFIRMATION
        if confirmation != CONFIRMATION: raise ValueError("Confirmation incorrecte")
        self.requests.append(confirmation)
        return {"active":True}

class FakeEngine:
    quality=0
    def __init__(self):
        self.models=[];self.bulk=FakeBulk();self.cleanup=FakeCleanup()
        self.library_actions=type('A',(),{'disk_stats':lambda _: {'new':{'total':100,'free':30,'used':70,'percent':70}}})()
        self.fav_calls=[];self.deletions=[]
    def status(self):return {'version':'4.0.0-independent','monitored':[],'recordings':[],'events':[]}
    def list_files(self):return []
    def add_model(self,model,auto):self.models.append((model,auto))
    def set_favorite(self,key,enabled):
        self.fav_calls.append((key,enabled));return enabled
    def delete_recording(self,key,confirmation):
        if confirmation != 'SUPPRIMER ' + key:raise ValueError('Confirmation incorrecte')
        self.deletions.append(key);return {'deleted':key,'bytes':12345}

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

    def test_cleanup_preview_and_confirmation_required(self):
        with urlopen(self.base+'/api/cleanup/preview') as resp:
            self.assertEqual(json.load(resp)['preview']['files'],0)
        wrong=Request(self.base+'/api/cleanup/converted',data=b'{"confirmation":"OUI"}',headers={'Content-Type':'application/json'},method='POST')
        with self.assertRaises(HTTPError) as context:urlopen(wrong)
        self.assertEqual(context.exception.code,400)
        self.assertEqual(server.API.engine.cleanup.requests,[])
        correct=Request(self.base+'/api/cleanup/converted',data=b'{"confirmation":"SUPPRIMER LES TS"}',headers={'Content-Type':'application/json'},method='POST')
        with urlopen(correct) as resp:
            self.assertEqual(resp.status,202)
        self.assertEqual(server.API.engine.cleanup.requests,['SUPPRIMER LES TS'])

    def test_storage_favorite_and_delete_http_routes(self):
        with urlopen(self.base+'/api/storage') as resp:
            self.assertEqual(json.load(resp)['storage']['new']['free'],30)
        payload=json.dumps({'path':'history/Old.recovered.ts','favorite':True}).encode()
        req=Request(self.base+'/api/favorite',data=payload,
                    headers={'Content-Type':'application/json'},method='POST')
        with urlopen(req) as resp:self.assertTrue(json.load(resp)['favorite'])
        self.assertEqual(server.API.engine.fav_calls,[('history/Old.recovered.ts',True)])
        url=self.base+'/api/file/delete'
        wrong=Request(url,data=json.dumps({'path':'history/Old.recovered.ts','confirmation':'OUI'}).encode(),
                      headers={'Content-Type':'application/json'},method='POST')
        with self.assertRaises(HTTPError) as err:urlopen(wrong)
        self.assertEqual(err.exception.code,400)
        self.assertEqual(server.API.engine.deletions,[])
        good=Request(url,data=json.dumps({'path':'history/Old.recovered.ts',
                                         'confirmation':'SUPPRIMER history/Old.recovered.ts'}).encode(),
                     headers={'Content-Type':'application/json'},method='POST')
        with urlopen(good) as resp:
            self.assertEqual(json.load(resp)['result']['bytes'],12345)
        self.assertEqual(server.API.engine.deletions,['history/Old.recovered.ts'])

    def test_cross_origin_forbidden(self):
        req=Request(self.base+'/api/model/add',data=b'{"model":"Test123","auto":true}',headers={'Origin':'https://attack.example'},method='POST')
        with self.assertRaises(HTTPError) as context:urlopen(req)
        self.assertEqual(context.exception.code,403)
        self.assertFalse(server.API.engine.models)

if __name__=='__main__': unittest.main()
