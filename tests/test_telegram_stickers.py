import unittest
from unittest.mock import patch

import simple_server as server
from app import telegram_moderation
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramStickerTests(unittest.TestCase):
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

    def test_group_sticker_is_learned_without_forcing_reply(self):
        update = telegram_update("", "group")
        update["message"].pop("text")
        update["message"]["sticker"] = {"file_id": "first", "file_unique_id": "u1", "emoji": "😀"}
        with isolated_storage(), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "complete") as complete:
            result = server.handle_telegram_update(update)
            self.assertTrue(result["ignored"])
            self.assertEqual(server.storage.telegram_sticker_ids(555, "😀"), ["first"])
            complete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
