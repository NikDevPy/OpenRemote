"""Self-hosted tunnel setup helper: isolated filesystem and fake control plane.

Never touches real user systemd or real OpenAI credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
SETUP = REPO / "scripts" / "setup_agent_tunnel.sh"
TEST_TUNNEL = "tunnel_0123456789abcdef0123456789abcdef"


class PublicOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir(mode=0o700)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        cfg_dir = self.home / ".config" / "openremote-agent"
        cfg_dir.mkdir(parents=True, mode=0o700)
        self.config = cfg_dir / "agent.json"
        self.keyfile = cfg_dir / "runtime-key"
        self.keyfile.write_text("test-secret-not-a-real-token\n")
        self.keyfile.chmod(0o600)
        self.config.write_text(json.dumps({
            "device_id": "testhost",
            "root": str(self.home / "sandbox"),
            "state": str(self.home / "state"),
            "role": "viewer",
            "commands": {},
            "peers": {},
            "workspaces": {},
            "ad_hoc_enabled": False,
        }))
        self.config.chmod(0o600)
        # Mock both external tools. The fake tunnel-client records arguments but
        # never calls OpenAI and never prints runtime secrets.
        fake_tunnel = self.bin / "fake-tunnel-client"
        fake_tunnel.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'printf "%s\\n" "$@" >> "$HOME/fake-tunnel-calls.txt"\n'
            'if [[ "$1" == "init" ]]; then\n'
            '  mkdir -p "$HOME/.config/tunnel-client"\n'
            '  touch "$HOME/.config/tunnel-client/openremote-agent.yaml"\n'
            "fi\n"
        )
        fake_tunnel.chmod(0o700)
        systemctl = self.bin / "systemctl"
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'printf "%s\\n" "$@" >> "$HOME/fake-systemctl-calls.txt"\n'
        )
        systemctl.chmod(0o700)
        self.env = dict(os.environ)
        self.env.update({
            "HOME": str(self.home),
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "TUNNEL_CLIENT": str(fake_tunnel),
            "OPENREMOTE_CONFIG": str(self.config),
            "OPENREMOTE_RUNTIME_KEY": str(self.keyfile),
            "OPENREMOTE_AGENT_PYTHON": os.sys.executable,
        })

    def tearDown(self):
        self.tmp.cleanup()

    def setup(self, tunnel_id=TEST_TUNNEL):
        return subprocess.run(
            ["bash", str(SETUP), tunnel_id],
            env=self.env, cwd=REPO,
            capture_output=True, text=True, timeout=25,
        )

    def test_example_configs_are_valid_json(self):
        for filename in ("local-agent.example.json", "gateway-agent.example.json"):
            data = json.loads((REPO / "examples" / filename).read_text())
            self.assertIn("device_id", data)
            self.assertFalse(data["ad_hoc_enabled"])
            self.assertIn(data["role"], ("viewer", "operator"))

    def test_public_docs_have_valid_internal_links(self):
        for name in (
            "README.md", "docs/QUICKSTART.md",
            "docs/SECURITY.md", "docs/TROUBLESHOOTING.md",
        ):
            path = REPO / name
            body = path.read_text(encoding="utf-8")
            for href in re.findall(r"\[[^\]]+\]\(([^)]+)\)", body):
                if href.startswith(("http://", "https://", "#")):
                    continue
                target = href.split("#", 1)[0]
                self.assertTrue((path.parent / target).is_file(),
                                f"Broken link in {name}: {href}")
            self.assertNotIn("/home/domenus/", body)
            self.assertNotIn("/home/nora-admin/", body)

    def test_setup_script_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SETUP)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_id_does_not_create_anything(self):
        result = self.setup("not-a-valid-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.home / ".config" / "tunnel-client").exists())
        self.assertFalse((self.home / ".config/systemd/user/openremote-agent-tunnel.service").exists())

    def test_generates_portable_service_without_leaking_secret(self):
        result = self.setup()
        self.assertEqual(result.returncode, 0, result.stderr + "\n" + result.stdout)
        wrapper = self.home / ".local/libexec/openremote-agent-tunnel-start"
        unit = self.home / ".config/systemd/user/openremote-agent-tunnel.service"
        profile = self.home / ".config/tunnel-client/openremote-agent.yaml"
        self.assertTrue(wrapper.is_file() and unit.is_file() and profile.is_file())
        self.assertEqual(wrapper.stat().st_mode & 0o777, 0o700)
        self.assertEqual(unit.stat().st_mode & 0o777, 0o600)
        self.assertIn("ExecStart=%h/.local/libexec", unit.read_text())
        self.assertIn("NoNewPrivileges=true", unit.read_text())
        self.assertNotIn("domenus", wrapper.read_text() + unit.read_text())
        self.assertNotIn("test-secret-not-a-real-token", wrapper.read_text() + unit.read_text())
        self.assertNotIn("test-secret-not-a-real-token", result.stdout + result.stderr)
        self.assertIn(TEST_TUNNEL, (self.home / "fake-tunnel-calls.txt").read_text())
        self.assertIn("enable", (self.home / "fake-systemctl-calls.txt").read_text())
        repeated = self.setup()
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("refusing to overwrite", repeated.stderr.lower())

    def test_rejects_unsafe_key_permissions(self):
        self.keyfile.chmod(0o644)
        result = self.setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mode 0600", result.stderr)
        self.assertFalse((self.home / ".config/tunnel-client").exists())


if __name__ == "__main__":
    unittest.main()
