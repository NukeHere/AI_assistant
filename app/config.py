from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    return int(value)


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "ai-assistant-core")
    app_env: str = os.getenv("APP_ENV", "local")
    api_token: str = os.getenv("APP_API_TOKEN", "change-me-client-token")
    database_path: Path = Path(os.getenv("DATABASE_PATH", "./data/assistant.sqlite3"))
    backup_dir: Path = Path(os.getenv("BACKUP_DIR", "./backups"))
    backup_interval_hours: int = _int_env("BACKUP_INTERVAL_HOURS", 12)
    backup_keep: int = _int_env("BACKUP_KEEP", 10)
    model_api_base_url: str = os.getenv("MODEL_API_BASE_URL", "https://api.openai.com/v1")
    model_api_key: str = os.getenv("MODEL_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "gpt-4o-mini")
    model_timeout_seconds: int = _int_env("MODEL_TIMEOUT_SECONDS", 60)


settings = Settings()
