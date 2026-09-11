from __future__ import annotations

import re

STRUCTURED_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(Observation|Diagnostic|Diagnosis|Recommended action|Command action|Action|Explanation|Conclusion)"
    r"(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)

ANA_BLOCK_LABELS = {
    "observation": "НАБЛЮДЕНИЕ",
    "diagnostic": "ДИАГНОСТИКА",
    "diagnosis": "ДИАГНОСТИКА",
    "recommended action": "ДЕЙСТВИЕ",
    "command action": "КОМАНДА",
    "action": "ДЕЙСТВИЕ",
    "explanation": "ОБЪЯСНЕНИЕ",
    "conclusion": "ВЫВОД",
}

ALIEN_BLOCK_LABELS = {
    "observation": "СИГНАЛ",
    "diagnostic": "ДИССОНАНС",
    "diagnosis": "ДИССОНАНС",
    "recommended action": "НОТА ДЕЙСТВИЯ",
    "command action": "КОМАНДНАЯ НОТА",
    "action": "НОТА ДЕЙСТВИЯ",
    "explanation": "ГЛУБИНА",
    "conclusion": "РЕЗОНАНС",
}

INLINE_MARKDOWN_PATTERNS = [
    re.compile(r"\*\*([^*]+)\*\*"),
    re.compile(r"__([^_]+)__"),
    re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"),
    re.compile(r"(?<!_)_([^_\n]+)_(?!_)"),
    re.compile(r"`([^`]+)`"),
]


def persona_labels(author: str) -> dict[str, str]:
    return ALIEN_BLOCK_LABELS if author == "ALIEN" else ANA_BLOCK_LABELS


def strip_inline_markdown(text: str) -> str:
    cleaned = text
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"^>\s?", "", cleaned)
    cleaned = re.sub(r"^\s*[-*+]\s+", "• ", cleaned)
    cleaned = re.sub(r"^\s*\d+[.)]\s+", lambda match: match.group(0).strip() + " ", cleaned)
    for pattern in INLINE_MARKDOWN_PATTERNS:
        cleaned = pattern.sub(r"\1", cleaned)
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = cleaned.strip(" *_")
    return cleaned


def structured_parts(author: str, line: str) -> tuple[str, str] | None:
    match = STRUCTURED_LINE_RE.match(line)
    if not match:
        return None
    label_key = match.group(1).lower()
    rest = strip_inline_markdown(match.group(2).strip())
    return persona_labels(author).get(label_key, label_key.upper()), rest
