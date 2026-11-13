"""Durable, owner-private workflow checkpoints for reconnecting MCP clients.

Only short metadata is stored, never prompts, credentials, audio or job logs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import time

from .jobs import JOB_ID, atomic_json

TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
STATES = {"active", "paused", "completed", "failed"}
MAX_TASKS = 250


class Checkpoints:
    def __init__(self, state: Path, coordinator):
        self.coordinator = coordinator
        self.root = state / "checkpoints"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if (self.root.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("Checkpoint directory must be private and owner-owned")

    @staticmethod
    def validate_id(task_id: str) -> None:
        if not isinstance(task_id, str) or TASK_ID.fullmatch(task_id) is None:
            raise ValueError("Invalid task ID")

    def _path(self, task_id: str) -> Path:
        self.validate_id(task_id)
        return self.root / (task_id + ".json")

    def get(self, task_id: str) -> dict:
        path = self._path(task_id)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown checkpoint")
        info = path.stat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise ValueError("Invalid checkpoint permissions")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("task_id") != task_id:
            raise ValueError("Invalid checkpoint identity")
        return data

    def save(self, task_id: str, stage: str, status: str = "active",
             summary: str = "", job_id: str | None = None,
             expected_revision: int = 0) -> dict:
        path = self._path(task_id)
        if not isinstance(stage, str) or STAGE.fullmatch(stage) is None:
            raise ValueError("Invalid checkpoint stage")
        if not isinstance(status, str) or status not in STATES:
            raise ValueError("Invalid checkpoint status")
        if (not isinstance(summary, str) or len(summary) > 240
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in summary)):
            raise ValueError("Summary must be 0-240 printable characters")
        if job_id is not None and (
                not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None):
            raise ValueError("Invalid job ID")
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Invalid checkpoint revision")
        fields = {"task_id": task_id, "stage": stage, "status": status,
                  "summary": summary, "job_id": job_id}
        with self.coordinator.lock("checkpoint", task_id):
            if path.is_symlink():
                raise ValueError("Invalid checkpoint path")
            current = self.get(task_id) if path.exists() else None
            if current:
                revision = current["revision"]
                if expected_revision == revision - 1 and all(
                        current.get(k) == v for k, v in fields.items()):
                    return {**current, "reused": True}
                if expected_revision != revision:
                    raise ValueError("Checkpoint revision conflict")
            elif expected_revision != 0:
                raise ValueError("Checkpoint revision conflict")
            elif sum(1 for f in self.root.glob("*.json")
                     if f.is_file() and not f.is_symlink()) >= MAX_TASKS:
                raise ValueError("Too many checkpoints")
            record = {**fields,
                      "revision": current["revision"] + 1 if current else 1,
                      "updated_at": int(time.time())}
            atomic_json(path, record)
            return {**record, "reused": False}

    def list(self, limit: int = 20) -> dict:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Checkpoint list limit must be 1-50")
        files = sorted(self.root.glob("*.json"),
                       key=lambda p: p.stat().st_mtime if not p.is_symlink() else 0,
                       reverse=True)
        results = []
        for path in files:
            if len(results) >= limit:
                break
            try:
                results.append(self.get(path.stem))
            except (OSError, ValueError, KeyError):
                continue
        return {"tasks": results}
