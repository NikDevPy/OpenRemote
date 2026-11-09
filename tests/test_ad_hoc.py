"""User-owned ad hoc argv execution is explicitly opt-in and auditable."""
import asyncio
import os
from pathlib import Path
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from openremote_agent.config import Config, Workspace
from openremote_agent.engine import Agent


class AdHocTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.root = d / "root"
        self.state = d / "state"
        self.project = d / "project"
        self.project.mkdir()
        self.config = Config(
            "terra", self.root, self.state, role="admin",
            workspaces={"projects": Workspace(self.project, "read")},
            ad_hoc_enabled=True,
        )
        self.agent = Agent(self.config)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_ad_hoc_linux_command(self):
        result = await self.agent.run_argv(["/usr/bin/id", "-un"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"].strip(), os.environ["USER"])
        entries = self.agent.audit.tail(5)
        self.assertTrue(any(e["tool"] == "run_argv" for e in entries))

    async def test_no_adhoc_without_explicit_config(self):
        self.agent.close()
        self.agent = Agent(Config("terra", self.root, self.state, role="admin"))
        with self.assertRaises(PermissionError):
            await self.agent.run_argv(["/usr/bin/uname", "-s"])

    async def test_role_enforced(self):
        self.agent.close()
        self.agent = Agent(Config(
            "terra", self.root, self.state, role="operator",
            ad_hoc_enabled=True,
        ))
        with self.assertRaises(PermissionError):
            await self.agent.run_argv(["/usr/bin/id"])

    async def test_input_validation(self):
        for argv in (
            [], ["id"], ["../bin/id"], ["/usr/bin/id", "\x00bad"],
            ["/usr/bin/id"] * 41,
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                await self.agent.run_argv(argv)
        for timeout in (0, 100, float("nan")):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                await self.agent.run_argv(["/usr/bin/id"], timeout=timeout)

    async def test_timeout_and_exit_code(self):
        with self.assertRaisesRegex(ValueError, "таймаут"):
            await self.agent.run_argv(["/usr/bin/sleep", "2"], timeout=0.3)
        result = await self.agent.run_argv(["/usr/bin/false"])
        self.assertNotEqual(result["exit_code"], 0)

    async def test_workspace_working_directory(self):
        result = await self.agent.run_argv(["/usr/bin/pwd"], workspace="projects")
        self.assertEqual(result["stdout"].strip(), str(self.project))
        with self.assertRaises(ValueError):
            await self.agent.run_argv(["/usr/bin/pwd"], workspace="not-registered")

    async def test_tool_annotations(self):
        from openremote_agent.server import Server
        srv = Server(self.agent)
        tools = {t.name: t for t in await srv.mcp.list_tools()}
        self.assertTrue(tools["run_argv"].annotations.destructiveHint)
        self.assertFalse(tools["run_argv"].annotations.readOnlyHint)
        self.assertTrue(tools["run_argv"].annotations.openWorldHint)


if __name__ == "__main__":
    unittest.main()
