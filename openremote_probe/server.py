"""Linux-стенд: stdio для частного туннеля, HTTP только на loopback."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import platform
import re
import sqlite3
import sys
import time
from typing import Literal
from typing_extensions import TypedDict
import uuid

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
import uvicorn


class SystemInfo(TypedDict):
    os: str
    machine: str
    probe_only: bool
    operation_id: str


class FileList(TypedDict):
    files: list[str]
    truncated: bool
    operation_id: str


class CommandResult(TypedDict):
    operation_id: str
    exit_code: int
    stdout: str
    stderr: str
    receipt: str | None

# Только фиксированная программа; nonce передаётся отдельным argv, shell не используется.
PROBE_PROGRAM = r'''
import hashlib,json,os,sys
command,nonce=sys.argv[1:]
payload=("OPENREMOTE:"+nonce+"\n").encode()
if command=="write_probe":
    fd=os.open(nonce+".receipt",os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
print(json.dumps({"nonce":nonce,"sha256":hashlib.sha256(payload).hexdigest(),"pid":os.getpid()}))
'''


class Probe:
    def __init__(self, root: Path):
        self.root = root.absolute()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.root.lstat()
        if self.root.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError("Нужен собственный каталог с правами 0700, без symlink")
        self.root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.busy = asyncio.Semaphore(1)
        # Отдельная SQLite вне экспортируемого каталога, содержимое запросов не сохраняется.
        self.db = sqlite3.connect(":memory:")
        self.db.execute("CREATE TABLE audit(id TEXT, tool TEXT, result TEXT, ms REAL)")
        self.mcp = FastMCP(
            "OpenRemote Probe", host="127.0.0.1", port=8765,
            stateless_http=True, json_response=True,
            instructions="Проверочный Linux-стенд. execute_command запускает только echo_probe и write_probe. Произвольный shell не поддерживается. Не заявляй, что локальный тест подтверждает работу ChatGPT Plus.",
        )
        read = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                               idempotentHint=True, openWorldHint=False)
        write = ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                idempotentHint=False, openWorldHint=False)
        self.mcp.tool(annotations=read)(self.system_info)
        self.mcp.tool(annotations=read)(self.list_files)
        self.mcp.tool(annotations=write)(self.execute_command)

    def close(self) -> None:
        os.close(self.root_fd)
        self.db.close()

    def record(self, tool: str, result: str, started: float) -> str:
        operation_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO audit VALUES(?,?,?,?)", (
            operation_id, tool, result, (time.monotonic() - started) * 1000))
        self.db.execute("DELETE FROM audit WHERE rowid NOT IN (SELECT rowid FROM audit ORDER BY rowid DESC LIMIT 256)")
        self.db.commit()
        return operation_id

    def system_info(self) -> SystemInfo:
        """Используй для определения ОС и архитектуры проверочного устройства. Не возвращает hostname, IP, окружение или секреты."""
        started = time.monotonic()
        return {"os": platform.system(), "machine": platform.machine(),
                "probe_only": True, "operation_id": self.record("system_info", "ok", started)}

    def list_files(self, path: str = ".") -> FileList:
        """Используй для просмотра до 100 имён в закрытом тестовом каталоге. Абсолютные пути, '..' и переходы через symlink запрещены."""
        started = time.monotonic()
        fd = os.dup(self.root_fd)
        try:
            if path.startswith("/") or ".." in path.split("/") or len(path) > 512:
                raise ValueError("Недопустимый путь")
            for component in path.split("/"):
                if component in ("", "."):
                    continue
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            # Не собираем все имена огромного каталога в память.
            with os.scandir(fd) as entries:
                names = []
                for entry in entries:
                    names.append(entry.name)
                    if len(names) == 101:
                        break
            return {"files": sorted(names[:100]), "truncated": len(names) > 100,
                    "operation_id": self.record("list_files", "ok", started)}
        except (ValueError, OSError):
            self.record("list_files", "denied", started)
            raise ValueError("Каталог недоступен по политике стенда") from None
        finally:
            os.close(fd)

    async def execute_command(
        self, command: Literal["echo_probe", "write_probe"], nonce: str
    ) -> CommandResult:
        """Используй для реального запуска фиксированной безопасной команды. echo_probe печатает квитанцию; write_probe также создаёт новый файл <nonce>.receipt. Это write-инструмент; произвольные команды и shell запрещены."""
        started = time.monotonic()
        if command not in ("echo_probe", "write_probe") or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", nonce):
            self.record("execute_command", "denied", started)
            raise ValueError("Допустимы только две тестовые команды и nonce из 8–64 букв, цифр, '_' или '-'")
        async with self.busy:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-c", PROBE_PROGRAM, command, nonce,
                cwd=f"/proc/self/fd/{self.root_fd}", pass_fds=(self.root_fd,),
                env={"LANG": "C.UTF-8"}, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                if proc.returncode is None:
                    proc.kill()
                await proc.communicate()
                self.record("execute_command", "interrupted", started)
                raise
            operation_id = self.record("execute_command", "ok" if proc.returncode == 0 else "error", started)
            # Текст traceback не экспортируется: может содержать локальные пути.
            return {"operation_id": operation_id, "exit_code": proc.returncode,
                    "stdout": stdout.decode()[:4096], "stderr": "Ошибка тестовой команды" if stderr else "",
                    "receipt": f"{nonce}.receipt" if command == "write_probe" and proc.returncode == 0 else None}


class LocalBearer:
    """Только локальный тестовый bearer; это НЕ OAuth-интеграция ChatGPT."""
    def __init__(self, app, token: str):
        if len(token) < 32:
            raise ValueError("OPENREMOTE_PROBE_TOKEN должен содержать минимум 32 символа")
        self.app, self.expected = app, hashlib.sha256(("Bearer " + token).encode()).digest()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            supplied = hashlib.sha256(headers.get(b"authorization", b"")).digest()
            if not hmac.compare_digest(supplied, self.expected):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверочный Linux MCP-стенд OpenRemote")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--root", type=Path, default=Path(".probe"))
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() == 0:
        parser.error("Стенд требует Linux и обычного пользователя")
    probe = Probe(args.root)
    try:
        if args.transport == "stdio":
            probe.mcp.run("stdio")
        else:
            app = LocalBearer(probe.mcp.streamable_http_app(), os.environ.get("OPENREMOTE_PROBE_TOKEN", ""))
            uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning", access_log=False)
    finally:
        probe.close()


if __name__ == "__main__":
    main()
