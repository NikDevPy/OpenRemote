"""Policy and MCP desktop integration; never opens a real system screen."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from openremote_agent.config import Config
from openremote_agent.engine import Agent
from openremote_agent.router import REQUIRED_ROLES
from openremote_agent.desktop import DesktopPortal, KEYSYMS, BUTTONS


class DesktopPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root = root / "sandbox"
        self.state = root / "state"

    def tearDown(self):
        self.temp.cleanup()

    def agent(self, **overrides):
        return Agent(Config("terra", self.root, self.state, "admin",
                            desktop_enabled=True, **overrides))

    def test_roles_are_admin_only(self):
        for name in ("desktop_status", "desktop_connect", "desktop_screenshot",
                     "desktop_click", "desktop_move", "desktop_drag", "desktop_scroll",
                     "desktop_key", "desktop_type_text",
                     "desktop_disconnect"):
            self.assertEqual(REQUIRED_ROLES[name], "admin")

    def test_owner_config_switches(self):
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.state, "admin",
                   desktop_input_enabled=True)
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.state, "admin",
                   desktop_enabled=1)
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.state, "admin",
                   desktop_enabled=True, desktop_input_enabled="true")

    def test_connect_never_bypasses_portal(self):
        a = self.agent(desktop_input_enabled=True)
        try:
            with patch.object(a.desktop, "connect",
                              return_value={"connected": True, "input_granted": True}) as approved:
                self.assertTrue(a.desktop_connect()["connected"])
                approved.assert_called_once_with(True)
            with patch.object(a.desktop, "connect", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    a.desktop_connect()
        finally:
            a.close()

    def test_read_only_session_cannot_issue_input(self):
        a = self.agent(desktop_input_enabled=False)
        try:
            with patch.object(a.desktop, "click") as click:
                with self.assertRaises(PermissionError):
                    a.desktop_click(123, 456)
                click.assert_not_called()
            with patch.object(a.desktop, "screenshot",
                              return_value={"mime_type": "image/jpeg",
                                            "image_base64": "aGVsbG8=", "width": 1,
                                            "height": 1, "native_width": 1, "native_height": 1}):
                self.assertIn("image_base64", a.desktop_screenshot())
        finally:
            a.close()

    def test_wrong_device_cannot_control_local_screen(self):
        a = self.agent(desktop_input_enabled=True)
        try:
            with self.assertRaises(ValueError):
                a.desktop_connect(device_id="unregistered")
            with self.assertRaises(ValueError):
                a.desktop_key("enter", device_id="unregistered")
        finally:
            a.close()

    def test_portal_has_safe_initial_state(self):
        p = DesktopPortal()
        self.assertFalse(p.status()["connected"])
        with self.assertRaises(PermissionError):
            p.screenshot()
        with self.assertRaises(PermissionError):
            p.click(10, 20)
        with self.assertRaises(PermissionError):
            p.type_text("hello")
        self.assertTrue(p.disconnect()["disconnected"])
        self.assertIn("left", BUTTONS)
        self.assertIn("escape", KEYSYMS)


if __name__ == "__main__":
    unittest.main()
