import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import Storage


class StorageCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "cache.db"
        self.store = Storage(self.path)
        self.store.init()
        self.store.ensure_conversation("tg-group-1", "tg-group-1")

    def test_warm_group_reads_do_not_open_database_and_new_messages_invalidate(self):
        self.store.add_message("tg-group-1", "user", "первое")
        first = self.store.recent_messages("tg-group-1", 50)
        self.assertEqual(first[0]["content"], "первое")
        self.assertEqual(self.store.pending_group_history("tg-group-1"), [])
        self.assertEqual(self.store.group_history_context("tg-group-1"), "")
        with patch.object(self.store, "_connect", side_effect=AssertionError("unexpected database read")):
            self.assertEqual(self.store.recent_messages("tg-group-1", 50), first)
            self.assertEqual(self.store.pending_group_history("tg-group-1"), [])
            self.assertEqual(self.store.group_history_context("tg-group-1"), "")
        self.store.add_message("tg-group-1", "user", "второе")
        self.assertEqual([row["content"] for row in self.store.recent_messages("tg-group-1", 50)], ["первое", "второе"])

    def test_compaction_invalidates_pending_and_summary(self):
        self.store.group_history_limit = 2
        self.store.group_compaction_batch = 2
        self.store.group_history_summarizer = lambda previous, lines, limit: "краткий контекст"
        self.store.pending_group_history("tg-group-1")
        self.store.group_history_context("tg-group-1")
        for number in range(4):
            self.store.add_message("tg-group-1", "user", str(number))
        self.assertEqual(self.store.pending_group_history("tg-group-1"), [])
        self.assertEqual(self.store.group_history_context("tg-group-1"), "краткий контекст")
        self.assertEqual([row["content"] for row in self.store.recent_messages("tg-group-1", 50)], ["2", "3"])

    def test_control_and_stickers_invalidate_after_writes(self):
        self.assertFalse(self.store.telegram_chat_control(555)["silent"])
        self.assertEqual(self.store.telegram_sticker_emojis(555), [])
        self.assertEqual(self.store.telegram_sticker_ids(555, "🫶"), [])
        self.store.set_telegram_chat_silent(555, True, 7)
        self.store.save_telegram_stickers(555, [{"file_id": "sticker", "file_unique_id": "unique", "emoji": "🫶"}])
        self.assertTrue(self.store.telegram_chat_control(555)["silent"])
        self.assertEqual(self.store.telegram_sticker_emojis(555), ["🫶"])
        self.assertEqual(self.store.telegram_sticker_ids(555, "🫶"), ["sticker"])
        with patch.object(self.store, "_connect", side_effect=AssertionError("unexpected database read")):
            self.assertTrue(self.store.telegram_chat_control(555)["silent"])
            self.assertEqual(self.store.telegram_sticker_ids(555, "🫶"), ["sticker"])

    def test_cache_expires_and_new_process_reads_persisted_data(self):
        self.store.add_message("tg-group-1", "user", "сохранено")
        self.store.recent_messages("tg-group-1", 50)
        reopened = Storage(self.path)
        self.assertEqual(reopened.recent_messages("tg-group-1", 50)[0]["content"], "сохранено")
        reopened.add_message("tg-group-1", "user", "внешнее")
        with patch("app.storage.time.monotonic", return_value=float("inf")):
            self.assertEqual([row["content"] for row in self.store.recent_messages("tg-group-1", 50)], ["сохранено", "внешнее"])


if __name__ == "__main__":
    unittest.main()
