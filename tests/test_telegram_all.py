import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import simple_server as server
from app.telegram_all import is_all_mention, send_all_mentions, track_group_members
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramAllTests(unittest.TestCase):
    def test_all_never_calls_unpaid_model(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "complete", side_effect=HTTPError("https://example.test", 402, "Payment Required", {}, None)) as complete, patch("app.telegram_all.api_call", return_value=[]):
            server.storage.set_telegram_chat_silent(555, True, 1)
            result = server.handle_telegram_update(telegram_update("@all", "supergroup"))
            self.assertEqual(result["handled"], "all_mentions")
            complete.assert_not_called()

    def test_all_send_failure_never_falls_through_to_model(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "complete") as complete, patch.object(server, "send_all_mentions", side_effect=HTTPError("https://example.test", 429, "Too Many Requests", {}, None)), patch.object(server, "try_send_telegram_message") as send:
            result = server.handle_telegram_update(telegram_update("@all", "group"))
            self.assertEqual(result["handled"], "all_mentions_failed")
            self.assertIn("не использует модель", send.call_args.args[1])
            complete.assert_not_called()

    def test_payment_error_is_not_reported_as_timeout(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", side_effect=HTTPError("https://example.test", 402, "Payment Required", {}, None)), patch.object(server, "try_send_telegram_message") as send:
            result = server.handle_telegram_update(telegram_update("Привет", "private"))
            self.assertEqual(result["handled"], "model_unavailable")
            self.assertIn("HTTP 402", send.call_args.args[1])
            self.assertNotIn("не ответила вовремя", send.call_args.args[1])

    def test_exact_mention(self):
        for text in ("@all", "Привет, @ALL!", "Сбор @all завтра"):
            self.assertTrue(is_all_mention(text))
        for text in ("@all_bot", "@alligator", "mail@all.com", "@@all"):
            self.assertFalse(is_all_mention(text))

    def test_quiet_and_closed_access_do_not_block_all(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", False), patch.object(server, "complete") as complete, patch("app.telegram_all.api_call", return_value=[]) as api:
            server.storage.set_telegram_chat_silent(555, True, 1)
            result = server.handle_telegram_update(telegram_update("Все сюда @all", "group", message_id=25))
            self.assertEqual(result["handled"], "all_mentions")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])
            complete.assert_not_called()
            payload = api.call_args.args[2]
            self.assertIn('tg://user?id=777', payload["text"])
            self.assertEqual(payload["parse_mode"], "HTML")

    def test_membership_is_scoped_persistent_and_tracks_departures(self):
        with isolated_storage():
            msg = telegram_update("hi", "group")["message"]
            msg["new_chat_members"] = [{"id": 888, "first_name": "Друг"}, {"id": 999, "is_bot": True}]
            track_group_members(server.storage, {"message": msg})
            self.assertEqual(len(server.storage.telegram_group_members(555)), 2)
            self.assertEqual(server.storage.telegram_group_members(556), [])
            track_group_members(server.storage, {"chat_member": {"chat": msg["chat"], "new_chat_member": {"user": {"id": 888}, "status": "left"}}})
            server.storage.init()
            self.assertEqual([x["user_id"] for x in server.storage.telegram_group_members(555)], ["777"])

    def test_migration_uses_existing_history(self):
        with isolated_storage():
            server.storage.record_telegram_group_message(555, 1, 888, "old", "Имя", "hi", "2026-01-01 00:00:00")
            server.storage.record_telegram_group_message(555, 2, 888, "new", "Имя", "hi", "2026-01-02 00:00:00")
            server.storage.init()
            self.assertEqual(server.storage.telegram_group_members(555)[0]["username"], "new")

    def test_mentions_escape_names_split_and_preserve_topic(self):
        with isolated_storage(), patch("app.telegram_all.api_call", return_value=[]) as api:
            for uid in range(1, 13):
                server.storage.record_telegram_group_member(555, {"id": uid, "first_name": "<Друг & Друг>"})
            send_all_mentions(server.storage, 555, "test", 123)
            sends = [call.args[2] for call in api.call_args_list if call.args[1] == "sendMessage"]
            self.assertEqual(len(sends), 3)
            self.assertEqual(sum(p["text"].count('<a href=') for p in sends), 12)
            self.assertTrue(all(p["message_thread_id"] == 123 for p in sends))
            self.assertIn("&lt;Друг &amp; Друг&gt;", sends[0]["text"])


if __name__ == "__main__":
    unittest.main()
