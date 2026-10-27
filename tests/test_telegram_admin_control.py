import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import simple_server as server
from app.telegram_admin_control import is_current_administrator, parse_control_command
from app.telegram_digest import MOSCOW
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramAdminControlTests(unittest.TestCase):
    def test_parses_targeted_commands_without_accepting_other_bot(self):
        names = {"vbdsthirdson_bot"}
        self.assertEqual(parse_control_command("бот, замолчи", None, names), "quiet")
        self.assertEqual(parse_control_command("бот, говори", None, names), "speak")
        self.assertEqual(parse_control_command("", {"command": "quiet", "target_username": ""}, names), "quiet")
        self.assertIsNone(parse_control_command("", {"command": "quiet", "target_username": "other_bot"}, names))

    def test_telegram_member_status_must_match_sender_id(self):
        with patch("app.telegram_admin_control.api_call", return_value={"status": "administrator", "user": {"id": 777}}):
            self.assertTrue(is_current_administrator(555, "777", "test"))
            self.assertFalse(is_current_administrator(555, "888", "test"))

    def test_admin_can_silence_and_resume_one_group_only(self):
        roster = [{"id": "777", "username": "telegram_user", "name": "Telegram"}]
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "fetch_administrators", return_value=roster) as fetch, patch.object(server, "is_current_administrator", return_value=True) as verify, patch.object(server, "send_telegram_message") as send, patch.object(server, "complete", return_value=("Я тут.", "mock")) as complete:
            quiet = telegram_update("бот, замолчи", "group", message_id=11)
            self.assertEqual(server.handle_telegram_update(quiet)["control"], "quiet")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])
            self.assertEqual(server.handle_telegram_update(quiet)["reason"], "duplicate_admin_control")
            self.assertEqual(server.handle_telegram_update(telegram_update("бот, ответь", "group", message_id=12))["reason"], "admin_silence")
            self.assertEqual(server.handle_telegram_update(telegram_update("/summary", "group", message_id=13))["reason"], "admin_silence")
            self.assertEqual(server.handle_telegram_update(telegram_update("бот, ответь", "private", message_id=14))["handled"], "message")
            other = telegram_update("бот, ответь", "group", message_id=15)
            other["message"]["chat"]["id"] = 556
            self.assertEqual(server.handle_telegram_update(other)["handled"], "message")
            self.assertEqual(server.handle_telegram_update(telegram_update("/speak", "group", message_id=16))["control"], "speak")
            self.assertFalse(server.storage.telegram_chat_control(555)["silent"])
            self.assertEqual(server.handle_telegram_update(telegram_update("бот, ответь", "group", message_id=17))["handled"], "message")
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(verify.call_count, 4)
            self.assertGreaterEqual(send.call_count, 4)
            self.assertGreaterEqual(complete.call_count, 3)

    def test_non_admin_cannot_change_silence(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "fetch_administrators", return_value=[]), patch.object(server, "is_current_administrator", return_value=False), patch.object(server, "try_send_telegram_message") as send:
            result = server.handle_telegram_update(telegram_update("/quiet", "group", message_id=21))
            self.assertEqual(result["reason"], "not_chat_admin")
            self.assertFalse(server.storage.telegram_chat_control(555)["silent"])
            send.assert_called_once()

    def test_explicit_mention_wakes_quiet_bot_without_resuming_group(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "complete", return_value=("Я тут.", "mock")) as complete, patch.object(server, "send_telegram_message") as send:
            server.storage.set_telegram_chat_silent(555, True, 1)
            self.assertEqual(server.handle_telegram_update(telegram_update("бот, ответь", "group", message_id=30))["reason"], "admin_silence")
            self.assertEqual(server.handle_telegram_update(telegram_update("/summary", "group", message_id=31))["reason"], "admin_silence")
            self.assertEqual(server.handle_telegram_update(telegram_update("@VBDsThirdSon_bot привет", "group", message_id=32))["handled"], "message")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])
            self.assertEqual(server.handle_telegram_update(telegram_update("обычное сообщение", "group", message_id=33))["reason"], "admin_silence")
            complete.assert_called_once()
            send.assert_called_once_with(555, "Я тут.")

    def test_explicit_mention_can_request_summary_while_quiet(self):
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "group_summary", return_value="Сводка за день.") as summary, patch.object(server, "send_telegram_message") as send:
            server.storage.set_telegram_chat_silent(555, True, 1)
            result = server.handle_telegram_update(telegram_update("@VBDsThirdSon_bot сделай сводку", "group", message_id=34))
            self.assertEqual(result["handled"], "daily_summary")
            summary.assert_called_once()
            send.assert_called_once_with(555, "Сводка за день.")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])

    def test_reply_to_bot_wakes_quiet_bot(self):
        update = telegram_update("привет", "group", message_id=36)
        update["message"]["reply_to_message"] = {"from": {"id": 123, "is_bot": True, "username": "VBDsThirdSon_bot"}}
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "complete", return_value=("Привет!", "mock")), patch.object(server, "send_telegram_message") as send:
            server.storage.set_telegram_chat_silent(555, True, 1)
            self.assertEqual(server.handle_telegram_update(update)["handled"], "message")
            send.assert_called_once_with(555, "Привет!")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])

    def test_explicit_mention_can_reach_moderation_while_quiet(self):
        directive = '{"telegram_action":{"mute":{"user_id":"888","minutes":1,"reason":"флуд"}}}'
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "complete", return_value=(directive, "mock")), patch("app.telegram_moderation.execute_mute", return_value={"success": True, "text": "Мут выдан.", "target_user_id": "888"}) as mute, patch.object(server, "send_telegram_message") as send:
            server.storage.set_telegram_chat_silent(555, True, 1)
            result = server.handle_telegram_update(telegram_update("@VBDsThirdSon_bot замути флудера", "group", message_id=35))
            self.assertEqual(result["handled"], "moderation")
            mute.assert_called_once()
            send.assert_called_once_with(555, "Мут выдан.")
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])

    def test_scheduled_summary_is_sent_during_silence(self):
        due = datetime.now(MOSCOW).replace(hour=23, minute=1)
        start = due.replace(hour=12, minute=0)
        sent_at = start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "try_send_telegram_message", return_value=True) as send, patch.object(server, "complete", return_value=("Весёлый день.", "mock")):
            server.storage.record_telegram_group_message(555, 1, 777, "telegram_user", "Telegram", "Новости", sent_at)
            server.storage.set_telegram_chat_silent(555, True, 2)
            self.assertEqual(server.process_due_daily_summaries_once(due), 1)
            self.assertEqual(server.process_due_daily_summaries_once(due), 0)
            send.assert_called_once()
            self.assertTrue(server.storage.telegram_chat_control(555)["silent"])


if __name__ == "__main__":
    unittest.main()
