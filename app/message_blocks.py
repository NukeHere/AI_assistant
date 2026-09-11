from __future__ import annotations

from app.text_render import strip_inline_markdown, structured_parts


def parse_render_blocks(author: str, text: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        cleaned = "\n".join(line for line in paragraph if line).strip()
        paragraph.clear()
        if cleaned:
            blocks.append({"type": "text", "label": "", "text": cleaned})

    for raw_line in text.splitlines():
        parts = structured_parts(author, raw_line)
        if parts:
            flush_paragraph()
            label, rest = parts
            blocks.append(
                {
                    "type": label.lower().replace(" ", "_"),
                    "label": label,
                    "text": strip_inline_markdown(rest),
                }
            )
            continue
        cleaned = strip_inline_markdown(raw_line)
        if cleaned:
            paragraph.append(cleaned)
        else:
            flush_paragraph()

    flush_paragraph()
    return blocks
