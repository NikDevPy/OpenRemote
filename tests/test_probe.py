import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from openremote_probe.server import LocalBearer, Probe

TOKEN = "test-token-only-never-use-in-production-0123456789"


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        self.probe = Probe(self.root)

    def tearDown(self):
        self.probe.close()
        self.tmp.cleanup()

    @asynccontextmanager
    async def client(self):
        app = LocalBearer(self.probe.mcp.streamable_http_app(), TOKEN)
        async with self.probe.mcp.session_manager.run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        headers={"Authorization": f"Bearer {TOKEN}"}) as http:
                async with streamable_http_client("http://localhost:8765/mcp", http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        self.assertEqual(init.serverInfo.name, "OpenRemote Probe")
                        yield session

    async def test_http_handshake_discovery_and_tools(self):
        async with self.client() as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
            self.assertEqual(set(tools), {"system_info", "list_files", "execute_command"})
            self.assertTrue(tools["system_info"].annotations.readOnlyHint)
            self.assertFalse(tools["execute_command"].annotations.readOnlyHint)
            result = await client.call_tool("system_info", {})
            self.assertEqual(result.structuredContent["os"], "Linux")
            result = await client.call_tool("execute_command", {"command": "write_probe", "nonce": "nonce_http_1234"})
            self.assertFalse(result.isError)
            data = result.structuredContent
            self.assertEqual(data["exit_code"], 0)
            self.assertEqual((self.root / data["receipt"]).read_text(), "OPENREMOTE:nonce_http_1234\n")
            receipt = json.loads(data["stdout"])
            self.assertEqual(receipt["sha256"], hashlib.sha256(b"OPENREMOTE:nonce_http_1234\n").hexdigest())
            self.assertNotEqual(receipt["pid"], os.getpid())
            listing = await client.call_tool("list_files", {})
            self.assertIn(data["receipt"], listing.structuredContent["files"])
            denied = await client.call_tool("execute_command", {"command": "sh", "nonce": "nonce_http_1234"})
            self.assertTrue(denied.isError)

    async def test_authentication(self):
        app = LocalBearer(self.probe.mcp.streamable_http_app(), TOKEN)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http:
            for authorization in ("", "Bearer wrong"):
                result = await http.post("/mcp", headers={"Authorization": authorization}, json={})
                self.assertEqual(result.status_code, 401)
        with self.assertRaises(ValueError):
            LocalBearer(app, "")

    async def test_paths_and_symlinks(self):
        (self.root / "escape").symlink_to(self.tmp.name, target_is_directory=True)
        for path in ("../", "/etc", "escape", "a/../../etc"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.probe.list_files(path)
        (self.root / "inside").mkdir()
        self.assertEqual(self.probe.list_files("inside")["files"], [])

    async def test_validation_and_no_overwrite(self):
        for nonce in ("../escape", "short", "x" * 65, "abcd1234\n"):
            with self.assertRaises(ValueError):
                await self.probe.execute_command("write_probe", nonce)
        first = await self.probe.execute_command("write_probe", "nonce_unique_1234")
        self.assertEqual(first["exit_code"], 0)
        again = await self.probe.execute_command("write_probe", "nonce_unique_1234")
        self.assertNotEqual(again["exit_code"], 0)
        (self.root / "nonce_symlink_1234.receipt").symlink_to(Path(self.tmp.name) / "outside")
        denied = await self.probe.execute_command("write_probe", "nonce_symlink_1234")
        self.assertNotEqual(denied["exit_code"], 0)
        self.assertFalse((Path(self.tmp.name) / "outside").exists())

    async def test_concurrent_execution(self):
        results = await asyncio.gather(*[
            self.probe.execute_command("write_probe", f"nonce_parallel_{i}") for i in range(4)])
        self.assertTrue(all(x["exit_code"] == 0 for x in results))
        self.assertEqual(len(list(self.root.glob("*.receipt"))), 4)

    async def test_listing_bound_and_audit_redaction(self):
        for i in range(105):
            (self.root / f"file_{i}").touch()
        result = self.probe.list_files()
        self.assertEqual(len(result["files"]), 100)
        self.assertTrue(result["truncated"])
        for _ in range(260):
            self.probe.system_info()
        rows = self.probe.db.execute("SELECT * FROM audit").fetchall()
        self.assertEqual(len(rows), 256)
        self.assertNotIn(TOKEN, str(rows))
        self.assertNotIn(str(self.root), str(rows))

    async def test_stdio_roundtrip_and_restart(self):
        for i in range(2):
            params = StdioServerParameters(command=sys.executable, args=[
                "-m", "openremote_probe.server", "--root", str(self.root)])
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool("execute_command", {
                        "command": "write_probe", "nonce": f"nonce_restart_{i}"})
                    self.assertEqual(result.structuredContent["exit_code"], 0)
        self.assertEqual(len(list(self.root.glob("*.receipt"))), 2)


if __name__ == "__main__":
    unittest.main()
