import unittest

from simple_server import mock_response, provider_mode, valid_messages


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


if __name__ == "__main__":
    unittest.main()
