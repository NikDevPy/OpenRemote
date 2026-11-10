"""Проверяемые операции агента и разграничение прав."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hashlib
import json
import os
import platform
import signal
import time

from .audit import Audit
from .config import Config, ROLES
from .filesystem import SafeFiles
from .jobs import Jobs
from .coordination import Coordinator
from .transfers import Transfers
from .desktop_ipc import DesktopClient


class Agent:
    MAX_OUTPUT = 8192

    def __init__(self, config: Config):
        self.config = config
        self.files = SafeFiles(config.root)
        try:
            self.audit = Audit(config.state)
        except BaseException:
            self.files.close()
            raise
        self.coordinator = Coordinator(config.state)
        self.jobs = Jobs(config.state, self.coordinator)
        self.transfers = Transfers(config.state, self._file_area)
        self._executions = asyncio.Semaphore(1)
        self.desktop = DesktopClient()

    def close(self) -> None:
        # Desktop portal worker is owner-scoped and may serve another live chat.
        # It exits after 15 minutes idle or an explicit desktop_disconnect.
        self.audit.close()
        self.files.close()

    def _desktop_guard(self, operation: str, device_id: str | None,
                       *, input_required: bool = False) -> float:
        started = self._guard(operation, "admin", device_id)
        if not self.config.desktop_enabled or (
                input_required and not self.config.desktop_input_enabled):
            self.audit.record(operation, "disabled", started)
            raise PermissionError("Владелец не включил графический доступ в конфигурации")
        return started

    def desktop_status(self, device_id: str | None = None) -> dict:
        started = self._guard("desktop_status", "admin", device_id)
        result = self.desktop.status() if self.config.desktop_enabled else {
            "connected": False, "input_granted": False,
            "backend": "disabled", "native_width": 0, "native_height": 0,
            "image_width": 0, "image_height": 0,
        }
        result["desktop_enabled"] = self.config.desktop_enabled
        result["desktop_input_enabled"] = self.config.desktop_input_enabled
        result["operation_id"] = self.audit.record("desktop_status", "ok", started)
        return result

    def desktop_connect(self, device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_connect", device_id)
        try:
            result = self.desktop.connect(self.config.desktop_input_enabled)
        except Exception:
            self.audit.record("desktop_connect", "denied", started)
            raise
        result["operation_id"] = self.audit.record("desktop_connect", "ok", started)
        return result

    def desktop_screenshot(self, max_width: int = 1280,
                           device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_screenshot", device_id)
        result = self.desktop.screenshot(max_width)
        result["operation_id"] = self.audit.record("desktop_screenshot", "ok", started)
        return result

    def desktop_click(self, x: int, y: int, button: str = "left",
                      device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_click", device_id, input_required=True)
        result = self.desktop.click(x, y, button)
        result["operation_id"] = self.audit.record("desktop_click", "ok", started)
        return result

    def desktop_move(self, x: int, y: int,
                     device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_move", device_id, input_required=True)
        result = self.desktop.move(x, y)
        result["operation_id"] = self.audit.record("desktop_move", "ok", started)
        return result

    def desktop_drag(self, x: int, y: int, to_x: int, to_y: int,
                     steps: int = 12, device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_drag", device_id, input_required=True)
        result = self.desktop.drag(x, y, to_x, to_y, steps)
        result["operation_id"] = self.audit.record("desktop_drag", "ok", started)
        return result

    def desktop_scroll(self, x: int, y: int, steps: int,
                       device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_scroll", device_id, input_required=True)
        result = self.desktop.scroll(x, y, steps)
        result["operation_id"] = self.audit.record("desktop_scroll", "ok", started)
        return result

    def desktop_key(self, name: str, device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_key", device_id, input_required=True)
        result = self.desktop.key(name)
        result["operation_id"] = self.audit.record("desktop_key", "ok", started)
        return result

    def desktop_type_text(self, text: str, device_id: str | None = None) -> dict:
        started = self._desktop_guard("desktop_type_text", device_id, input_required=True)
        result = self.desktop.type_text(text)
        result["operation_id"] = self.audit.record("desktop_type_text", "ok", started)
        return result

    def desktop_disconnect(self, device_id: str | None = None) -> dict:
        started = self._guard("desktop_disconnect", "admin", device_id)
        result = self.desktop.disconnect()
        result["operation_id"] = self.audit.record("desktop_disconnect", "ok", started)
        return result

    def command_digest(self, command_id: str) -> str:
        """Привязать локальное разрешение к узлу, каталогу и точному argv."""
        command = self.config.commands[command_id]
        payload = {
            "device": self.config.device_id,
            "sandbox": str(self.files.root),
            "argv": list(command.argv),
            "timeout": command.timeout,
            "risk": command.risk,
        }
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(normalized.encode()).hexdigest()

    def _guard(self, operation: str, min_role: str, device_id: str | None) -> float:
        started = time.monotonic()
        if device_id is not None and device_id != self.config.device_id:
            self.audit.record(operation, "unknown_device", started)
            raise ValueError("Устройство не зарегистрировано; удалённый транспорт не подключён")
        if ROLES[self.config.role] < ROLES[min_role]:
            self.audit.record(operation, "forbidden", started)
            raise PermissionError(f"Операция требует роли {min_role} или выше")
        return started

    def system_info(self, device_id: str | None = None) -> dict:
        started = self._guard("system_info", "viewer", device_id)
        result = {"device_id": self.config.device_id,
                  "os": platform.system(), "machine": platform.machine(),
                  "role": self.config.role, "online": True}
        result["operation_id"] = self.audit.record("system_info", "ok", started)
        return result

    def list_devices(self) -> dict:
        started = self._guard("list_devices", "viewer", None)
        return {"devices": [{"device_id": self.config.device_id, "status": "online", "transport": "local"}],
                "operation_id": self.audit.record("list_devices", "ok", started)}

    @contextmanager
    def _file_area(self, workspace: str, writing: bool = False):
        if workspace == "sandbox":
            yield self.files
            return
        spec = self.config.workspaces.get(workspace)
        if spec is None:
            raise ValueError("Рабочая область не зарегистрирована")
        if writing and spec.access != "write":
            raise PermissionError("Рабочая область доступна только для чтения")
        handler = SafeFiles(spec.path, private=False)
        try:
            yield handler
        finally:
            handler.close()

    def list_workspaces(self, device_id: str | None = None) -> dict:
        started = self._guard("list_workspaces", "viewer", device_id)
        names = [{"name": "sandbox", "access": "write"}]
        names.extend({"name": name, "access": spec.access}
                     for name, spec in sorted(self.config.workspaces.items()))
        return {"workspaces": names, "operation_id": self.audit.record("list_workspaces", "ok", started)}

    def list_files(self, path: str = ".", device_id: str | None = None,
                   workspace: str = "sandbox") -> dict:
        started = self._guard("list_files", "viewer", device_id)
        try:
            with self._file_area(workspace) as area:
                result = area.list_files(path)
        except (ValueError, OSError, PermissionError):
            self.audit.record("list_files", "denied", started)
            raise ValueError("Каталог недоступен по политике OpenRemote") from None
        result["operation_id"] = self.audit.record("list_files", "ok", started)
        return result

    def read_text(self, path: str, device_id: str | None = None,
                  workspace: str = "sandbox") -> dict:
        started = self._guard("read_text", "viewer", device_id)
        try:
            with self._file_area(workspace) as area:
                result = area.read_text(path)
        except (ValueError, OSError, UnicodeError, PermissionError):
            self.audit.record("read_text", "denied", started)
            raise ValueError("Файл недоступен или не является допустимым UTF-8 текстом") from None
        result["operation_id"] = self.audit.record("read_text", "ok", started)
        return result

    def write_text(self, path: str, content: str, device_id: str | None = None,
                   workspace: str = "sandbox") -> dict:
        started = self._guard("write_text", "operator", device_id)
        try:
            with self._file_area(workspace, writing=True) as area:
                with self.coordinator.file(str(area.root), path):
                    result = area.write_text(path, content)
        except (ValueError, OSError, UnicodeError, PermissionError):
            self.audit.record("write_text", "denied", started)
            raise ValueError("Создание файла запрещено: путь, размер или существующий файл") from None
        result["operation_id"] = self.audit.record("write_text", "ok", started)
        return result

    def replace_text(self, path: str, content: str, expected_sha256: str,
                     workspace: str = "sandbox", device_id: str | None = None) -> dict:
        started = self._guard("replace_text", "operator", device_id)
        try:
            with self._file_area(workspace, writing=True) as area:
                with self.coordinator.file(str(area.root), path):
                    result = area.replace_text(path, content, expected_sha256)
        except (ValueError, OSError, UnicodeError, PermissionError):
            self.audit.record("replace_text", "denied", started)
            raise ValueError("Замена запрещена, файл изменён или путь вне рабочей области") from None
        result["operation_id"] = self.audit.record("replace_text", "ok", started)
        return result

    def preview_command(self, command_id: str, device_id: str | None = None) -> dict:
        """Вернуть описание политики без запуска и без раскрытия argv."""
        started = self._guard("preview_command", "viewer", device_id)
        command = self.config.commands.get(command_id)
        if command is None:
            self.audit.record("preview_command", "denied", started)
            raise ValueError("Команда отсутствует в списке разрешённых")
        info = {
            "command_id": command_id,
            "summary": command.summary or f"Администратор разрешил команду {command_id}",
            "risk": command.risk,
            "required_role": "admin" if command.risk == "privileged" else "operator",
            "requires_local_approval": command.risk == "privileged",
            "approval_pending": self.audit.command_approved(command_id, self.command_digest(command_id)) if command.risk == "privileged" else False,
            "timeout_seconds": command.timeout,
        }
        info["operation_id"] = self.audit.record("preview_command", "ok", started)
        return info

    def audit_tail(self, limit: int = 20) -> dict:
        started = self._guard("audit_tail", "admin", None)
        entries = self.audit.tail(limit)
        return {"entries": entries, "operation_id": self.audit.record("audit_tail", "ok", started)}

    def binary_file_info(self, path: str, workspace: str = "sandbox",
                         device_id: str | None = None) -> dict:
        started = self._guard("binary_file_info", "viewer", device_id)
        result = self.transfers.file_info(workspace, path)
        result["operation_id"] = self.audit.record("binary_file_info", "ok", started)
        return result

    def read_binary_chunk(self, path: str, workspace: str = "sandbox", offset: int = 0,
                          max_bytes: int = 16384, device_id: str | None = None) -> dict:
        started = self._guard("read_binary_chunk", "viewer", device_id)
        result = self.transfers.download(workspace, path, offset, max_bytes)
        result["operation_id"] = self.audit.record("read_binary_chunk", "ok", started)
        return result

    def begin_upload(self, path: str, total_size: int, sha256: str,
                     workspace: str = "sandbox",
                     expected_existing_sha256: str | None = None,
                     device_id: str | None = None) -> dict:
        started = self._guard("begin_upload", "operator", device_id)
        result = self.transfers.begin(workspace, path, total_size, sha256, expected_existing_sha256)
        result["operation_id"] = self.audit.record("begin_upload", "ok", started)
        return result

    def upload_chunk(self, upload_id: str, offset: int, data_base64: str,
                     device_id: str | None = None) -> dict:
        started = self._guard("upload_chunk", "operator", device_id)
        result = self.transfers.append(upload_id, offset, data_base64)
        result["operation_id"] = self.audit.record("upload_chunk", "ok", started)
        return result

    def upload_status(self, upload_id: str, device_id: str | None = None) -> dict:
        started = self._guard("upload_status", "operator", device_id)
        result = self.transfers.status(upload_id)
        result["operation_id"] = self.audit.record("upload_status", "ok", started)
        return result

    def finish_upload(self, upload_id: str, device_id: str | None = None) -> dict:
        started = self._guard("finish_upload", "operator", device_id)
        workspace, path = self.transfers.target(upload_id)
        with self._file_area(workspace, writing=True) as area:
            with self.coordinator.file(str(area.root), path):
                result = self.transfers.finish(upload_id)
        result["operation_id"] = self.audit.record("finish_upload", "ok", started)
        return result

    def cancel_upload(self, upload_id: str, device_id: str | None = None) -> dict:
        started = self._guard("cancel_upload", "operator", device_id)
        result = self.transfers.cancel(upload_id)
        result["operation_id"] = self.audit.record("cancel_upload", "ok", started)
        return result

    def start_job(self, argv: list[str], workspace: str = "sandbox",
                  timeout_seconds: int = 3600, device_id: str | None = None,
                  task_label: str | None = None,
                  idempotency_key: str | None = None) -> dict:
        """Launch detached process with bounded logs; survives stdio disconnect."""
        started = self._guard("start_job", "admin", device_id)
        if not self.config.ad_hoc_enabled:
            self.audit.record("start_job", "disabled", started)
            raise PermissionError("ad_hoc_enabled is required for arbitrary background jobs")
        try:
            with self._file_area(workspace) as area:
                result = self.jobs.start(argv, str(area.root), timeout_seconds,
                                         task_label=task_label, idempotency_key=idempotency_key)
        except (ValueError, OSError):
            self.audit.record("start_job", "denied", started)
            raise ValueError("Invalid job command, workspace or timeout") from None
        result["operation_id"] = self.audit.record("start_job", "ok", started)
        return result

    def job_status(self, job_id: str, device_id: str | None = None) -> dict:
        started = self._guard("job_status", "admin", device_id)
        result = self.jobs.status(job_id)
        result["operation_id"] = self.audit.record("job_status", "ok", started)
        return result

    def list_jobs(self, limit: int = 20, device_id: str | None = None) -> dict:
        started = self._guard("list_jobs", "admin", device_id)
        result = self.jobs.list(limit)
        result["operation_id"] = self.audit.record("list_jobs", "ok", started)
        return result

    def job_output(self, job_id: str, stream: str = "stdout", offset: int = 0,
                   limit_bytes: int = 8192, device_id: str | None = None) -> dict:
        started = self._guard("job_output", "admin", device_id)
        result = self.jobs.output(job_id, stream, offset, limit_bytes)
        result["operation_id"] = self.audit.record("job_output", "ok", started)
        return result

    def purge_job(self, job_id: str, device_id: str | None = None) -> dict:
        started = self._guard("purge_job", "admin", device_id)
        result = self.jobs.purge(job_id)
        result["operation_id"] = self.audit.record("purge_job", "ok", started)
        return result

    def cancel_job(self, job_id: str, device_id: str | None = None) -> dict:
        started = self._guard("cancel_job", "admin", device_id)
        result = self.jobs.cancel(job_id)
        result["operation_id"] = self.audit.record("cancel_job", "ok", started)
        return result

    async def run_argv(self, argv: list[str], workspace: str = "sandbox",
                       timeout: float = 30, device_id: str | None = None) -> dict:
        """Cross-process capped direct execution."""
        started = time.monotonic()
        try:
            with self.coordinator.slot("direct-commands", limit=4):
                return await self._run_argv_inner(argv, workspace, timeout, device_id)
        except TimeoutError:
            self.audit.record("run_argv", "capacity", started)
            raise ValueError("Concurrent command capacity reached; retry later") from None

    async def _run_argv_inner(self, argv: list[str], workspace: str = "sandbox",
                              timeout: float = 30, device_id: str | None = None) -> dict:
        """Запуск произвольного argv в контексте пользователя ОС, без shell=True.

        Высокорисковая функция, включаемая только отдельным флагом локального
        конфига. Сервер не повышает системные права и не обходит правила клиента.
        """
        started = self._guard("run_argv", "admin", device_id)
        if not self.config.ad_hoc_enabled:
            self.audit.record("run_argv", "disabled", started)
            raise PermissionError("Владелец устройства не включил ad_hoc_enabled")
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 40 or
                not all(isinstance(x, str) and x and "\x00" not in x for x in argv) or
                sum(len(x) for x in argv) > 8192 or not os.path.isabs(argv[0])):
            self.audit.record("run_argv", "invalid", started)
            raise ValueError("Нужен список из 1–40 аргументов с абсолютным исполняемым файлом")
        if not isinstance(timeout, (int, float)) or not 0.2 <= timeout <= 60:
            self.audit.record("run_argv", "invalid", started)
            raise ValueError("Таймаут должен быть 0.2–60 секунд")
        async with self._executions:
            try:
                with self._file_area(workspace) as area:
                    proc = await asyncio.create_subprocess_exec(
                        *argv,
                        cwd=str(area.root),
                        env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin",
                             "HOME": os.path.expanduser("~")},
                        stdin=asyncio.subprocess.DEVNULL,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                        start_new_session=True,
                    )
            except (OSError, ValueError, PermissionError):
                self.audit.record("run_argv", "spawn_error", started)
                raise ValueError("Команду нельзя запустить или рабочая область недоступна") from None

            async def read_capped(stream):
                chunks = []
                size = 0
                while True:
                    part = await stream.read(4096)
                    if not part:
                        return b"".join(chunks)
                    size += len(part)
                    if size > self.MAX_OUTPUT:
                        raise ValueError("Вывод превышает 8 КиБ")
                    chunks.append(part)

            try:
                stdout, stderr, _ = await asyncio.wait_for(
                    asyncio.gather(read_capped(proc.stdout), read_capped(proc.stderr), proc.wait()),
                    timeout=timeout,
                )
            except (TimeoutError, ValueError, asyncio.CancelledError) as exc:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await proc.wait()
                self.audit.record("run_argv",
                                  "timeout" if isinstance(exc, TimeoutError) else "interrupted",
                                  started)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ValueError("Выполнение прервано: таймаут или превышен лимит вывода") from None
            result = {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
            result["operation_id"] = self.audit.record(
                "run_argv", "ok" if proc.returncode == 0 else "failed", started)
            return result

    async def execute_command(self, command_id: str, device_id: str | None = None) -> dict:
        """Capped execution of a locally approved command."""
        started = time.monotonic()
        try:
            with self.coordinator.slot("direct-commands", limit=4):
                return await self._execute_command_inner(command_id, device_id)
        except TimeoutError:
            self.audit.record("execute_command", "capacity", started)
            raise ValueError("Concurrent command capacity reached; retry later") from None

    async def _execute_command_inner(self, command_id: str, device_id: str | None = None) -> dict:
        """Без shell и произвольных аргументов: только команда из локальной политики."""
        started = self._guard("execute_command", "operator", device_id)
        command = self.config.commands.get(command_id)
        if command is None:
            self.audit.record("execute_command", "denied", started)
            raise ValueError("Команда отсутствует в локальном списке разрешённых")
        if command.risk == "privileged" and ROLES[self.config.role] < ROLES["admin"]:
            self.audit.record("execute_command", "forbidden", started)
            raise PermissionError("Привилегированная команда требует роли admin")
        async with self._executions:
            if command.risk == "privileged" and not self.audit.consume_command(command_id, self.command_digest(command_id)):
                self.audit.record("execute_command", "approval_required", started)
                raise PermissionError("Нужно разовое разрешение владельца на устройстве. Используйте локальный --approve-command")
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command.argv, cwd=str(self.files.root),
                    stdin=asyncio.subprocess.DEVNULL,
                    env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin"},
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )
            except OSError:
                self.audit.record("execute_command", "spawn_error", started)
                raise ValueError("Не удалось запустить разрешённую программу") from None

            async def capped_read(stream):
                chunks = []
                size = 0
                while True:
                    part = await stream.read(4096)
                    if not part:
                        return b"".join(chunks)
                    size += len(part)
                    if size > self.MAX_OUTPUT:
                        raise ValueError("Превышен лимит вывода команды")
                    chunks.append(part)

            try:
                stdout, stderr, _ = await asyncio.wait_for(
                    asyncio.gather(capped_read(proc.stdout), capped_read(proc.stderr), proc.wait()),
                    timeout=command.timeout,
                )
            except (TimeoutError, ValueError, asyncio.CancelledError) as exc:
                if proc.returncode is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                await proc.wait()
                self.audit.record("execute_command",
                                  "timeout" if isinstance(exc, TimeoutError) else "interrupted", started)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                raise ValueError("Команда прервана: таймаут или превышение лимита вывода") from None

            result = {
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", errors="replace"),
                "stderr": stderr.decode("utf-8", errors="replace"),
            }
            result["operation_id"] = self.audit.record(
                "execute_command", "ok" if proc.returncode == 0 else "failed", started)
            return result
