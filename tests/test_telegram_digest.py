import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import simple_server as server
from app.telegram_digest import MOSCOW, day_bounds, summarize
from tests.test_simple_server_stdlib import isolated_storage, telegram_update


class TelegramDigestTests(unittest.TestCase):
    def _message(self, text: str, user_id: int, message_id: int, when: datetime) -> dict[str, object]:
        update = telegram_update(text, "group", message_id=message_id)
        update["message"]["date"] = int(when.timestamp())
        update["message"]["from"] = {"id": user_id, "is_bot": False, "username": f"user{user_id}", "first_name": f"Участник {user_id}"}
        return update

    def test_records_each_human_once_and_summarizes_only_active_people(self):
        today = datetime.now(MOSCOW)
        prompts = []
        def complete(messages):
            prompts.append(messages)
            return "Участник 777 запустил обсуждение проекта, а Участник 888 принёс файл — у чата появился план.", "mock"
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "send_telegram_message") as send, patch.object(server, "complete", side_effect=complete):
            first = self._message("Обсудили проект", 777, 10, today)
            second = self._message("Послал файл", 888, 11, today)
            server.handle_telegram_update(first)
            server.handle_telegram_update(first)  # webhook retry
            server.handle_telegram_update(second)
            request = self._message("/summary", 777, 12, today)
            result = server.handle_telegram_update(request)
            duplicate = server.handle_telegram_update(request)
            start, end = day_bounds(today.date())
            rows = server.storage.telegram_group_messages_between(555, start, end)
        self.assertEqual(result["handled"], "daily_summary")
        self.assertEqual(duplicate["reason"], "duplicate_summary_request")
        self.assertEqual(len(rows), 2)
        self.assertEqual(send.call_count, 1)
        summary = send.call_args.args[1]
        self.assertIn("Участник 777", summary)
        self.assertIn("Участник 888", summary)
        self.assertNotIn("/summary", summary)
        self.assertNotIn("•", summary)
        self.assertEqual(len(prompts), 1)
        self.assertIn("Обсудили проект", prompts[0][1]["content"])
        self.assertIn("Послал файл", prompts[0][1]["content"])
        self.assertIn("сцены", prompts[0][0]["content"])

    def test_long_history_condenses_chronological_scenes_before_story(self):
        day = datetime.now(MOSCOW).date()
        rows = [
            {"user_id": str(777 + i % 2), "display_name": f"Участник {777 + i % 2}",
             "username": "", "sent_at": "2026-10-13 12:00:00", "content": "Обсуждение проекта " + "x" * 900}
            for i in range(20)
        ]
        prompts = []
        def generate(messages):
            prompts.append(messages)
            return "Сцена: оба участника обсуждали проект." if "часть хронологии" in messages[0]["content"] else "Оба участника закрутили историю проекта."
        result = summarize(rows, day, generate)
        self.assertGreater(len(prompts), 2)
        self.assertIn("Участник 777", prompts[-1][0]["content"])
        self.assertIn("Участник 778", prompts[-1][0]["content"])
        self.assertIn("Сцена", prompts[-1][1]["content"])
        self.assertIn("закрутили историю", result)

    def test_scheduled_summary_is_sent_once_after_23_moscow(self):
        day = datetime.now(MOSCOW).date()
        noon = datetime.combine(day, datetime.min.time(), MOSCOW).replace(hour=12)
        due = noon.replace(hour=23, minute=1)
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "TG_OPEN_ACCESS", True), patch.object(server, "send_telegram_message"), patch.object(server, "try_send_telegram_message", return_value=True) as send, patch.object(server, "complete", return_value=("делился новостями", "mock")):
            server.handle_telegram_update(self._message("Новости проекта", 777, 10, datetime.now(MOSCOW)))
            self.assertEqual(server.process_due_daily_summaries_once(noon), 0)
            self.assertEqual(server.process_due_daily_summaries_once(due), 1)
            self.assertEqual(server.process_due_daily_summaries_once(due + timedelta(minutes=1)), 0)
            self.assertEqual(send.call_count, 1)

    def test_no_messages_means_no_scheduled_summary(self):
        due = datetime.now(MOSCOW).replace(hour=23, minute=1)
        with isolated_storage(), patch.object(server, "TG_BOT_API_KEY", "test"), patch.object(server, "try_send_telegram_message") as send:
            self.assertEqual(server.process_due_daily_summaries_once(due), 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
