"""Tests for public differences while preserving original workflow algorithms."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import os
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]

def load(name,rel):
    spec=importlib.util.spec_from_file_location(name,ROOT/rel)
    assert spec and spec.loader
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class PortTests(unittest.TestCase):
    def test_http_only_rejects_private_ssh_path(self):
        m=load('starter_public','skills/meeting-auto-join/scripts/start-vexa-context-canary.py')
        with self.assertRaisesRegex(RuntimeError,'VEXA_API_ENDPOINT'):
            m.submit(b'private payload','host','container')

    def test_full_original_workflows_not_stub_replacements(self):
        minimum={'meeting-auto-join':500,'meeting-finalize':700,'internal-meeting-prep':650,
                 'meeting-transcript-to-notion':400,'post-meeting-approval':200,'retrieve-meeting-flow-context':100}
        for name,n in minimum.items():
            text=(ROOT/'skills'/name/'SKILL.md').read_text()
            self.assertGreater(len(text.splitlines()),n,name)
            self.assertIn('public-runtime.md',text)

    def test_webhook_routes_preserve_agent_work(self):
        path=ROOT/'scripts/configure_runtime.py'
        self.assertTrue(path.exists(),'need install/configuration for full skill + agent webhook runtime')
        m=load('runtime_config','scripts/configure_runtime.py')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'private').mkdir()
            (root/'private/config.json').write_text(json.dumps({'calendar_owner_email':'person@example.org'}))
            m.configure(root)
            home=root/'private/hermes'
            routes=json.loads((home/'webhook_subscriptions.json').read_text())
            self.assertIn('internal-meeting-prep',routes['meeting-prep']['skills'])
            self.assertIn('meeting-finalize',routes['meeting-finished']['skills'])
            self.assertNotIn('meeting-started',routes)
            self.assertTrue((home/'webhook_subscriptions.redacted.json').exists())
            self.assertNotIn(routes['meeting-prep']['secret'],(home/'webhook_subscriptions.redacted.json').read_text())

if __name__=='__main__':unittest.main()
