import unittest
from unittest.mock import patch
import simple_server as server
from tests.test_simple_server_stdlib import telegram_update, isolated_storage

class RestartTests(unittest.TestCase):
    def test_backlog_skipped_before_storage_model_or_send(self):
        update = telegram_update("бот, привет", "supergroup")
        update["message"]["date"] = 99
        with patch.object(server, "TELEGRAM_STARTED_AT", 100), patch.object(server, "storage") as storage, patch.object(server, "complete") as complete, patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["reason"], "before_server_restart")
            self.assertEqual(storage.mock_calls, [])
            complete.assert_not_called()
            send.assert_not_called()

    def test_fresh_message_after_restart_is_processed(self):
        update = telegram_update("Привет")
        update["message"]["date"] = 101
        with isolated_storage(), patch.object(server, "TELEGRAM_STARTED_AT", 100), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
            send.assert_called_once()

    def test_new_edit_of_old_message_is_not_backlog(self):
        update = telegram_update("Привет")
        event = update.pop("message")
        event.update(date=99, edit_date=101)
        update["edited_message"] = event
        with isolated_storage(), patch.object(server, "TELEGRAM_STARTED_AT", 100), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message"):
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
