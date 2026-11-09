"""Постоянный аудит без содержимого файлов, команд, аргументов и секретов."""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import time
import uuid


class Audit:
    def __init__(self, state: Path):
        state = state.absolute()
        state.mkdir(parents=True, mode=0o700, exist_ok=True)
        st = state.lstat()
        if state.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError("Каталог аудита должен иметь права 0700")
        path = state / "audit.sqlite3"
        if path.is_symlink():
            raise ValueError("Символьная ссылка для базы аудита запрещена")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if path.stat().st_mode & 0o077:
            raise ValueError("База аудита доступна другим пользователям")
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS audit ("
            "id TEXT PRIMARY KEY, ts INTEGER NOT NULL, tool TEXT NOT NULL,"
            "result TEXT NOT NULL, duration_ms REAL NOT NULL)"
        )
        self.db.commit()

    def record(self, tool: str, result: str, started: float) -> str:
        operation_id = str(uuid.uuid4())
        self.db.execute("INSERT INTO audit VALUES(?,?,?,?,?)", (
            operation_id, int(time.time()), tool, result, round((time.monotonic()-started)*1000, 2)))
        self.db.commit()
        return operation_id

    def tail(self, limit: int = 20) -> list[dict]:
        if limit < 1 or limit > 100:
            raise ValueError("Лимит: 1–100")
        cur = self.db.execute("SELECT id,ts,tool,result,duration_ms FROM audit ORDER BY ts DESC,rowid DESC LIMIT ?", (limit,))
        return [dict(zip(("id", "timestamp", "tool", "result", "duration_ms"), row)) for row in cur]

    def close(self) -> None:
        self.db.close()
