import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('meeting_app',ROOT/'meeting_app.py')
assert spec and spec.loader
app=importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class HttpIntegrationTests(unittest.TestCase):
    def test_real_local_http_roundtrip_with_synthetic_service(self):
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,format,*args): pass
            def respond(self,value):
                body=json.dumps(value).encode(); self.send_response(200)
                self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                self.assert_auth()
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append(('POST',self.path,payload))
                self.respond({'id':7,'status':'requested'})
            def assert_auth(self):
                if self.headers.get('X-API-Key')!='synthetic-test-token': raise AssertionError('Missing auth')
            def do_GET(self):
                self.assert_auth(); calls.append(('GET',self.path,None))
                self.respond({'id':7,'status':'completed','segments':[{'speaker':'Alex','text':'Synthetic HTTP integration evidence.'}]})
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        api=app.http(f'http://127.0.0.1:{server.server_port}','synthetic-test-token')
        now=datetime(2030,1,1,tzinfo=timezone.utc)
        event={'id':'synthetic','summary':'Demo [record]','hangoutLink':'https://meet.google.com/abc-defg-hij',
               'start':{'dateTime':now.isoformat()},'end':{'dateTime':(now+timedelta(minutes=5)).isoformat()}}
        with tempfile.TemporaryDirectory() as tmp:
            worker=app.Worker(Path(tmp),'Synthetic Bot','[record]')
            worker.tick([event],now,api); worker.tick([event],now,api)
            self.assertEqual(worker.states()[0]['phase'],'captured')
            self.assertEqual([x[:2] for x in calls],[('POST','/bots'),('GET','/transcripts/by-id/7')])

    def test_http_rejects_credentials_and_remote_plaintext(self):
        for endpoint in ['http://example.com','https://user:password@example.com','https://example.com?token=abc']:
            with self.assertRaises(ValueError): app.http(endpoint,'synthetic')

if __name__=='__main__': unittest.main()
