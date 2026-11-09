"""Live end-to-end: terra stdio MCP -> trusted SSH/Tailscale peer MCP.

Only operates in OpenRemote's dedicated sandbox, never SmartStation data.
Requires local private configuration with one peer called rock.
"""
import asyncio
import json
import os
from pathlib import Path
import secrets
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    config = Path(os.environ.get(
        "OPENREMOTE_SMOKE_CONFIG", str(Path.home() / ".config/openremote-agent/terra.json")
    ))
    if not config.is_file():
        raise SystemExit("Private terra config not installed; no live test performed")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openremote_agent.server", "--config", str(config), "--transport", "stdio"],
    )
    results = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()

            async def call(tool, **args):
                result = await client.call_tool(tool, args)
                if result.isError or not isinstance(result.structuredContent, dict):
                    raise AssertionError(f"{tool} failed: {result.content!r}")
                return result.structuredContent

            devices = await call("list_devices")
            assert {d["device_id"] for d in devices["devices"]} == {"terra", "rock"}, devices
            results["registered"] = [d["device_id"] for d in devices["devices"]]

            rock = await call("system_info", device_id="rock")
            assert rock["device_id"] == "rock" and rock["os"] == "Linux", rock
            results["rock_system_info"] = {"os": rock["os"], "machine": rock["machine"]}

            policy = await call("preview_command", device_id="rock", command_id="python-version")
            assert policy["risk"] == "standard" and policy["requires_local_approval"] is False, policy
            results["rock_policy_preview"] = "standard"

            command = await call("execute_command", device_id="rock", command_id="python-version")
            assert command["exit_code"] == 0 and "Python" in command["stdout"], command
            results["rock_command"] = command["stdout"].strip()

            filename = f"openremote-smoke-{secrets.token_hex(8)}.txt"
            content = "OpenRemote SSH MCP smoke test. No SmartStation files touched.\n"
            created = await call("write_text", device_id="rock", path=filename, content=content)
            assert created["created"] and created["size"] == len(content.encode()), created
            read_back = await call("read_text", device_id="rock", path=filename)
            assert read_back["content"] == content, read_back
            listing = await call("list_files", device_id="rock", path=".")
            assert any(row["name"] == filename for row in listing["entries"]), listing
            results["rock_receipt_verified"] = True

            traversal = await client.call_tool("read_text", {
                "device_id": "rock", "path": "../rock.json"})
            assert traversal.isError, traversal
            results["remote_traversal_denied"] = True

            unknown = await client.call_tool("system_info", {"device_id": "unregistered"})
            assert unknown.isError, unknown
            results["unknown_device_denied"] = True

            local = await call("system_info", device_id="terra")
            assert local["device_id"] == "terra"
            results["terra_still_works"] = True
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
