#!/usr/bin/env python3
"""Install a separate Hermes checkout/home. Does not change an existing profile."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '93dd24359dc011283a02098af84f63404f72ad15'


def main():
    source = ROOT / '.build/hermes'
    home = ROOT / 'private/hermes'
    venv = ROOT / '.hermes-venv'
    if source.exists() or venv.exists():
        raise SystemExit('Isolated Hermes source/venv already exists. Inspect it; not overwriting.')
    source.parent.mkdir(exist_ok=True)
    subprocess.run(['git','clone','--filter=blob:none','--no-checkout','https://github.com/NousResearch/hermes-agent.git',str(source)],check=True)
    subprocess.run(['git','checkout','--detach',COMMIT],cwd=source,check=True)
    subprocess.run([sys.executable,'-m','venv',str(venv)],check=True)
    subprocess.run([str(venv/'bin/python'),'-m','pip','install','-e',str(source)],check=True)
    home.mkdir(parents=True,mode=0o700)
    for skill in (ROOT/'skills').iterdir():
        if skill.is_dir(): shutil.copytree(skill,home/'skills'/skill.name,dirs_exist_ok=False)
    print('Installed isolated Hermes. Next:')
    print('source ./activate.sh')
    print('hermes setup')
    print('Authorize your model provider yourself. Never supply Google passwords or tokens in chat.')


if __name__=='__main__': main()
