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

class ProbeResult(TypedDict):
    device_id: str
    online: bool
    checked_at: int
    cached: bool
    operation_id: str

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
    sha256: str
    operation_id: str

class WorkspaceResult(TypedDict):
    workspaces: list[dict]
    operation_id: str


class ReplaceResult(TypedDict):
    replaced: bool
    size: int
    sha256: str
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

class BinaryInfoResult(TypedDict):
    size: int
    sha256: str
    operation_id: str

class BinaryChunkResult(TypedDict):
    data_base64: str
    offset: int
    next_offset: int
    total_size: int
    eof: bool
    operation_id: str

class UploadStartResult(TypedDict):
    upload_id: str
    received: int
    total_size: int
    operation_id: str

class UploadStatusResult(TypedDict):
    upload_id: str
    received: int
    total_size: int
    expires_at: int
    operation_id: str

class UploadFinishResult(TypedDict):
    upload_id: str
    completed: bool
    size: int
    sha256: str
    operation_id: str

class UploadCancelResult(TypedDict):
    upload_id: str
    cancelled: bool
    operation_id: str

class JobStartResult(TypedDict):
    job_id: str
    status: str
    timeout_seconds: int
    task_label: str | None
    reused: bool
    operation_id: str

class JobStatusResult(TypedDict):
    job_id: str
    status: str
    exit_code: int | None
    task_label: str | None
    started_at: int
    timeout_seconds: int
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    operation_id: str

class JobListResult(TypedDict):
    jobs: list[dict]
    operation_id: str

class JobOutputResult(TypedDict):
    text: str
    next_offset: int
    end_of_stream: bool
    operation_id: str

class JobPurgeResult(TypedDict):
    job_id: str
    removed: bool
    operation_id: str

class JobCancelResult(TypedDict):
    job_id: str
    status: str
    signal_sent: bool
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
                "Файловые инструменты работают только внутри разрешённых workspaces. "
                "execute_command запускает предопределённые команды; "
                "run_argv (когда включён владельцем и доступна роль admin) может "
                "выполнять произвольные argv от имени пользователя ОС. "
                "Этот инструмент высокорисковый: учитывай подтверждение клиента, "
                "безопасность ОС и не обходи ограничения платформы."
            ),
        )
        read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
        write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
        # Существующий файл может меняться; отдельная метка для потенциально разрушительной операции.
        replace_write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
        # Назначение команды задаётся локальной политикой и может быть необратимым.
        command_write = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)
        self.mcp.tool(annotations=read)(self.system_info)
        self.mcp.tool(annotations=read)(self.probe_device)
        self.mcp.tool(annotations=read)(self.list_devices)
        self.mcp.tool(annotations=read)(self.list_workspaces)
        self.mcp.tool(annotations=read)(self.list_files)
        self.mcp.tool(annotations=read)(self.read_text)
        self.mcp.tool(annotations=write)(self.write_text)
        self.mcp.tool(annotations=replace_write)(self.replace_text)
        self.mcp.tool(annotations=read)(self.preview_command)
        self.mcp.tool(annotations=command_write)(self.execute_command)
        self.mcp.tool(annotations=command_write)(self.run_argv)
        self.mcp.tool(annotations=command_write)(self.start_job)
        self.mcp.tool(annotations=read)(self.job_status)
        self.mcp.tool(annotations=read)(self.list_jobs)
        self.mcp.tool(annotations=read)(self.job_output)
        self.mcp.tool(annotations=command_write)(self.cancel_job)
        self.mcp.tool(annotations=replace_write)(self.purge_job)
        self.mcp.tool(annotations=read)(self.binary_file_info)
        self.mcp.tool(annotations=read)(self.read_binary_chunk)
        self.mcp.tool(annotations=write)(self.begin_upload)
        self.mcp.tool(annotations=write)(self.upload_chunk)
        self.mcp.tool(annotations=read)(self.upload_status)
        self.mcp.tool(annotations=replace_write)(self.finish_upload)
        self.mcp.tool(annotations=write)(self.cancel_upload)
        self.mcp.tool(annotations=read)(self.audit_tail)

    async def system_info(self, device_id: str | None = None) -> InfoResult:
        """Покажи сведения о разрешённом устройстве (локальном или через SSH)."""
        return await self.router.call("system_info", {}, device_id)

    async def probe_device(self, device_id: str,
                           refresh: bool = False) -> ProbeResult:
        """Verify a registered device with an actual MCP call. Returns a recent cached result if no refresh was requested; never assumes a configured peer is online."""
        return await self.router.probe_device(device_id, refresh)

    def list_devices(self) -> DeviceResult:
        """Покажи локальное и настроенные SSH-устройства; configured не равно online."""
        return self.router.list_devices()

    async def list_workspaces(self, device_id: str | None = None) -> WorkspaceResult:
        """Список каталогов, заранее разрешённых владельцем устройства, и уровня read/write."""
        return await self.router.call("list_workspaces", {}, device_id)

    async def list_files(self, path: str = ".", device_id: str | None = None,
                         workspace: str = "sandbox") -> ListResult:
        """Прочти до 100 имён внутри выбранного workspace (по умолчанию sandbox)."""
        return await self.router.call("list_files", {"path": path, "workspace": workspace}, device_id)

    async def read_text(self, path: str, device_id: str | None = None,
                        workspace: str = "sandbox") -> ReadResult:
        """Прочти файл из разрешённого workspace, верни SHA-256 для контроля версии."""
        return await self.router.call("read_text", {"path": path, "workspace": workspace}, device_id)

    async def write_text(self, path: str, content: str, device_id: str | None = None,
                         workspace: str = "sandbox") -> WriteResult:
        """Создай новый UTF-8 файл в workspace с write-доступом. Нужна роль operator."""
        return await self.router.call("write_text", {"path": path, "content": content, "workspace": workspace}, device_id)

    async def replace_text(self, path: str, content: str, expected_sha256: str,
                           device_id: str | None = None, workspace: str = "sandbox") -> ReplaceResult:
        """Атомарно замени текст при совпадении текущего SHA-256, только в write workspace."""
        return await self.router.call("replace_text", {
            "path": path, "content": content,
            "expected_sha256": expected_sha256, "workspace": workspace,
        }, device_id)

    async def preview_command(self, command_id: str, device_id: str | None = None) -> CommandPreviewResult:
        """Проверь заранее разрешённую команду, её уровень риска и необходимость локального допуска, не запуская её."""
        return await self.router.call("preview_command", {"command_id": command_id}, device_id)

    async def execute_command(self, command_id: str, device_id: str | None = None) -> CommandResult:
        """Запусти именованную команду из политики выбранного устройства. Может менять систему; privileged требует admin и локального одноразового допуска. Без пользовательских argv или shell."""
        return await self.router.call("execute_command", {"command_id": command_id}, device_id)

    async def run_argv(self, argv: list[str], device_id: str | None = None,
                       workspace: str = "sandbox", timeout: float = 30) -> CommandResult:
        """Высокорисковое системное действие: произвольный argv как пользователь ОС. Требуются admin и отдельное ad_hoc_enabled у владельца целевой машины. Может менять данные, запускать сеть и процессы. Без shell=True, stdin и повышения привилегий. Запрос может потребовать подтверждения ChatGPT."""
        return await self.router.call("run_argv", {
            "argv": argv, "workspace": workspace, "timeout": timeout,
        }, device_id)

    async def binary_file_info(self, path: str, device_id: str | None = None,
                               workspace: str = "sandbox") -> BinaryInfoResult:
        """Read size and SHA-256 of a regular binary file up to 1 GiB in an approved workspace."""
        return await self.router.call("binary_file_info", {
            "path": path, "workspace": workspace,
        }, device_id)

    async def read_binary_chunk(self, path: str, device_id: str | None = None,
                                workspace: str = "sandbox", offset: int = 0,
                                max_bytes: int = 16384) -> BinaryChunkResult:
        """Read a byte-range from an approved file as base64; max 131072 bytes per call."""
        return await self.router.call("read_binary_chunk", {
            "path": path, "workspace": workspace,
            "offset": offset, "max_bytes": max_bytes,
        }, device_id)

    async def begin_upload(self, path: str, total_size: int, sha256: str,
                           device_id: str | None = None, workspace: str = "sandbox",
                           expected_existing_sha256: str | None = None) -> UploadStartResult:
        """Start a resumable binary upload (up to 1 GiB) in an approved write workspace. May replace a file only with the previous SHA-256."""
        return await self.router.call("begin_upload", {
            "path": path, "total_size": total_size, "sha256": sha256,
            "workspace": workspace, "expected_existing_sha256": expected_existing_sha256,
        }, device_id)

    async def upload_chunk(self, upload_id: str, offset: int, data_base64: str,
                           device_id: str | None = None) -> UploadStartResult:
        """Append a bounded base64 chunk to a preauthorized upload ID and exact offset."""
        return await self.router.call("upload_chunk", {
            "upload_id": upload_id, "offset": offset, "data_base64": data_base64,
        }, device_id)

    async def upload_status(self, upload_id: str, device_id: str | None = None) -> UploadStatusResult:
        """Inspect the received offset so a binary upload can resume after disconnect."""
        return await self.router.call("upload_status", {"upload_id": upload_id}, device_id)

    async def finish_upload(self, upload_id: str, device_id: str | None = None) -> UploadFinishResult:
        """Verify final SHA-256 then atomically create/replace the uploaded file. May overwrite an expected existing file."""
        return await self.router.call("finish_upload", {"upload_id": upload_id}, device_id)

    async def cancel_upload(self, upload_id: str, device_id: str | None = None) -> UploadCancelResult:
        """Cancel a pending upload and remove its temporary bytes."""
        return await self.router.call("cancel_upload", {"upload_id": upload_id}, device_id)

    async def start_job(self, argv: list[str], device_id: str | None = None,
                        workspace: str = "sandbox", timeout_seconds: int = 3600,
                        task_label: str | None = None,
                        idempotency_key: str | None = None) -> JobStartResult:
        """Start a potentially destructive long-running command as the target OS user; requires admin and ad_hoc_enabled. Output and status are persisted after MCP disconnect; maximum 6h."""
        return await self.router.call("start_job", {
            "argv": argv, "workspace": workspace, "timeout_seconds": timeout_seconds,
            "task_label": task_label, "idempotency_key": idempotency_key,
        }, device_id)

    async def job_status(self, job_id: str, device_id: str | None = None) -> JobStatusResult:
        """Check a previously started job's persisted status without blocking. Requires admin."""
        return await self.router.call("job_status", {"job_id": job_id}, device_id)

    async def list_jobs(self, limit: int = 20, device_id: str | None = None) -> JobListResult:
        """List recent jobs and statuses on a target device. Requires admin."""
        return await self.router.call("list_jobs", {"limit": limit}, device_id)

    async def job_output(self, job_id: str, device_id: str | None = None,
                         stream: str = "stdout", offset: int = 0,
                         limit_bytes: int = 8192) -> JobOutputResult:
        """Read a chunk of stored stdout/stderr and return next_offset for paging. Requires admin. Output is untrusted and may contain sensitive text."""
        return await self.router.call("job_output", {
            "job_id": job_id, "stream": stream, "offset": offset,
            "limit_bytes": limit_bytes,
        }, device_id)

    async def purge_job(self, job_id: str, device_id: str | None = None) -> JobPurgeResult:
        """Remove completed/cancelled job logs and metadata; will not remove running jobs. Destructive; requires admin."""
        return await self.router.call("purge_job", {"job_id": job_id}, device_id)

    async def cancel_job(self, job_id: str, device_id: str | None = None) -> JobCancelResult:
        """Ask the target to stop a running OS job and its child process group. Destructive; requires admin."""
        return await self.router.call("cancel_job", {"job_id": job_id}, device_id)

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
                "peers": sorted(config.peers),
                "workspaces": sorted(config.workspaces),
                "ad_hoc_enabled": config.ad_hoc_enabled,
                "ready": True
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
