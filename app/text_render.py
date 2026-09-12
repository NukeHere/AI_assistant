from __future__ import annotations

import re

STRUCTURED_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(Observation|Diagnostic|Diagnosis|Recommended action|Command action|Action|Explanation|Conclusion|Наблюдение|Диагностика|Рекомендуемое действие|Командное действие|Действие|Объяснение|Итог|Вывод)"
    r"(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)

ANA_BLOCK_LABELS = {
    "observation": "НАБЛЮДЕНИЕ",
    "наблюдение": "НАБЛЮДЕНИЕ",
    "diagnostic": "ДИАГНОСТИКА",
    "diagnosis": "ДИАГНОСТИКА",
    "диагностика": "ДИАГНОСТИКА",
    "recommended action": "ДЕЙСТВИЕ",
    "рекомендуемое действие": "ДЕЙСТВИЕ",
    "command action": "КОМАНДА",
    "командное действие": "КОМАНДА",
    "action": "ДЕЙСТВИЕ",
    "действие": "ДЕЙСТВИЕ",
    "explanation": "ОБЪЯСНЕНИЕ",
    "объяснение": "ОБЪЯСНЕНИЕ",
    "conclusion": "ВЫВОД",
    "итог": "ВЫВОД",
    "вывод": "ВЫВОД",
}

ALIEN_BLOCK_LABELS = {
    "observation": "СИГНАЛ",
    "наблюдение": "СИГНАЛ",
    "diagnostic": "ДИССОНАНС",
    "diagnosis": "ДИССОНАНС",
    "диагностика": "ДИССОНАНС",
    "recommended action": "НОТА ДЕЙСТВИЯ",
    "рекомендуемое действие": "НОТА ДЕЙСТВИЯ",
    "command action": "КОМАНДНАЯ НОТА",
    "командное действие": "КОМАНДНАЯ НОТА",
    "action": "НОТА ДЕЙСТВИЯ",
    "действие": "НОТА ДЕЙСТВИЯ",
    "explanation": "ГЛУБИНА",
    "объяснение": "ГЛУБИНА",
    "conclusion": "РЕЗОНАНС",
    "итог": "РЕЗОНАНС",
    "вывод": "РЕЗОНАНС",
}

TELEGRAM_HANDLE_RE = re.compile(r"@(?:(?:[A-Za-z0-9_])|(?:\\_)){5,64}")

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
    protected_handles: dict[str, str] = {}

    def protect_handle(match: re.Match[str]) -> str:
        placeholder = f"§TGMENTION{len(protected_handles)}§"
        protected_handles[placeholder] = match.group(0).replace("\\_", "_")
        return placeholder

    cleaned = TELEGRAM_HANDLE_RE.sub(protect_handle, text)
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
    cleaned = re.sub(r"^>\s?", "", cleaned)
    cleaned = re.sub(r"^\s*[-*+]\s+", "• ", cleaned)
    cleaned = re.sub(r"^\s*\d+[.)]\s+", lambda match: match.group(0).strip() + " ", cleaned)
    for pattern in INLINE_MARKDOWN_PATTERNS:
        cleaned = pattern.sub(r"\1", cleaned)
    cleaned = cleaned.replace("```", "")
    cleaned = cleaned.replace("**", "").replace("__", "")
    cleaned = cleaned.strip(" *_")
    for placeholder, handle in protected_handles.items():
        cleaned = cleaned.replace(placeholder, handle)
    return cleaned


def structured_parts(author: str, line: str) -> tuple[str, str] | None:
    match = STRUCTURED_LINE_RE.match(line)
    if not match:
        return None
    label_key = match.group(1).lower()
    rest = strip_inline_markdown(match.group(2).strip())
    return persona_labels(author).get(label_key, label_key.upper()), rest

