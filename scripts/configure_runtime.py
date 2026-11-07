#!/usr/bin/env python3
"""Install the port into a new attendee-local Hermes home, with signed prep/finish routes."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys


def write(path, text):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    path.write_text(text);path.chmod(0o600)


def configure(root):
    root=Path(root).resolve(); home=root/'private/hermes'
    config_path=root/'private/config.json'
    config=json.loads(config_path.read_text())
    if (home/'webhook_subscriptions.json').exists():
        raise RuntimeError('Runtime routes already exist; inspect configuration rather than overwriting')
    env_path = root/'.env'
    env = dict(line.split('=',1) for line in env_path.read_text().splitlines() if '=' in line and not line.startswith('#')) if env_path.exists() else {}
    key = env.get('HERMES_WEBHOOK_SECRET') or secrets.token_urlsafe(32)
    if 'HERMES_WEBHOOK_SECRET' not in env:
        with env_path.open('a') as f: f.write('HERMES_WEBHOOK_SECRET='+key+'\n')
        env_path.chmod(0o600)
    write(root/'private/webhook-key',key+'\n')
    config.update({'hermes_home':str(home),'meetings_dir':str(home/'workspace/meetings'),
                   'webhook_key_file':str(root/'private/webhook-key'),
                   'prep_webhook_url':'http://127.0.0.1:8644/webhooks/meeting-prep',
                   'finish_webhook_url':'http://127.0.0.1:8644/webhooks/meeting-finished',
                   'stt_url':'http://127.0.0.1:19083','stt_key_file':str(root/'private/stt-key'),
                   'stt_model':'whisper-1','notion_enabled':bool(config.get('notion_parent_page_id')),
                   'slack_enabled':False,'tasks_enabled':False})
    write(config_path,json.dumps(config,indent=2))
    if (root/'.env').exists():
        env=dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
        if env.get('STT_TOKEN'):write(root/'private/stt-key',env['STT_TOKEN']+'\n')
    # Source repo available in actual invocation; tests use an empty temporary root.
    if (root/'skills').exists():
        for source in (root/'skills').iterdir():
            if source.is_dir() and (source/'SKILL.md').exists():
                shutil.copytree(source,home/'skills/meeting'/source.name,dirs_exist_ok=True)
    if (root/'scheduler').exists():
        shutil.copytree(root/'scheduler',home/'scripts/meeting-trigger',dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('node_modules','test*','__pycache__'))
    routes={}
    for name,skill in [('meeting-prep','internal-meeting-prep'),('meeting-finished','meeting-finalize')]:
        routes[name]={'description':'Meeting workflow '+name,'events':[], 'secret':key,'skills':[skill,'meeting'],
                      'deliver':'log','prompt':
                      'Read references/public-runtime.md in the loaded skill first. Execute the full original workflow for the event JSON below. '
                      'Use only attendee-configured services. Preserve _dispatch executor receipts and stop if fenced. '
                      'No legacy bot spawn. Optional Slack/email/tasks require explicit enablement and human approval. '
                      'Dispatch fields: key={_dispatch.key} phase={_dispatch.phase} attempt={_dispatch.attempt} '
                      'delivery_id={_dispatch.delivery_id} claim_path={_dispatch.claim_path} receipt_path={_dispatch.receipt_path}. '
                      'First run node $MEETING_EXECUTOR_SCRIPT begin with those literal --key --phase --attempt --delivery-id --claim-path --receipt-path fields. '
                      'Heartbeat before side effects; finish --status succeeded only after verified artifacts, otherwise failed with an honest outcome. Exit code 3 means fenced: stop. '
                      'Write durable local artifacts when an optional integration is absent. Event payload: {__raw__}'}
    write(home/'webhook_subscriptions.json',json.dumps(routes,indent=2))
    redacted=json.loads(json.dumps(routes))
    for route in redacted.values():route['secret']='REDACTED'
    write(home/'webhook_subscriptions.redacted.json',json.dumps(redacted,indent=2))
    import yaml
    existing=home/'config.yaml'
    settings=yaml.safe_load(existing.read_text()) if existing.exists() else {}
    settings=settings or {}
    settings.setdefault('platforms',{})['webhook']={'enabled':True,'extra':{'host':'127.0.0.1','port':8644,'secret':key}}
    write(existing,yaml.safe_dump(settings,sort_keys=False))
    public=json.loads(json.dumps(settings));public['platforms']['webhook']['extra']['secret']='REDACTED'
    write(home/'config.redacted.yaml',yaml.safe_dump(public,sort_keys=False))
    profile_env='\n'.join([
        'HERMES_MEET_BACKEND=vexa-primary',
        'VEXA_API_ENDPOINT='+config.get('vexa_url','http://127.0.0.1:19056'),
        'VEXA_API_KEY_FILE='+config.get('vexa_key_file',str(root/'private/vexa-key')),
        'VEXA_BOT_NAME='+config.get('bot_name','Meeting Bot'),
        'MEETING_EXECUTOR_SCRIPT='+str(root/'scheduler/executor-receipt.js'),
        'MEETING_STT_URL='+config['stt_url'],
        'MEETING_STT_KEY_FILE='+config['stt_key_file'],
        'MEETING_STT_MODEL=whisper-1',
        'MEETING_EXECUTOR_SCRIPT='+str(root/'scheduler/executor-receipt.js'),
        'MEETING_NOTION_ENABLED='+str(bool(config.get('notion_parent_page_id'))).lower(),
        'MEETING_SLACK_ENABLED=false','MEETING_TASKS_ENABLED=false',
        'NOTION_TOKEN_FILE='+config.get('notion_token_file',str(root/'private/notion-token')),
        'NOTION_PARENT_PAGE_ID='+config.get('notion_parent_page_id',''),
        'CALENDAR_OWNER_EMAIL='+config.get('calendar_owner_email',''),
    ])+'\n'
    if (home/'.env').exists():
        # Never overwrite model credentials; append only the new workflow config.
        profile_env=(home/'.env').read_text()+'\n'+profile_env
    write(home/'.env',profile_env)
    print('Full meeting skills and signed prep/finish webhook routes installed. Vexa start remains direct. No secrets printed.')


if __name__=='__main__':configure(Path(__file__).resolve().parents[1])
