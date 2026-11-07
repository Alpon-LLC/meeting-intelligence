import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "send-transcript-email.py"
SPEC = importlib.util.spec_from_file_location("send_transcript_email", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class SendTranscriptEmailTest(unittest.TestCase):
    def setUp(self):
        old_domains, old_bot = MODULE.DEFAULT_DOMAINS, MODULE.BOT_ADDRESSES
        MODULE.DEFAULT_DOMAINS = ("example.org", "example.net")
        MODULE.BOT_ADDRESSES = {"bot@example.org"}
        self.addCleanup(setattr, MODULE, "DEFAULT_DOMAINS", old_domains)
        self.addCleanup(setattr, MODULE, "BOT_ADDRESSES", old_bot)

    def test_filters_external_declined_and_bot_addresses(self):
        event = {
            "organizer": {"email": "owner@example.org"},
            "attendees": [
                {"email": "member@example.net", "responseStatus": "accepted"},
                {"email": "declined@example.org", "responseStatus": "declined"},
                {"email": "external@example.com"},
                {"email": "bot@example.org"},
                {"email": "OWNER@EXAMPLE.ORG"},
            ],
        }
        self.assertEqual(MODULE.recipients(event), ["member@example.net", "owner@example.org"])

    def test_sends_once_and_records_private_receipt(self):
        calls = []

        def sender(script, to, subject, body):
            calls.append((script, to, subject, body))
            return {"status": "sent", "id": "gmail-1", "threadId": "thread-1"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps({"title": "Friday", "attendees": [{"email": "a@example.org"}]}))
            triage = root / "triage.json"
            triage.write_text(json.dumps({"notion_page_url": "https://notion.so/page", "transcript_summary": "Summary"}))
            receipt = root / "email.json"
            first = MODULE.deliver(event, triage, receipt, root / "google.py", sender=sender)
            second = MODULE.deliver(event, triage, receipt, root / "google.py", sender=sender)
            self.assertEqual(first["status"], "sent")
            self.assertEqual(second["status"], "already_sent")
            self.assertEqual(len(calls), 1)
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("Summary", receipt.read_text())

    def test_other_sent_receipt_blocks_a_second_publisher(self):
        calls = []

        def sender(script, to, subject, body):
            calls.append((script, to, subject, body))
            return {"status": "sent", "id": "gmail-2", "threadId": "thread-2"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            event = root / "event.json"
            event.write_text(json.dumps({"title": "Friday", "attendees": [{"email": "a@example.org"}]}))
            triage = root / "triage.json"
            triage.write_text(json.dumps({"notion_page_url": "https://notion.so/new", "transcript_summary": "New"}))
            (root / "transcript-email.json").write_text(json.dumps({
                "status": "sent",
                "recipients": ["a@example.org"],
                "message_id": "gmail-1",
                "notion_page_url": "https://notion.so/original",
            }))
            result = MODULE.deliver(event, triage, root / "transcript.email.receipt.json", root / "google.py", sender=sender)
            self.assertEqual(result["status"], "already_sent_other_receipt")
            self.assertEqual(result["message_id"], "gmail-1")
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
