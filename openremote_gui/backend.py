"""Low-overhead local dashboard backend; never reads secrets or command argv.

No MCP/SSH polling in the periodic snapshot. Peer probes are explicit actions.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shlex
import subprocess
import time
from urllib.parse import urlsplit
from urllib.request import urlopen

from openremote_agent.config import load_config
from openremote_agent.jobs import pid_start


def _read(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Untrusted state file")
    return json.loads(path.read_text(encoding="utf-8"))


def _health(url: str) -> bool:
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost"):
        raise ValueError("Only loopback health URLs are allowed")
    if parsed.path != "/readyz" or parsed.username or parsed.password:
        raise ValueError("Invalid health endpoint")
    try:
        with urlopen(url, timeout=0.6) as response:
            return response.status == 200 and response.read(16).strip() == b"ready"
    except (OSError, TimeoutError, ValueError):
        return False


def _memory() -> str:
    try:
        mem = {}
        for row in Path("/proc/meminfo").read_text().splitlines():
            label, _, amount = row.partition(":")
            if label in ("MemTotal", "MemAvailable"):
                mem[label] = int(amount.strip().split()[0])
        return f"{(mem['MemTotal']-mem['MemAvailable']) / 1048576:.1f} / {mem['MemTotal']/1048576:.1f} GiB"
    except (OSError, ValueError, KeyError):
        return "Unavailable"


class Backend:
    def __init__(self, config_path: Path, health_url: str = "http://127.0.0.1:18767/readyz"):
        self.config_path = Path(config_path).expanduser().absolute()
        self.health_url = health_url

    def snapshot(self) -> dict:
        config = load_config(self.config_path)
        state = config.state
        jobs = []
        folder = state / "jobs"
        if folder.is_dir() and not folder.is_symlink():
            candidates = sorted(
                (p for p in folder.iterdir()
                 if p.is_dir() and not p.is_symlink() and len(p.name) == 32
                 and all(x in "0123456789abcdef" for x in p.name)),
                key=lambda p: p.stat().st_mtime,
                reverse=True)
            for entry in candidates[:30]:
                try:
                    meta = _read(entry / "meta.json")
                    result_file = entry / "result.json"
                    result = _read(result_file) if result_file.is_file() else {}
                    pid = meta.get("worker_pid")
                    alive = (isinstance(pid, int) and
                             pid_start(pid) == meta.get("worker_start_ticks"))
                    status = result.get("status") or ("running" if alive else "interrupted")
                    jobs.append({
                        "id": entry.name,
                        "label": str(meta.get("task_label") or "Unlabelled job")[:64],
                        "status": status,
                        "started": int(meta.get("started_at", 0)),
                        "stdout_bytes": (entry / "stdout.log").stat().st_size
                        if (entry / "stdout.log").is_file() else 0,
                        "stderr_bytes": (entry / "stderr.log").stat().st_size
                        if (entry / "stderr.log").is_file() else 0,
                    })
                except (OSError, ValueError, TypeError, KeyError):
                    continue
        uploads = []
        folder = state / "uploads"
        if folder.is_dir() and not folder.is_symlink():
            for manifest in list(folder.glob("*.json"))[:30]:
                try:
                    data = _read(manifest)
                    uploads.append({
                        "id": str(data.get("upload_id") or manifest.stem)[:32],
                        "name": Path(str(data.get("path") or "unnamed")).name[:80],
                        "received": max(0, int(data.get("received", 0))),
                        "total": max(0, int(data.get("total_size", 0))),
                    })
                except (OSError, ValueError, TypeError):
                    continue
        try:
            load = f"{os.getloadavg()[0]:.2f} / {os.cpu_count() or 1} cores"
        except OSError:
            load = "Unavailable"
        return {
            "local_id": config.device_id,
            "role": config.role,
            "ad_hoc": config.ad_hoc_enabled,
            "peers": sorted(config.peers),
            "workspaces": len(config.workspaces) + 1,
            "tunnel_ready": _health(self.health_url),
            "load": load,
            "memory": _memory(),
            "jobs": jobs,
            "uploads": uploads,
            "timestamp": time.time(),
        }

    def probe_peer(self, device_id: str) -> dict:
        """Verify a trusted configured peer on demand; never automatically."""
        config = load_config(self.config_path)
        peer = config.peers.get(device_id)
        if peer is None:
            raise ValueError("Device is not registered")
        command = shlex.join([
            peer.python, "-m", "openremote_agent.server",
            "--config", peer.config_path, "--doctor",
        ])
        result = subprocess.run(
            ["/usr/bin/ssh", "-T", "-q", "-o", "BatchMode=yes",
             "-o", "NumberOfPasswordPrompts=0",
             "-o", "StrictHostKeyChecking=yes",
             "-o", "ConnectTimeout=4", "-o", "ClearAllForwardings=yes",
             "-o", "ForwardAgent=no", "-o", "PermitLocalCommand=no",
             "-o", f"HostName={peer.host_name}", peer.target, command],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=8, check=False,
        )
        if result.returncode != 0 or len(result.stdout) > 8192:
            return {"device_id": device_id, "online": False,
                    "detail": "Agent did not answer"}
        try:
            data = json.loads(result.stdout)
            good = data.get("ready") is True and data.get("device_id") == device_id
            return {"device_id": device_id, "online": good,
                    "detail": "Agent verified" if good else "Identity/health mismatch"}
        except (ValueError, TypeError):
            return {"device_id": device_id, "online": False,
                    "detail": "Invalid agent response"}
