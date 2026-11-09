"""Multi-chat concurrency: isolated MCP processes sharing one device state.

Uses many independent Agent instances to model simultaneous ChatGPT chats.
All files and jobs are temporary. No real devices or services are changed.
"""
from __future__ import annotations

import hashlib
import multiprocessing as mp
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

from openremote_agent.config import Config
from openremote_agent.engine import Agent


def worker_replace(root, state, gate, out, index, expected):
    try:
        agent = Agent(Config("multi-chat", Path(root), Path(state), role="operator"))
        try:
            gate.wait(timeout=12)
            value = agent.replace_text("shared.txt", "writer-" + str(index), expected)
            out.put(("ok", index, value["sha256"]))
        finally:
            agent.close()
    except Exception as exc:
        out.put(("error", index, type(exc).__name__))


def worker_read(root, state, gate, out, index):
    try:
        agent = Agent(Config("multi-chat", Path(root), Path(state), role="viewer"))
        try:
            gate.wait(timeout=12)
            info = agent.system_info()
            files = agent.list_files()
            out.put(("ok", index, info["device_id"] == "multi-chat"
                     and isinstance(files["entries"], list)))
        finally:
            agent.close()
    except Exception as exc:
        out.put(("error", index, type(exc).__name__))


def worker_job(root, state, gate, out, index):
    try:
        agent = Agent(Config("multi-chat", Path(root), Path(state),
                             role="admin", ad_hoc_enabled=True))
        try:
            gate.wait(timeout=12)
            info = agent.start_job(["/usr/bin/sleep", "10"],
                                   timeout_seconds=15,
                                   task_label="chat-" + str(index),
                                   idempotency_key="request-chat-" + str(index).zfill(4))
            out.put(("ok", index, info["job_id"]))
        finally:
            agent.close()
    except Exception as exc:
        out.put(("error", index, type(exc).__name__))


def worker_slot(root, state, gate, out, index):
    from openremote_agent.coordination import Coordinator
    try:
        coordinator = Coordinator(Path(state))
        gate.wait(timeout=12)
        with coordinator.slot("capacity-test", limit=3, timeout=0.2):
            time.sleep(0.65)
        out.put(("ok", index, True))
    except Exception as exc:
        out.put(("error", index, type(exc).__name__))


def worker_alias(root, state, gate, out, index, expected):
    from openremote_agent.config import Workspace
    try:
        target = Path(root)
        config = Config("multi-chat", target, Path(state), role="operator",
                        workspaces={"one": Workspace(target, "write"),
                                    "two": Workspace(target, "write")})
        agent = Agent(config)
        try:
            gate.wait(timeout=12)
            chosen = "one" if index % 2 else "two"
            result = agent.replace_text("shared.txt", "aliased-" + str(index),
                                        expected, workspace=chosen)
            out.put(("ok", index, result["sha256"]))
        finally:
            agent.close()
    except Exception as exc:
        out.put(("error", index, type(exc).__name__))


@unittest.skipUnless(sys.platform == "linux", "Linux concurrency semantics")
class ConcurrentChatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="openremote-concurrency-")
        self.root = Path(self.tmp.name) / "sandbox"
        self.state = Path(self.tmp.name) / "state"
        self.root.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.ctx = mp.get_context("fork")

    def tearDown(self):
        self.tmp.cleanup()

    def run_workers(self, worker, count, *params):
        gate = self.ctx.Barrier(count)
        out = self.ctx.Queue()
        jobs = [self.ctx.Process(
            target=worker,
            args=(str(self.root), str(self.state), gate, out, i, *params),
        ) for i in range(count)]
        for proc in jobs:
            proc.start()
        for proc in jobs:
            proc.join(timeout=22)
            if proc.is_alive():
                proc.kill()
                proc.join(timeout=3)
                self.fail("Hung simulated ChatGPT request")
            self.assertEqual(proc.exitcode, 0)
        result = [out.get(timeout=3) for _ in jobs]
        out.close()
        out.join_thread()
        return result

    def test_read_only_parallel_requests(self):
        outcomes = self.run_workers(worker_read, 12)
        self.assertEqual(len(outcomes), 12)
        self.assertEqual([x for x in outcomes if x[0] != "ok" or not x[2]], [])

    def test_same_file_conflict_only_one_winner(self):
        (self.root / "shared.txt").write_text("before", encoding="utf-8")
        old_hash = hashlib.sha256(b"before").hexdigest()
        outcomes = self.run_workers(worker_replace, 8, old_hash)
        winners = [x for x in outcomes if x[0] == "ok"]
        rejects = [x for x in outcomes if x[0] == "error"]
        self.assertEqual(len(winners), 1, outcomes)
        self.assertEqual(len(rejects), 7, outcomes)
        self.assertTrue(all(x[2] == "ValueError" for x in rejects), outcomes)
        self.assertEqual((self.root / "shared.txt").read_text(), "writer-" + str(winners[0][1]))

    def test_direct_command_capacity_is_cross_process(self):
        outcome = self.run_workers(worker_slot, 7)
        success = [x for x in outcome if x[0] == "ok"]
        blocked = [x for x in outcome if x[0] == "error"]
        self.assertEqual(len(success), 3, outcome)
        self.assertEqual(len(blocked), 4, outcome)
        self.assertTrue(all(x[2] == "TimeoutError" for x in blocked), outcome)

    def test_workspace_aliases_share_same_file_lock(self):
        (self.root / "shared.txt").write_text("baseline", encoding="utf-8")
        old_hash = hashlib.sha256(b"baseline").hexdigest()
        outcome = self.run_workers(worker_alias, 8, old_hash)
        self.assertEqual(len([x for x in outcome if x[0] == "ok"]), 1, outcome)
        self.assertEqual(len([x for x in outcome if x[0] == "error"]), 7, outcome)

    def test_job_slots_across_processes_and_replay(self):
        outcomes = self.run_workers(worker_job, 11)
        ok = [x for x in outcomes if x[0] == "ok"]
        rejected = [x for x in outcomes if x[0] == "error"]
        self.assertEqual(len(ok), 8, outcomes)
        self.assertEqual(len(rejected), 3, outcomes)
        self.assertTrue(all(x[2] == "ValueError" for x in rejected), rejected)
        agent = Agent(Config("multi-chat", self.root, self.state,
                             role="admin", ad_hoc_enabled=True))
        try:
            same = agent.start_job(["/usr/bin/sleep", "10"], timeout_seconds=15,
                                   task_label="chat-" + str(ok[0][1]),
                                   idempotency_key="request-chat-" + str(ok[0][1]).zfill(4))
            self.assertTrue(same["reused"])
            self.assertEqual(same["job_id"], ok[0][2])
            with self.assertRaises(ValueError):
                agent.start_job(["/usr/bin/false"], timeout_seconds=15,
                                task_label="chat-" + str(ok[0][1]),
                                idempotency_key="request-chat-" + str(ok[0][1]).zfill(4))
        finally:
            for _, _, jid in ok:
                try:
                    agent.cancel_job(jid)
                except (OSError, ValueError):
                    pass
            for _ in range(35):
                remaining = [j for j in agent.list_jobs(limit=30)["jobs"]
                             if j["status"] in ("starting", "running", "cancelling")]
                if not remaining:
                    break
                time.sleep(0.1)
            agent.close()


if __name__ == "__main__":
    unittest.main()
