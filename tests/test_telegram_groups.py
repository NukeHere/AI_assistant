import unittest
from unittest.mock import patch

import simple_server as server
from app import telegram_moderation as moderation
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class GroupTests(unittest.TestCase):
    def setUp(self):
        key = patch.object(server, "TG_BOT_API_KEY", "test")
        key.start()
        self.addCleanup(key.stop)

    def test_shared_history_isolated_from_private_memory(self):
        prompts = []
        def complete(messages):
            prompts.append(messages)
            return "Ответ группе", "mock"
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "send_telegram_message"), patch.object(server, "complete", complete):
            first = telegram_update("бот, мой проект Альфа", "supergroup")
            server.handle_telegram_update(first)
            second = telegram_update("бот, о чём говорили?", "supergroup")
            second["message"]["from"]["id"] = 888
            second["message"]["from"]["username"] = "second"
            server.handle_telegram_update(second)
            self.assertTrue(any("Альфа" in m["content"] for m in prompts[-1]))
            self.assertTrue(any("id=777" in m["content"] for m in prompts[-1]))
            third = telegram_update("бот, другая группа", "supergroup")
            third["message"]["chat"]["id"] = 556
            server.handle_telegram_update(third)
            self.assertFalse(any("Альфа" in m["content"] for m in prompts[-1]))

    def test_open_access_is_temporary_and_keeps_profiles_separate(self):
        with isolated_storage(), patch.object(server, "send_telegram_message"), patch.object(server, "TG_OPEN_ACCESS", True):
            result = server.handle_telegram_update(telegram_update("/id"))
            self.assertEqual(result["handled"], "whoami")
            self.assertFalse(server.storage.telegram_client_context("777")[1])
            a = server.storage.telegram_client_context("777")[0]
            b = server.storage.telegram_client_context("888")[0]
            self.assertNotEqual(a, b)
            with patch.object(server, "TG_OPEN_ACCESS", False), patch.object(server, "TG_GUEST_CHAT_MODE", False):
                result = server.handle_telegram_update(telegram_update("привет"))
                self.assertEqual(result["handled"], "guest_disabled")


class ModerationTests(unittest.TestCase):
    def test_mute_resolves_username_and_checks_current_owner(self):
        replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "member", "user": {"id": 777, "username": "User_Name"}}, True]
        lookup = lambda name: {"telegram_user_id": "777"} if name == "user_name" else None
        with patch.object(moderation, "api_call", side_effect=replies) as api:
            result = moderation.execute_mute(-123, "supergroup", {"username": "@USER_NAME", "minutes": 2, "reason": "флуд"}, "test", lookup)
            self.assertTrue(result["success"])
            self.assertEqual(api.call_args.args[2]["user_id"], 777)

    def test_unknown_or_conflicting_username_never_mutes(self):
        with patch.object(moderation, "api_call") as api:
            for known, target in ((None, ""), ({"telegram_user_id": "777"}, "888")):
                result = moderation.execute_mute(-123, "supergroup", {"username": "person", "user_id": target, "minutes": 1, "reason": "флуд"}, "test", lambda _: known)
                self.assertFalse(result["success"])
            api.assert_not_called()

    def test_stale_username_never_mutes_old_owner(self):
        replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "member", "user": {"id": 777, "username": "new_name"}}]
        with patch.object(moderation, "api_call", side_effect=replies) as api:
            result = moderation.execute_mute(-123, "supergroup", {"username": "old_name", "minutes": 1, "reason": "флуд"}, "test", lambda _: {"telegram_user_id": "777"})
            self.assertFalse(result["success"])
            self.assertFalse(any(c.args[1] == "restrictChatMember" for c in api.call_args_list))

    def setUp(self):
        moderation._rights_cache.clear()
        key = patch.object(server, "TG_BOT_API_KEY", "test")
        key.start()
        self.addCleanup(key.stop)

    def test_valid_mute_and_server_enforced_duration(self):
        for minutes in (1, 10):
            moderation._rights_cache.clear()
            replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "member", "user": {"username": "user_name"}}, True]
            with patch.object(moderation, "api_call", side_effect=replies) as api, patch.object(moderation.time, "time", return_value=1000):
                result = moderation.execute_mute(-123, "supergroup", {"user_id": "777", "minutes": minutes, "reason": "флуд"}, "test")
                self.assertTrue(result["success"])
                self.assertIn("@user_name", result["text"])
                self.assertEqual(api.call_args.args[2]["until_date"], 1000 + minutes * 60)

    def test_invalid_durations_never_call_telegram(self):
        with patch.object(moderation, "api_call") as api:
            for minutes in (0, -1, 11, True, 1.5, "10", None):
                self.assertFalse(moderation.execute_mute(-123, "supergroup", {"user_id": "777", "minutes": minutes, "reason": "флуд"}, "test")["success"])
            api.assert_not_called()

    def test_no_rights_admin_target_and_api_failure(self):
        for replies in (
            [{"id": 99}, {"status": "member"}],
            [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "administrator"}],
            [RuntimeError("request failed")],
        ):
            moderation._rights_cache.clear()
            with patch.object(moderation, "api_call", side_effect=replies) as api:
                result = moderation.execute_mute(-123, "supergroup", {"user_id": "777", "minutes": 1, "reason": "флуд"}, "test")
                self.assertFalse(result["success"])
                self.assertFalse(any(c.args[1] == "restrictChatMember" for c in api.call_args_list))

    def test_handler_announces_only_real_moderation_result(self):
        raw = '{"telegram_action":{"mute":{"user_id":"777","minutes":2,"reason":"флуд"}}}'
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=(raw, "mock")), patch.object(server, "send_telegram_message") as send, patch.object(moderation, "execute_mute", return_value={"success": False, "text": "Мут не выдан: нет прав."}):
            result = server.handle_telegram_update(telegram_update("бот, проверь флуд", "supergroup"))
            self.assertFalse(result["success"])
            send.assert_called_once_with(555, "Мут не выдан: нет прав.")
