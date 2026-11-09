import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from mcp import StdioServerParameters

from openremote_agent.config import Config, SSHPeer, load_config
from openremote_agent.engine import Agent
from openremote_agent.router import Router
from openremote_agent.server import LocalAuth, Server


class RouterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "terra-sandbox"
        self.state = base / "terra-state"
        self.peer = SSHPeer(
            target="rock-admin", host_name="100.101.102.103",
            python="/opt/openremote/.venv/bin/python",
            config_path="/home/rock/.config/openremote.json",
        )
        self.config = Config(
            device_id="terra", root=self.root, state=self.state, role="operator",
            peers={"rock": self.peer},
        )
        self.agent = Agent(self.config)
        self.router = Router(self.agent)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_devices_not_claimed_online(self):
        devices = self.router.list_devices()["devices"]
        self.assertEqual([d["device_id"] for d in devices], ["terra", "rock"])
        self.assertEqual(devices[1]["status"], "configured")
        self.assertIsNone(devices[1]["online"])

    async def test_local_and_peer_routing(self):
        self.agent.write_text("local.txt", "local")
        result = await self.router.call("read_text", {"path": "local.txt"})
        self.assertEqual(result["content"], "local")
        with patch.object(self.router, "_remote_call", new_callable=AsyncMock) as remote:
            remote.return_value = {"content": "remote", "size": 6, "operation_id": "remoteid"}
            result = await self.router.call("read_text", {"path": "remote.txt"}, "rock")
            self.assertEqual(result["content"], "remote")
            remote.assert_awaited_once_with(
                self.peer, "read_text", {"path": "remote.txt", "device_id": "rock"})
        with self.assertRaisesRegex(ValueError, "списке разрешённых"):
            await self.router.call("system_info", {}, "unknown-host")

    async def test_local_role_gates_remote_writes(self):
        viewer = Agent(Config(
            device_id="viewer1", root=self.root, state=self.state,
            role="viewer", peers={"rock": self.peer}
        ))
        try:
            router = Router(viewer)
            with patch.object(router, "_remote_call", new_callable=AsyncMock) as remote:
                with self.assertRaises(PermissionError):
                    await router.call("write_text", {"path": "x", "content": "x"}, "rock")
                with self.assertRaises(PermissionError):
                    await router.call("execute_command", {"command_id": "python-version"}, "rock")
                remote.assert_not_awaited()
        finally:
            viewer.close()

    async def test_peer_validation(self):
        for bad in ("-proxy", "bad;ls", "evil host", "$(whoami)"):
            with self.subTest(target=bad), self.assertRaises(ValueError):
                SSHPeer(bad, "100.0.0.1", "/usr/bin/python3", "/home/agent.json")
        with self.assertRaises(ValueError):
            SSHPeer("rock", "bad;echo", "/usr/bin/python3", "/home/agent.json")
        with self.assertRaises(ValueError):
            SSHPeer("rock", "100.0.0.1", "python", "/home/agent.json")
        with self.assertRaises(ValueError):
            Config("terra", self.root, self.state, peers={"terra": self.peer})

    async def test_ssh_args_pinned_and_no_tool_input(self):
        params = self.router.ssh_params(self.peer)
        self.assertEqual(params.command, "/usr/bin/ssh")
        self.assertIn("StrictHostKeyChecking=yes", params.args)
        self.assertIn("BatchMode=yes", params.args)
        self.assertIn("ForwardAgent=no", params.args)
        self.assertIn("HostName=100.101.102.103", params.args)
        self.assertEqual(params.args[-2], "rock-admin")
        self.assertIn("/opt/openremote/.venv/bin/python", params.args[-1])
        self.assertNotIn("remote.txt", " ".join(params.args))

    async def test_http_authentication_and_status(self):
        srv = Server(self.agent)
        app = LocalAuth(srv.mcp.streamable_http_app(), "x" * 32, self.agent)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1:8766",
        ) as http:
            denied = await http.get("/status")
            self.assertEqual(denied.status_code, 401)
            denied = await http.get("/status", headers={"Authorization": "Bearer wrong"})
            self.assertEqual(denied.status_code, 401)
            authorized = await http.get(
                "/status", headers={"Authorization": "Bearer " + "x" * 32})
            self.assertEqual(authorized.status_code, 200)
            self.assertEqual(authorized.json()["device_id"], "terra")
            self.assertNotIn("rock-admin", str(authorized.json()))
        with self.assertRaises(ValueError):
            LocalAuth(srv.mcp.streamable_http_app(), "weak", self.agent)

    async def test_ssh_failure_is_sanitized_and_audited(self):
        with patch.object(Router, "ssh_params", return_value=StdioServerParameters(
            command="/usr/bin/false", args=[]
        )):
            with self.assertRaisesRegex(ValueError, "Ошибка SSH/MCP"):
                await self.router.call("system_info", {}, "rock")
        results = [row["result"] for row in self.agent.audit.tail(10)]
        self.assertIn("remote_error", results)

    async def test_load_peer_config(self):
        path = Path(self.tmp.name) / "config.json"
        path.write_text(json.dumps({
            "device_id": "terra", "root": str(self.root),
            "state": str(self.state), "role": "viewer",
            "peers": {"rock": {
                "target": "rock-admin", "host_name": "100.101.102.103",
                "python": "/usr/bin/python3", "config_path": "/home/agent.json"
            }}
        }))
        os.chmod(path, 0o600)
        self.assertEqual(load_config(path).peers["rock"].target, "rock-admin")

    async def test_remote_stdio_mcp_roundtrip_without_network(self):
        # Локальный stdio процесс моделирует SSH-перенаправление без реальной сети.
        basedir = Path(self.tmp.name)
        rockroot = basedir / "rock-sandbox"
        rockstate = basedir / "rock-state"
        rockconfig = basedir / "rock.json"
        rockconfig.write_text(json.dumps({
            "device_id": "rock", "root": str(rockroot), "state": str(rockstate),
            "role": "viewer", "commands": {},
        }))
        os.chmod(rockconfig, 0o600)
        with patch.object(Router, "ssh_params", return_value=StdioServerParameters(
            command=sys.executable, args=["-m", "openremote_agent.server",
                                          "--config", str(rockconfig)]
        )):
            info = await self.router.call("system_info", {}, "rock")
            self.assertEqual(info["device_id"], "rock")
            files = await self.router.call("list_files", {"path": "."}, "rock")
            self.assertEqual(files["entries"], [])
            with self.assertRaisesRegex(ValueError, "Ошибка SSH/MCP"):
                await self.router.call("write_text", {"path": "fail", "content": "x"}, "rock")
        self.assertFalse((rockroot / "fail").exists())


if __name__ == "__main__":
    unittest.main()
