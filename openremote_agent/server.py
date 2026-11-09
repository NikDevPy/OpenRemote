"""MCP stdio или HTTP только через loopback с bearer в окружении."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shlex
import time
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

class CommandPreviewResult(TypedDict):
    command_id: str
    summary: str
    risk: str
    required_role: str
    requires_local_approval: bool
    approval_pending: bool
    timeout_seconds: float
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
from .router import Router


class Server:
    def __init__(self, agent: Agent):
        self.agent = agent
        self.router = Router(agent)
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
        # Назначение команды задаётся локальной политикой и может быть необратимым.
        command_write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
        self.mcp.tool(annotations=read)(self.system_info)
        self.mcp.tool(annotations=read)(self.list_devices)
        self.mcp.tool(annotations=read)(self.list_files)
        self.mcp.tool(annotations=read)(self.read_text)
        self.mcp.tool(annotations=write)(self.write_text)
        self.mcp.tool(annotations=read)(self.preview_command)
        self.mcp.tool(annotations=command_write)(self.execute_command)
        self.mcp.tool(annotations=read)(self.audit_tail)

    async def system_info(self, device_id: str | None = None) -> InfoResult:
        """Покажи сведения о разрешённом устройстве (локальном или через SSH)."""
        return await self.router.call("system_info", {}, device_id)

    def list_devices(self) -> DeviceResult:
        """Покажи локальное и настроенные SSH-устройства; configured не равно online."""
        return self.router.list_devices()

    async def list_files(self, path: str = ".", device_id: str | None = None) -> ListResult:
        """Прочти до 100 имён в sandbox выбранного устройства, не выходя за его границы."""
        return await self.router.call("list_files", {"path": path}, device_id)

    async def read_text(self, path: str, device_id: str | None = None) -> ReadResult:
        """Прочти UTF-8 файл до 64 КиБ в sandbox выбранного устройства."""
        return await self.router.call("read_text", {"path": path}, device_id)

    async def write_text(self, path: str, content: str, device_id: str | None = None) -> WriteResult:
        """Создай новый файл в sandbox выбранного устройства. Нужна operator, перезапись запрещена."""
        return await self.router.call("write_text", {"path": path, "content": content}, device_id)

    async def preview_command(self, command_id: str, device_id: str | None = None) -> CommandPreviewResult:
        """Проверь заранее разрешённую команду, её уровень риска и необходимость локального допуска, не запуская её."""
        return await self.router.call("preview_command", {"command_id": command_id}, device_id)

    async def execute_command(self, command_id: str, device_id: str | None = None) -> CommandResult:
        """Запусти именованную команду из политики выбранного устройства. Может менять систему; privileged требует admin и локального одноразового допуска. Без пользовательских argv или shell."""
        return await self.router.call("execute_command", {"command_id": command_id}, device_id)

    async def audit_tail(self, limit: int = 20, device_id: str | None = None) -> AuditResult:
        """Последние события аудита на выбранном устройстве, без путей и содержимого. Нужна admin."""
        return await self.router.call("audit_tail", {"limit": limit}, device_id)


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
    parser.add_argument("--approve-command", metavar="COMMAND_ID", default=None,
                        help="Локальное одноразовое разрешение привилегированной команды")
    parser.add_argument("--approve-ttl", type=int, default=120,
                        help="Срок локального разрешения в секундах, максимум 300")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("Linux MVP работает от обычного пользователя, не root")
    config = load_config(args.config)
    agent = Agent(config)
    try:
        if args.approve_command is not None:
            command = config.commands.get(args.approve_command)
            if command is None or command.risk != "privileged" or config.role != "admin":
                parser.error("Разрешить можно только команду risk=privileged при роли admin")
            if not 1 <= args.approve_ttl <= 300:
                parser.error("Срок разрешения должен быть от 1 до 300 секунд")
            if not (sys.stdin.isatty() and sys.stdout.isatty()):
                parser.error("Подтверждение допускается только в интерактивном локальном терминале")
            print(f"DEVICE: {config.device_id}; COMMAND: {args.approve_command}; RISK: privileged")
            print(f"Действие: {command.summary or 'Команда из локального конфига'}")
            print("Точная команда из локальной политики:", shlex.join(command.argv))
            phrase = f"APPROVE {args.approve_command}"
            if input(f"Введите {phrase}: ").strip() != phrase:
                parser.error("Разрешение не предоставлено")
            agent.audit.grant_command(args.approve_command, agent.command_digest(args.approve_command), args.approve_ttl)
            agent.audit.record("command_approval", "local_approved", time.monotonic())
            print("Разовое разрешение записано в локальную базу. Его можно использовать один раз.")
            return
        if args.doctor:
            print(json.dumps({
                "device_id": config.device_id, "role": config.role,
                "sandbox": str(config.root), "audit": str(config.state),
                "commands": sorted(config.commands),
                "peers": sorted(config.peers), "ready": True
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
