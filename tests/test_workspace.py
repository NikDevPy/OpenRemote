"""Проверки управляемых файловых рабочих областей."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openremote_agent.config import Config, Workspace, load_config
from openremote_agent.engine import Agent


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "sandbox"
        self.state = base / "state"
        self.project = base / "owned-project"
        self.project.mkdir(mode=0o750)
        self.project.chmod(0o750)
        self.readonly = base / "docs"
        self.readonly.mkdir(mode=0o750)
        (self.readonly / "readme.txt").write_text("docs")
        self.cfg = Config(
            "terra", self.root, self.state, role="operator",
            workspaces={
                "projects": Workspace(self.project, "write"),
                "docs": Workspace(self.readonly, "read"),
            },
        )
        self.agent = Agent(self.cfg)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_workspace_discovery_and_readonly(self):
        result = self.agent.list_workspaces()
        self.assertEqual([w["name"] for w in result["workspaces"]],
                         ["sandbox", "docs", "projects"])
        self.assertEqual(self.agent.read_text("readme.txt", workspace="docs")["content"], "docs")
        with self.assertRaises(ValueError):
            self.agent.write_text("new.txt", "x", workspace="docs")
        with self.assertRaises(ValueError):
            self.agent.replace_text("readme.txt", "x", hashlib.sha256(b"docs").hexdigest(),
                                    workspace="docs")
        self.assertFalse((self.readonly / "new.txt").exists())

    async def test_create_read_replace_conflict(self):
        result = self.agent.write_text("hello.txt", "first", workspace="projects")
        self.assertTrue(result["created"])
        before = self.agent.read_text("hello.txt", workspace="projects")
        self.assertEqual(before["sha256"], hashlib.sha256(b"first").hexdigest())
        after = self.agent.replace_text(
            "hello.txt", "second", before["sha256"], workspace="projects")
        self.assertTrue(after["replaced"])
        self.assertEqual(after["sha256"], hashlib.sha256(b"second").hexdigest())
        with self.assertRaises(ValueError):
            self.agent.replace_text(
                "hello.txt", "third", before["sha256"], workspace="projects")
        self.assertEqual((self.project / "hello.txt").read_text(), "second")
        self.assertFalse(any(p.name.startswith(".openremote-") for p in self.project.iterdir()))

    async def test_existing_mode_kept_and_symlink_denied(self):
        original = self.project / "existing.txt"
        original.write_text("a")
        original.chmod(0o640)
        digest = hashlib.sha256(b"a").hexdigest()
        self.agent.replace_text("existing.txt", "b", digest, workspace="projects")
        self.assertEqual(original.stat().st_mode & 0o777, 0o640)
        (self.project / "outside").symlink_to(self.readonly, target_is_directory=True)
        (self.project / "linked.txt").symlink_to(self.readonly / "readme.txt")
        for name in ("outside/readme.txt", "linked.txt", "../docs/readme.txt",
                     "/etc/passwd"):
            with self.subTest(path=name), self.assertRaises(ValueError):
                self.agent.read_text(name, workspace="projects")
        with self.assertRaises(ValueError):
            self.agent.replace_text("linked.txt", "no", hashlib.sha256(b"docs").hexdigest(),
                                    workspace="projects")
        self.assertEqual((self.readonly / "readme.txt").read_text(), "docs")

    async def test_unknown_and_invalid_config(self):
        with self.assertRaises(ValueError):
            self.agent.read_text("a", workspace="unknown")
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.state,
                   workspaces={"sandbox": Workspace(self.project)})
        with self.assertRaises(ValueError):
            Workspace(Path("relative"))
        (self.project / "notdir.txt").write_text("x")
        with self.assertRaises(ValueError):
            Workspace(self.project / "notdir.txt")
        cfg_path = Path(self.tmp.name) / "config.json"
        cfg_path.write_text(json.dumps({
            "device_id": "terra", "root": str(self.root), "state": str(self.state),
            "workspaces": {"projects": {"path": str(self.project), "access": "write"}}
        }))
        os.chmod(cfg_path, 0o600)
        self.assertEqual(load_config(cfg_path).workspaces["projects"].access, "write")

    async def test_mcp_stdio_workspace_roundtrip(self):
        cfg = Path(self.tmp.name) / "stdio.json"
        cfg.write_text(json.dumps({
            "device_id": "terra", "root": str(self.root), "state": str(self.state),
            "role": "operator",
            "workspaces": {"projects": {"path": str(self.project), "access": "write"}}
        }))
        os.chmod(cfg, 0o600)
        async with stdio_client(StdioServerParameters(
            command=os.sys.executable, args=[
                "-m", "openremote_agent.server", "--config", str(cfg)]
        )) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = {x.name for x in (await client.list_tools()).tools}
                self.assertIn("replace_text", tools)
                self.assertIn("list_workspaces", tools)
                response = await client.call_tool("list_workspaces", {})
                self.assertFalse(response.isError)
                self.assertEqual(response.structuredContent["workspaces"][1]["name"], "projects")
                response = await client.call_tool("write_text", {
                    "path": "mcp.txt", "content": "one", "workspace": "projects"})
                self.assertFalse(response.isError)
                response = await client.call_tool("read_text", {
                    "path": "mcp.txt", "workspace": "projects"})
                digest = response.structuredContent["sha256"]
                response = await client.call_tool("replace_text", {
                    "path": "mcp.txt", "content": "two",
                    "expected_sha256": digest, "workspace": "projects"})
                self.assertFalse(response.isError)
                self.assertEqual((self.project / "mcp.txt").read_text(), "two")


if __name__ == "__main__":
    unittest.main()
