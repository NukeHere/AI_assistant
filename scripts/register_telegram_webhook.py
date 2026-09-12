from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


def load_local_env() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    load_local_env()
    token = os.getenv("TG_BOT_API_KEY", "").strip()
    if not token:
        raise SystemExit("TG_BOT_API_KEY is required in environment or local .env")
    webhook_url = os.getenv("TG_WEBHOOK_URL", "https://ai-assistant-4yn0.onrender.com/v1/telegram/webhook").strip()
    secret = os.getenv("TG_WEBHOOK_SECRET", "").strip()
    payload: dict[str, object] = {"url": webhook_url, "drop_pending_updates": False}
    if secret:
        payload["secret_token"] = secret
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/setWebhook",
        data=raw,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    safe_result = {key: data.get(key) for key in ["ok", "description", "error_code"] if key in data}
    print(json.dumps(safe_result, ensure_ascii=False, indent=2))
    if not data.get("ok"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()