import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.storage import Storage
import simple_server as server
from app.personas import Persona

class GroupHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Storage(Path(self.tmp.name) / 'test.db')
        self.store.init()
        self.store.group_history_limit = 50

    def add(self, scope, texts):
        self.store.ensure_conversation(scope, scope)
        for text in texts:
            self.store.add_message(scope, 'user', text)

    def test_exact_fifty_and_twenty_five_batch_with_rolling_context(self):
        seen = []
        def summarize(previous, lines, limit):
            seen.append((previous, lines))
            return previous + ' merged ' + lines[0]
        self.store.group_history_summarizer = summarize
        self.add('tg-group-1', [str(n) for n in range(74)])
        self.assertEqual(len(self.store.recent_messages('tg-group-1', 200)), 50)
        self.assertEqual(len(self.store.pending_group_history('tg-group-1')), 24)
        self.assertEqual(seen, [])
        self.add('tg-group-1', ['74'])
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0][1]), 25)
        self.assertEqual(self.store.pending_group_history('tg-group-1'), [])
        self.add('tg-group-1', [str(n) for n in range(75, 100)])
        self.assertEqual(len(seen), 2)
        self.assertIn('merged', seen[1][0])
        self.assertEqual(len(self.store.recent_messages('tg-group-1', 200)), 50)

    def test_isolation_persistence_and_private_unchanged(self):
        self.add('tg-group-1', ['alice: Friday'] + [str(n) for n in range(75)])
        self.add('tg-group-2', ['other'])
        self.add('private', [str(n) for n in range(80)])
        reopened = Storage(self.store.db_path)
        reopened.init()
        self.assertIn('Friday', reopened.group_history_context('tg-group-1'))
        self.assertEqual(len(reopened.pending_group_history('tg-group-1')), 1)
        self.assertEqual(reopened.group_history_context('tg-group-2'), '')
        self.assertEqual(len(reopened.recent_messages('private', 200)), 80)

    def test_pending_messages_are_available_to_model(self):
        self.add('tg-group-1', ['alice: pending agreement'] + [str(n) for n in range(50)])
        with patch.object(server, 'storage', self.store):
            messages = server.build_messages_for_model('tg-group-1', 'tg-group-1', 'hi', Persona.FRIEND, channel='telegram_group')
        self.assertIn('pending agreement', messages[1]['content'])
        self.assertEqual(len(messages), 53)

    def test_pending_context_is_bounded_when_compaction_lags(self):
        with self.store._connect() as conn:
            conn.executemany(
                "insert into group_history_pending (conversation_id, role, content, created_at) values (?, ?, ?, ?)",
                [('tg-group-1', 'user', str(n), '2026-10-14 10:00:00') for n in range(100)],
            )
        pending = self.store.pending_group_history('tg-group-1')
        self.assertEqual(len(pending), 25)
        self.assertEqual(pending[0]['content'], '75')
        self.assertEqual(pending[-1]['content'], '99')

    def test_snapshot_restores_summary_and_pending(self):
        self.add('tg-group-1', [str(n) for n in range(80)])
        snapshot = self.store.export_client_snapshot('tg-group-1')
        restored = Storage(Path(self.tmp.name) / 'restored.db')
        restored.init()
        restored.import_client_snapshot('tg-group-1', snapshot)
        self.assertEqual(restored.group_history_context('tg-group-1'), self.store.group_history_context('tg-group-1'))
        self.assertEqual(len(restored.pending_group_history('tg-group-1')), 5)

    def test_failed_model_uses_bounded_saved_fallback(self):
        def fail(*args):
            raise TimeoutError()
        self.store.group_history_summarizer = fail
        self.store.group_summary_limit = 256
        self.add('tg-group-1', ['important agreement'] + ['x' * 2000 for _ in range(74)])
        self.assertTrue(self.store.group_history_context('tg-group-1'))
        self.assertLessEqual(len(self.store.group_history_context('tg-group-1')), 256)
        self.assertEqual(self.store.pending_group_history('tg-group-1'), [])

    def test_raw_daily_archive_also_bounded(self):
        for n in range(75):
            self.store.record_telegram_group_message(-1, n, 123, 'alice', 'Alice', f'message {n}', '2026-10-14 10:00:00')
        rows = self.store.telegram_group_messages_between(-1, '2026-10-14', '2026-10-15')
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]['content'], 'message 25')
