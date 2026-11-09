"""Проверяемые операции агента и разграничение прав."""
from __future__ import annotations

import asyncio
import os
import platform
import signal
import time

from .audit import Audit
from .config import Config, ROLES
from .filesystem import SafeFiles


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
        self._executions = asyncio.Semaphore(1)

    def close(self) -> None:
        self.audit.close()
        self.files.close()

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

    def list_files(self, path: str = ".", device_id: str | None = None) -> dict:
        started = self._guard("list_files", "viewer", device_id)
        try:
            result = self.files.list_files(path)
        except (ValueError, OSError):
            self.audit.record("list_files", "denied", started)
            raise ValueError("Каталог недоступен по политике OpenRemote") from None
        result["operation_id"] = self.audit.record("list_files", "ok", started)
        return result

    def read_text(self, path: str, device_id: str | None = None) -> dict:
        started = self._guard("read_text", "viewer", device_id)
        try:
            result = self.files.read_text(path)
        except (ValueError, OSError, UnicodeError):
            self.audit.record("read_text", "denied", started)
            raise ValueError("Файл недоступен или не является допустимым UTF-8 текстом") from None
        result["operation_id"] = self.audit.record("read_text", "ok", started)
        return result

    def write_text(self, path: str, content: str, device_id: str | None = None) -> dict:
        started = self._guard("write_text", "operator", device_id)
        try:
            result = self.files.write_text(path, content)
        except (ValueError, OSError, UnicodeError):
            self.audit.record("write_text", "denied", started)
            raise ValueError("Создание файла запрещено: путь, размер или существующий файл") from None
        result["operation_id"] = self.audit.record("write_text", "ok", started)
        return result

    def audit_tail(self, limit: int = 20) -> dict:
        started = self._guard("audit_tail", "admin", None)
        entries = self.audit.tail(limit)
        return {"entries": entries, "operation_id": self.audit.record("audit_tail", "ok", started)}

    async def execute_command(self, command_id: str, device_id: str | None = None) -> dict:
        """Без shell и произвольных аргументов: только команда из локальной политики."""
        started = self._guard("execute_command", "operator", device_id)
        command = self.config.commands.get(command_id)
        if command is None:
            self.audit.record("execute_command", "denied", started)
            raise ValueError("Команда отсутствует в локальном списке разрешённых")
        async with self._executions:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *command.argv, cwd=str(self.files.root),
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
