"""Test owner-enabled argv tool through real terra->rock MCP routing."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    cfg = Path(os.environ.get(
        "OPENREMOTE_SMOKE_CONFIG",
        str(Path.home() / ".config/openremote-agent/terra-v0.3-test.json"),
    ))
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "openremote_agent.server", "--config", str(cfg)],
    )
    statuses = {}
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            assert tools["run_argv"].annotations.destructiveHint
            assert tools["run_argv"].annotations.openWorldHint
            for device in ("terra", "rock"):
                r = await session.call_tool("run_argv", {
                    "device_id": device, "workspace": "projects",
                    "argv": ["/usr/bin/id", "-un"], "timeout": 10,
                })
                if r.isError:
                    raise AssertionError(f"{device}: {r.content}")
                assert r.structuredContent["exit_code"] == 0
                statuses[device] = {
                    "account": r.structuredContent["stdout"].strip(),
                    "exit_code": r.structuredContent["exit_code"],
                }
            refused = await session.call_tool("run_argv", {
                "device_id": "unknown", "argv": ["/usr/bin/id"], "timeout": 5,
            })
            assert refused.isError
            statuses["unregistered_device_denied"] = True
    print(json.dumps(statuses, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
