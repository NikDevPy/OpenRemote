"""Маршрутизатор: локальный Agent или один из заранее разрешённых SSH peer."""
from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .config import ROLES, SSHPeer
from .engine import Agent


REQUIRED_ROLES = {
    "system_info": "viewer",
    "list_files": "viewer",
    "read_text": "viewer",
    "write_text": "operator",
    "execute_command": "operator",
    "preview_command": "viewer",
    "audit_tail": "admin",
}


class Router:
    def __init__(self, agent: Agent):
        self.agent = agent

    def list_devices(self) -> dict:
        result = self.agent.list_devices()
        # "configured" не означает, что сеть/SSH действительно доступны.
        for name in sorted(self.agent.config.peers):
            result["devices"].append({
                "device_id": name, "status": "configured",
                "transport": "ssh", "online": None,
            })
        return result

    @staticmethod
    def ssh_params(peer: SSHPeer) -> StdioServerParameters:
        # SSH выполняет эту команду через remote shell. Только локальный конфиг
        # формирует argv; параметры MCP tool в shell НИКОГДА не попадают.
        remote_command = shlex.join([
            peer.python, "-m", "openremote_agent.server",
            "--config", peer.config_path, "--transport", "stdio",
        ])
        return StdioServerParameters(
            command="/usr/bin/ssh",
            args=[
                "-T", "-q",
                "-o", "BatchMode=yes",
                "-o", "NumberOfPasswordPrompts=0",
                "-o", "StrictHostKeyChecking=yes",
                "-o", "ConnectTimeout=5",
                "-o", "ClearAllForwardings=yes",
                "-o", "ForwardAgent=no",
                "-o", "PermitLocalCommand=no",
                "-o", f"HostName={peer.host_name}",
                peer.target, remote_command,
            ],
        )

    async def _remote_call(
        self, peer: SSHPeer, tool: str, arguments: dict[str, Any]
    ) -> dict:
        params = self.ssh_params(peer)
        try:
            # 5s SSH connect + до 60s allowlist-команда + запас на MCP handshake.
            async with asyncio.timeout(75):
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        response = await session.call_tool(tool, arguments)
                        if response.isError:
                            # Скрываем трассировки, удалённые пути и конфигурацию.
                            raise ValueError("Удалённый агент отклонил операцию")
                        if not isinstance(response.structuredContent, dict):
                            raise ValueError("Удалённый MCP вернул результат без структуры")
                        return response.structuredContent
        except asyncio.CancelledError:
            raise
        except Exception:
            raise ValueError("Ошибка SSH/MCP или отказ удалённого агента") from None

    async def call(
        self, tool: str, arguments: dict[str, Any],
        device_id: str | None = None
    ) -> dict:
        min_role = REQUIRED_ROLES.get(tool)
        if min_role is None:
            raise ValueError("Неизвестный инструмент")
        target = device_id or self.agent.config.device_id
        if target == self.agent.config.device_id:
            # Локальная реализация ещё раз проверяет роли и пути.
            fn = getattr(self.agent, tool)
            if tool == "execute_command":
                return await fn(**arguments)
            return fn(**arguments)

        started = time.monotonic()
        peer = self.agent.config.peers.get(target)
        if peer is None:
            self.agent.audit.record(tool, "unknown_device", started)
            raise ValueError("Устройство отсутствует в списке разрешённых")
        if ROLES[self.agent.config.role] < ROLES[min_role]:
            self.agent.audit.record(tool, "forbidden", started)
            raise PermissionError(f"Для операции необходима роль {min_role}")

        # Удалённый Agent отдельно проверяет собственные роли и свой sandbox.
        remote_args = dict(arguments)
        if tool != "audit_tail":
            remote_args["device_id"] = target
        try:
            result = await self._remote_call(peer, tool, remote_args)
        except BaseException:
            self.agent.audit.record(tool, "remote_error", started)
            raise
        self.agent.audit.record(tool, "remote_ok", started)
        return result
