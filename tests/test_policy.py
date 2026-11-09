"""Tests for independently authorized command policies (no privileged action executed)."""
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import time
import unittest

from openremote_agent.config import CommandSpec, Config
from openremote_agent.engine import Agent


class PrivilegedPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "sandbox"
        self.state = base / "state"
        self.config = Config(
            "test-host", self.root, self.state, role="admin",
            commands={
                "privileged-test": CommandSpec(
                    ("/usr/bin/true",), risk="privileged",
                    summary="Тестовая команда без побочных эффектов"
                ),
                "normal-test": CommandSpec(("/usr/bin/true",)),
            },
        )
        self.agent = Agent(self.config)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_privileged_denied_until_approved_and_one_shot(self):
        preview = self.agent.preview_command("privileged-test")
        self.assertEqual(preview["required_role"], "admin")
        self.assertEqual(preview["risk"], "privileged")
        self.assertFalse(preview["approval_pending"])
        with self.assertRaises(PermissionError):
            await self.agent.execute_command("privileged-test")
        self.agent.audit.grant_command("privileged-test", self.agent.command_digest("privileged-test"), ttl=60)
        self.assertTrue(self.agent.preview_command("privileged-test")["approval_pending"])
        done = await self.agent.execute_command("privileged-test")
        self.assertEqual(done["exit_code"], 0)
        self.assertFalse(self.agent.preview_command("privileged-test")["approval_pending"])
        with self.assertRaises(PermissionError):
            await self.agent.execute_command("privileged-test")

    async def test_approved_command_not_reusable_in_parallel(self):
        self.agent.audit.grant_command("privileged-test", self.agent.command_digest("privileged-test"), ttl=60)
        results = await asyncio.gather(
            self.agent.execute_command("privileged-test"),
            self.agent.execute_command("privileged-test"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, dict) and r.get("exit_code") == 0 for r in results), 1)
        self.assertEqual(sum(isinstance(r, PermissionError) for r in results), 1)

    async def test_grant_persists_across_agent_restart(self):
        self.agent.audit.grant_command("privileged-test", self.agent.command_digest("privileged-test"), 60)
        self.agent.close()
        self.agent = Agent(self.config)
        self.assertTrue(self.agent.audit.command_approved("privileged-test", self.agent.command_digest("privileged-test")))
        self.assertEqual((await self.agent.execute_command("privileged-test"))["exit_code"], 0)

    async def test_expired_approval_cannot_execute(self):
        self.agent.audit.grant_command("privileged-test", self.agent.command_digest("privileged-test"), 60)
        self.agent.audit.db.execute(
            "UPDATE command_approvals SET expires_at=? WHERE command_id=?",
            (int(time.time()) - 10, "privileged-test"),
        )
        self.agent.audit.db.commit()
        with self.assertRaises(PermissionError):
            await self.agent.execute_command("privileged-test")

    async def test_role_and_local_policies(self):
        viewer = Agent(Config(
            "other-host", self.root, self.state, "viewer",
            commands=self.config.commands))
        try:
            with self.assertRaises(PermissionError):
                await viewer.execute_command("privileged-test")
        finally:
            viewer.close()
        operator = Agent(Config(
            "other-host", self.root, self.state, "operator",
            commands=self.config.commands))
        try:
            with self.assertRaises(PermissionError):
                await operator.execute_command("privileged-test")
            self.assertEqual((await operator.execute_command("normal-test"))["exit_code"], 0)
        finally:
            operator.close()

    async def test_preview_does_not_reveal_executable(self):
        preview = self.agent.preview_command("privileged-test")
        self.assertNotIn("argv", preview)
        self.assertNotIn("/usr/bin/", str(preview))

    async def test_dangerous_names_require_privileged_policy(self):
        for cmd in (
            ("/usr/bin/sudo", "-n", "/usr/bin/id"),
            ("/usr/bin/mount", "--version"),
            ("/usr/bin/dd", "--version"),
            ("/usr/bin/reboot", "--help"),
            ("/bin/bash", "-c", "echo hi"),
        ):
            with self.subTest(argv=cmd), self.assertRaises(ValueError):
                CommandSpec(cmd, risk="standard")
        allowed = CommandSpec(
            ("/usr/bin/sudo", "-n", "/usr/bin/id"),
            risk="privileged",
            summary="Проверка идентификатора с отдельно настроенным sudoers",
        )
        self.assertEqual(allowed.risk, "privileged")
        with self.assertRaises(ValueError):
            CommandSpec(("/usr/bin/sudo", "/usr/bin/id"), risk="privileged")
        with self.assertRaises(ValueError):
            CommandSpec(("/usr/bin/id",), timeout=float("nan"))

    async def test_approval_bound_to_exact_command_policy(self):
        old_digest = self.agent.command_digest("privileged-test")
        self.agent.audit.grant_command("privileged-test", old_digest, 60)
        self.agent.config.commands["privileged-test"] = CommandSpec(
            ("/usr/bin/false",), risk="privileged", summary="Другая команда"
        )
        new_digest = self.agent.command_digest("privileged-test")
        self.assertNotEqual(old_digest, new_digest)
        self.assertFalse(self.agent.audit.command_approved("privileged-test", new_digest))
        with self.assertRaises(PermissionError):
            await self.agent.execute_command("privileged-test")

    async def test_cli_refuses_noninteractive_approval(self):
        cfg = Path(self.tmp.name) / "command-policy.json"
        cfg.write_text(json.dumps({
            "device_id": "test-host",
            "root": str(self.root), "state": str(self.state), "role": "admin",
            "commands": {"privileged-test": {
                "argv": ["/usr/bin/true"], "risk": "privileged",
                "summary": "Безопасная тестовая команда",
            }},
        }))
        os.chmod(cfg, 0o600)
        proc = subprocess.run([
            sys.executable, "-m", "openremote_agent.server",
            "--config", str(cfg), "--approve-command", "privileged-test",
        ], input="APPROVE privileged-test\n", text=True, capture_output=True, timeout=12)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("интерактивном", proc.stderr)
        self.assertFalse(self.agent.audit.command_approved("privileged-test", self.agent.command_digest("privileged-test")))

    async def test_invalid_local_approval_ttl(self):
        for ttl in (0, 301):
            with self.assertRaises(ValueError):
                self.agent.audit.grant_command("privileged-test", self.agent.command_digest("privileged-test"), ttl)


if __name__ == "__main__":
    unittest.main()
