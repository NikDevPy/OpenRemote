"""GUI backend safety and read-only status tests (no Qt dependency)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from openremote_gui.backend import Backend, _health


class GuiBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.root = base / "sandbox"
        self.state = base / "state"
        self.root.mkdir(mode=0o700)
        self.state.mkdir(mode=0o700)
        self.config = base / "agent.json"
        self.config.write_text(json.dumps({
            "device_id": "test-gateway",
            "root": str(self.root),
            "state": str(self.state),
            "role": "viewer",
            "ad_hoc_enabled": False,
            "commands": {},
            "workspaces": {},
            "peers": {},
        }))
        self.config.chmod(0o600)
        self.backend = Backend(self.config)

    def tearDown(self):
        self.temp.cleanup()

    def test_local_snapshot_without_network_or_secret_exposure(self):
        jobs = self.state / "jobs" / ("e" * 32)
        jobs.mkdir(parents=True)
        (jobs / "meta.json").write_text(json.dumps({
            "worker_pid": -1, "worker_start_ticks": None,
            "started_at": int(time.time()), "task_label": "Build A",
            "argv": ["secret-do-not-display"],
        }))
        (jobs / "result.json").write_text(json.dumps({
            "status": "completed", "exit_code": 0,
        }))
        transfers = self.state / "uploads"
        transfers.mkdir()
        (transfers / ("f" * 32 + ".json")).write_text(json.dumps({
            "upload_id": "f" * 32, "path": "test.bin",
            "received": 32768, "total_size": 65536,
        }))
        with patch("openremote_gui.backend._health", return_value=True) as health:
            snapshot = self.backend.snapshot()
            health.assert_called_once()
        self.assertEqual(snapshot["local_id"], "test-gateway")
        self.assertEqual(snapshot["jobs"][0]["status"], "completed")
        self.assertEqual(snapshot["jobs"][0]["label"], "Build A")
        self.assertEqual(snapshot["uploads"][0]["received"], 32768)
        self.assertNotIn("secret-do-not-display", json.dumps(snapshot))
        self.assertNotIn("argv", json.dumps(snapshot))

    def test_remote_probe_only_for_configured_devices(self):
        with self.assertRaises(ValueError):
            self.backend.probe_peer("unregistered")

    def test_health_url_must_be_loopback(self):
        for invalid in ("https://evil.example/readyz",
                        "http://127.0.0.1:18767/secret",
                        "http://user:pass@127.0.0.1:18767/readyz"):
            with self.assertRaises(ValueError):
                _health(invalid)

    def test_missing_config_rejected(self):
        with self.assertRaises((OSError, ValueError)):
            Backend(self.config.parent / "missing.json").snapshot()


if __name__ == "__main__":
    unittest.main()
