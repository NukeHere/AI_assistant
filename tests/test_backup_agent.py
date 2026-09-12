import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import backup_agent


class BackupAgentTests(unittest.TestCase):
    def test_snapshot_url_is_derived_from_api_endpoints(self) -> None:
        self.assertEqual(
            backup_agent.snapshot_url("https://example.com/v1/message"),
            "https://example.com/v1/snapshot",
        )
        self.assertEqual(
            backup_agent.snapshot_url("https://example.com/v1/chat/simple"),
            "https://example.com/v1/snapshot",
        )
        self.assertEqual(
            backup_agent.snapshot_url("https://example.com"),
            "https://example.com/v1/snapshot",
        )

    def test_backup_rotation_keeps_latest_files(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            backup_dir = Path(temp_dir)
            for index in range(3):
                path = backup_dir / backup_agent.backup_filename(
                    "primary-user",
                    datetime(2026, 1, 1, 12, index, tzinfo=timezone.utc),
                )
                path.write_text("{}", encoding="utf-8")
            removed = backup_agent.prune_backups(backup_dir, "primary-user", keep=2)
            remaining = backup_agent.list_backups(backup_dir, "primary-user")

        self.assertEqual(len(removed), 1)
        self.assertEqual(len(remaining), 2)
        self.assertIn("120200", remaining[0].name)


    def test_sync_backup_restores_when_remote_is_lighter_than_local(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_request(url: str, token: str, payload: dict[str, object], timeout: int = 60) -> dict[str, object]:
            calls.append(payload)
            if payload["action"] == "export":
                return {"snapshot": {"client_id": "primary-user", "conversations": [], "messages": [], "memories": [], "memory_cells": [], "timed_memories": []}}
            return {"ok": True, "imported": {"messages": 1}}

        old_request = backup_agent.request_snapshot
        backup_agent.request_snapshot = fake_request
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
                backup_dir = Path(temp_dir)
                latest = backup_dir / backup_agent.backup_filename(
                    "primary-user",
                    datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
                )
                latest.write_text(
                    '{"client_id":"primary-user","conversations":[{"id":"default"}],"messages":[{"content":"old"}],"memories":[],"memory_cells":[],"timed_memories":[]}',
                    encoding="utf-8",
                )
                action, path = backup_agent.sync_backup("https://example.com/v1/snapshot", "token", "primary-user", backup_dir, keep=10)
        finally:
            backup_agent.request_snapshot = old_request

        self.assertEqual(action, "restored")
        self.assertEqual(path, latest)
        self.assertEqual(calls[-1]["action"], "import")

    def test_snapshot_weight_includes_telegram_links(self) -> None:
        self.assertEqual(
            backup_agent.snapshot_weight({"telegram_users": [{"telegram_user_id": "777", "app_client_id": "primary-user"}]}),
            1,
        )
    def test_latest_backup_can_use_desktop_snapshot_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            root = Path(temp_dir)
            backup_dir = root / "backups"
            backup_dir.mkdir()
            desktop_snapshot = root / "snapshot-primary-user.json"
            desktop_snapshot.write_text('{"client_id":"primary-user","conversations":[],"messages":[{"content":"desktop"}]}', encoding="utf-8")

            latest = backup_agent.latest_backup(backup_dir, "primary-user")

        self.assertEqual(latest, desktop_snapshot)

if __name__ == "__main__":
    unittest.main()


