"""Optional fail-closed health monitor for the local OpenAI tunnel client.

Only a loopback /readyz endpoint may be probed. Restarts are disabled by
default and require a fixed, owner-selected systemd user unit.
"""
from __future__ import annotations

import argparse
import fcntl
import http.client
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import urlsplit

from .jobs import atomic_json

SERVICE = re.compile(r"openremote-[a-zA-Z0-9_-]*tunnel\.service\Z")


def parse_readyz(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "::1")
            or parsed.path != "/readyz" or parsed.query or parsed.fragment
            or parsed.username or parsed.password):
        raise ValueError("Only a loopback HTTP /readyz URL is allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid health port") from exc
    if port is None or not 1 <= port <= 65535:
        raise ValueError("An explicit loopback port is required")
    return parsed.hostname, port


def probe(url: str) -> tuple[bool, str]:
    host, port = parse_readyz(url)
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request("GET", "/readyz")
        response = conn.getresponse()
        response.read(1024)
        return (True, "ready") if response.status == 200 else (
            False, "http_" + str(response.status))
    except (OSError, http.client.HTTPException):
        return False, "unreachable"
    finally:
        conn.close()


def check(url: str, state_dir: Path, service: str = "openremote-agent-tunnel.service",
          *, allow_restart: bool = False, failures: int = 3,
          cooldown_seconds: int = 300) -> dict:
    parse_readyz(url)
    if not isinstance(service, str) or not SERVICE.fullmatch(service):
        raise ValueError("Only a named OpenRemote tunnel user service is allowed")
    if type(failures) is not int or not 1 <= failures <= 20:
        raise ValueError("Invalid failure threshold")
    if type(cooldown_seconds) is not int or cooldown_seconds < 60:
        raise ValueError("Cooldown must be at least 60 seconds")
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = state_dir.lstat()
    if (state_dir.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("Monitor state must be owner-private")
    lock = state_dir / "monitor.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        path = state_dir / "status.json"
        if path.is_symlink():
            raise ValueError("Monitor state cannot be a symlink")
        status = json.loads(path.read_text()) if path.exists() else {}
        healthy, reason = probe(url)
        now = int(time.time())
        count = 0 if healthy else status.get("consecutive_failures", 0) + 1
        if type(count) is not int or count < 0:
            count = 1
        result = {
            "checked_at": now, "healthy": healthy, "reason": reason,
            "consecutive_failures": count,
            "last_healthy": now if healthy else status.get("last_healthy"),
            "last_restart": status.get("last_restart", 0),
            "restart_attempts": status.get("restart_attempts", 0),
            "restarted": False,
        }
        if (allow_restart and not healthy and count >= failures
                and now - result["last_restart"] >= cooldown_seconds):
            # Never restart arbitrary service names or system units.
            proc = subprocess.run(["systemctl", "--user", "restart", service],
                                  capture_output=True, timeout=12, check=False)
            result["restart_attempts"] += 1
            result["last_restart"] = now
            result["restarted"] = proc.returncode == 0
            result["reason"] = "restart_requested" if result["restarted"] else "restart_failed"
        atomic_json(path, result)
        return result
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenRemote loopback tunnel health monitor")
    parser.add_argument("--url", default="http://127.0.0.1:18767/readyz")
    parser.add_argument("--state-dir", type=Path,
                        default=Path.home() / ".local/state/openremote-tunnel")
    parser.add_argument("--service", default="openremote-agent-tunnel.service")
    parser.add_argument("--allow-restart", action="store_true",
                        help="Explicitly enable bounded user-service restarts")
    parser.add_argument("--failures", type=int, default=3)
    parser.add_argument("--cooldown-seconds", type=int, default=300)
    args = parser.parse_args()
    result = check(args.url, args.state_dir, args.service,
                   allow_restart=args.allow_restart, failures=args.failures,
                   cooldown_seconds=args.cooldown_seconds)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
