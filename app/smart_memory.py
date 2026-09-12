from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MEMORY_BLOCK_RE = re.compile(r"```assistant_memory\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]{3,}")

MEMORY_DIRECTIVE_PROMPT = """
Функции умной памяти доступны через приватный управляющий блок.
Используй их только когда это полезно. Пользователь не должен видеть этот блок: сервер его вырежет и обработает.

Чтобы сохранить важную или тематическую информацию, добавь в самый конец ответа такой fenced JSON-блок:
```assistant_memory
{"remember":[{"kind":"important|thematic","summary":"очень короткая подсказка памяти","full":"полная извлечённая информация","topics":["тема"],"importance":1-5}]}
```

Используй kind="important" для устойчивых фактов, которые почти всегда стоит помнить: личность, стабильные предпочтения, долгосрочные проекты, повторяющиеся ограничения, важные люди, правила доступа и ключевые решения.
Используй kind="thematic" для фактов, полезных только внутри определённой темы.

Поле summary должно быть настолько коротким, чтобы его можно было дёшево добавлять в будущие промпты.
Поле full должно содержать полную извлечённую информацию, чтобы её можно было позже запросить целиком.
Никогда не сохраняй пароли, API-ключи, приватные токены или сырые секреты.

Если краткие подсказки памяти показывают, что для текущего ответа нужна полная память, запроси recall в конце ответа вместо угадывания:
```assistant_memory
{"recall":["тема или summary для поиска"]}
```

Если запрашиваешь recall, по возможности дай полезный предварительный ответ. Сервер может выполнить второй проход с полной найденной памятью.

Временная память доступна для будущих напоминаний. Если пользователь просит запомнить, уведомить, разбудить или вернуть информацию в конкретную будущую дату/время, либо если напоминание явно полезно, добавь приватный блок:
```assistant_memory
{"timers":[{"summary":"очень короткая подсказка напоминания","full":"полное содержимое напоминания","due_at":"2030-04-17T12:34:56Z","timezone":"Europe/Moscow"}]}
```
Используй абсолютные ISO-8601 значения due_at. Если пользователь дал относительное время, рассчитай его от текущего серверного времени, показанного в системном промпте. Summary держи очень коротким. Не используй timers для секретов.

Переключение личности тоже доступно как приватная функция. Если пользователь просит сменить стиль/личность или разговору явно нужен другой активный режим, добавь в самый конец ответа один из блоков:
```assistant_memory
{"persona":"ANA"}
```
или:
```assistant_memory
{"persona":"ALIEN"}
```
Переключайся только когда это помогает или когда пользователь попросил. В видимом ответе кратко упомяни смену; сервер сохранит её для следующего сообщения.

Telegram-маршрутизация доступна только в Telegram-каналах. Используй её, когда ответ в группе должен упомянуть отправителя, когда конфиденциальную информацию нужно перенести в личку, или когда нужен короткий ответ в группу плюс более полный ответ в личные сообщения:
```assistant_memory
{"telegram_action":{"reply_to":"group|private|both","mention_sender":true,"group_text":"необязательный короткий ответ в группу","private_text":"необязательный личный ответ","reaction_emoji":"👍"}}
```
Используй reply_to="private" для конфиденциальной или персональной информации. Используй mention_sender=true, когда групповой ответ должен явно обратиться к отправителю. Если вместо текста достаточно реакции на сообщение пользователя, добавь reaction_emoji. Если private_text или group_text не указаны, сервер использует видимый ответ. Никогда не помещай секреты в telegram_action.
""".strip()

SECRET_HINTS = (
    "api_key",
    "api key",
    "token",
    "токен",
    "password",
    "пароль",
    "secret",
    "ключ",
    "authorization",
    "bearer",
)


@dataclass(frozen=True)
class MemoryDirective:
    remember: list[dict[str, Any]]
    recall: list[str]
    timers: list[dict[str, Any]]
    persona: str | None
    telegram_action: dict[str, Any] | None


def extract_memory_directive(text: str) -> tuple[str, MemoryDirective]:
    remember: list[dict[str, Any]] = []
    recall: list[str] = []
    timers: list[dict[str, Any]] = []
    persona: str | None = None
    telegram_action: dict[str, Any] | None = None

    def consume(match: re.Match[str]) -> str:
        nonlocal remember, recall, persona, telegram_action
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return ""
        if isinstance(payload, dict):
            raw_remember = payload.get("remember", [])
            if isinstance(raw_remember, list):
                remember.extend(item for item in raw_remember if isinstance(item, dict))
            raw_recall = payload.get("recall", [])
            if isinstance(raw_recall, list):
                recall.extend(str(item).strip() for item in raw_recall if str(item).strip())
            elif isinstance(raw_recall, str) and raw_recall.strip():
                recall.append(raw_recall.strip())
            raw_timers = payload.get("timers", payload.get("schedule", []))
            if isinstance(raw_timers, list):
                timers.extend(item for item in raw_timers if isinstance(item, dict))
            raw_persona = str(payload.get("persona") or payload.get("mode") or "").strip().upper()
            if raw_persona in {"ANA", "ALIEN"}:
                persona = raw_persona
            raw_telegram = payload.get("telegram_action", payload.get("telegram"))
            if isinstance(raw_telegram, dict):
                telegram_action = raw_telegram
        return ""

    cleaned = MEMORY_BLOCK_RE.sub(consume, text).strip()
    return cleaned, MemoryDirective(remember=remember, recall=recall, timers=timers, persona=persona, telegram_action=telegram_action)


def sanitize_memory_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in items:
        kind = str(item.get("kind") or "thematic").lower()
        if kind not in {"important", "thematic", "manual"}:
            kind = "thematic"
        summary = normalize_space(str(item.get("summary") or ""))[:500]
        full = normalize_space(str(item.get("full") or item.get("content") or ""))[:5000]
        topics = item.get("topics") or []
        if isinstance(topics, str):
            topics = [topics]
        clean_topics = [normalize_space(str(topic)).lower()[:80] for topic in topics if normalize_space(str(topic))]
        try:
            importance = int(item.get("importance") or (5 if kind == "important" else 2))
        except (TypeError, ValueError):
            importance = 2
        importance = max(1, min(5, importance))

        if not summary and full:
            summary = full[:180]
        if not full and summary:
            full = summary
        if not summary or not full:
            continue
        if has_secret_hint(summary) or has_secret_hint(full):
            continue

        sanitized.append(
            {
                "kind": kind,
                "summary": summary,
                "full": full,
                "topics": clean_topics,
                "importance": importance,
            }
        )
    return sanitized


def build_memory_context(summary_rows: list[dict[str, Any]], full_rows: list[dict[str, Any]] | None = None) -> str:
    parts: list[str] = []
    if summary_rows:
        parts.append("Доступные краткие записи памяти:")
        for row in summary_rows:
            label = "важное" if int(row.get("importance", 1)) >= 4 or row.get("kind") == "important" else "тематическое"
            parts.append(f"- [{label}] {row['summary']}")
    if full_rows:
        parts.append("Найденные полные ячейки памяти:")
        for row in full_rows:
            parts.append(f"- {row['full_content']}")
    return "\n".join(parts)


def score_text(query: str, *values: str) -> int:
    query_tokens = set(tokens(query))
    if not query_tokens:
        return 0
    haystack_tokens: set[str] = set()
    for value in values:
        haystack_tokens.update(tokens(value))
    return len(query_tokens & haystack_tokens)


def tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_RE.finditer(text)]


def normalize_space(text: str) -> str:
    return " ".join(text.split())


def has_secret_hint(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in SECRET_HINTS)


def sanitize_timed_memory_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in items:
        summary = normalize_space(str(item.get("summary") or ""))[:300]
        full = normalize_space(str(item.get("full") or item.get("content") or ""))[:3000]
        due_at = normalize_due_at(str(item.get("due_at") or item.get("time") or item.get("datetime") or ""))
        timezone_name = normalize_space(str(item.get("timezone") or "Europe/Moscow"))[:80]
        if not summary and full:
            summary = full[:180]
        if not full and summary:
            full = summary
        if not summary or not full or not due_at:
            continue
        if has_secret_hint(summary) or has_secret_hint(full):
            continue
        sanitized.append({"summary": summary, "full": full, "due_at": due_at, "timezone": timezone_name})
    return sanitized


def normalize_due_at(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if cleaned.endswith("Z"):
        candidate = cleaned[:-1] + "+00:00"
    else:
        candidate = cleaned
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_timed_memory_context(due_rows: list[dict[str, Any]], now_utc: str, now_local: str) -> str:
    parts = [f"Текущее серверное время: {now_utc} UTC; Europe/Moscow: {now_local}."]
    parts.append("Когда полезно, можешь создавать временные напоминания через приватные assistant_memory timers.")
    if due_rows:
        parts.append("Наступившие временные напоминания для этого сообщения:")
        for row in due_rows[:8]:
            parts.append(f"- [{row.get('due_at')}] {row.get('full_content') or row.get('summary')}")
    return "\n".join(parts)






