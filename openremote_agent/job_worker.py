"""Detached worker for Jobs. Spawned with no terminal or inherited MCP pipes."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import time
import uuid

MAX_LOG = 8 * 1024 * 1024


def save_result(folder: Path, value: dict) -> None:
    dest = folder / "result.json"
    temp = folder / (".result-" + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fp:
        json.dump(value, fp, ensure_ascii=False)
        fp.flush()
        os.fsync(fp.fileno())
    os.replace(temp, dest)


async def execute(folder: Path) -> None:
    spec_path = folder / "spec.json"
    spec = json.loads(spec_path.read_text())
    spec_path.unlink()
    cancel = folder / "cancel"
    running = None
    cancelled = False

    def stop_child():
        if running is not None and running.returncode is None:
            try:
                os.killpg(running.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def terminate(*_):
        nonlocal cancelled
        cancelled = True
        stop_child()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    async def capture(source, name):
        saved = 0
        clipped = False
        out = folder / (name + ".log")
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = await source.read(65536)
                if not chunk:
                    break
                allowance = max(0, MAX_LOG - saved)
                if allowance:
                    part = chunk[:allowance]
                    handle.write(part)
                    saved += len(part)
                if len(chunk) > allowance:
                    clipped = True
            handle.flush()
            os.fsync(handle.fileno())
        return clipped

    if cancel.exists():
        save_result(folder, {"status": "cancelled", "exit_code": None,
                             "ended_at": int(time.time()),
                             "stdout_truncated": False, "stderr_truncated": False})
        return

    try:
        running = await asyncio.create_subprocess_exec(
            *spec["argv"], cwd=spec["cwd"],
            env={"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin",
                 "HOME": os.path.expanduser("~")},
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        save_result(folder, {"status": "spawn_error", "exit_code": None,
                             "ended_at": int(time.time()),
                             "stdout_truncated": False, "stderr_truncated": False})
        return

    stdout_task = asyncio.create_task(capture(running.stdout, "stdout"))
    stderr_task = asyncio.create_task(capture(running.stderr, "stderr"))
    deadline = time.monotonic() + spec["timeout_seconds"]
    timed_out = False
    stopping_since = None

    while running.returncode is None:
        if cancel.exists():
            cancelled = True
        if time.monotonic() >= deadline:
            timed_out = True
        if (cancelled or timed_out) and stopping_since is None:
            stop_child()
            stopping_since = time.monotonic()
        if stopping_since is not None and time.monotonic() - stopping_since > 2.0:
            try:
                os.killpg(running.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(running.wait(), timeout=0.2)
        except TimeoutError:
            pass

    # The child could spawn descendants with inherited pipes. Drain only a
    # bounded time, then close the streams to avoid blocking indefinitely.
    try:
        out_clip, err_clip = await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task), timeout=3.0)
    except TimeoutError:
        out_clip, err_clip = True, True
        for task in (stdout_task, stderr_task):
            task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)

    status = ("cancelled" if cancelled or cancel.exists() else
              "timed_out" if timed_out else
              "completed" if running.returncode == 0 else "failed")
    save_result(folder, {"status": status, "exit_code": running.returncode,
                         "ended_at": int(time.time()),
                         "stdout_truncated": out_clip, "stderr_truncated": err_clip})


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(2)
    folder = Path(sys.argv[1])
    if folder.is_symlink() or not folder.is_dir():
        raise SystemExit(2)
    try:
        asyncio.run(execute(folder))
    except BaseException:
        # Best effort: caller can still report "interrupted" if worker dies.
        try:
            save_result(folder, {"status": "worker_error", "exit_code": None,
                                 "ended_at": int(time.time()),
                                 "stdout_truncated": False, "stderr_truncated": False})
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
