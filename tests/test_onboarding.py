"""Secure first-run onboarding tests; no external API/credentials required."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from openremote_gui.onboarding import FirstRun, SetupError, SetupPaths


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openremote-onboarding-")
        self.home = Path(self.temp.name)
        self.paths = SetupPaths(self.home)
        self.manager = FirstRun(
            self.paths,
            agent_command=[sys.executable, "-c",
                           "import sys; sys.exit(0)"],
        )
        self.key = "sk-test-" + "a" * 50
        self.tunnel = "tunnel_" + "b" * 32
        client = self.home / "official-tunnel-client-test"
        client.write_text(
            "#!/usr/bin/env python3\n"
            "import sys, pathlib, os\n"
            "args=sys.argv[1:]\n"
            "if args == ['--version']: sys.exit(0)\n"
            "if args[0]=='init':\n"
            "  assert 'CONTROL_PLANE_API_KEY' in os.environ\n"
            "  path=pathlib.Path(os.environ['HOME'])/'.config/tunnel-client/openremote-agent.yaml'\n"
            "  path.parent.mkdir(parents=True,exist_ok=True)\n"
            "  path.write_text('tunnel_' + ('b'*32))\n"
            "  sys.exit(0)\n"
            "if args[0]=='doctor':\n"
            "  sys.exit(0)\n"
            "sys.exit(2)\n"
        )
        client.chmod(0o700)
        self.client = client
        self.old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(self.home)

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.old_home
        self.temp.cleanup()

    def test_bootstrap_and_private_permissions(self):
        result = self.manager.configure_tunnel(
            device_id="workstation", tunnel_id=self.tunnel,
            runtime_key=self.key, binary=str(self.client),
            start_service=False,
        )
        self.assertTrue(result["configured"])
        config = json.loads(self.paths.config.read_text())
        self.assertEqual(config["role"], "viewer")
        self.assertFalse(config["ad_hoc_enabled"])
        self.assertEqual(config["device_id"], "workstation")
        self.assertEqual(self.paths.key.read_text().strip(), self.key)
        for private in (self.paths.key, self.paths.config,
                        self.paths.profile, self.paths.wrapper, self.paths.service):
            self.assertEqual(stat.S_IMODE(private.stat().st_mode) & 0o077, 0)
        self.assertNotIn(self.key, self.paths.wrapper.read_text())
        self.assertNotIn(self.key, self.paths.profile.read_text())
        self.assertNotIn(self.key, self.paths.service.read_text())
        self.assertNotIn(self.key, json.dumps(result))
        self.assertNotIn(self.key, self.paths.config.read_text())
        self.assertIn("NoNewPrivileges=true", self.paths.service.read_text())
        self.assertTrue(self.manager.installed())

    def test_refuses_unexpected_existing_files_and_never_overwrites_key(self):
        self.manager.configure_tunnel(
            device_id="workstation", tunnel_id=self.tunnel,
            runtime_key=self.key, binary=str(self.client),
            start_service=False,
        )
        # Same owner, key, profile, unit and command: retry is safe.
        second = self.manager.configure_tunnel(
            device_id="workstation", tunnel_id=self.tunnel,
            runtime_key=self.key, binary=str(self.client),
            start_service=False,
        )
        self.assertTrue(second["configured"])
        self.assertEqual(self.paths.key.read_text().strip(), self.key)
        with self.assertRaises(SetupError):
            self.manager.configure_tunnel(
                device_id="workstation", tunnel_id=self.tunnel,
                runtime_key="sk-other-" + "z"*40, binary=str(self.client),
                start_service=False,
            )

    def test_invalid_credentials_have_no_side_effects(self):
        for wrong in ("abc", "tunnel_" + "z"*32, "tunnel_" + "f"*31):
            with self.assertRaises(SetupError):
                self.manager.configure_tunnel(
                    device_id="abc", tunnel_id=wrong,
                    runtime_key=self.key, binary=str(self.client),
                    start_service=False,
                )
        self.assertFalse(self.paths.key.exists())
        with self.assertRaises(SetupError):
            self.manager.configure_tunnel(
                device_id="abc", tunnel_id=self.tunnel,
                runtime_key="bad key with spaces", binary=str(self.client),
                start_service=False,
            )
        self.assertFalse(self.paths.config.exists())

    def test_existing_config_not_destroyed(self):
        self.manager.prepare_local("workstation")
        before = self.paths.config.read_bytes()
        with self.assertRaises(SetupError):
            self.manager.prepare_local("another-machine")
        self.assertEqual(self.paths.config.read_bytes(), before)

    def test_setup_rejects_symlink_config_dir(self):
        self.paths.config_dir.parent.mkdir(parents=True)
        self.paths.config_dir.symlink_to(self.home / "fake-target")
        with self.assertRaises(SetupError):
            self.manager.prepare_local("workstation")


if __name__ == "__main__":
    unittest.main()
