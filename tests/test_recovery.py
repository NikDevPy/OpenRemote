"""Durable task recovery, bounded snapshots and gateway routing."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock

from openremote_agent.config import Config, SSHPeer
from openremote_agent.engine import Agent
from openremote_agent.router import Router


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.config = Config("test-host", base / "sandbox", base / "state",
                             role="admin", ad_hoc_enabled=True)
        self.agent = Agent(self.config)
        self.jobs = []

    def tearDown(self):
        for job_id in self.jobs:
            try:
                self.agent.cancel_job(job_id)
            except (OSError, ValueError):
                pass
        self.agent.close()
        self.temp.cleanup()

    async def _finished_job(self):
        started = self.agent.start_job([sys.executable, "-c",
            "print('HELLO', flush=True); print('WARN', file=__import__('sys').stderr, flush=True)"],
            timeout_seconds=12)
        job_id = started["job_id"]
        self.jobs.append(job_id)
        for _ in range(90):
            if self.agent.jobs.status(job_id)["status"] not in ("starting", "running", "cancelling"):
                return job_id
            await asyncio.sleep(.1)
        self.fail("Job did not finish")

    async def test_snapshot_survives_agent_restart_and_tails_are_bounded(self):
        job_id = await self._finished_job()
        self.agent.close()
        self.agent = Agent(self.config)
        result = self.agent.job_snapshot(job_id, tail_bytes=20)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("HELLO", result["stdout_tail"])
        self.assertIn("WARN", result["stderr_tail"])
        self.assertLessEqual(len(result["stdout_tail"].encode("utf-8")), 20)
        self.assertGreaterEqual(result["elapsed_seconds"], 0)
        with self.assertRaises(ValueError):
            self.agent.job_snapshot(job_id, tail_bytes=5000)

    async def test_checkpoint_retry_concurrency_and_resume(self):
        job_id = await self._finished_job()
        a = self.agent.task_checkpoint("smartstation-stage", "benchmark",
                                        summary="Nora/Shrek FPS comparison",
                                        job_id=job_id)
        self.assertEqual(a["revision"], 1)
        self.assertFalse(a["reused"])
        b = self.agent.task_checkpoint("smartstation-stage", "benchmark",
                                        summary="Nora/Shrek FPS comparison",
                                        job_id=job_id)
        self.assertEqual(b["revision"], 1)
        self.assertTrue(b["reused"])
        with self.assertRaisesRegex(ValueError, "revision conflict"):
            self.agent.task_checkpoint("smartstation-stage", "deploy",
                                       expected_revision=0)
        c = self.agent.task_checkpoint("smartstation-stage", "deploy",
                                       summary="Verified rollback",
                                       expected_revision=1, status="completed",
                                       job_id=job_id)
        self.assertEqual(c["revision"], 2)
        self.agent.close()
        self.agent = Agent(self.config)
        resumed = self.agent.task_resume("smartstation-stage")
        self.assertEqual(resumed["task"]["stage"], "deploy")
        self.assertEqual(resumed["job"]["status"], "completed")
        self.assertIn("HELLO", resumed["job"]["stdout_tail"])
        self.assertIn("smartstation-stage", [x["task_id"]
                                             for x in self.agent.list_tasks()["tasks"]])

    async def test_unknown_purged_job_can_still_resume(self):
        job_id = await self._finished_job()
        self.agent.task_checkpoint("recoverable", "collect", job_id=job_id)
        self.agent.purge_job(job_id)
        resumed = self.agent.task_resume("recoverable")
        self.assertTrue(resumed["job_missing"])
        self.assertIsNone(resumed["job"])

    async def test_checkpoint_invalid_values_and_permissions(self):
        for task_id in ("../oops", "/etc/passwd", "", "x" * 70):
            with self.assertRaises(ValueError):
                self.agent.task_checkpoint(task_id, "stage")
        with self.assertRaises(ValueError):
            self.agent.task_checkpoint("safe", "stage", summary="bad\nsecrets")
        with self.assertRaises(ValueError):
            self.agent.task_checkpoint("safe", "stage", job_id="not-a-job")
        with self.assertRaises(ValueError):
            self.agent.task_checkpoint("safe", "stage", expected_revision=True)
        base = Path(self.temp.name) / "readonly"
        guest = Agent(Config("guest", base / "sandbox", base / "state",
                             role="viewer"))
        try:
            with self.assertRaises(PermissionError):
                guest.list_tasks()
            with self.assertRaises(PermissionError):
                guest.task_checkpoint("safe", "stage")
        finally:
            guest.close()

    async def test_router_forwards_to_registered_terra_without_new_auth(self):
        peer = SSHPeer("terra", "terra.example", "/usr/bin/python3",
                       "/opt/openremote/terra.json")
        base = Path(self.temp.name) / "gateway"
        gateway = Agent(Config("gateway", base / "sandbox", base / "state",
                               role="admin", peers={"terra": peer}))
        try:
            router = Router(gateway)
            router._remote_call = AsyncMock(return_value={
                "task": {"task_id": "recover", "stage": "done"},
                "job": None, "job_missing": False, "operation_id": "test"})
            result = await router.call("task_resume",
                                       {"task_id": "recover", "tail_bytes": 128},
                                       device_id="terra")
            self.assertEqual(result["task"]["stage"], "done")
            called_peer, called_tool, called_args = router._remote_call.await_args.args
            self.assertEqual(called_peer, peer)
            self.assertEqual(called_tool, "task_resume")
            self.assertEqual(called_args["device_id"], "terra")
            self.assertEqual(called_args["tail_bytes"], 128)
        finally:
            gateway.close()

    async def test_router_local_tools(self):
        router = Router(self.agent)
        checkpoint = await router.call("task_checkpoint",
                                       {"task_id": "route", "stage": "ready"})
        result = await router.call("task_resume", {"task_id": "route"})
        self.assertEqual(result["task"]["revision"], checkpoint["revision"])


if __name__ == "__main__":
    unittest.main()
