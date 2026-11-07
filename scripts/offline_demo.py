#!/usr/bin/env python3
"""Explicit synthetic local replay. No Google, Vexa, model or Notion requests."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('meeting_app',ROOT/'meeting_app.py')
assert spec is not None and spec.loader is not None
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)
now = datetime(2030,1,1,10,tzinfo=timezone.utc)
event = {'id':'synthetic-room-race','summary':'Synthetic demo [record]',
         'start':{'dateTime':now.isoformat()},'end':{'dateTime':(now+timedelta(minutes=5)).isoformat()},
         'hangoutLink':'https://meet.google.com/abc-defg-hij'}
requests=[]
def fake_api(method,path,payload=None):
    requests.append((method,path))
    if method=='POST': return {'id':123,'status':'requested'}
    return {'id':123,'native_meeting_id':'abc-defg-hij','status':'completed','segments':[
        {'speaker':'Alex','start':0,'text':'We agree to demonstrate calendar-triggered capture.'},
        {'speaker':'Sam','start':4,'text':'I will review the draft. Do not publish without approval.'}]}
with tempfile.TemporaryDirectory(prefix='synthetic-meeting-') as tmp:
    worker=app.Worker(Path(tmp),'Synthetic Bot','[record]')
    worker.tick([event],now,fake_api)
    assert worker.states()[0]['phase']=='requested'
    worker.tick([event],now,fake_api)
    state=worker.states()[0]
    assert state['phase']=='captured'
    assert 'Do not publish' in (Path(state['directory'])/'transcript.md').read_text()
    print(json.dumps({'mode':'SYNTHETIC OFFLINE REPLAY','result':'PASS','requests_simulated':requests,
                     'proved':['one submission','requested is not admitted','exact-ID transcript persisted'],
                     'not_tested':['live Google login','Vexa capture','Hermes model','Notion','Docker']},indent=2))
