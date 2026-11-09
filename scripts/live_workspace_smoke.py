"""Live OpenRemote workspaces test: staged terra MCP -> rock MCP over SSH.

Creates a uniquely named test file under rock/OpenRemote-agent-v0.3.
Does not touch SmartStation. Test file can be removed after manual verification.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import secrets
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    config = Path(os.environ.get(
        "OPENREMOTE_SMOKE_CONFIG",
        str(Path.home() / ".config/openremote-agent/terra-v0.3-test.json"),
    ))
    if not config.is_file():
        raise RuntimeError("Private OpenRemote v0.3 test config not found")
    result = {}
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openremote_agent.server", "--config", str(config)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async def call(tool, **kwargs):
                response = await session.call_tool(tool, kwargs)
                if response.isError or not isinstance(response.structuredContent, dict):
                    raise AssertionError(f"{tool} failed: {response.content}")
                return response.structuredContent

            for target in ("terra", "rock"):
                workspaces = await call("list_workspaces", device_id=target)
                assert any(item["name"] == "projects" and item["access"] == "write"
                           for item in workspaces["workspaces"]), workspaces
                listing = await call("list_files", device_id=target, workspace="projects")
                assert isinstance(listing["entries"], list)
                result[target + "_project_area"] = "read/write, listed"

            path = "OpenRemote-agent-v0.3/.workspace-smoke-" + secrets.token_hex(8) + ".txt"
            created = await call("write_text", device_id="rock", workspace="projects",
                                 path=path, content="OpenRemote v0.3 initial\n")
            assert created["created"]
            before = await call("read_text", device_id="rock", workspace="projects", path=path)
            assert before["content"] == "OpenRemote v0.3 initial\n"
            changed = await call("replace_text", device_id="rock", workspace="projects",
                                 path=path, expected_sha256=before["sha256"],
                                 content="OpenRemote v0.3 updated\n")
            assert changed["replaced"]
            after = await call("read_text", device_id="rock", workspace="projects", path=path)
            assert after["sha256"] == changed["sha256"]
            assert after["content"] == "OpenRemote v0.3 updated\n"
            result["write_replace_read_rock"] = "pass"

            stale = await session.call_tool("replace_text", {
                "device_id": "rock", "workspace": "projects", "path": path,
                "expected_sha256": before["sha256"], "content": "stale\n",
            })
            assert stale.isError
            result["stale_hash_rejected"] = True

            blocked = await session.call_tool("read_text", {
                "device_id": "rock", "workspace": "projects",
                "path": "../.ssh/authorized_keys",
            })
            assert blocked.isError
            result["traversal_denied"] = True
            result["test_path_for_cleanup"] = path
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
