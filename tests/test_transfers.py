"""Bounded, resumable binary upload and download tests."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from openremote_agent.config import Config
from openremote_agent.engine import Agent
from openremote_agent.transfers import MAX_CHUNK


class TransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.config = Config("local", base / "sandbox", base / "state", role="operator")
        self.agent = Agent(self.config)

    def tearDown(self):
        self.agent.close()
        self.temp.cleanup()

    def begin(self, name, data, expected=None):
        return self.agent.begin_upload(name, len(data), hashlib.sha256(data).hexdigest(),
                                       expected_existing_sha256=expected)["upload_id"]

    def send(self, upload_id, raw, step=32768):
        for offset in range(0, len(raw), step):
            self.agent.upload_chunk(upload_id, offset,
                                    base64.b64encode(raw[offset:offset + step]).decode())

    def test_binary_roundtrip_persists_after_session_reconnect(self):
        data = os.urandom(1024 * 1024 + 517)
        upload_id = self.begin("large.bin", data)
        self.send(upload_id, data[:450000], step=50000)
        self.agent.close()
        self.agent = Agent(self.config)
        self.assertEqual(self.agent.upload_status(upload_id)["received"], 450000)
        self.send_remaining(upload_id, data, 450000)
        finished = self.agent.finish_upload(upload_id)
        self.assertTrue(finished["completed"])
        self.assertEqual(finished["sha256"], hashlib.sha256(data).hexdigest())
        info = self.agent.binary_file_info("large.bin")
        self.assertEqual(info["size"], len(data))
        self.assertEqual(info["sha256"], finished["sha256"])
        chunks = []
        for offset in range(0, len(data), MAX_CHUNK):
            result = self.agent.read_binary_chunk("large.bin", offset=offset, max_bytes=MAX_CHUNK)
            chunks.append(base64.b64decode(result["data_base64"]))
        self.assertEqual(b"".join(chunks), data)

    def send_remaining(self, upload_id, raw, beginning):
        for offset in range(beginning, len(raw), 65536):
            self.agent.upload_chunk(upload_id, offset,
                                    base64.b64encode(raw[offset:offset + 65536]).decode())

    def test_reject_invalid_offset_and_hash_without_overwrite(self):
        data = b"abcd" * 8000
        upload_id = self.begin("data.bin", data)
        with self.assertRaises(ValueError):
            self.agent.upload_chunk(upload_id, 20, base64.b64encode(data).decode())
        self.agent.upload_chunk(upload_id, 0, base64.b64encode(data).decode())
        with self.assertRaises(ValueError):
            self.agent.upload_chunk(upload_id, 0, "QQ==")
        with self.assertRaises(ValueError):
            self.agent.finish_upload(upload_id + "other")
        self.assertTrue(self.agent.finish_upload(upload_id)["completed"])
        with self.assertRaises(ValueError):
            self.begin("data.bin", data)
        replacement = b"new data"
        wrong_old = "0" * 64
        second = self.begin("data.bin", replacement, expected=wrong_old)
        self.send(second, replacement)
        with self.assertRaises(ValueError):
            self.agent.finish_upload(second)
        self.assertEqual((self.config.root / "data.bin").read_bytes(), data)
        self.assertTrue(self.agent.cancel_upload(second)["cancelled"])

    def test_replace_with_expected_hash_and_cancel(self):
        old = b"existing" * 4000
        (self.config.root / "orig.bin").write_bytes(old)
        current = hashlib.sha256(old).hexdigest()
        payload = os.urandom(300000)
        upload_id = self.begin("orig.bin", payload, expected=current)
        self.send(upload_id, payload)
        finish = self.agent.finish_upload(upload_id)
        self.assertTrue(finish["completed"])
        self.assertEqual((self.config.root / "orig.bin").read_bytes(), payload)
        abandoned = self.begin("unwanted.bin", b"1234")
        self.agent.upload_chunk(abandoned, 0, base64.b64encode(b"1234").decode())
        self.agent.cancel_upload(abandoned)
        self.assertFalse((self.config.root / "unwanted.bin").exists())

    def test_incomplete_symlink_and_policy_rejections(self):
        data = b"hello world"
        upload_id = self.begin("incomplete.bin", data)
        with self.assertRaises(ValueError):
            self.agent.finish_upload(upload_id)
        self.agent.cancel_upload(upload_id)
        (self.config.root / "link").symlink_to(self.config.state, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            self.agent.begin_upload("link/escape.bin", 0, hashlib.sha256(b"").hexdigest())
        with self.assertRaises(ValueError):
            self.agent.begin_upload("../escape.bin", 0, hashlib.sha256(b"").hexdigest())
        with self.assertRaises(ValueError):
            self.agent.begin_upload("empty.bin", 1, "invalid-hash")

    def test_chunk_boundaries(self):
        data = b"q" * (MAX_CHUNK + 1)
        upload_id = self.begin("capped.bin", data)
        with self.assertRaises(ValueError):
            self.agent.upload_chunk(upload_id, 0, base64.b64encode(data).decode())
        with self.assertRaises(ValueError):
            self.agent.upload_chunk(upload_id, 0, "not-base64!!")
        with self.assertRaises(ValueError):
            self.agent.read_binary_chunk("missing.bin", max_bytes=MAX_CHUNK + 1)
        self.agent.cancel_upload(upload_id)

    def test_expired_upload_can_be_cancelled_and_pruned(self):
        import json
        import time
        upload_id = self.begin("stale.bin", b"x" * 8)
        manifest = self.config.state / "uploads" / (upload_id + ".json")
        data = json.loads(manifest.read_text())
        data["expires_at"] = int(time.time()) - 1
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.agent.upload_status(upload_id)
        # A new upload automatically prunes an expired upload.
        newer = self.begin("fresh.bin", b"fresh")
        self.assertFalse(manifest.exists())
        self.agent.cancel_upload(newer)

    def test_upload_requires_operator(self):
        self.agent.close()
        self.agent = Agent(Config("local", self.config.root, self.config.state, role="viewer"))
        with self.assertRaises(PermissionError):
            self.agent.begin_upload("unwanted.bin", 1, hashlib.sha256(b"x").hexdigest())


if __name__ == "__main__":
    unittest.main()
