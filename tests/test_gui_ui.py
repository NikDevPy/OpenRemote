"""Optional Qt offscreen smoke; standard Linux agent tests do not need PySide6."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

GUI_AVAILABLE = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(GUI_AVAILABLE, "Install the optional gui extra to test Qt")
class GuiOffscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        sandbox = base / "sandbox"
        state = base / "state"
        sandbox.mkdir(mode=0o700)
        state.mkdir(mode=0o700)
        self.config = base / "agent.json"
        self.config.write_text(json.dumps({
            "device_id": "demo-gateway", "root": str(sandbox),
            "state": str(state), "role": "viewer", "commands": {},
            "workspaces": {}, "peers": {}, "ad_hoc_enabled": False,
        }))
        self.config.chmod(0o600)

    def tearDown(self):
        self.tmp.cleanup()

    def test_existing_hostname_config_detected(self):
        from openremote_gui.app import discover_config
        base = Path(self.tmp.name)
        config_dir = base / ".config" / "openremote-agent"
        config_dir.mkdir(parents=True, exist_ok=True)
        older = config_dir / "terra-v0.5-test.json"
        older.write_text("{}")
        actual = config_dir / "terra.json"
        actual.write_text("{}")
        self.assertEqual(discover_config(home=base, host="terra"), actual)
        # Caller-specified config always wins.
        self.assertEqual(discover_config(explicit=older, home=base, host="terra"), older)

    def test_diagram_text_is_inside_all_cards_at_supported_sizes(self):
        from openremote_gui.wizard import Diagram, COPY
        from PySide6.QtGui import QFontMetrics
        from PySide6.QtGui import QPixmap, QPainter
        from PySide6.QtCore import Qt
        for language in ("ru", "en"):
            for width in (690, 740, 890, 1060):
                view = Diagram()
                view.language = language
                view.resize(width, 200)
                boxes = view.geometry_for(width)
                self.assertEqual(len(boxes), 3)
                self.assertLess(boxes[0].right() + 8, boxes[1].left())
                self.assertLess(boxes[1].right() + 8, boxes[2].left())
                surface = QPixmap(width, 200)
                painter = QPainter(surface)
                try:
                    for idx, key in enumerate(("diagram_device", "diagram_cloud", "diagram_chat")):
                        caption = COPY[language][key]
                        font = view.font_for_label(caption, boxes[idx].width() - 24, painter)
                        fm = QFontMetrics(font)
                        self.assertLessEqual(
                            max(map(fm.horizontalAdvance, caption.splitlines())),
                            boxes[idx].width() - 24,
                            f"{language}: {key} @ width {width}")
                finally:
                    painter.end()

    def test_remote_online_indicator_uses_green_badge(self):
        from openremote_gui.app import MainWindow
        from openremote_gui.backend import Backend
        from PySide6.QtWidgets import QLabel
        window = MainWindow(Backend(self.config))
        try:
            sample = window.backend.snapshot()
            sample["peers"] = ["rock"]
            window._snapshot = sample
            window._peer_ready({"device_id": "rock", "online": True})
            self.app.processEvents()
            badge = window.findChild(QLabel, "deviceStatus_rock")
            self.assertIsNotNone(badge)
            self.assertTrue(any(label in badge.text() for label in ("Online", "В сети")))
            self.assertIn("#18A77A", badge.styleSheet())
        finally:
            window._closing = True
            window.pool.waitForDone(5000)
            window.close()
            window.deleteLater()

    def test_pages_theme_and_no_tray_fallback(self):
        from openremote_gui.app import MainWindow
        from openremote_gui.backend import Backend
        window = MainWindow(Backend(self.config))
        try:
            snap = window.backend.snapshot()
            window._snapshot = snap
            window.render_pages()
            self.assertEqual(window.pages.count(), 4)
            self.assertTrue(window.nav_buttons[0].isChecked())
            window.show_page(1)
            self.assertTrue(window.nav_buttons[1].isChecked())
            window.dark = True
            window.set_theme()
            self.assertTrue(window.dark)
            self.assertEqual(window.pages.currentIndex(), 1)
            window.show()
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            self.assertFalse(window.tray)
        finally:
            window._closing = True
            window.pool.waitForDone(5000)
            window.close()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
