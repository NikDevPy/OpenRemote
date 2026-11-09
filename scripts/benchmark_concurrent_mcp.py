"""Repeatable concurrent-client MCP benchmark (no ChatGPT hosted traffic).

Runs independent stdio MCP sessions that share one private temporary audit
database, exercising the same agent concurrency paths as independent chats.
Read-only by default. Optional trusted SSH peer checks are also read-only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def request(params, target):
    started = time.perf_counter()
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as client:
            await client.initialize()
            for name, args in [
                ("system_info", {"device_id": target}),
                ("list_workspaces", {"device_id": target}),
                ("list_files", {"device_id": target,
                                "workspace": "sandbox", "path": "."}),
            ]:
                result = await client.call_tool(name, args)
                if result.isError:
                    raise RuntimeError(name + " was refused")
    return time.perf_counter() - started


async def run(config_path, target, sessions):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openremote_agent.server", "--config", str(config_path),
              "--transport", "stdio"],
    )
    started = time.perf_counter()
    results = await asyncio.gather(
        *(request(params, target) for _ in range(sessions)),
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - started
    good = [x for x in results if not isinstance(x, BaseException)]
    errors = [type(x).__name__ for x in results if isinstance(x, BaseException)]
    return {
        "target": target, "concurrent_sessions": sessions,
        "tool_calls": 3 * sessions, "success": len(good),
        "errors": errors[:5], "elapsed_seconds": round(elapsed, 3),
        "median_session_seconds": round(statistics.median(good), 3) if good else None,
        "max_session_seconds": round(max(good), 3) if good else None,
    }


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--config", type=Path, required=True)
    cli.add_argument("--target", default="terra")
    cli.add_argument("--sessions", type=int, default=8)
    args = cli.parse_args()
    if not 1 <= args.sessions <= 12:
        cli.error("Use 1–12 synthetic clients; avoid overloading a real host")
    original = json.loads(args.config.read_text())
    with tempfile.TemporaryDirectory(prefix="openremote-mcp-bench-") as tmp:
        folder = Path(tmp)
        state = folder / "state"
        root = folder / "sandbox"
        state.mkdir(mode=0o700)
        root.mkdir(mode=0o700)
        original["state"] = str(state)
        original["root"] = str(root)
        conf = folder / "config.json"
        conf.write_text(json.dumps(original))
        conf.chmod(0o600)
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        result = asyncio.run(run(conf, args.target, args.sessions))
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        result["child_cpu_seconds"] = round(
            after.ru_utime + after.ru_stime - before.ru_utime - before.ru_stime, 3)
        result["max_one_child_rss_mib"] = round(after.ru_maxrss / 1024, 1)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if not result["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
