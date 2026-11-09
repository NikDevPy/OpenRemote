"""Постоянный аудит без содержимого файлов, команд, аргументов и секретов."""
from __future__ import annotations

import os
import fcntl
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
        # Serialize one-time journal/DDL migration across independent MCP
        # processes. SQLite busy_timeout alone cannot guard concurrent changes
        # to journal_mode or schema during the very first connection.
        lockfile = state / ".audit-schema.lock"
        lockfd = os.open(lockfile, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if os.fstat(lockfd).st_mode & 0o077:
                raise ValueError("Unsafe audit schema lock permissions")
            fcntl.flock(lockfd, fcntl.LOCK_EX)
            try:
                self.db = sqlite3.connect(path, timeout=15.0)
                self.db.execute("PRAGMA busy_timeout=15000")
                self.db.execute("PRAGMA journal_mode=WAL")
                self.db.execute("PRAGMA synchronous=NORMAL")
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS audit ("
                    "id TEXT PRIMARY KEY, ts INTEGER NOT NULL, tool TEXT NOT NULL,"
                    "result TEXT NOT NULL, duration_ms REAL NOT NULL)"
                )
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS command_approvals ("
                    "command_id TEXT PRIMARY KEY, expires_at INTEGER NOT NULL, "
                    "command_digest TEXT NOT NULL DEFAULT '')"
                )
                cols = {row[1] for row in self.db.execute("PRAGMA table_info(command_approvals)")}
                if "command_digest" not in cols:
                    self.db.execute("ALTER TABLE command_approvals "
                                    "ADD COLUMN command_digest TEXT NOT NULL DEFAULT ''")
                self.db.execute(
                    "CREATE TABLE IF NOT EXISTS peer_probes ("
                    "device_id TEXT PRIMARY KEY, checked_at INTEGER NOT NULL,"
                    "online INTEGER NOT NULL)"
                )
                self.db.commit()
            finally:
                fcntl.flock(lockfd, fcntl.LOCK_UN)
        finally:
            os.close(lockfd)

    def grant_command(self, command_id: str, command_digest: str, ttl: int = 120) -> None:
        """Локальное разовое разрешение. Не открывать как MCP-инструмент."""
        if not (1 <= ttl <= 300):
            raise ValueError("Срок разрешения: 1–300 секунд")
        if len(command_digest) != 64 or any(c not in "0123456789abcdef" for c in command_digest):
            raise ValueError("Требуется SHA-256 отпечаток точной команды")
        with self.db:
            self.db.execute(
                "INSERT INTO command_approvals(command_id,expires_at,command_digest) VALUES(?,?,?) "
                "ON CONFLICT(command_id) DO UPDATE SET "
                "expires_at=excluded.expires_at,command_digest=excluded.command_digest",
                (command_id, int(time.time()) + ttl, command_digest),
            )

    def command_approved(self, command_id: str, command_digest: str) -> bool:
        record = self.db.execute(
            "SELECT 1 FROM command_approvals WHERE command_id=? AND command_digest=? AND expires_at>?",
            (command_id, command_digest, int(time.time())),
        ).fetchone()
        return record is not None

    def consume_command(self, command_id: str, command_digest: str) -> bool:
        """Атомарно погасить разрешение только для одобренной точной команды."""
        with self.db:
            cursor = self.db.execute(
                "DELETE FROM command_approvals WHERE command_id=? AND command_digest=? AND expires_at>?",
                (command_id, command_digest, int(time.time())),
            )
        return cursor.rowcount == 1

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

    def probe_get(self, device_id: str, max_age_seconds: int = 20) -> dict | None:
        row = self.db.execute(
            "SELECT checked_at, online FROM peer_probes WHERE device_id=?",
            (device_id,),
        ).fetchone()
        if row is None or int(time.time()) - row[0] >= max_age_seconds:
            return None
        return {"device_id": device_id, "checked_at": row[0],
                "online": bool(row[1])}

    def probe_put(self, device_id: str, online: bool) -> dict:
        when = int(time.time())
        with self.db:
            self.db.execute(
                "INSERT INTO peer_probes(device_id,checked_at,online) VALUES(?,?,?) "
                "ON CONFLICT(device_id) DO UPDATE SET "
                "checked_at=excluded.checked_at, online=excluded.online",
                (device_id, when, int(online)),
            )
        return {"device_id": device_id, "checked_at": when, "online": online}

    def close(self) -> None:
        self.db.close()
