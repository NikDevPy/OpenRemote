"""Cross-process advisory coordination for file mutations and admission control.

Protects operations performed by cooperating OpenRemote agents, not external
programs that ignore advisory locks. Locks live outside exposed workspaces.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import time

from .filesystem import SafeFiles


class Coordinator:
    def __init__(self, state: Path):
        self.root = state / "locks"
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if (self.root.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ValueError("Lock directory must be private (0700) and owner-owned")

    @contextmanager
    def lock(self, namespace: str, key: str, timeout: float = 4.0):
        if not isinstance(namespace, str) or not isinstance(key, str):
            raise ValueError("Invalid lock identity")
        digest = hashlib.sha256((namespace + "\x00" + key).encode("utf-8")).hexdigest()
        file = self.root / (digest + ".lock")
        fd = os.open(file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Invalid lock file permissions")
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Resource busy: concurrent OpenRemote operation")
                    time.sleep(0.025)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextmanager
    def file(self, workspace: str, path: str, timeout: float = 4.0):
        # Both text and binary operations use the SAME namespace, so a stale
        # expected SHA-256 cannot win when two OpenRemote processes race.
        with self.lock("file", workspace + "\x00" + "/".join(SafeFiles.parts(path)), timeout=timeout):
            yield

    @contextmanager
    def slot(self, namespace: str, limit: int = 4, timeout: float = 2.0):
        """Bound simultaneous short OS commands across all ChatGPT chats.

        Use file-descriptor advisory locks: the kernel releases them if a
        process dies. This is not an authentication or OS sandbox.
        """
        if not 1 <= limit <= 16:
            raise ValueError("Invalid process concurrency limit")
        deadline = time.monotonic() + timeout
        selected = None
        while selected is None:
            for index in range(limit):
                file = self.root / (hashlib.sha256(
                    ("slot:" + namespace + ":" + str(index)).encode()
                ).hexdigest() + ".lock")
                fd = os.open(file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                try:
                    st = os.fstat(fd)
                    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                        raise ValueError("Invalid concurrency slot permissions")
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    selected = fd
                    break
                except BlockingIOError:
                    os.close(fd)
                except BaseException:
                    os.close(fd)
                    raise
            if selected is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Concurrent command capacity reached; retry later")
                time.sleep(0.04)
        try:
            yield
        finally:
            fcntl.flock(selected, fcntl.LOCK_UN)
            os.close(selected)
