"""Local tunnel monitor tests: no real service is ever restarted."""
from __future__ import annotations

import http.server
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch, Mock

from openremote_agent.tunnel_health import check, parse_readyz


class ReadyHandler(http.server.BaseHTTPRequestHandler):
    status = 200

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()
        self.wfile.write(b"ready" if self.status == 200 else b"unavailable")

    def log_message(self, *_args):
        return


class TunnelHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "state"
        self.server = http.server.HTTPServer(("127.0.0.1", 0), ReadyHandler)
        self.url = "http://127.0.0.1:%d/readyz" % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def test_validates_loopback_and_no_redirects(self):
        self.assertEqual(parse_readyz(self.url)[0], "127.0.0.1")
        for url in ("https://127.0.0.1:9999/readyz",
                    "http://example.com:1234/readyz",
                    "http://127.0.0.1:1234/admin",
                    "http://127.0.0.1:1234/readyz?token=secret"):
            with self.assertRaises(ValueError):
                parse_readyz(url)

    def test_persistent_failure_count_and_recovery(self):
        ReadyHandler.status = 503
        a = check(self.url, self.state)
        b = check(self.url, self.state)
        self.assertFalse(a["healthy"])
        self.assertEqual(b["consecutive_failures"], 2)
        ReadyHandler.status = 200
        c = check(self.url, self.state)
        self.assertTrue(c["healthy"])
        self.assertEqual(c["consecutive_failures"], 0)
        self.assertTrue((self.state / "status.json").exists())

    @patch("openremote_agent.tunnel_health.subprocess.run")
    def test_restart_only_after_threshold_with_cooldown(self, restart):
        restart.return_value = Mock(returncode=0)
        ReadyHandler.status = 503
        x = check(self.url, self.state, allow_restart=True, failures=2)
        self.assertFalse(x["restarted"])
        y = check(self.url, self.state, allow_restart=True, failures=2)
        self.assertTrue(y["restarted"])
        z = check(self.url, self.state, allow_restart=True, failures=2)
        self.assertFalse(z["restarted"])
        self.assertEqual(restart.call_count, 1)
        self.assertEqual(restart.call_args.args[0],
                         ["systemctl", "--user", "restart", "openremote-agent-tunnel.service"])

    def test_rejects_arbitrary_restart_target(self):
        with self.assertRaises(ValueError):
            check(self.url, self.state, service="ssh.service", allow_restart=True)


if __name__ == "__main__":
    unittest.main()
