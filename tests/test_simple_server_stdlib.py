import tempfile
import unittest
from pathlib import Path

import simple_server
from app.capabilities import CAPABILITIES_PROMPT
from app.personas import Persona, build_system_prompt, detect_persona_switch, extract_alien_glossary_terms
from app.smart_memory import extract_memory_directive
from app.storage import Storage
from simple_server import (
    handle_history_request,
    handle_message_request,
    handle_snapshot_request,
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
                    "client_id": "primary-user",
                    "conversation_id": "behaviour-test",
                    "text": "переключись на инопланетный режим",
                }
            )
            answer = handle_message_request(
                {
                    "client_id": "primary-user",
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
                    "client_id": "primary-user",
                    "conversation_id": "memory-test",
                    "text": "запомни: мой основной проект называется AI Assistant",
                }
            )
            listed = handle_message_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "memory-test",
                    "text": "/memory",
                }
            )

        self.assertTrue(stored["memory_updated"])
        self.assertIn("AI Assistant", listed["text"])

    def test_functions_command_lists_available_capabilities(self) -> None:
        with isolated_storage():
            response = handle_message_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "functions-test",
                    "text": "/functions",
                }
            )

        self.assertIn("/ana", response["text"])
        self.assertIn("/alien", response["text"])
        self.assertIn("/memory", response["text"])
        self.assertIn("Умная память", response["text"])
        self.assertIn("Conclusion", response["text"])

    def test_model_system_prompt_contains_available_functions(self) -> None:
        with isolated_storage():
            messages = simple_server.build_messages_for_model(
                "primary-user",
                "prompt-functions-test",
                "Что ты умеешь?",
                Persona.ANA,
            )

        system_prompt = messages[0]["content"]
        self.assertIn("Available assistant functions and boundaries", system_prompt)
        self.assertIn("/functions", system_prompt)
        self.assertIn("assistant_memory recall", system_prompt)
        self.assertIn("Current limits", system_prompt)
        self.assertIn(CAPABILITIES_PROMPT, system_prompt)

    def test_model_memory_directive_is_saved_and_hidden(self) -> None:
        old_complete = simple_server.complete

        def fake_complete(messages: list[dict[str, str]]) -> tuple[str, str]:
            return (
                "Запомнил рабочий факт.\n"
                "```assistant_memory\n"
                '{"remember":[{"kind":"important","summary":"пользователь работает над AI Assistant",'
                '"full":"Пользователь работает над проектом AI Assistant как личным агентом.",'
                '"topics":["ai assistant","project"],"importance":5}]}\n'
                "```",
                "mock",
            )

        with isolated_storage():
            simple_server.complete = fake_complete
            try:
                response = handle_message_request(
                    {
                        "client_id": "primary-user",
                        "conversation_id": "smart-save-test",
                        "text": "Я делаю AI Assistant",
                    }
                )
                memories = simple_server.storage.list_memories("primary-user")
            finally:
                simple_server.complete = old_complete

        self.assertEqual(response["memory_saved"], 1)
        self.assertNotIn("assistant_memory", response["text"])
        self.assertIn("AI Assistant", memories[0]["summary"])

    def test_model_can_recall_full_memory_for_second_pass(self) -> None:
        old_complete = simple_server.complete
        calls: list[list[dict[str, str]]] = []

        def fake_complete(messages: list[dict[str, str]]) -> tuple[str, str]:
            calls.append(messages)
            if len(calls) == 1:
                return (
                    "Уточняю память.\n"
                    "```assistant_memory\n"
                    '{"recall":["любимый проект"]}\n'
                    "```",
                    "mock",
                )
            joined = "\n".join(message["content"] for message in messages)
            assert "Recalled full memory cells" in joined
            return "Полная память использована.", "mock"

        with isolated_storage():
            simple_server.storage.add_memory_cell(
                client_id="primary-user",
                kind="important",
                summary="любимый проект пользователя",
                full_content="Любимый тестовый проект пользователя называется AI Assistant.",
                topics=["любимый проект", "ai assistant"],
                importance=5,
            )
            simple_server.complete = fake_complete
            try:
                response = handle_message_request(
                    {
                        "client_id": "primary-user",
                        "conversation_id": "smart-recall-test",
                        "text": "Как называется мой любимый проект?",
                    }
                )
            finally:
                simple_server.complete = old_complete

        self.assertEqual(response["memory_recalled"], 1)
        self.assertEqual(response["text"], "Полная память использована.")
        self.assertEqual(len(calls), 2)

    def test_history_endpoint_returns_recent_messages(self) -> None:
        with isolated_storage():
            handle_message_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "history-test",
                    "text": "Привет",
                }
            )
            history = handle_history_request(
                {"client_id": "primary-user", "conversation_id": "history-test", "limit": 10}
            )

        self.assertEqual(history["persona"], "ANA")
        self.assertGreaterEqual(len(history["messages"]), 2)
        self.assertEqual(history["messages"][0]["role"], "user")

    def test_snapshot_export_import_restores_memory_after_empty_storage(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            first = Storage(Path(temp_dir) / "first.sqlite3")
            first.init()
            first.remember("primary-user", "manual", "любимый цвет пользователя — синий", importance=4)
            first.ensure_conversation("primary-user", "default")
            first.add_message("default", "user", "Привет")
            snapshot = first.export_client_snapshot("primary-user")

            second = Storage(Path(temp_dir) / "second.sqlite3")
            second.init()
            imported = second.import_client_snapshot("primary-user", snapshot)
            memories = second.list_memories("primary-user")
            history = second.recent_messages("default", 10)

        self.assertGreaterEqual(imported["memory_cells"], 1)
        self.assertIn("синий", memories[0]["summary"])
        self.assertEqual(history[0]["content"], "Привет")

    def test_snapshot_endpoint_exports_and_imports_client_state(self) -> None:
        with isolated_storage():
            handle_message_request(
                {
                    "client_id": "primary-user",
                    "conversation_id": "snapshot-test",
                    "text": "запомни: Render без диска теряет sqlite память",
                }
            )
            exported = handle_snapshot_request({"client_id": "primary-user", "action": "export"})
            simple_server.storage = Storage(Path(simple_server.storage.db_path).with_name("empty.sqlite3"))
            simple_server.storage.init()
            imported = handle_snapshot_request(
                {"client_id": "primary-user", "action": "import", "snapshot": exported["snapshot"]}
            )
            listed = handle_message_request(
                {"client_id": "primary-user", "conversation_id": "snapshot-test", "text": "/memory"}
            )

        self.assertTrue(imported["ok"])
        self.assertIn("Render", listed["text"])

    def test_alien_glossary_extracts_stable_terms(self) -> None:
        terms = extract_alien_glossary_terms("API сервера отдаёт ошибку")

        self.assertEqual(terms["api"], "окно")
        self.assertEqual(terms["сервер"], "храм")
        self.assertEqual(terms["ошибка"], "диссонанс")

    def test_memory_directive_parser_removes_private_block(self) -> None:
        cleaned, directive = extract_memory_directive(
            'Ответ.\n```assistant_memory\n{"recall":["проект"]}\n```'
        )

        self.assertEqual(cleaned, "Ответ.")
        self.assertEqual(directive.recall, ["проект"])

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
