"""Live staged v0.4 smoke: job survives SSH/MCP disconnect; binary transfers.

Uses authorized terra -> rock SSH, isolated v0.4 state, and a temporary test
file in the rock OpenRemote sandbox. No SmartStation files are changed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    cfg = Path(os.environ.get(
        "OPENREMOTE_SMOKE_CONFIG",
        str(Path.home() / ".config/openremote-agent/terra-v0.4-test.json"),
    ))
    out = {}
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "openremote_agent.server", "--config", str(cfg)]
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            names = {x.name for x in (await client.list_tools()).tools}
            assert len(names) == 24, names
            out["tools"] = len(names)

            async def call(name, **kwargs):
                reply = await client.call_tool(name, kwargs)
                if reply.isError or not isinstance(reply.structuredContent, dict):
                    raise RuntimeError(f"{name} was rejected: {str(reply.content)[:180]}")
                return reply.structuredContent

            # Tool call closes its own SSH connection as soon as it returns.
            job = await call("start_job", device_id="rock", workspace="sandbox",
                             argv=["/usr/bin/python3", "-c",
                                   "import time;print('job-started',flush=True);"
                                   "time.sleep(4);print('job-finished',flush=True)"],
                             timeout_seconds=15)
            job_id = job["job_id"]
            started = await call("job_status", device_id="rock", job_id=job_id)
            out["remote_job_started"] = started["status"] in ("running", "completed")
            for _ in range(30):
                state = await call("job_status", device_id="rock", job_id=job_id)
                if state["status"] not in ("running", "starting", "cancelling"):
                    break
                await asyncio.sleep(.3)
            assert state["status"] == "completed" and state["exit_code"] == 0, state
            logs = await call("job_output", device_id="rock", job_id=job_id, limit_bytes=8192)
            assert "job-started" in logs["text"] and "job-finished" in logs["text"], logs
            out["remote_job_after_ssh_disconnect"] = "completed; output verified"

            cancellable = await call("start_job", device_id="rock", workspace="sandbox",
                                     argv=["/usr/bin/sleep", "30"], timeout_seconds=45)
            await asyncio.sleep(.5)
            cancellation = await call("cancel_job", device_id="rock", job_id=cancellable["job_id"])
            assert cancellation["signal_sent"], cancellation
            for _ in range(20):
                stopped = await call("job_status", device_id="rock", job_id=cancellable["job_id"])
                if stopped["status"] not in ("running", "cancelling", "starting"):
                    break
                await asyncio.sleep(.3)
            assert stopped["status"] == "cancelled", stopped
            out["remote_job_cancelled"] = True

            # Create a 270+KiB binary in the separate sandbox, then download
            # chunks and compare every byte. No MCP response contains the test bytes.
            payload = os.urandom(274513)
            name = "openremote-v04-smoke-" + secrets.token_hex(8) + ".bin"
            digest = hashlib.sha256(payload).hexdigest()
            upload = await call("begin_upload", device_id="rock", workspace="sandbox",
                                path=name, total_size=len(payload), sha256=digest)
            try:
                for offset in range(0, len(payload), 65536):
                    await call("upload_chunk", device_id="rock", upload_id=upload["upload_id"],
                               offset=offset,
                               data_base64=base64.b64encode(payload[offset:offset + 65536]).decode())
                status = await call("upload_status", device_id="rock", upload_id=upload["upload_id"])
                assert status["received"] == len(payload), status
                finish = await call("finish_upload", device_id="rock", upload_id=upload["upload_id"])
                assert finish["completed"] and finish["sha256"] == digest, finish
                info = await call("binary_file_info", device_id="rock", workspace="sandbox", path=name)
                assert info["sha256"] == digest and info["size"] == len(payload), info
                chunks = []
                for offset in range(0, len(payload), 131072):
                    chunk = await call("read_binary_chunk", device_id="rock", workspace="sandbox",
                                       path=name, offset=offset, max_bytes=131072)
                    chunks.append(base64.b64decode(chunk["data_base64"]))
                assert b"".join(chunks) == payload
                out["binary_file_verified_bytes"] = len(payload)
            finally:
                # Clean the test file only, not any unrelated project content.
                cleanup = await call("run_argv", device_id="rock", workspace="sandbox",
                                     argv=["/usr/bin/rm", "-f", "--", name], timeout=10)
                assert cleanup["exit_code"] == 0

            local = await call("start_job", device_id="terra", workspace="sandbox",
                               argv=["/usr/bin/uname", "-s"], timeout_seconds=10)
            for _ in range(15):
                state = await call("job_status", device_id="terra", job_id=local["job_id"])
                if state["status"] not in ("running", "starting", "cancelling"):
                    break
                await asyncio.sleep(.2)
            assert state["status"] == "completed", state
            out["terra_job"] = "completed"

    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
