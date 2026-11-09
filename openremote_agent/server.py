"""MCP stdio или HTTP только через loopback с bearer в окружении."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
from typing import TypedDict

class InfoResult(TypedDict):
    device_id: str
    os: str
    machine: str
    role: str
    online: bool
    operation_id: str

class DeviceResult(TypedDict):
    devices: list[dict]
    operation_id: str

class ListResult(TypedDict):
    entries: list[dict]
    truncated: bool
    operation_id: str

class ReadResult(TypedDict):
    content: str
    size: int
    operation_id: str

class WriteResult(TypedDict):
    created: bool
    size: int
    operation_id: str

class CommandResult(TypedDict):
    exit_code: int
    stdout: str
    stderr: str
    operation_id: str

class AuditResult(TypedDict):
    entries: list[dict]
    operation_id: str

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
import uvicorn

from .config import load_config
from .engine import Agent


class Server:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.mcp = FastMCP(
            "OpenRemote", host="127.0.0.1", port=8766,
            stateless_http=True, json_response=True,
            instructions=(
                "OpenRemote доступен только на явно зарегистрированных устройствах. "
                "Файлы ограничены sandbox. execute_command запускает исключительно "
                "предопределённые локальным администратором команды; произвольный shell "
                "и аргументы запрещены. Уважай отказ и не обходи ограничений."
            ),
        )
        read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
        write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
        self.mcp.tool(annotations=read)(self.system_info)
        self.mcp.tool(annotations=read)(self.list_devices)
        self.mcp.tool(annotations=read)(self.list_files)
        self.mcp.tool(annotations=read)(self.read_text)
        self.mcp.tool(annotations=write)(self.write_text)
        self.mcp.tool(annotations=write)(self.execute_command)
        self.mcp.tool(annotations=read)(self.audit_tail)

    def system_info(self, device_id: str | None = None) -> InfoResult:
        """Покажи ОС, архитектуру и роль локального устройства. device_id необязателен."""
        return self.agent.system_info(device_id)

    def list_devices(self) -> DeviceResult:
        """Перечисли зарегистрированные устройства. Удалённые устройства появятся после настройки транспорта."""
        return self.agent.list_devices()

    def list_files(self, path: str = ".", device_id: str | None = None) -> ListResult:
        """Прочти до 100 имён в каталоге sandbox. Только относительные пути; symlink не обходятся."""
        return self.agent.list_files(path, device_id)

    def read_text(self, path: str, device_id: str | None = None) -> ReadResult:
        """Прочти UTF-8 файл не более 64 КиБ внутри sandbox, без переходов по symlink."""
        return self.agent.read_text(path, device_id)

    def write_text(self, path: str, content: str, device_id: str | None = None) -> WriteResult:
        """Создай новый UTF-8 файл до 64 КиБ в sandbox. Требуется operator; перезапись запрещена."""
        return self.agent.write_text(path, content, device_id)

    async def execute_command(self, command_id: str, device_id: str | None = None) -> CommandResult:
        """Выполни разрешённый локальным конфигом command_id без shell и пользовательских argv. Требуется operator."""
        return await self.agent.execute_command(command_id, device_id)

    def audit_tail(self, limit: int = 20) -> AuditResult:
        """Последние события постоянного аудита без путей и содержимого. Требуется admin."""
        return self.agent.audit_tail(limit)


class LocalAuth:
    """Локальная защита; НЕ заменяет OAuth при публичном размещении."""

    def __init__(self, app, token: str, agent: Agent):
        if len(token) < 32:
            raise ValueError("Для HTTP требуется OPENREMOTE_TOKEN длиной не менее 32 символов")
        self.app = app
        self.expected = hashlib.sha256(("Bearer " + token).encode()).digest()
        self.agent = agent

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            value = hashlib.sha256(headers.get(b"authorization", b"")).digest()
            if not hmac.compare_digest(value, self.expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
            if scope.get("path") == "/status":
                await JSONResponse({
                    "device_id": self.agent.config.device_id,
                    "role": self.agent.config.role,
                    "status": "ok",
                    "command_count": len(self.agent.config.commands),
                })(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenRemote: защищённый MCP-агент")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--doctor", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("Linux MVP работает от обычного пользователя, не root")
    config = load_config(args.config)
    agent = Agent(config)
    try:
        if args.doctor:
            print(json.dumps({
                "device_id": config.device_id, "role": config.role,
                "sandbox": str(config.root), "audit": str(config.state),
                "commands": sorted(config.commands), "ready": True
            }, ensure_ascii=False, indent=2))
            return
        server = Server(agent)
        if args.transport == "stdio":
            server.mcp.run("stdio")
        else:
            token = os.environ.get("OPENREMOTE_TOKEN", "")
            app = LocalAuth(server.mcp.streamable_http_app(), token, agent)
            uvicorn.run(app, host="127.0.0.1", port=8766, access_log=False, log_level="warning")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
