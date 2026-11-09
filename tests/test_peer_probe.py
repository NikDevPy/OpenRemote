"""On-demand peer health discovery and cross-chat cache."""
from __future__ import annotations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from openremote_agent.config import Config, SSHPeer
from openremote_agent.engine import Agent
from openremote_agent.router import Router


class PeerProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        peer = SSHPeer("trusted-alias", "100.64.1.23",
                       "/usr/bin/python3", "/tmp/example-agent.json")
        self.agent = Agent(Config("gateway", base / "sandbox", base / "state",
                                  peers={"peer": peer}))
        self.router = Router(self.agent)

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    async def test_cached_verified_device_probe(self):
        remote = AsyncMock(return_value={"device_id": "peer", "online": True})
        self.router._remote_call = remote
        first = await self.router.probe_device("peer")
        self.assertTrue(first["online"])
        self.assertFalse(first["cached"])
        second = await self.router.probe_device("peer")
        self.assertTrue(second["online"])
        self.assertTrue(second["cached"])
        self.assertEqual(remote.await_count, 1)
        fresh = await self.router.probe_device("peer", refresh=True)
        self.assertTrue(fresh["online"])
        self.assertEqual(remote.await_count, 2)

    async def test_identity_mismatch_is_not_online(self):
        self.router._remote_call = AsyncMock(
            return_value={"device_id": "another-device", "online": True})
        result = await self.router.probe_device("peer")
        self.assertFalse(result["online"])
        self.assertFalse(result["cached"])

    async def test_unknown_device_rejected(self):
        with self.assertRaises(ValueError):
            await self.router.probe_device("unregistered")


if __name__ == "__main__":
    unittest.main()
