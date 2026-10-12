import unittest
from unittest.mock import patch

import simple_server as server
from app import telegram_moderation as moderation
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class GroupTests(unittest.TestCase):
    def test_bare_mention_keeps_nonempty_input_and_direct_address_context(self):
        prompts = []
        def complete(messages):
            prompts.append(messages)
            return "Я здесь.", "mock"
        with isolated_storage(), patch.object(server, "TG_BOT_USERNAME", "VBDsThirdSon_bot"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", complete), patch.object(server, "send_telegram_message") as send:
            result = server.handle_telegram_update(telegram_update("@VBDsThirdSon_bot", "supergroup"))
            self.assertTrue(result["telegram_group_sent"])
            self.assertIn("адресовано тебе напрямую", "\n".join(m["content"] for m in prompts[0]))
            send.assert_called_once()

    def test_group_note_is_available_in_private_context_only_for_sender(self):
        prompts = []
        def complete(messages):
            prompts.append(messages)
            return '{"telegram_action":{"reply_to":"ignore","group_note":"В группе договорились обсудить проект Альфа завтра."}}', "mock"
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "send_telegram_message"), patch.object(server, "complete", complete):
            server.handle_telegram_update(telegram_update("бот, обсудим Альфу завтра", "supergroup"))
            server.handle_telegram_update(telegram_update("О чём договорились в группе?"))
            context = "\n".join(m["content"] for m in prompts[-1])
            self.assertIn("не личная переписка", context)
            self.assertIn("chat_id=555", context)
            self.assertIn("Альфа", context)
            other = telegram_update("Что было?")
            other["message"]["from"]["id"] = 888
            server.handle_telegram_update(other)
            self.assertFalse(any("Альфа" in m["content"] for m in prompts[-1]))

    def test_subscription_preserves_existing_types_and_pending_updates(self):
        info = {"url": "https://ai-assistant-4yn0.onrender.com/v1/telegram/webhook", "allowed_updates": ["message", "callback_query"], "max_connections": 10}
        with patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_WEBHOOK_SECRET", "test-secret"), patch.dict("os.environ", {"TG_WEBHOOK_URL": info["url"]}), patch.object(moderation, "api_call", side_effect=[info, True]) as api:
            server.ensure_telegram_reaction_subscription()
            payload = api.call_args.args[2]
            self.assertEqual(payload["allowed_updates"], ["message", "callback_query", "message_reaction"])
            self.assertFalse(payload["drop_pending_updates"])
            self.assertEqual(payload["max_connections"], 10)

    def test_ignore_sends_nothing_even_with_text_and_reaction(self):
        with patch.object(server, "send_telegram_message") as send, patch.object(server, "try_set_telegram_reaction") as react:
            result = server.route_telegram_response(-1, "777", {}, "supergroup", {"text": "Не нужно отправлять", "telegram_action": {"reply_to": "ignore", "reaction_emoji": "👍"}}, message_id=4)
            self.assertEqual(result["telegram_route"], "ignore")
            send.assert_not_called()
            react.assert_not_called()

    def test_ignore_after_explanation_is_not_sent_to_group(self):
        raw = 'We should ignore as no direct question or request.{\n"telegram_action": {\n"reply_to": "ignore"\n}\n}'
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=(raw, "mock")), patch.object(server, "send_telegram_message") as send, patch.object(server, "try_set_telegram_reaction") as react:
            result = server.handle_telegram_update(telegram_update("бот, просто проверка", "supergroup"))
            self.assertEqual(result["telegram_route"], "ignore")
            send.assert_not_called()
            react.assert_not_called()

    def test_removed_reaction_is_visible_in_group_history(self):
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True):
            server.handle_telegram_reaction_update({"chat": {"id": -123, "type": "supergroup"}, "message_id": 7, "user": {"id": 777, "username": "user_name"}, "old_reaction": [{"type": "emoji", "emoji": "👍"}], "new_reaction": []})
            history = server.storage.recent_messages("tg-group--123", 5)
            self.assertIn("👍 → нет", history[-1]["content"])
            self.assertIn("id=777", history[-1]["content"])

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
    def test_unmute_releases_restricted_member(self):
        replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "restricted", "is_member": True, "user": {"id": 777, "username": "user_name"}}, True]
        with patch.object(moderation, "api_call", side_effect=replies) as api:
            result = moderation.execute_unmute(-123, "supergroup", {"user_id": "777", "reason": "ошибка модерации"}, "test")
            self.assertTrue(result["success"])
            self.assertIn("снят мут", result["text"])
            self.assertTrue(all(api.call_args.args[2]["permissions"].values()))
            self.assertEqual(api.call_args.args[2]["until_date"], 0)

    def test_unmute_does_not_claim_success_when_telegram_fails(self):
        replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "restricted", "is_member": True}, RuntimeError("denied")]
        with patch.object(moderation, "api_call", side_effect=replies):
            result = moderation.execute_unmute(-123, "supergroup", {"user_id": "777", "reason": "ошибка"}, "test")
            self.assertFalse(result["success"])
            self.assertIn("не снят", result["text"])

    def test_mute_resolves_username_and_checks_current_owner(self):
        replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "member", "user": {"id": 777, "username": "User_Name"}}, True]
        lookup = lambda name: {"telegram_user_id": "777"} if name == "user_name" else None
        with patch.object(moderation, "api_call", side_effect=replies) as api:
            result = moderation.execute_mute(-123, "supergroup", {"username": "@USER_NAME", "minutes": 1, "reason": "флуд"}, "test", lookup)
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
        for minutes in (1,):
            moderation._rights_cache.clear()
            replies = [{"id": 99}, {"status": "administrator", "can_restrict_members": True}, {"status": "member", "user": {"username": "user_name"}}, True]
            with patch.object(moderation, "api_call", side_effect=replies) as api, patch.object(moderation.time, "time", return_value=1000):
                result = moderation.execute_mute(-123, "supergroup", {"user_id": "777", "minutes": minutes, "reason": "флуд"}, "test")
                self.assertTrue(result["success"])
                self.assertIn("@user_name", result["text"])
                self.assertEqual(api.call_args.args[2]["until_date"], 1000 + minutes * 60)
                self.assertTrue(api.call_args.args[2]["permissions"]["can_react_to_messages"])

    def test_invalid_durations_never_call_telegram(self):
        with patch.object(moderation, "api_call") as api:
            for minutes in (0, -1, 2, 10, 11, True, 1.5, "10", None):
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
        raw = '{"telegram_action":{"reply_to":"ignore","mute":{"user_id":"777","minutes":2,"reason":"флуд"}}}'
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=(raw, "mock")), patch.object(server, "send_telegram_message") as send, patch.object(moderation, "execute_mute", return_value={"success": False, "text": "Мут не выдан: нет прав."}):
            result = server.handle_telegram_update(telegram_update("бот, проверь флуд", "supergroup"))
            self.assertFalse(result["success"])
            send.assert_called_once_with(555, "Мут не выдан: нет прав.")
