import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.storage import Storage


class TursoStorageTests(unittest.TestCase):
    def test_requires_both_credentials(self):
        with self.assertRaises(ValueError):
            Storage(Path("unused.sqlite3"), remote_url="turso://example")
        with self.assertRaises(ValueError):
            Storage(Path("unused.sqlite3"), auth_token="secret")

    def test_remote_backend_keeps_stickers_and_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            remote_file = Path(directory) / "remote.sqlite3"
            local_file = Path(directory) / "unused" / "local.sqlite3"

            def connect(url, *, auth_token):
                self.assertEqual(url, "turso://example")
                self.assertEqual(auth_token, "test-token")
                return sqlite3.connect(remote_file)

            with patch("turso_serverless.connect", side_effect=connect):
                storage = Storage(local_file, remote_url="turso://example", auth_token="test-token")
                storage.init()
                storage.ensure_conversation("client", "conversation")
                storage.save_telegram_stickers(555, [{"file_id": "file", "file_unique_id": "unique",
                                                       "emoji": "🙂", "set_name": "test"}])
                storage.set_telegram_chat_silent(555, True, 7)

                reopened = Storage(local_file, remote_url="turso://example", auth_token="test-token")
                self.assertEqual(reopened.conversation_state("conversation")["message_count"], 0)
                self.assertEqual(reopened.telegram_sticker_ids(555, "🙂"), ["file"])
                self.assertTrue(reopened.telegram_chat_control(555)["silent"])
                self.assertFalse(local_file.exists())


if __name__ == "__main__":
    unittest.main()
