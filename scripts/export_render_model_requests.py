from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_RENDER_MESSAGE_URL = "https://ai-assistant-4yn0.onrender.com/v1/message"


def load_local_env(repo: Path) -> None:
    for env_path in [repo / ".env", repo / "desktop.env"]:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def endpoint_url(base_url: str, endpoint: str) -> str:
    base = base_url.strip() or DEFAULT_RENDER_MESSAGE_URL
    known = ["/v1/message", "/v1/chat/simple", "/v1/history", "/v1/snapshot", "/v1/sync", "/v1/logs"]
    for suffix in known:
        if base.endswith(suffix):
            return base[: -len(suffix)] + endpoint
    if base.endswith("/v1"):
        return base + endpoint.removeprefix("/v1")
    return base.rstrip("/") + endpoint


def redact(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\b\d{8,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TELEGRAM_BOT_TOKEN]", text)
    text = re.sub(r"\b[0-9a-f]{32}\.[A-Za-z0-9_-]{16,}\b", "[REDACTED_API_TOKEN]", text, flags=re.I)
    text = re.sub(r"(api[_-]?key|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", text, flags=re.I)
    return text


def fetch_logs(logs_url: str, token: str, limit: int) -> list[dict[str, Any]]:
    body = json.dumps({"limit": limit}).encode("utf-8")
    request = urllib.request.Request(
        logs_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + token},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return [item for item in payload.get("logs") or [] if isinstance(item, dict)]


def event_key(item: dict[str, Any]) -> tuple[object, object, object]:
    return (item.get("client_id"), item.get("conversation_id"), item.get("channel"))


def find_telegram_route(logs: list[dict[str, Any]], start_index: int, request_text: str) -> dict[str, Any] | None:
    # telegram_handled usually follows message_handled immediately after routing.
    for item in logs[start_index + 1 : min(len(logs), start_index + 6)]:
        if item.get("event") != "telegram_handled":
            continue
        if request_text and item.get("text_preview") and str(item.get("text_preview")) != request_text:
            # If model input had bot mention stripped, still accept close events in the small window.
            pass
        return item
    return None


def action_lines(response: dict[str, Any], route: dict[str, Any] | None) -> list[str]:
    details = response.get("telegram_action_details")
    if not isinstance(details, dict) and route:
        details = route.get("telegram_action_details")
    if not isinstance(details, dict):
        details = {}
    source = route or response
    lines: list[str] = []
    if response.get("telegram_action") or route:
        lines.append(f'- telegram_action: `{redact(response.get("telegram_action"))}`')
        if source.get("telegram_route") is not None:
            lines.append(f'- route: `{redact(source.get("telegram_route"))}`')
        for key in ["telegram_group_sent", "telegram_private_sent", "telegram_reaction_sent", "telegram_text_suppressed"]:
            if source.get(key) is not None:
                lines.append(f'- {key}: `{redact(source.get(key))}`')
        for key in [
            "reply_to",
            "mention_sender",
            "private_to_username",
            "private_to_telegram_user_id",
            "sent_private_to",
            "unknown_private_recipient",
            "reaction_emoji",
        ]:
            if details.get(key) not in (None, ""):
                lines.append(f'- {key}: `{redact(details.get(key))}`')
        preview_keys = [
            "group_text_preview",
            "private_text_preview",
            "sent_group_text_preview",
            "sent_private_text_preview",
            "fallback_group_text_preview",
        ]
        for key in preview_keys:
            if details.get(key):
                lines.append(f'- {key}: {redact(details.get(key))!r}')
    else:
        lines.append("- telegram_action: `False`")
    return lines


def build_report(logs: list[dict[str, Any]], logs_url: str, count: int) -> str:
    pending: dict[tuple[object, object, object], tuple[int, dict[str, Any]]] = {}
    pairs: list[tuple[tuple[int, dict[str, Any]] | None, tuple[int, dict[str, Any]], dict[str, Any] | None]] = []
    for index, item in enumerate(logs):
        event = item.get("event")
        key = event_key(item)
        if event == "message_received":
            pending[key] = (index, item)
        elif event == "message_handled":
            request_pair = pending.pop(key, None)
            request_text = str(request_pair[1].get("text_preview") or "") if request_pair else ""
            pairs.append((request_pair, (index, item), find_telegram_route(logs, index, request_text)))

    lines = [
        "# Последние реальные запросы/ответы с Render",
        "",
        f"Источник: `{logs_url}`",
        f"Сформировано: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Примечание: выгрузка построена по серверному audit-log Render. Старые записи могут не иметь полного `telegram_action_details`; новые записи после текущего деплоя будут писать детали действий напрямую.",
        "",
    ]
    last = pairs[-count:]
    if not last:
        lines.append("Пар `message_received` -> `message_handled` не найдено в логах Render.")
        return "\n".join(lines)

    for number, (request_pair, response_pair, route) in enumerate(last, 1):
        req_index, request = request_pair if request_pair else (None, {})
        res_index, response = response_pair
        lines += [
            f"## {number}. {redact(response.get('time_utc'))}",
            "",
        ]
        for key in ["client_id", "conversation_id", "channel", "persona", "model_mode"]:
            lines.append(f"- {key}: `{redact(response.get(key))}`")
        if response.get("command"):
            lines.append(f"- command: `{redact(response.get('command'))}`")
        lines.append(
            f"- memory_saved: `{redact(response.get('memory_saved'))}`; "
            f"memory_recalled: `{redact(response.get('memory_recalled'))}`; "
            f"timed_memory_saved: `{redact(response.get('timed_memory_saved'))}`"
        )
        lines.append(f"- server_log_index: request=`{req_index}` response=`{res_index}` route=`{logs.index(route) if route in logs else ''}`")
        lines += ["", "**Запрос:**", "", "```text", redact(request.get("text_preview")) if request else "[не найдено связанное message_received]", "```", ""]
        lines += ["**Ответ модели/обработчика:**", "", "```text", redact(response.get("response_preview")), "```", ""]
        lines += ["**Действия модели / Telegram:**", ""]
        lines += action_lines(response, route)
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    load_local_env(repo)
    base_url = os.getenv("ASSISTANT_API_URL") or os.getenv("API_URL") or DEFAULT_RENDER_MESSAGE_URL
    token = os.getenv("APP_API_TOKEN") or os.getenv("ASSISTANT_API_TOKEN") or ""
    if not token:
        print("APP_API_TOKEN not found in env/.env", file=sys.stderr)
        return 2
    logs_url = endpoint_url(base_url, "/v1/logs")
    logs = fetch_logs(logs_url, token, limit=500)
    out = repo / "data" / "last-20-render-model-requests.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(logs, logs_url, count=20), encoding="utf-8")
    print(out)
    print(f"logs={len(logs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
