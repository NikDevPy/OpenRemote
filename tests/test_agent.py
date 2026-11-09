import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openremote_agent.config import Config, CommandSpec, load_config
from openremote_agent.engine import Agent


class AgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "sandbox"
        self.state = base / "state"
        self.config = Config(
            device_id="terra", root=self.root, state=self.state,
            role="operator", commands={"python-version": CommandSpec(("/usr/bin/python3", "--version"), 3)}
        )
        self.agent = Agent(self.config)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_roles_and_devices(self):
        self.assertEqual(self.agent.system_info()["device_id"], "terra")
        self.assertEqual(self.agent.list_devices()["devices"][0]["status"], "online")
        with self.assertRaises(ValueError):
            self.agent.system_info("rock")
        with self.assertRaises(PermissionError):
            self.agent.audit_tail()
        viewer = Agent(Config("viewer1", self.root, self.state, "viewer"))
        try:
            with self.assertRaises(PermissionError):
                viewer.write_text("denied.txt", "not allowed")
            with self.assertRaises(PermissionError):
                await viewer.execute_command("python-version")
        finally:
            viewer.close()

    async def test_files_and_no_overwrite(self):
        self.assertTrue(self.agent.write_text("hello.txt", "Привет")["created"])
        self.assertEqual(self.agent.read_text("hello.txt")["content"], "Привет")
        self.assertEqual(self.agent.list_files()["entries"][0]["type"], "file")
        with self.assertRaises(ValueError):
            self.agent.write_text("hello.txt", "overwrite")
        with self.assertRaises(ValueError):
            self.agent.write_text("large.txt", "a" * 65537)
        self.assertEqual((self.root / "hello.txt").read_text(), "Привет")

    async def test_paths_symlinks_and_special_files(self):
        (self.root / "escape").symlink_to(self.tmp.name, target_is_directory=True)
        (self.root / "external").symlink_to(Path(self.tmp.name) / "outside")
        for path in ("../etc/passwd", "/etc/passwd", "escape/item", "external",
                     "a/../../etc/passwd", "a\\b"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    self.agent.read_text(path)
        with self.assertRaises(ValueError):
            self.agent.write_text("external", "x")
        self.assertFalse((Path(self.tmp.name) / "outside").exists())
        with self.assertRaises(ValueError):
            self.agent.read_text(".")
        os.mkfifo(self.root / "pipe")
        with self.assertRaises(ValueError):
            self.agent.read_text("pipe")

    async def test_allowlist_and_audit(self):
        with self.assertRaises(ValueError):
            await self.agent.execute_command("sh")
        result = await self.agent.execute_command("python-version")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("Python", result["stdout"])
        self.assertEqual(self.agent.audit.db.execute("SELECT COUNT(*) FROM audit").fetchone()[0], 2)
        self.assertEqual(self.agent.audit.db.execute(
            "SELECT COUNT(*) FROM audit WHERE result='denied'").fetchone()[0], 1)
        self.assertFalse((self.root / "unwanted.txt").exists())

    async def test_admin_audit_persistence(self):
        self.agent.write_text("audit.txt", "top secret")
        self.agent.close()
        self.agent = Agent(Config("terra", self.root, self.state, "admin"))
        tail = self.agent.audit_tail()
        self.assertTrue(any(x["tool"] == "write_text" for x in tail["entries"]))
        self.assertNotIn("top secret", str(tail))
        self.assertNotIn("audit.txt", str(tail))

    async def test_config_validation(self):
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.root / "state")
        with self.assertRaises(ValueError):
            Config("a/b", self.root, self.state)
        with self.assertRaises(ValueError):
            CommandSpec(("sh", "-c", "date"))
        cfg = Path(self.tmp.name) / "cfg.json"
        cfg.write_text(json.dumps({
            "device_id": "terra", "root": str(self.root), "state": str(self.state),
            "role": "viewer", "commands": {}
        }))
        os.chmod(cfg, 0o600)
        self.assertEqual(load_config(cfg).role, "viewer")
        os.chmod(cfg, 0o666)
        with self.assertRaises(ValueError):
            load_config(cfg)

    async def test_stdio_mcp_roundtrip(self):
        cfg = Path(self.tmp.name) / "stdio.json"
        cfg.write_text(json.dumps({
            "device_id": "terra", "root": str(self.root), "state": str(self.state),
            "role": "operator", "commands": {
                "python-version": {"argv": ["/usr/bin/python3", "--version"]}
            }
        }))
        os.chmod(cfg, 0o600)
        params = StdioServerParameters(command=os.sys.executable, args=[
            "-m", "openremote_agent.server", "--config", str(cfg)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name: t for t in (await session.list_tools()).tools}
                self.assertEqual(set(tools), {
                    "system_info", "list_devices", "list_workspaces",
                    "list_files", "read_text", "write_text", "replace_text",
                    "execute_command", "run_argv", "preview_command", "audit_tail",
                    "start_job", "job_status", "list_jobs", "job_output", "cancel_job", "purge_job",
                    "binary_file_info", "read_binary_chunk", "begin_upload",
                    "upload_chunk", "upload_status", "finish_upload", "cancel_upload"
                })
                self.assertFalse(tools["execute_command"].annotations.readOnlyHint)
                self.assertTrue(tools["execute_command"].annotations.destructiveHint)
                self.assertTrue(tools["preview_command"].annotations.readOnlyHint)
                self.assertTrue(tools["read_text"].annotations.readOnlyHint)
                result = await session.call_tool("write_text", {
                    "path": "roundtrip.txt", "content": "works"
                })
                self.assertFalse(result.isError)
                result = await session.call_tool("read_text", {"path": "roundtrip.txt"})
                self.assertFalse(result.isError, str(result.content))
                self.assertIn("works", str(result.content))
                result = await session.call_tool("execute_command", {"command_id": "python-version"})
                self.assertEqual(result.structuredContent["exit_code"], 0)
                denied = await session.call_tool("execute_command", {"command_id": "not-allowed"})
                self.assertTrue(denied.isError)


if __name__ == "__main__":
    unittest.main()
