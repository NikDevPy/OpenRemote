"""Policy and MCP desktop integration; never opens a real system screen."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from openremote_agent.config import Config
from openremote_agent.engine import Agent
from openremote_agent.router import REQUIRED_ROLES
from openremote_agent.desktop import DesktopPortal, KEYSYMS, BUTTONS, require_labwc_chooser


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

    def test_read_only_connect_uses_screencast_not_remote_desktop(self):
        portal = DesktopPortal()
        portal.screencast, portal.remote = object(), object()
        dbus = SimpleNamespace(String=str, ObjectPath=str, UInt32=int, Boolean=bool)
        responses = [{"session_handle": "/test"}, {},
                     {"streams": [(42, {"size": (1280, 720)})]}]
        with patch("openremote_agent.desktop._system_dbus_gi", return_value=(dbus, None)), \
             patch.object(portal, "_setup"), \
             patch.object(portal, "_request", side_effect=responses) as request:
            result = portal.connect(False)
        self.assertTrue(result["connected"])
        self.assertFalse(result["input_granted"])
        self.assertEqual([call.args[1] for call in request.call_args_list],
                         ["CreateSession", "SelectSources", "Start"])
        self.assertTrue(all(call.args[0] is portal.screencast
                            for call in request.call_args_list))

    def test_input_connect_requires_remote_desktop_without_fallback(self):
        portal = DesktopPortal()
        portal.screencast, portal.remote = object(), object()
        dbus = SimpleNamespace(String=str, ObjectPath=str, UInt32=int, Boolean=bool)
        with patch("openremote_agent.desktop._system_dbus_gi", return_value=(dbus, None)), \
             patch.object(portal, "_setup"), \
             patch.object(portal, "_request", side_effect=PermissionError("denied")) as request, \
             patch.object(portal, "disconnect"):
            with self.assertRaises(PermissionError):
                portal.connect(True)
        self.assertEqual(request.call_count, 1)
        self.assertIs(request.call_args.args[0], portal.remote)

    def test_labwc_rejects_missing_or_noninteractive_chooser(self):
        config = Path(self.temp.name) / ".config/xdg-desktop-portal-wlr/labwc"
        config.parent.mkdir(parents=True)
        with patch("pathlib.Path.home", return_value=Path(self.temp.name)), \
             patch("shutil.which", return_value="/usr/bin/slurp"):
            with self.assertRaises(PermissionError):
                require_labwc_chooser()
            config.write_text("[screencast]\nchooser_type=none\n")
            with self.assertRaises(PermissionError):
                require_labwc_chooser()
            config.write_text("[screencast]\nchooser_type=simple\n"
                              "chooser_cmd=/usr/bin/slurp -f %o -or\n")
            config.chmod(0o600)
            require_labwc_chooser()
            config.chmod(0o666)
            with self.assertRaises(PermissionError):
                require_labwc_chooser()


if __name__ == "__main__":
    unittest.main()
