import tempfile
import unittest
from pathlib import Path

import simple_server
from app.smart_memory import extract_memory_directive, sanitize_timed_memory_items
from app.storage import Storage
from simple_server import handle_message_request, handle_sync_request


class TimedMemoryTests(unittest.TestCase):
    def test_memory_directive_parses_timers(self) -> None:
        cleaned, directive = extract_memory_directive(
            'Готово.\n```assistant_memory\n'
            '{"timers":[{"summary":"помидор","full":"Вспомнить слово помидор",'
            '"due_at":"2030-04-17T12:34:56Z","timezone":"Europe/Moscow"}]}\n```'
        )

        self.assertEqual(cleaned, "Готово.")
        self.assertEqual(directive.timers[0]["summary"], "помидор")
        self.assertEqual(sanitize_timed_memory_items(directive.timers)[0]["due_at"], "2030-04-17T12:34:56Z")

    def test_model_can_create_timed_memory_and_timers_command_lists_it(self) -> None:
        old_complete = simple_server.complete

        def fake_complete(messages: list[dict[str, str]]) -> tuple[str, str]:
            return (
                'Поставила временную память.\n```assistant_memory\n'
                '{"timers":[{"summary":"помидор","full":"Вспомнить слово помидор",'
                '"due_at":"2030-04-17T12:34:56Z","timezone":"Europe/Moscow"}]}\n```',
                "mock",
            )

        with isolated_storage():
            simple_server.complete = fake_complete
            try:
                response = handle_message_request(
                    {"client_id": "primary-user", "conversation_id": "timers-test", "text": "Напомни про помидор"}
                )
                listed = handle_message_request(
                    {"client_id": "primary-user", "conversation_id": "timers-test", "text": "/timers"}
                )
            finally:
                simple_server.complete = old_complete

        self.assertEqual(response["timed_memory_saved"], 1)
        self.assertNotIn("assistant_memory", response["text"])
        self.assertIn("помидор", listed["text"])

    def test_due_timed_memory_is_materialized_during_sync(self) -> None:
        with isolated_storage():
            simple_server.storage.add_timed_memory(
                "primary-user",
                "due-test",
                "помидор",
                "Вспомнить слово помидор",
                "2000-01-01T00:00:00Z",
            )
            synced = handle_sync_request({"client_id": "primary-user", "conversation_id": "due-test", "messages": []})

        system_messages = [item["content"] for item in synced["messages"] if item["role"] == "system"]
        self.assertTrue(any("помидор" in item for item in system_messages))

    def test_snapshot_roundtrip_keeps_timed_memories(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            first = Storage(Path(temp_dir) / "first.sqlite3")
            first.init()
            first.add_timed_memory(
                "primary-user",
                "snapshot-timer",
                "помидор",
                "Вспомнить слово помидор",
                "2030-04-17T12:34:56Z",
            )
            snapshot = first.export_client_snapshot("primary-user")

            second = Storage(Path(temp_dir) / "second.sqlite3")
            second.init()
            imported = second.import_client_snapshot("primary-user", snapshot)
            timers = second.list_timed_memories("primary-user", "snapshot-timer")

        self.assertEqual(imported["timed_memories"], 1)
        self.assertEqual(timers[0]["summary"], "помидор")


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
