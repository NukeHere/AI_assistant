import unittest
from unittest.mock import patch

import simple_server as server
from app import telegram_moderation
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramStickerTests(unittest.TestCase):
    def test_expanded_families_keep_the_same_intent(self):
        pairs = (("🥳", "🎉"), ("🤔", "🧐"), ("😢", "🫂"), ("👎", "❌"), ("😴", "💤"))
        for requested, available in pairs:
            with self.subTest(requested=requested), isolated_storage(), patch.object(server, "TG_STICKER_VARIATION_CHANCE", 1), patch.object(server.random, "random", return_value=0):
                server.storage.save_telegram_stickers(555, [{"file_id": available, "file_unique_id": available, "emoji": available}])
                self.assertEqual(server.choose_telegram_sticker_emoji(555, requested), available)

    def test_thumbs_up_can_use_similar_sticker_from_chat(self):
        with isolated_storage(), patch.object(server, "TG_STICKER_VARIATION_CHANCE", 1), patch.object(server.random, "random", return_value=0), patch.object(server.random, "choice", side_effect=["💪", "strong"]), patch.object(telegram_moderation, "api_call", return_value=True) as api:
            server.storage.save_telegram_stickers(555, [{"file_id": "strong", "file_unique_id": "strong", "emoji": "💪"}])
            sent, kind = server.send_telegram_expression(555, "👍", 10)
            self.assertTrue(sent)
            self.assertEqual(kind, "sticker")
            self.assertEqual(api.call_args.args[2]["sticker"], "strong")

    def test_variation_does_not_invent_sticker_or_change_explicit_emoji(self):
        with isolated_storage(), patch.object(server, "TG_STICKER_VARIATION_CHANCE", 1), patch.object(server.random, "random", return_value=0):
            self.assertEqual(server.choose_telegram_sticker_emoji(555, "👍"), "👍")
            self.assertEqual(server.choose_telegram_sticker_emoji(555, "❤️‍🔥"), "❤️‍🔥")

    def test_all_pack_emojis_are_eligible_including_combinations(self):
        stickers = [
            {"file_id": f"file-{i}", "file_unique_id": f"unique-{i}", "emoji": f"😀{i}"}
            for i in range(40)
        ]
        stickers.append({"file_id": "combo", "file_unique_id": "combo", "emoji": "❤️‍🔥"})
        with isolated_storage():
            server.storage.save_telegram_stickers(555, stickers)
            available = server.storage.telegram_sticker_emojis(555)
            self.assertEqual(len(available), 41)
            self.assertIn("❤️‍🔥", available)
            self.assertEqual(server.storage.telegram_sticker_ids(555, "❤️‍🔥"), ["combo"])

    def test_sticker_only_directive_never_silently_disappears(self):
        with isolated_storage(), patch.object(server.random, "random", return_value=0.99), patch.object(server, "TG_STICKER_REPLY_CHANCE", 0), patch.object(telegram_moderation, "api_call", return_value=True) as api:
            server.storage.save_telegram_stickers(555, [{"file_id": "file", "file_unique_id": "unique", "emoji": "👍"}])
            result = server.route_telegram_response(555, "777", {}, "supergroup", {"text": "", "telegram_action": {"sticker_emoji": "👍"}}, message_id=10)
            self.assertEqual(result["telegram_route"], "sticker")
            api.assert_called_once()

    def test_plural_colloquial_sticker_request_bypasses_model(self):
        with isolated_storage(), patch.object(telegram_moderation, "api_call", return_value=True), patch.object(server, "complete") as complete:
            server.storage.save_telegram_stickers(555, [{"file_id": "file", "file_unique_id": "unique", "emoji": "👍"}])
            result = server.handle_telegram_update(telegram_update("Кидай стикеры"))
            self.assertEqual(result["handled"], "sticker")
            complete.assert_not_called()

    def test_model_timeout_is_acknowledged_with_visible_private_fallback(self):
        with isolated_storage(), patch.object(server, "complete", side_effect=TimeoutError()), patch.object(server, "send_telegram_message") as send:
            result = server.handle_telegram_update(telegram_update("Привет"))
            self.assertEqual(result["handled"], "model_unavailable")
            send.assert_called_once()

    def setUp(self):
        key = patch.object(server, "TG_BOT_API_KEY", "test")
        key.start()
        self.addCleanup(key.stop)

    def test_received_sticker_imports_pack_and_preserves_emoji_variants(self):
        incoming = {"file_id": "first", "file_unique_id": "unique-first", "emoji": "😀", "set_name": "test_pack"}
        pack = {"stickers": [
            incoming,
            {"file_id": "second", "file_unique_id": "unique-second", "emoji": "😀"},
            {"file_id": "third", "file_unique_id": "unique-third", "emoji": "👍"},
        ]}
        update = telegram_update("")
        update["message"].pop("text")
        update["message"]["sticker"] = incoming
        with isolated_storage(), patch.object(telegram_moderation, "api_call", return_value=pack) as api, patch.object(server, "complete", return_value=("Привет!", "mock")), patch.object(server, "send_telegram_message"):
            result = server.handle_telegram_update(update)
            self.assertEqual(result["handled"], "message")
            self.assertEqual(server.storage.telegram_sticker_ids(555, "😀"), ["first", "second"])
            self.assertEqual(server.storage.telegram_sticker_ids(555, "👍"), ["third"])
            server.handle_telegram_update(update)
            self.assertEqual(api.call_count, 1)

    def test_expression_uses_random_sticker_with_matching_emoji(self):
        with isolated_storage(), patch.object(server, "TG_STICKER_REPLY_CHANCE", 1.0), patch.object(server.random, "random", return_value=0.0), patch.object(server.random, "choice", return_value="second"), patch.object(telegram_moderation, "api_call", return_value=True) as api, patch.object(server, "send_telegram_message") as send:
            server.storage.save_telegram_stickers(555, [
                {"file_id": "first", "file_unique_id": "u1", "emoji": "😀"},
                {"file_id": "second", "file_unique_id": "u2", "emoji": "😀"},
            ])
            result = server.route_telegram_response(555, "777", {}, "private", {"text": "Хорошо", "telegram_action": {"sticker_emoji": "😀"}}, message_id=10)
            self.assertEqual(result["telegram_route"], "sticker")
            self.assertEqual(api.call_args.args[1], "sendSticker")
            self.assertEqual(api.call_args.args[2]["sticker"], "second")
            send.assert_not_called()

    def test_expression_can_send_emoji_without_known_sticker(self):
        with isolated_storage(), patch.object(server, "TG_STICKER_REPLY_CHANCE", 1.0), patch.object(server.random, "random", return_value=0.0), patch.object(server, "send_telegram_message") as send:
            result = server.route_telegram_response(555, "777", {}, "private", {"text": "Хорошо", "telegram_action": {"sticker_emoji": "👍"}}, message_id=10)
            self.assertEqual(result["telegram_route"], "emoji")
            send.assert_called_once_with(555, "👍")

    def test_group_text_then_sticker_are_two_messages(self):
        with isolated_storage(), patch.object(telegram_moderation, "api_call", return_value=True) as api, patch.object(server, "send_telegram_message") as send:
            server.storage.save_telegram_stickers(555, [{"file_id": "heart-file", "file_unique_id": "heart", "emoji": "🫶"}])
            result = server.route_telegram_response(555, "777", {}, "supergroup", {
                "text": "Ок.",
                "telegram_action": {"reply_to": "group", "group_text": "Ок.", "sticker_emoji": "🫶"},
            }, message_id=10)
            send.assert_called_once_with(555, "Ок.")
            self.assertEqual(api.call_args.args[1], "sendSticker")
            self.assertEqual(api.call_args.args[2]["sticker"], "heart-file")
            self.assertTrue(result["telegram_group_sent"])
            self.assertTrue(result["telegram_expression_sent"])

    def test_private_text_then_emoji_fallback_are_two_messages(self):
        with isolated_storage(), patch.object(server, "send_telegram_message") as send:
            result = server.route_telegram_response(555, "777", {}, "private", {
                "text": "Ок.",
                "telegram_action": {"reply_to": "private", "private_text": "Ок.", "sticker_emoji": "🫶"},
            }, message_id=10)
            self.assertEqual([call.args for call in send.call_args_list], [(555, "Ок."), (555, "🫶")])
            self.assertTrue(result["telegram_private_sent"])
            self.assertEqual(result["telegram_expression_kind"], "emoji")

    def test_explicit_request_sends_sticker_without_probability_gate(self):
        with isolated_storage(), patch.object(server, "TG_STICKER_REPLY_CHANCE", 0.0), patch.object(server.random, "choice", side_effect=["😀", "sticker-file"]), patch.object(telegram_moderation, "api_call", return_value=True) as api, patch.object(server, "send_telegram_message") as send, patch.object(server, "complete") as complete:
            server.storage.save_telegram_stickers(555, [{"file_id": "sticker-file", "file_unique_id": "u1", "emoji": "😀"}])
            result = server.handle_telegram_update(telegram_update("отправь стикер"))
            self.assertEqual(result["handled"], "sticker")
            self.assertEqual(api.call_args.args[1], "sendSticker")
            self.assertEqual(api.call_args.args[2]["sticker"], "sticker-file")
            send.assert_not_called()
            complete.assert_not_called()

    def test_explicit_request_explains_when_chat_has_no_stickers(self):
        with isolated_storage(), patch.object(server, "send_telegram_message") as send, patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(telegram_update("отправь стикер"))
            self.assertEqual(result["handled"], "sticker_unavailable")
            self.assertIn("Пришли стикер", send.call_args.args[1])
            complete.assert_not_called()

    def test_group_sticker_reaches_model_without_forcing_reply(self):
        update = telegram_update("", "group")
        update["message"].pop("text")
        update["message"]["sticker"] = {"file_id": "first", "file_unique_id": "u1", "emoji": "😀"}
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete", return_value=('{"telegram_action":{"reply_to":"ignore"}}', "mock")) as complete:
            result = server.handle_telegram_update(update)
            self.assertEqual(result["telegram_route"], "ignore")
            self.assertEqual(server.storage.telegram_sticker_ids(555, "😀"), ["first"])
            complete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
