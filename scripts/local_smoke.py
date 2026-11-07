"""Реальная проверка TCP/HTTP; запуск из корня: python scripts/local_smoke.py."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def main():
    with tempfile.TemporaryDirectory() as tmp:
        token = secrets.token_urlsafe(32)
        env = dict(os.environ, OPENREMOTE_PROBE_TOKEN=token)
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen([
                sys.executable, "-m", "openremote_probe.server", "--transport", "http",
                "--root", tmp], env=env, stdout=log, stderr=log)
            try:
                # Только loopback: не отправлять тестовый токен системному proxy.
                async with httpx.AsyncClient(trust_env=False) as http:
                    for _ in range(50):
                        try:
                            denied = await http.post("http://127.0.0.1:8765/mcp", json={})
                            break
                        except httpx.ConnectError:
                            if process.poll() is not None:
                                raise RuntimeError("Сервер завершился при запуске")
                            await asyncio.sleep(.1)
                    else:
                        raise RuntimeError("Сервер не запустился")
                    assert denied.status_code == 401
                async with httpx.AsyncClient(
                    trust_env=False, headers={"Authorization": "Bearer " + token}
                ) as http:
                    async with streamable_http_client(
                        "http://127.0.0.1:8765/mcp", http_client=http
                    ) as (read, write, _):
                        async with ClientSession(read, write) as client:
                            init = await client.initialize()
                            tools = await client.list_tools()
                            nonce = secrets.token_hex(12)
                            result = await client.call_tool("execute_command", {
                                "command": "write_probe", "nonce": nonce})
                            assert result.structuredContent["exit_code"] == 0
                            assert (Path(tmp) / (nonce + ".receipt")).read_text() == "OPENREMOTE:" + nonce + "\n"
                            print(json.dumps({
                                "transport": "streamable-http over loopback TCP",
                                "server": init.serverInfo.name,
                                "protocol": init.protocolVersion,
                                "unauthorized_status": denied.status_code,
                                "tools": [tool.name for tool in tools.tools],
                                "exit_code": 0, "receipt_verified": True,
                                "chatgpt_plus_verified": False,
                            }, ensure_ascii=False, indent=2))
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


asyncio.run(main())
