"""Regression: pending commands must not reserve all global OS command slots."""
import asyncio
from contextlib import contextmanager
from pathlib import Path
import tempfile
import unittest

from openremote_agent.config import Config
from openremote_agent.engine import Agent


class CommandAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_mixed_commands_do_not_hold_process_slots(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            agent = Agent(Config("admission-test", root / "root", root / "state",
                                 role="admin", ad_hoc_enabled=True))
            try:
                busy = 0
                maximum = 0
                started = []

                @contextmanager
                def slot(namespace, limit=4, timeout=2.0):
                    nonlocal busy, maximum
                    self.assertEqual(namespace, "direct-commands")
                    busy += 1
                    maximum = max(maximum, busy)
                    try:
                        yield
                    finally:
                        busy -= 1

                async def fake_run(*args, **kwargs):
                    started.append("run")
                    await asyncio.sleep(0.025)
                    return {"exit_code": 0}

                async def fake_approved(*args, **kwargs):
                    started.append("approved")
                    await asyncio.sleep(0.025)
                    return {"exit_code": 0}

                agent.coordinator.slot = slot
                agent._run_argv_inner = fake_run
                agent._execute_command_inner = fake_approved
                results = await asyncio.gather(*[
                    agent.run_argv(["/usr/bin/true"]) if i % 2 else
                    agent.execute_command("python-version")
                    for i in range(8)
                ])
                self.assertTrue(all(r["exit_code"] == 0 for r in results))
                self.assertEqual(len(started), 8)
                self.assertEqual(maximum, 1, "Queued commands must not hoard global slots")
                self.assertEqual(busy, 0)
            finally:
                agent.close()


if __name__ == "__main__":
    unittest.main()
