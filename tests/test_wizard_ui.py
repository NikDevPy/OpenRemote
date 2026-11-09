"""Qt offscreen tests for the consumer first-run wizard."""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

QT = importlib.util.find_spec("PySide6") is not None
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@unittest.skipUnless(QT, "Optional PySide6 not installed")
class WizardUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        from openremote_gui.onboarding import FirstRun, SetupPaths
        from openremote_gui.wizard import SetupWizard
        self.temp = tempfile.TemporaryDirectory(prefix="openremote-wizard-ui-")
        self.manager = FirstRun(SetupPaths(Path(self.temp.name)))
        self.wizard = SetupWizard(self.manager, language="ru")
        self.wizard.show()
        self.app.processEvents()

    def tearDown(self):
        self.wizard.close()
        self.wizard.pool.waitForDone(5000)
        self.wizard.deleteLater()
        self.temp.cleanup()

    def test_steps_and_language_have_six_pages(self):
        self.assertEqual(self.wizard.stack.count(), 6)
        self.assertIn("Ваш компьютер", self.wizard.title.text())
        for i in range(3):
            self.wizard.advance()
            self.assertEqual(self.wizard.index, i + 1)
        self.assertTrue(self.wizard.tunnel.isVisible())
        self.assertEqual(self.wizard.api_key.echoMode().name, "Password")
        self.wizard.show_secret.setChecked(True)
        self.assertEqual(self.wizard.api_key.echoMode().name, "Normal")
        self.wizard.show_secret.setChecked(False)
        self.assertEqual(self.wizard.api_key.echoMode().name, "Password")

    def test_gates_prevent_skipping_local_configuration(self):
        self.wizard.index = 4
        self.wizard._show_step()
        self.assertFalse(self.wizard.next.isEnabled())
        self.wizard.configured = True
        self.wizard._show_step()
        self.assertTrue(self.wizard.next.isEnabled())
        self.wizard.advance()
        self.assertEqual(self.wizard.index, 5)

    def test_english_copy(self):
        from openremote_gui.wizard import SetupWizard
        alt = SetupWizard(self.manager, language="en")
        try:
            self.assertEqual(alt.title.text(), "Your computer. Your ChatGPT.")
            alt.advance()
            self.assertEqual(alt.title.text(), "Create an OpenAI account")
        finally:
            alt.close()
            alt.deleteLater()

    def test_no_secret_written_until_explicit_setup(self):
        self.wizard.index = 3
        self.wizard._show_step()
        self.wizard.api_key.setText("sk-test-" + "z"*40)
        self.wizard.tunnel.setText("tunnel_" + "b"*32)
        self.assertFalse(self.manager.paths.key.exists())
        self.assertFalse(self.manager.paths.config.exists())


if __name__ == "__main__":
    unittest.main()
