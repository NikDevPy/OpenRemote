"""Durable, device-local process jobs, independent from an MCP stdio connection.

Jobs run with the agent's OS identity. They are NOT a privilege boundary.
"""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_JOB_SECONDS = 21600
MAX_OUTPUT_CHUNK = 16384
MAX_ACTIVE_JOBS = 8
MAX_STORED_JOBS = 250


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def pid_start(pid: int) -> str | None:
    """Linux /proc starttime prevents signalling a recycled PID."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
        fields = data[data.rfind(") ") + 2:].split()
        return fields[19]
    except (OSError, IndexError, ValueError):
        return None


class Jobs:
    def __init__(self, state: Path, coordinator=None):
        from .coordination import Coordinator
        self.coordinator = coordinator or Coordinator(state)
        self.root = state / "jobs"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.root.lstat()
        if (self.root.is_symlink() or not stat.S_ISDIR(st.st_mode) or
                st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise ValueError("Job state must be a private owner-only directory")

    def _folder(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or JOB_ID.fullmatch(job_id) is None:
            raise ValueError("Invalid job ID")
        folder = self.root / job_id
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError("Unknown job ID")
        return folder

    @staticmethod
    def _load(path: Path) -> dict:
        if path.is_symlink():
            raise ValueError("Invalid job metadata")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _alive(meta: dict) -> bool:
        return pid_start(meta.get("worker_pid", -1)) == meta.get("worker_start_ticks")

    def start(self, argv: list[str], cwd: str, timeout_seconds: int = 3600,
              task_label: str | None = None,
              idempotency_key: str | None = None) -> dict:
        """Admit one background job atomically across concurrent MCP processes.

        Caller-provided labels are organizational only, not authenticated chat
        identity. Idempotency keys protect against retries and duplicate starts.
        """
        if (not isinstance(argv, list) or not 1 <= len(argv) <= 40 or
                not all(isinstance(arg, str) and arg and "\x00" not in arg for arg in argv) or
                sum(map(len, argv)) > 8192 or not os.path.isabs(argv[0])):
            raise ValueError("Need an absolute executable and 1–40 bounded argv")
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= MAX_JOB_SECONDS:
            raise ValueError("Job timeout must be 1–21600 seconds")
        if not os.path.isdir(cwd):
            raise ValueError("Working directory missing")
        if task_label is not None and (not isinstance(task_label, str)
                or len(task_label) > 64 or not task_label
                or not all(c.isprintable() for c in task_label)):
            raise ValueError("Task label must be 1–64 printable characters")
        if idempotency_key is not None and (not isinstance(idempotency_key, str)
                or not re.fullmatch(r"[a-zA-Z0-9_.:-]{8,128}", idempotency_key)):
            raise ValueError("Idempotency key must be 8–128 safe characters")
        key_hash = (hashlib.sha256(idempotency_key.encode()).hexdigest()
                    if idempotency_key else None)
        fingerprint = hashlib.sha256(json.dumps(
            [argv, cwd, timeout_seconds, task_label],
            separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

        with self.coordinator.lock("jobs", "admission", timeout=5):
            previous = [p for p in self.root.iterdir()
                        if p.is_dir() and JOB_ID.fullmatch(p.name)]
            if key_hash is not None:
                for p in previous:
                    try:
                        info = self._load(p / "meta.json")
                    except (OSError, ValueError):
                        continue
                    if info.get("idempotency_hash") == key_hash:
                        if info.get("request_fingerprint") != fingerprint:
                            raise ValueError("Idempotency key already belongs to a different command")
                        return {"job_id": p.name,
                                "status": self.status(p.name)["status"],
                                "timeout_seconds": info["timeout_seconds"],
                                "task_label": info.get("task_label"),
                                "reused": True}
            if len(previous) >= MAX_STORED_JOBS:
                raise ValueError("Job history limit reached; purge finished jobs")
            active = 0
            for previous_folder in previous:
                try:
                    meta = self._load(previous_folder / "meta.json")
                    if not (previous_folder / "result.json").exists() and self._alive(meta):
                        active += 1
                except (ValueError, OSError):
                    continue
            if active >= MAX_ACTIVE_JOBS:
                raise ValueError("Too many active jobs; wait or cancel a job")
            job_id = uuid.uuid4().hex
            folder = self.root / job_id
            folder.mkdir(mode=0o700)
            spec = {"argv": argv, "cwd": cwd, "timeout_seconds": timeout_seconds}
            atomic_json(folder / "spec.json", spec)
            try:
                # Detached worker survives its caller's MCP/SSH connection.
                devnull = os.open(os.devnull, os.O_RDWR)
                try:
                    pid = os.posix_spawn(
                        sys.executable,
                        [sys.executable, str(Path(__file__).with_name("job_worker.py")), str(folder)],
                        {"PATH": "/usr/bin:/bin", "HOME": os.path.expanduser("~"), "LANG": "C.UTF-8"},
                        file_actions=[
                            (os.POSIX_SPAWN_DUP2, devnull, 0),
                            (os.POSIX_SPAWN_DUP2, devnull, 1),
                            (os.POSIX_SPAWN_DUP2, devnull, 2),
                            (os.POSIX_SPAWN_CLOSE, devnull),
                        ],
                        setsid=True,
                    )
                finally:
                    os.close(devnull)
                ticks = pid_start(pid)
                meta = {"job_id": job_id, "worker_pid": pid,
                        "worker_start_ticks": ticks,
                        "started_at": int(time.time()),
                        "timeout_seconds": timeout_seconds, "task_label": task_label,
                        "idempotency_hash": key_hash, "request_fingerprint": fingerprint}
                atomic_json(folder / "meta.json", meta)
                return {"job_id": job_id, "status": "starting",
                        "timeout_seconds": timeout_seconds,
                        "task_label": task_label, "reused": False}
            except BaseException:
                for item in folder.iterdir():
                    item.unlink()
                folder.rmdir()
                raise

    def status(self, job_id: str) -> dict:
        folder = self._folder(job_id)
        meta = self._load(folder / "meta.json")
        result_path = folder / "result.json"
        if result_path.exists():
            result = self._load(result_path)
            status = result["status"]
            exit_code = result.get("exit_code")
        elif self._alive(meta):
            status = "cancelling" if (folder / "cancel").exists() else "running"
            exit_code = None
        else:
            status = "cancelled" if (folder / "cancel").exists() else "interrupted"
            exit_code = None
        return {
            "job_id": job_id, "status": status, "exit_code": exit_code,
            "task_label": meta.get("task_label"),
            "started_at": meta["started_at"], "timeout_seconds": meta["timeout_seconds"],
            "stdout_bytes": (folder / "stdout.log").stat().st_size if (folder / "stdout.log").exists() else 0,
            "stderr_bytes": (folder / "stderr.log").stat().st_size if (folder / "stderr.log").exists() else 0,
            "stdout_truncated": bool(self._load(result_path).get("stdout_truncated")) if result_path.exists() else False,
            "stderr_truncated": bool(self._load(result_path).get("stderr_truncated")) if result_path.exists() else False,
        }

    def list(self, limit: int = 20) -> dict:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("Limit must be 1–50")
        ids = sorted((x for x in self.root.iterdir() if x.is_dir() and JOB_ID.fullmatch(x.name)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for p in ids[:limit]:
            try:
                result.append(self.status(p.name))
            except (OSError, ValueError):
                continue
        return {"jobs": result}

    def output(self, job_id: str, stream: str = "stdout", offset: int = 0,
               limit_bytes: int = 8192) -> dict:
        folder = self._folder(job_id)
        if stream not in ("stdout", "stderr"):
            raise ValueError("Stream must be stdout or stderr")
        if type(offset) is not int or offset < 0 or type(limit_bytes) is not int or not 1 <= limit_bytes <= MAX_OUTPUT_CHUNK:
            raise ValueError("Invalid offset/limit")
        log = folder / (stream + ".log")
        fd = os.open(log, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK) if log.exists() else None
        if fd is None:
            return {"text": "", "next_offset": offset, "end_of_stream": False}
        try:
            os.lseek(fd, offset, os.SEEK_SET)
            chunk = os.read(fd, limit_bytes)
            size = os.fstat(fd).st_size
            return {"text": chunk.decode("utf-8", errors="replace"),
                    "next_offset": offset + len(chunk), "end_of_stream": offset + len(chunk) >= size}
        finally:
            os.close(fd)

    def purge(self, job_id: str) -> dict:
        """Delete finished job metadata/logs (not arbitrary filesystem paths)."""
        folder = self._folder(job_id)
        status = self.status(job_id)["status"]
        if status in ("running", "starting", "cancelling"):
            raise ValueError("Cancel/wait for the job before removing its logs")
        allowed = {"meta.json", "result.json", "stdout.log", "stderr.log", "spec.json", "cancel"}
        for item in folder.iterdir():
            if item.name not in allowed or item.is_symlink() or not item.is_file():
                raise ValueError("Unexpected file in private job directory; refusing purge")
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
        return {"job_id": job_id, "removed": True}

    def cancel(self, job_id: str) -> dict:
        folder = self._folder(job_id)
        meta = self._load(folder / "meta.json")
        current = self.status(job_id)
        if current["status"] not in ("running", "cancelling"):
            return {"job_id": job_id, "status": current["status"], "signal_sent": False}
        marker = folder / "cancel"
        fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        if self._alive(meta):
            # A pidfd pins the actual process, avoiding PID-reuse races between
            # checking /proc and signalling. Linux 5.3+ / Python 3.11+.
            try:
                pidfd = os.pidfd_open(meta["worker_pid"], 0)
                try:
                    if pid_start(meta["worker_pid"]) != meta["worker_start_ticks"]:
                        return {"job_id": job_id, "status": "interrupted", "signal_sent": False}
                    signal.pidfd_send_signal(pidfd, signal.SIGTERM)
                finally:
                    os.close(pidfd)
                return {"job_id": job_id, "status": "cancelling", "signal_sent": True}
            except ProcessLookupError:
                pass
        return {"job_id": job_id, "status": "interrupted", "signal_sent": False}
