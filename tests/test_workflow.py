import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load_app():
    spec = importlib.util.spec_from_file_location('meeting_app', ROOT / 'meeting_app.py')
    if spec is None or not (ROOT / 'meeting_app.py').exists():
        raise AssertionError('meeting_app.py must implement the workflow')
    assert spec.loader is not None
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.app = load_app()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = datetime(2030, 1, 1, 10, tzinfo=timezone.utc)

    def event(self, **kw):
        value = {'id': 'synthetic-occurrence', 'summary': 'Synthetic demo [record]',
                 'start': {'dateTime': self.now.isoformat()},
                 'end': {'dateTime': (self.now + timedelta(minutes=20)).isoformat()},
                 'hangoutLink': 'https://meet.google.com/abc-defg-hij', 'status': 'confirmed'}
        value.update(kw)
        return value

    def test_extract_only_real_meet_host(self):
        self.assertEqual(self.app.meet_code(self.event()), 'abc-defg-hij')
        self.assertIsNone(self.app.meet_code(self.event(hangoutLink='https://evil.test/meet.google.com/abc-defg-hij')))

    def test_consent_marker_and_window_required(self):
        self.assertTrue(self.app.eligible(self.event(), self.now, '[record]', 120))
        self.assertFalse(self.app.eligible(self.event(summary='No consent'), self.now, '[record]', 120))
        self.assertFalse(self.app.eligible(self.event(status='cancelled'), self.now, '[record]', 120))
        self.assertFalse(self.app.eligible(self.event(), self.now - timedelta(hours=1), '[record]', 120))

    def test_join_once_and_wait_for_actual_admission(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            return {'id': 42, 'status': 'requested'}
        worker.tick([self.event()], self.now, api)
        worker.tick([self.event()], self.now, api)
        self.assertEqual(len(calls), 2)  # create once then exact transcript poll
        self.assertEqual(calls[0][:2], ('POST', '/bots'))
        self.assertEqual(calls[1][:2], ('GET', '/transcripts/by-id/42'))
        state = worker.states()[0]
        self.assertEqual(state['phase'], 'requested')
        self.assertEqual(state['bot_id'], 42)

    def test_missing_bot_id_never_blindly_resubmits(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        def api(method, path, payload=None):
            return {'status': 'requested'}
        worker.tick([self.event()], self.now, api)
        worker.tick([self.event()], self.now, api)
        self.assertEqual(worker.states()[0]['phase'], 'needs_review')

    def test_failed_submission_unknown_outcome_is_not_retried(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        calls = []
        def api(*args):
            calls.append(args)
            raise TimeoutError('unknown outcome')
        worker.tick([self.event()], self.now, api)
        worker.tick([self.event()], self.now, api)
        self.assertEqual(len(calls), 1)
        self.assertEqual(worker.states()[0]['phase'], 'needs_review')

    def test_terminal_transcript_is_saved_and_summary_called(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        worker.tick([self.event()], self.now, lambda *a: {'id': 42})
        summary_calls = []
        doc = {'id': 42, 'status': 'completed', 'segments': [
            {'speaker': 'Alex', 'text': 'We agree to test the synthetic demo.', 'start': 0}]}
        worker.tick([self.event()], self.now, lambda *a: doc, lambda p: summary_calls.append(p))
        state = worker.states()[0]
        self.assertEqual(state['phase'], 'captured')
        self.assertEqual(len(summary_calls), 1)
        self.assertTrue((Path(state['directory']) / 'transcript.md').exists())

    def test_wrong_bot_transcript_is_rejected(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        worker.tick([self.event()], self.now, lambda *a: {'id': 42})
        worker.tick([self.event()], self.now, lambda *a: {'id': 99, 'status': 'completed', 'segments': [{'text': 'wrong'}]})
        self.assertEqual(worker.states()[0]['phase'], 'needs_review')

    def test_empty_terminal_waits_for_flush_before_review(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        worker.tick([self.event()], self.now, lambda *a: {'id': 42})
        worker.tick([self.event()], self.now, lambda *a: {'id': 42, 'status': 'completed', 'segments': []})
        self.assertEqual(worker.states()[0]['phase'], 'awaiting_transcript')
        worker.tick([self.event()], self.now + timedelta(minutes=6), lambda *a: {'id': 42, 'status': 'completed', 'segments': []})
        self.assertEqual(worker.states()[0]['phase'], 'needs_review')

    def test_summary_rejects_partial_capture_without_review(self):
        directory = self.root / 'event'
        directory.mkdir()
        (directory / 'transcript.md').write_text('Synthetic partial speech')
        self.app.atomic(directory / 'state.json', {'phase': 'captured', 'capture_status': 'failed'})
        with self.assertRaises(PermissionError):
            self.app.summarize_directory(directory, 'must-not-run')

    def test_notion_requires_explicit_approval(self):
        directory = self.root / 'event'
        directory.mkdir()
        (directory / 'summary.md').write_text('Synthetic summary')
        with self.assertRaises(PermissionError):
            self.app.publish_notion(directory, 'test-parent', lambda *a: {}, approved=False)

    def test_notion_verifies_and_deduplicates(self):
        directory = self.root / 'event'
        directory.mkdir()
        (directory / 'summary.md').write_text('Synthetic summary')
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path, payload))
            if method == 'POST': return {'id': 'page-1', 'url': 'https://notion.so/page-1'}
            return {'object': 'page', 'id': 'page-1'}
        self.app.publish_notion(directory, 'test-parent', api, approved=True)
        self.app.publish_notion(directory, 'test-parent', api, approved=True)
        self.assertEqual(sum(x[0] == 'POST' for x in calls), 1)

    def test_active_bot_stops_at_scheduled_end(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        worker.tick([self.event()], self.now, lambda *a: {'id': 42})
        calls = []
        def api(method, path, payload=None):
            calls.append((method, path))
            return {'id': 42, 'status': 'active', 'start_time': self.now.isoformat()}
        worker.tick([], self.now + timedelta(minutes=21), api)
        self.assertIn(('DELETE', '/bots/google_meet/abc-defg-hij'), calls)
        self.assertEqual(worker.states()[0]['phase'], 'stopping')

    def test_submission_failure_blocks_other_events_until_reconciled(self):
        worker = self.app.Worker(self.root, 'Test Bot', '[record]')
        calls = []
        def api(*args):
            calls.append(args)
            raise TimeoutError('unknown outcome')
        worker.tick([self.event()], self.now, api)
        worker.tick([self.event(id='different-event')], self.now, api)
        self.assertEqual(len(calls), 1)

    def test_unsafe_state_directory_rejected(self):
        with self.assertRaises(ValueError):
            self.app.safe_directory(self.root, '../escape')


if __name__ == '__main__':
    unittest.main()
