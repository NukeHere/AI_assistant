from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MEMORY_BLOCK_RE = re.compile(r"```assistant_memory\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]{3,}")

MEMORY_DIRECTIVE_PROMPT = """
Smart memory functions are available through a private control block.
Use them only when useful. The user should not see this block; the server will
consume it.

To save important or thematic information, append this exact fenced JSON block at
the very end of your answer:
```assistant_memory
{"remember":[{"kind":"important|thematic","summary":"very short memory cue","full":"complete extracted information","topics":["topic"],"importance":1-5}]}
```

Use kind="important" for durable facts that should almost always be remembered:
identity, stable preferences, long-term projects, recurring constraints,
important people, access rules, and major decisions. Use kind="thematic" for
facts useful only in a topic area.

The summary must be compact enough to include in future prompts. The full field
must contain the complete extracted information so it can be recalled later.
Never store passwords, API keys, private tokens, or raw secrets.

When short memory cues indicate that full memory is needed for the current
answer, request recall at the end instead of guessing:
```assistant_memory
{"recall":["topic or summary to search"]}
```

If you request recall, give a useful preliminary answer if possible. The server
may run a second pass with the recalled full memory.

Timed memory is available for future reminders. If the user asks to remember,
notify, wake up, or bring something back at a specific future date/time, or if
setting a reminder is clearly useful, append this private block:
```assistant_memory
{"timers":[{"summary":"very short reminder cue","full":"complete reminder content","due_at":"2030-04-17T12:34:56Z","timezone":"Europe/Moscow"}]}
```
Use absolute ISO-8601 due_at values. If the user gives relative time, calculate
it from the Current server time shown in the system prompt. Keep summaries very
short. Never use timers for secrets.

Persona switching is also available as a private function. If the user asks you
to change style/persona, or the conversation clearly calls for another active
mode, append one of these blocks at the very end of your answer:
```assistant_memory
{"persona":"ANA"}
```
or:
```assistant_memory
{"persona":"ALIEN"}
```
Only switch when it helps or when the user asks. Mention the switch briefly in
the visible answer; the server will persist it for the next turn.
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


def extract_memory_directive(text: str) -> tuple[str, MemoryDirective]:
    remember: list[dict[str, Any]] = []
    recall: list[str] = []
    timers: list[dict[str, Any]] = []
    persona: str | None = None

    def consume(match: re.Match[str]) -> str:
        nonlocal remember, recall, persona
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
        return ""

    cleaned = MEMORY_BLOCK_RE.sub(consume, text).strip()
    return cleaned, MemoryDirective(remember=remember, recall=recall, timers=timers, persona=persona)


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
        parts.append("Available memory summaries:")
        for row in summary_rows:
            label = "important" if int(row.get("importance", 1)) >= 4 or row.get("kind") == "important" else "thematic"
            parts.append(f"- [{label}] {row['summary']}")
    if full_rows:
        parts.append("Recalled full memory cells:")
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
    parts = [f"Current server time: {now_utc} UTC; Europe/Moscow: {now_local}."]
    parts.append("You may create timed memory reminders with private assistant_memory timers when useful.")
    if due_rows:
        parts.append("Due timed memories for this turn:")
        for row in due_rows[:8]:
            parts.append(f"- [{row.get('due_at')}] {row.get('full_content') or row.get('summary')}")
    return "\n".join(parts)


