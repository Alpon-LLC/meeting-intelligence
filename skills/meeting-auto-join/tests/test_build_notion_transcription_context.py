from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build-notion-transcription-context.py"
SPEC = importlib.util.spec_from_file_location("build_notion_transcription_context", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class NotionContextTest(unittest.TestCase):
    def setUp(self):
        self.previous_domains = MODULE.INTERNAL_DOMAINS
        MODULE.INTERNAL_DOMAINS = ("@example.org",)
        self.addCleanup(setattr, MODULE, "INTERNAL_DOMAINS", self.previous_domains)

    def fixtures(self):
        users = {"operator@example.org": "user-operator"}
        tasks = [
            {
                "title": "[FE] Add Open Graph support - created by John",
                "assignee_ids": ["user-operator"],
                "project_ids": ["project-aeo"],
            },
            {
                "title": "Unrelated accounting task",
                "assignee_ids": ["someone-else"],
                "project_ids": ["project-other"],
            },
        ]
        projects = {
            "project-aeo": {"properties": {
                "Project name": {"title": [{"plain_text": "Example Analytics"}]},
                "Client": {"relation": [{"id": "client-exampleorg"}]},
            }},
            "project-other": {"properties": {
                "Project name": {"title": [{"plain_text": "Other Project"}]},
            }},
        }
        clients = {
            "client-exampleorg": {"properties": {"Client name": {"title": [{"plain_text": "ExampleOrg"}]}}},
        }
        return users, tasks, projects, clients

    def test_standup_uses_bounded_attendee_tasks_projects_and_clients(self):
        users, tasks, projects, clients = self.fixtures()
        event = {
            "title": "ExampleOrg Daily Standup",
            "attendees": [{"email": "operator@example.org"}],
        }
        mode, terms = MODULE.build_terms(event, users, tasks, projects, clients)
        self.assertEqual(mode, "attendee_tasks")
        self.assertEqual([item["text"] for item in terms], ["ExampleOrg", "Example Analytics", "Add Open Graph support"])

    def test_client_sync_only_uses_title_matched_projects(self):
        users, tasks, projects, clients = self.fixtures()
        event = {
            "title": "Example Analytics weekly sync",
            "attendees": [{"email": "operator@example.org"}],
        }
        mode, terms = MODULE.build_terms(event, users, tasks, projects, clients)
        self.assertEqual(mode, "title_matched")
        self.assertIn("Example Analytics", [item["text"] for item in terms])
        self.assertNotIn("Other Project", [item["text"] for item in terms])


if __name__ == "__main__":
    unittest.main()
