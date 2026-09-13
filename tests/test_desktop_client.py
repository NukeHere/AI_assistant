import unittest
from types import SimpleNamespace

import desktop_client


class DesktopClipboardShortcutTests(unittest.TestCase):
    def test_clipboard_shortcuts_support_russian_keyboard_layout(self) -> None:
        app = object.__new__(desktop_client.ChatApp)
        cases = [
            (67, "с", "copy"),
            (86, "м", "paste"),
            (88, "ч", "cut"),
            (65, "ф", "select_all"),
        ]
        for keycode, keysym, expected in cases:
            with self.subTest(keysym=keysym):
                event = SimpleNamespace(keycode=keycode, keysym=keysym, char=keysym, state=0x0004)
                self.assertEqual(app._clipboard_action_from_event(event), expected)
                self.assertTrue(app._event_has_control(event))

    def test_clipboard_shortcuts_ignore_non_control_keys(self) -> None:
        app = object.__new__(desktop_client.ChatApp)
        event = SimpleNamespace(keycode=86, keysym="м", char="м", state=0)
        self.assertFalse(app._event_has_control(event))


if __name__ == "__main__":
    unittest.main()
