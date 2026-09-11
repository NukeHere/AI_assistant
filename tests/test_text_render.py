import unittest

from app.text_render import strip_inline_markdown, structured_parts


class TextRenderTests(unittest.TestCase):
    def test_strip_inline_markdown_removes_common_markup(self) -> None:
        self.assertEqual(strip_inline_markdown("**Важно** и `код`"), "Важно и код")
        self.assertEqual(strip_inline_markdown("- **пункт**"), "• пункт")
        self.assertEqual(strip_inline_markdown("### Заголовок"), "Заголовок")

    def test_structured_parts_maps_persona_labels_and_cleans_content(self) -> None:
        label, rest = structured_parts("ALIEN", "**Observation:** **Окно** открыто")
        self.assertEqual(label, "СИГНАЛ")
        self.assertEqual(rest, "Окно открыто")


if __name__ == "__main__":
    unittest.main()
