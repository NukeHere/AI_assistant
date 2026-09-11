import tempfile
import unittest
from pathlib import Path

import simple_server
from app.personas import Persona, build_system_prompt, detect_persona_switch, extract_alien_glossary_terms
from app.storage import Storage
from simple_server import (
    handle_history_request,
    handle_message_request,
    mock_response,
    provider_mode,
    valid_messages,
)


class SimpleServerTests(unittest.TestCase):
    def test_valid_messages_accepts_chat_history(self) -> None:
        self.assertTrue(
            valid_messages(
                [
                    {"role": "system", "content": "You are helpful."},
                    {"role": "user", "content": "Привет"},
                    {"role": "assistant", "content": "Здравствуйте."},
                ]
            )
        )

    def test_valid_messages_rejects_empty_history(self) -> None:
        self.assertFalse(valid_messages([]))

    def test_mock_response_uses_last_user_message(self) -> None:
        response = mock_response(
            [
                {"role": "user", "content": "Первое"},
                {"role": "assistant", "content": "Ответ"},
                {"role": "user", "content": "Второе"},
            ]
        )
        self.assertIn("Второе", response)
        self.assertNotIn("Первое", response)

    def test_default_provider_is_mock(self) -> None:
        self.assertEqual(provider_mode(), "mock")

    def test_message_endpoint_switches_persona_and_keeps_context(self) -> None:
        with isolated_storage():
            switched = handle_message_request(
                {
                    "client_id": "desktop",
                    "conversation_id": "behaviour-test",
                    "text": "переключись на инопланетный режим",
                }
            )
            answer = handle_message_request(
                {
                    "client_id": "desktop",
                    "conversation_id": "behaviour-test",
                    "text": "Объясни Docker простыми словами",
                }
            )

        self.assertEqual(switched["persona"], "ALIEN")
        self.assertTrue(switched["switched"])
        self.assertEqual(answer["persona"], "ALIEN")
        self.assertFalse(answer["switched"])

    def test_memory_command_stores_and_lists_user_note(self) -> None:
        with isolated_storage():
            stored = handle_message_request(
                {
                    "client_id": "desktop",
                    "conversation_id": "memory-test",
                    "text": "запомни: мой основной проект называется AI Assistant",
                }
            )
            listed = handle_message_request(
                {
                    "client_id": "desktop",
                    "conversation_id": "memory-test",
                    "text": "/memory",
                }
            )

        self.assertTrue(stored["memory_updated"])
        self.assertIn("AI Assistant", listed["text"])

    def test_history_endpoint_returns_recent_messages(self) -> None:
        with isolated_storage():
            handle_message_request(
                {
                    "client_id": "desktop",
                    "conversation_id": "history-test",
                    "text": "Привет",
                }
            )
            history = handle_history_request(
                {"client_id": "desktop", "conversation_id": "history-test", "limit": 10}
            )

        self.assertEqual(history["persona"], "ANA")
        self.assertGreaterEqual(len(history["messages"]), 2)
        self.assertEqual(history["messages"][0]["role"], "user")

    def test_alien_glossary_extracts_stable_terms(self) -> None:
        terms = extract_alien_glossary_terms("API сервера отдаёт ошибку")

        self.assertEqual(terms["api"], "окно")
        self.assertEqual(terms["сервер"], "храм")
        self.assertEqual(terms["ошибка"], "диссонанс")

    def test_persona_prompts_support_structured_user_facing_cues(self) -> None:
        ana = build_system_prompt(Persona.ANA)
        alien = build_system_prompt(Persona.ALIEN)

        self.assertIn("does not use ALIEN metaphors", ana)
        self.assertIn("window", alien)
        self.assertIn("Priority order", alien)
        self.assertIn("user-facing Markdown cues", ana)
        self.assertIn("Command action", alien)
        self.assertNotIn("Default answer shape", ana)
        self.assertNotIn("Default answer shape", alien)

    def test_dialogue_quality_cases_are_represented(self) -> None:
        sample_cases = [
            "Привет",
            "Объясни Docker простыми словами",
            "У меня nginx отдаёт 502",
            "Напиши Python-функцию",
            "Я задолбался, ничего не работает",
            "Что ты думаешь о людях?",
            "Объясни разницу TCP и UDP",
            "Помоги составить план проекта",
            "Почему запрос SQL медленный?",
            "Переключись на инопланетный режим",
            "Следующее сообщение после переключения",
            "Верни АНА",
            "Продолжение предыдущей темы после переключения обратно",
        ]

        self.assertEqual(len(sample_cases), 13)
        self.assertEqual(detect_persona_switch(sample_cases[9]), Persona.ALIEN)
        self.assertEqual(detect_persona_switch(sample_cases[11]), Persona.ANA)


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
