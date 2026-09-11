import tempfile
import unittest
from pathlib import Path

import simple_server
from app.message_blocks import parse_render_blocks
from app.storage import Storage
from simple_server import handle_message_request, handle_sync_request


class SyncAndBlocksTests(unittest.TestCase):
    def test_sync_merges_events_without_dropping_other_device_messages(self) -> None:
        with isolated_storage():
            first = handle_sync_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "sync-test",
                    "messages": [
                        {
                            "message_id": "desktop-1",
                            "role": "user",
                            "content": "Сообщение с компьютера",
                            "created_at": "2026-09-11T08:00:00Z",
                            "client_created_at": "2026-09-11T08:00:00Z",
                            "device_id": "desktop",
                        }
                    ],
                }
            )
            second = handle_sync_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "sync-test",
                    "messages": [
                        {
                            "message_id": "android-1",
                            "role": "user",
                            "content": "Сообщение с телефона",
                            "created_at": "2026-09-11T08:01:00Z",
                            "client_created_at": "2026-09-11T08:01:00Z",
                            "device_id": "android",
                        }
                    ],
                }
            )

        self.assertEqual(first["imported"], 1)
        self.assertEqual(second["imported"], 1)
        contents = [item["content"] for item in second["messages"]]
        self.assertIn("Сообщение с компьютера", contents)
        self.assertIn("Сообщение с телефона", contents)

    def test_message_response_includes_render_blocks(self) -> None:
        with isolated_storage():
            response = handle_message_request(
                {"client_id": "primary-user", "conversation_id": "blocks-test", "text": "Привет"}
            )

        self.assertIn("render_blocks", response)
        self.assertTrue(response["render_blocks"])
        self.assertEqual(response["render_blocks"][0]["label"], "НАБЛЮДЕНИЕ")

    def test_parse_render_blocks_hides_markdown_labels(self) -> None:
        blocks = parse_render_blocks("ALIEN", "**Observation:** Нота принята.\nОбычный текст")

        self.assertEqual(blocks[0]["label"], "СИГНАЛ")
        self.assertNotIn("**", blocks[0]["text"])
        self.assertEqual(blocks[1]["type"], "text")


class isolated_storage:
    def __enter__(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old_storage = simple_server.storage
        simple_server.storage = Storage(Path(self.temp_dir.name) / "assistant.sqlite3")
        simple_server.storage.init()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        simple_server.storage = self.old_storage
        self.temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
