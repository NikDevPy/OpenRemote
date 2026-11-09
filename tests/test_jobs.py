"""Persistent local job tests. All subprocesses are harmless and bounded."""
from __future__ import annotations
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from openremote_agent.config import Config
from openremote_agent.engine import Agent


class JobsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.config = Config("test-host", base / "sandbox", base / "state",
                             role="admin", ad_hoc_enabled=True)
        self.agent = Agent(self.config)
        self.ids = []

    def tearDown(self):
        for job_id in self.ids:
            try:
                self.agent.cancel_job(job_id)
            except (ValueError, OSError):
                pass
        self.agent.close()
        self.temp.cleanup()

    def start(self, argv, timeout=10):
        info = self.agent.start_job(argv, timeout_seconds=timeout)
        self.ids.append(info["job_id"])
        return info["job_id"]

    async def wait_job(self, job_id, seconds=8):
        for _ in range(int(seconds / 0.1)):
            status = self.agent.job_status(job_id)
            if status["status"] not in ("starting", "running", "cancelling"):
                return status
            await asyncio.sleep(.1)
        self.fail("Job did not finish within test window")

    async def test_job_persists_after_closing_agent(self):
        job_id = self.start([sys.executable, "-c",
                             "import time; print('BEGIN',flush=True); time.sleep(.6); print('DONE',flush=True)"])
        self.agent.close()
        self.agent = Agent(self.config)
        result = await self.wait_job(job_id)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(result["exit_code"], 0)
        first = self.agent.job_output(job_id, limit_bytes=8)
        second = self.agent.job_output(job_id, offset=first["next_offset"])
        self.assertIn("BEGIN", first["text"] + second["text"])
        self.assertIn("DONE", first["text"] + second["text"])
        self.assertEqual(self.agent.list_jobs()["jobs"][0]["job_id"], job_id)

    async def test_cancellation_does_not_kill_unrelated_pid(self):
        job_id = self.start(["/usr/bin/sleep", "30"])
        await asyncio.sleep(.3)
        response = self.agent.cancel_job(job_id)
        self.assertTrue(response["signal_sent"], response)
        result = await self.wait_job(job_id)
        self.assertEqual(result["status"], "cancelled")
        self.assertFalse(self.agent.cancel_job(job_id)["signal_sent"])

    async def test_timeout(self):
        job_id = self.start(["/usr/bin/sleep", "10"], timeout=1)
        result = await self.wait_job(job_id)
        self.assertEqual(result["status"], "timed_out", result)

    async def test_log_bounding(self):
        job_id = self.start([sys.executable, "-c",
                             "import sys;sys.stdout.write('z'*(9*1024*1024))"])
        result = await self.wait_job(job_id)
        self.assertEqual(result["status"], "completed", result)
        self.assertTrue(result["stdout_truncated"])
        self.assertEqual(result["stdout_bytes"], 8 * 1024 * 1024)
        last = self.agent.job_output(job_id, offset=8 * 1024 * 1024 - 10, limit_bytes=128)
        self.assertEqual(len(last["text"]), 10)

    async def test_purge_completed_jobs(self):
        job_id = self.start(["/usr/bin/true"])
        await self.wait_job(job_id)
        result = self.agent.purge_job(job_id)
        self.assertTrue(result["removed"])
        self.ids.remove(job_id)
        with self.assertRaises(ValueError):
            self.agent.job_status(job_id)

    async def test_reject_unauthorized_or_invalid(self):
        self.agent.close()
        self.agent = Agent(Config("test-host", self.config.root, self.config.state, role="admin"))
        with self.assertRaises(PermissionError):
            self.agent.start_job(["/usr/bin/sleep", "1"])
        self.agent.close()
        self.agent = Agent(self.config)
        with self.assertRaises(ValueError):
            self.agent.start_job(["sleep", "1"])
        with self.assertRaises(ValueError):
            self.agent.start_job(["/usr/bin/sleep", "1"], timeout_seconds=0)
        for bad in ("bogus", "../" + "0"*32):
            with self.assertRaises(ValueError):
                self.agent.job_status(bad)
            with self.assertRaises(ValueError):
                self.agent.cancel_job(bad)
        with self.assertRaises(ValueError):
            self.agent.job_output("0"*32, stream="not-a-stream")


if __name__ == "__main__":
    unittest.main()
