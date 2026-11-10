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
    "probe_device": "viewer",
    "list_workspaces": "viewer",
    "list_files": "viewer",
    "read_text": "viewer",
    "write_text": "operator",
    "replace_text": "operator",
    "run_argv": "admin",
    "start_job": "admin",
    "job_status": "admin",
    "list_jobs": "admin",
    "job_output": "admin",
    "cancel_job": "admin",
    "purge_job": "admin",
    "binary_file_info": "viewer",
    "read_binary_chunk": "viewer",
    "begin_upload": "operator",
    "upload_chunk": "operator",
    "upload_status": "operator",
    "finish_upload": "operator",
    "cancel_upload": "operator",
    "execute_command": "operator",
    "preview_command": "viewer",
    "audit_tail": "admin",
    "desktop_status": "admin",
    "desktop_connect": "admin",
    "desktop_screenshot": "admin",
    "desktop_click": "admin",
    "desktop_move": "admin",
    "desktop_drag": "admin",
    "desktop_scroll": "admin",
    "desktop_key": "admin",
    "desktop_type_text": "admin",
    "desktop_disconnect": "admin",
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
        self, peer: SSHPeer, tool: str, arguments: dict[str, Any],
        timeout_seconds: float = 75,
    ) -> dict:
        params = self.ssh_params(peer)
        try:
            # 5s SSH connect + до 60s allowlist-команда + запас на MCP handshake.
            async with asyncio.timeout(timeout_seconds):
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

    async def probe_device(self, device_id: str, refresh: bool = False) -> dict:
        """On-demand verified MCP probe, cached briefly across conversations.

        Configured SSH peers are not assumed online. Only trusted, owner
        registered target IDs can be probed; no user-supplied hostnames.
        """
        started = time.monotonic()
        target = device_id or self.agent.config.device_id
        if target == self.agent.config.device_id:
            info = self.agent.system_info()
            return {"device_id": target, "online": bool(info["online"]),
                    "checked_at": int(time.time()), "cached": False,
                    "operation_id": self.agent.audit.record("probe_device", "local", started)}
        peer = self.agent.config.peers.get(target)
        if peer is None:
            self.agent.audit.record("probe_device", "unknown_device", started)
            raise ValueError("Unknown registered device")
        if ROLES[self.agent.config.role] < ROLES["viewer"]:
            raise PermissionError("Viewer role required")
        if not refresh:
            cached = self.agent.audit.probe_get(target)
            if cached is not None:
                cached["cached"] = True
                cached["operation_id"] = self.agent.audit.record("probe_device", "cached", started)
                return cached

        try:
            with self.agent.coordinator.lock("peer-probe", target, timeout=5):
                if not refresh:
                    cached = self.agent.audit.probe_get(target)
                    if cached is not None:
                        cached["cached"] = True
                        cached["operation_id"] = self.agent.audit.record("probe_device", "cached", started)
                        return cached
                try:
                    info = await self._remote_call(
                        peer, "system_info", {"device_id": target}, timeout_seconds=12)
                    online = (info.get("device_id") == target and info.get("online") is True)
                except asyncio.CancelledError:
                    raise
                except (ValueError, TimeoutError, OSError):
                    online = False
                value = self.agent.audit.probe_put(target, online)
        except TimeoutError:
            # Another chat already runs a probe. A busy result does not mean offline.
            self.agent.audit.record("probe_device", "busy", started)
            raise ValueError("Peer probe in progress; retry shortly") from None
        value["cached"] = False
        value["operation_id"] = self.agent.audit.record(
            "probe_device", "online" if value["online"] else "offline", started)
        return value

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
            if tool in {"execute_command", "run_argv"}:
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
