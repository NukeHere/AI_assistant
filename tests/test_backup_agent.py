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


if __name__ == "__main__":
    unittest.main()
