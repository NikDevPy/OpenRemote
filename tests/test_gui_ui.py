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
