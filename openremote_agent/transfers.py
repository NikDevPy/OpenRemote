"""Resumable bounded binary transfer inside owner-approved file workspaces."""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time
import uuid

from .jobs import atomic_json

UPLOAD_ID = re.compile(r"[0-9a-f]{32}\Z")
MAX_BINARY = 1024 * 1024 * 1024
MAX_CHUNK = 131072
MAX_SESSIONS = 8
SESSION_SECONDS = 24 * 3600


def validate_digest(value: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("Expected lowercase hex SHA-256")


def file_hash(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        part = os.read(fd, 1 << 20)
        if not part:
            break
        digest.update(part)
    return digest.hexdigest()


def checked_file(area, path: str) -> tuple[int, int]:
    parts = area.parts(path)
    parent = area.parent_fd(parts)
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise ValueError("Only ordinary files can be transferred")
        if info.st_size > MAX_BINARY:
            os.close(fd)
            raise ValueError("File exceeds the 1 GiB transfer limit")
        return parent, fd
    except BaseException:
        os.close(parent)
        raise


class Transfers:
    def __init__(self, state: Path, area_provider):
        self.root = state / "uploads"
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.root.lstat()
        if (self.root.is_symlink() or not stat.S_ISDIR(st.st_mode) or
                st.st_uid != os.getuid() or st.st_mode & 0o077):
            raise ValueError("Upload state must be a private owner-only directory")
        self.area_provider = area_provider

    def _manifest_path(self, upload_id: str) -> Path:
        if not isinstance(upload_id, str) or not UPLOAD_ID.fullmatch(upload_id):
            raise ValueError("Invalid upload ID")
        path = self.root / (upload_id + ".json")
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unknown upload ID")
        return path

    @contextmanager
    def _locked(self, upload_id, allow_expired: bool = False):
        path = self._manifest_path(upload_id)
        lock = self.root / (upload_id + ".lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            info = json.loads(path.read_text(encoding="utf-8"))
            if not allow_expired and int(time.time()) > info["expires_at"]:
                raise ValueError("Upload expired; cancel and restart")
            yield path, info
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def prune_expired(self) -> int:
        """Best-effort cleanup of expired uploads in currently accessible workspaces."""
        count = 0
        for manifest in list(self.root.glob("*.json"))[:256]:
            try:
                with self._locked(manifest.stem, allow_expired=True) as (_, data):
                    expired = int(time.time()) > data["expires_at"]
                if expired:
                    self.cancel(manifest.stem)
                    count += 1
            except (OSError, ValueError, PermissionError, KeyError):
                # Broken/inaccessible state is left for manual inspection.
                pass
        return count

    def begin(self, workspace: str, path: str, total_size: int,
              sha256: str, expected_existing_sha256: str | None = None) -> dict:
        if type(total_size) is not int or not 0 <= total_size <= MAX_BINARY:
            raise ValueError("Upload size must be 0–1 GiB")
        validate_digest(sha256)
        if expected_existing_sha256 is not None:
            validate_digest(expected_existing_sha256)
        self.prune_expired()
        if len(list(self.root.glob("*.json"))) >= MAX_SESSIONS:
            raise ValueError("Too many pending uploads; finish or cancel a transfer")
        upload_id = uuid.uuid4().hex
        temp_name = ".openremote-upload-" + upload_id
        with self.area_provider(workspace, writing=True) as area:
            parts = area.parts(path)
            parent = area.parent_fd(parts)
            try:
                try:
                    info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    info = None
                if info is None and expected_existing_sha256 is not None:
                    raise ValueError("No existing file to replace")
                if info is not None:
                    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                        raise ValueError("Existing target is not an owned ordinary file")
                    if expected_existing_sha256 is None:
                        raise ValueError("Target exists; supply expected_existing_sha256")
                fd = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent)
                os.close(fd)
            finally:
                os.close(parent)
        data = {
            "upload_id": upload_id, "workspace": workspace, "path": path,
            "temp_name": temp_name, "total_size": total_size,
            "sha256": sha256, "expected_existing_sha256": expected_existing_sha256,
            "received": 0, "expires_at": int(time.time()) + SESSION_SECONDS,
        }
        try:
            atomic_json(self.root / (upload_id + ".json"), data)
        except BaseException:
            with self.area_provider(workspace, writing=True) as area:
                parts = area.parts(path)
                parent = area.parent_fd(parts)
                try:
                    os.unlink(temp_name, dir_fd=parent)
                finally:
                    os.close(parent)
            raise
        return {"upload_id": upload_id, "received": 0, "total_size": total_size}

    def status(self, upload_id: str) -> dict:
        with self._locked(upload_id) as (_, data):
            return {"upload_id": upload_id, "received": data["received"],
                    "total_size": data["total_size"], "expires_at": data["expires_at"]}

    def append(self, upload_id: str, offset: int, data_base64: str) -> dict:
        if type(offset) is not int or offset < 0:
            raise ValueError("Invalid chunk offset")
        if not isinstance(data_base64, str) or len(data_base64) > (4 * ((MAX_CHUNK + 2) // 3)):
            raise ValueError("Chunk encoded length exceeds limit")
        try:
            payload = base64.b64decode(data_base64, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Invalid base64 transfer chunk") from None
        if not payload or len(payload) > MAX_CHUNK:
            raise ValueError("Upload chunks must be 1–131072 bytes")
        with self._locked(upload_id) as (manifest_path, data):
            if offset != data["received"] or offset + len(payload) > data["total_size"]:
                raise ValueError("Upload offset or chunk size mismatch")
            with self.area_provider(data["workspace"], writing=True) as area:
                parts = area.parts(data["path"])
                parent = area.parent_fd(parts)
                try:
                    fd = os.open(data["temp_name"], os.O_WRONLY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        st = os.fstat(fd)
                        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size != offset:
                            raise ValueError("Upload temporary file has unexpected state")
                        os.lseek(fd, offset, os.SEEK_SET)
                        remaining = payload
                        while remaining:
                            count = os.write(fd, remaining)
                            remaining = remaining[count:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                finally:
                    os.close(parent)
            data["received"] += len(payload)
            atomic_json(manifest_path, data)
            return {"upload_id": upload_id, "received": data["received"],
                    "total_size": data["total_size"]}

    def target(self, upload_id: str) -> tuple[str, str]:
        """Read immutable upload target for the shared cross-process file lock."""
        with self._locked(upload_id) as (_, info):
            return info["workspace"], info["path"]

    def finish(self, upload_id: str) -> dict:
        with self._locked(upload_id) as (manifest_path, data):
            if data["received"] != data["total_size"]:
                raise ValueError("Incomplete transfer: resume uploading before finish")
            with self.area_provider(data["workspace"], writing=True) as area:
                parts = area.parts(data["path"])
                parent = area.parent_fd(parts)
                try:
                    fd = os.open(data["temp_name"], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        st = os.fstat(fd)
                        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_size != data["total_size"]:
                            raise ValueError("Unexpected upload temporary file")
                        if file_hash(fd) != data["sha256"]:
                            raise ValueError("Upload SHA-256 mismatch")
                    finally:
                        os.close(fd)
                    name = parts[-1]
                    if data["expected_existing_sha256"] is None:
                        # A hard-link creation is atomic and fails if the target exists.
                        os.link(data["temp_name"], name, src_dir_fd=parent,
                                dst_dir_fd=parent, follow_symlinks=False)
                        os.unlink(data["temp_name"], dir_fd=parent)
                    else:
                        target = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                        try:
                            previous = os.fstat(target)
                            if not stat.S_ISREG(previous.st_mode) or previous.st_uid != os.getuid():
                                raise ValueError("Cannot overwrite non-owned or special file")
                            if previous.st_size > MAX_BINARY or file_hash(target) != data["expected_existing_sha256"]:
                                raise ValueError("Existing file changed; replacement refused")
                            latest = os.stat(name, dir_fd=parent, follow_symlinks=False)
                            if (previous.st_ino, previous.st_mtime_ns, previous.st_size, previous.st_dev) != (
                                    latest.st_ino, latest.st_mtime_ns, latest.st_size, latest.st_dev):
                                raise ValueError("Concurrent change detected")
                            os.chmod(data["temp_name"], stat.S_IMODE(previous.st_mode),
                                     dir_fd=parent, follow_symlinks=False)
                            os.replace(data["temp_name"], name, src_dir_fd=parent, dst_dir_fd=parent)
                        finally:
                            os.close(target)
                    os.fsync(parent)
                finally:
                    os.close(parent)
            manifest_path.unlink()
            return {"upload_id": upload_id, "completed": True,
                    "size": data["total_size"], "sha256": data["sha256"]}

    def cancel(self, upload_id: str) -> dict:
        with self._locked(upload_id, allow_expired=True) as (manifest_path, data):
            with self.area_provider(data["workspace"], writing=True) as area:
                parts = area.parts(data["path"])
                parent = area.parent_fd(parts)
                try:
                    os.unlink(data["temp_name"], dir_fd=parent)
                finally:
                    os.close(parent)
            manifest_path.unlink()
            return {"upload_id": upload_id, "cancelled": True}

    def file_info(self, workspace: str, path: str) -> dict:
        with self.area_provider(workspace) as area:
            parent, fd = checked_file(area, path)
            try:
                size = os.fstat(fd).st_size
                digest = file_hash(fd)
                return {"size": size, "sha256": digest}
            finally:
                os.close(fd)
                os.close(parent)

    def download(self, workspace: str, path: str, offset: int = 0,
                 max_bytes: int = 16384) -> dict:
        if type(offset) is not int or offset < 0 or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_CHUNK:
            raise ValueError("Invalid download offset or length")
        with self.area_provider(workspace) as area:
            parent, fd = checked_file(area, path)
            try:
                size = os.fstat(fd).st_size
                if offset > size:
                    raise ValueError("Offset exceeds file size")
                os.lseek(fd, offset, os.SEEK_SET)
                data = os.read(fd, max_bytes)
                return {"data_base64": base64.b64encode(data).decode("ascii"),
                        "offset": offset, "next_offset": offset + len(data),
                        "total_size": size, "eof": offset + len(data) >= size}
            finally:
                os.close(fd)
                os.close(parent)
