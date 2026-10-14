import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import simple_server as server
from tests.test_simple_server_stdlib import telegram_update, isolated_storage

class RestartTests(unittest.TestCase):
    def test_old_passive_group_backlog_is_skipped(self):
        update = telegram_update("разговор между участниками", "supergroup")
        update["message"]["date"] = int(datetime.now(timezone.utc).timestamp()) - 600
        with patch.object(server, "storage") as storage, patch.object(server, "complete") as complete, patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["reason"], "old_passive_group_update")
            self.assertEqual(storage.mock_calls, [])
            complete.assert_not_called()
            send.assert_not_called()

    def test_fresh_message_after_restart_is_processed(self):
        update = telegram_update("Привет")
        update["message"]["date"] = int(datetime.now(timezone.utc).timestamp())
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
            send.assert_called_once()

    def test_message_that_woke_server_is_processed(self):
        update = telegram_update("Привет")
        update["message"]["date"] = int(datetime.now(timezone.utc).timestamp()) - 600
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
            send.assert_called_once()

    def test_new_edit_of_old_message_is_not_backlog(self):
        update = telegram_update("Привет")
        event = update.pop("message")
        event.update(date=99, edit_date=int(datetime.now(timezone.utc).timestamp()))
        update["edited_message"] = event
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message"):
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")

    def test_direct_group_request_survives_long_cold_start(self):
        update = telegram_update("бот, привет", "supergroup")
        update["message"]["date"] = int(datetime.now(timezone.utc).timestamp()) - 3600
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=("Привет", "mock")), patch.object(server, "send_telegram_message") as send:
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
            send.assert_called_once()
