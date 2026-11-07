#!/usr/bin/env python3
"""Explicit local setup operations. Never deploys until the user selects 'up'."""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

VEXA_REPO = 'https://github.com/Vexa-ai/vexa.git'
VEXA_COMMIT = 'f1a62fbbdfd2c98cb162582d82c31fe8102acc89'
ROOT = Path(__file__).resolve().parent


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f: f.write(text)


def initialize(root=ROOT):
    root = Path(root).resolve()
    if (root / '.env').exists(): return
    token = lambda: secrets.token_hex(24)
    docker_gid = os.stat('/var/run/docker.sock').st_gid if Path('/var/run/docker.sock').exists() else 0
    env = {'COMPOSE_PROJECT_NAME': 'meeting-lab', 'DB_NAME': 'vexa', 'DB_USER': 'postgres',
           'DB_PASSWORD': token(), 'ADMIN_TOKEN': token(), 'INTERNAL_API_SECRET': token(),
           'VEXA_SYSTEM_WEBHOOK_SECRET': token(), 'HERMES_WEBHOOK_SECRET': token(),
           'STORAGE_ACCESS_KEY': 'meetinglab', 'STORAGE_SECRET_KEY': token(), 'STT_TOKEN': token(),
           'DOCKER_GID': str(docker_gid), 'BOT_NAME': 'Meeting Lab Bot',
           'STT_MODEL': 'small', 'VEXA_BOT_IMAGE': 'meeting-lab/vexa-bot:0.12.28',
           'VEXA_MEETING_API_IMAGE': 'meeting-lab/meeting-api:0.12.28',
           'VEXA_GATEWAY_PORT': '19056', 'VEXA_ADMIN_PORT': '19058'}
    config = {'meetings_dir': str(root / 'private/hermes/workspace/meetings'), 'bot_name': env['BOT_NAME'],
              'hermes_home': str(root / 'private/hermes'), 'calendar_owner_email': '',
              'consent_marker': '[record]', 'recording_consent_confirmed': False,
              'vexa_url': 'http://127.0.0.1:19056', 'vexa_key_file': str(root / 'private/vexa-key'),
              'calendar_token_file': str(root / 'private/calendar-token.json'), 'calendar_id': 'primary',
              'notion_token_file': str(root / 'private/notion-token'), 'notion_parent_page_id': '',
              'hermes_bin': str(root / '.hermes-venv/bin/hermes')}
    private_write(root / 'private/config.json', json.dumps(config, indent=2))
    private_write(root / '.env', ''.join(f'{k}={v}\n' for k,v in env.items()))


def run(args, cwd=ROOT):
    subprocess.run(args, cwd=cwd, check=True)


def compose(*args): run(['docker', 'compose', '--env-file', '.env', *args])


def env_values():
    return dict(line.split('=',1) for line in (ROOT / '.env').read_text().splitlines() if '=' in line and not line.startswith('#'))


def fetch_source():
    dest = ROOT / '.build/vexa'
    if dest.exists():
        raise SystemExit('Build source already exists; inspect it rather than overwriting. Use a fresh clone for another build.')
    dest.parent.mkdir(exist_ok=True)
    run(['git','clone','--filter=blob:none','--no-checkout',VEXA_REPO,str(dest)])
    run(['git','checkout','--detach',VEXA_COMMIT], dest)
    patch = ROOT / 'patches/vexa.patch'
    run(['git','apply','--check',str(patch)], dest)
    run(['git','apply',str(patch)], dest)
    run(['git','diff','--check'], dest)
    print('Fetched exact Vexa commit and applied reviewed patch. No images built yet.')


def build_images():
    dest = ROOT / '.build/vexa'
    actual = subprocess.check_output(['git','rev-parse','HEAD'],cwd=dest,text=True).strip()
    if actual != VEXA_COMMIT: raise SystemExit('Unexpected Vexa base commit')
    env = env_values()
    patch_sha = hashlib.sha256((ROOT/'patches/vexa.patch').read_bytes()).hexdigest()
    marker = VEXA_COMMIT + '-patch-' + patch_sha
    run(['docker','build','-f','core/meetings/modules/join/Dockerfile.env','-t','vexa/meet-join-env:dev','core/meetings/modules/join'],dest)
    run(['docker','build','--build-arg','VEXA_IMAGE_VERSION='+marker,'-f','core/meetings/services/bot/Dockerfile','-t',env['VEXA_BOT_IMAGE'],'.'],dest)
    run(['docker','build','-f',str(ROOT/'deploy/Dockerfile.meeting-api'),'-t',env['VEXA_MEETING_API_IMAGE'],'.'],dest)
    run(['docker','build','-f','core/meetings/services/transcription/Dockerfile.cpu','-t','meeting-lab/transcription:0.12.28','.'],dest)
    print('Built bot, meeting API and local CPU transcription. Source remains in .build/vexa.')


def bootstrap():
    from meeting_app import http, atomic
    env = env_values()
    output = ROOT / 'private/vexa-key'
    if output.exists(): raise SystemExit('API key exists; no users/tokens changed')
    api = http('http://127.0.0.1:'+env['VEXA_ADMIN_PORT'], env['ADMIN_TOKEN'], 'X-Admin-API-Key')
    email = input('Google account email used for the bot: ').strip()
    if '@' not in email: raise SystemExit('Valid email required')
    user = api('POST','/admin/users',{'email':email,'name':env['BOT_NAME'],'max_concurrent_bots':1})
    result = api('POST',f"/admin/users/{int(user['id'])}/tokens",{'name':'meeting-lab','scopes':['bot']})
    if not result.get('token'): raise SystemExit('API did not return a token')
    atomic(output, result['token']+'\n')
    print('API key saved privately; no token printed.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['init','doctor','fetch','build','up','down','login','bootstrap','install','notion','consent'])
    args=parser.parse_args()
    if args.action=='init': initialize(); print('Private configuration ready.'); return
    if args.action=='doctor':
        for cmd in [['docker','version'],['docker','compose','version'],['git','--version']]: run(cmd)
        print('Docker access permits host-level changes. Use only your own local Docker engine.'); return
    if args.action=='fetch': return fetch_source()
    if args.action=='build': return build_images()
    if args.action=='up': return compose('up','-d','--wait')
    if args.action=='down': return compose('down')  # no volume deletion
    if args.action=='login':
        print('Open http://127.0.0.1:16080/vnc.html in your local browser. Sign in yourself; never give passwords/MFA to Devin. Login browser must stay running until completion.')
        return compose('--profile','login','run','--rm','--service-ports','login')
    if args.action=='bootstrap': return bootstrap()
    if args.action=='install':
        run([sys.executable,'-m','venv','.venv'])
        run([str(ROOT/'.venv/bin/python'),'-m','pip','install','-r','requirements.txt'])
        run(['npm','ci','--ignore-scripts','--no-audit','--no-fund'])
        print('Python and original JS scheduler dependencies installed. Follow docs/HERMES.md.'); return
    if args.action=='notion':
        token=getpass.getpass('Notion integration token (hidden): ').strip()
        parent=input('Parent Notion page ID shared with integration: ').strip()
        if not token or not parent: raise SystemExit('Both fields required')
        private_write(ROOT/'private/notion-token',token+'\n')
        from meeting_app import read,atomic
        config=read(ROOT/'private/config.json'); config['notion_parent_page_id']=parent
        atomic(ROOT/'private/config.json',config)
        print('Notion configured. Each publication still requires --approve.'); return
    if args.action=='consent':
        print('Only mark events [record] after organizer and participants agree to bot attendance, audio capture and transcription. Audio/profile data stays in your local Docker storage; configured model/Notion providers receive content when used.')
        if input('Type I HAVE CONSENT to enable scheduling: ')!='I HAVE CONSENT': raise SystemExit('Not enabled')
        from meeting_app import read,atomic
        config=read(ROOT/'private/config.json'); config['recording_consent_confirmed']=True
        atomic(ROOT/'private/config.json',config)


if __name__=='__main__': main()
