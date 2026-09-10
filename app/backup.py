from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings


class BackupService:
    def __init__(self, database_path: Path, backup_dir: Path):
        self.database_path = database_path
        self.backup_dir = backup_dir

    def run_once(self) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.backup_dir / f"assistant-{stamp}.sqlite3"
        shutil.copy2(self.database_path, target)
        self._trim()
        return target

    def kept_count(self) -> int:
        return len(self._backup_files())

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(max(settings.backup_interval_hours, 1) * 60 * 60)
            if self.database_path.exists():
                self.run_once()

    def _trim(self) -> None:
        files = self._backup_files()
        for old_file in files[settings.backup_keep :]:
            old_file.unlink(missing_ok=True)

    def _backup_files(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        return sorted(
            self.backup_dir.glob("assistant-*.sqlite3"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
