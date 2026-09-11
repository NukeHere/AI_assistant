from __future__ import annotations

import json
import re
from dataclasses import dataclass
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


def extract_memory_directive(text: str) -> tuple[str, MemoryDirective]:
    remember: list[dict[str, Any]] = []
    recall: list[str] = []

    def consume(match: re.Match[str]) -> str:
        nonlocal remember, recall
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
        return ""

    cleaned = MEMORY_BLOCK_RE.sub(consume, text).strip()
    return cleaned, MemoryDirective(remember=remember, recall=recall)


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
