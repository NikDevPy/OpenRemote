"""Safe first-run setup for a personal Linux OpenRemote gateway.

No OpenAI password is collected. The runtime key is written only to a 0600
owner-owned file, never to QSettings, arguments, screenshots or logs.
The user must obtain their own Tunnel ID/key and official tunnel-client.
"""
from __future__ import annotations

import json
import hmac
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from openremote_agent.config import DEVICE_RE, load_config

TUNNEL_RE = re.compile(r"tunnel_[0-9a-fA-F]{32}\Z")
PROFILE = "openremote-agent"
OFFICIAL_TUNNELS = "https://platform.openai.com/settings/organization/tunnels"
OFFICIAL_KEYS = "https://platform.openai.com/settings/organization/api-keys"
OFFICIAL_CLIENT = "https://github.com/openai/tunnel-client/releases/latest"
OFFICIAL_CHATGPT = "https://chatgpt.com/"
OFFICIAL_ACCOUNT = "https://platform.openai.com/signup"
OFFICIAL_GUIDE = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"


class SetupError(ValueError):
    """Sanitized error messages only, safe to present in the GUI."""


@dataclass(frozen=True)
class SetupPaths:
    """A private user's files; no project checkout is required at runtime."""
    home: Path
    @property
    def config_dir(self) -> Path:
        return self.home / ".config/openremote-agent"
    @property
    def config(self) -> Path:
        return self.config_dir / "agent.json"
    @property
    def key(self) -> Path:
        return self.config_dir / "runtime-key"
    @property
    def sandbox(self) -> Path:
        return self.home / ".local/share/openremote-agent/sandbox"
    @property
    def state(self) -> Path:
        return self.home / ".local/state/openremote-agent"
    @property
    def profile(self) -> Path:
        return self.home / ".config/tunnel-client" / f"{PROFILE}.yaml"
    @property
    def wrapper(self) -> Path:
        return self.home / ".local/libexec/openremote-agent-tunnel-start"
    @property
    def service(self) -> Path:
        return self.home / ".config/systemd/user/openremote-agent-tunnel.service"


def _safe_file(path: Path, content: bytes, mode: int) -> None:
    if path.exists() or path.is_symlink():
        raise SetupError(f"Existing {path.name} will not be overwritten")
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, mode)
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _private_dir(path: Path) -> None:
    if path.is_symlink():
        raise SetupError("Configuration directory cannot be a symbolic link")
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = path.stat()
    if st.st_uid != os.getuid() or not stat.S_ISDIR(st.st_mode):
        raise SetupError("Configuration directory owner is not valid")
    if st.st_mode & 0o077:
        path.chmod(0o700)


def _check_key(key: str) -> None:
    if not isinstance(key, str) or not 15 <= len(key) <= 4096 or any(c.isspace() or c == "\x00" for c in key):
        raise SetupError("Enter a valid, single-line OpenAI runtime API key")


def _check_binary(value: str | Path) -> Path:
    binary = Path(value).expanduser().absolute()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise SetupError("Select an executable official tunnel-client binary")
    return binary


def _run(binary: list[str], timeout: int, *, key: str | None = None, cwd: Path | None = None) -> None:
    env = os.environ.copy()
    if key is not None:
        env["CONTROL_PLANE_API_KEY"] = key
    try:
        result = subprocess.run(
            binary, cwd=str(cwd) if cwd is not None else None,
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=timeout, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        raise SetupError("Operation timed out; verify network and permissions") from None
    except OSError:
        raise SetupError("Could not start the local executable") from None
    finally:
        # The key was not added to the parent process environment.
        env.pop("CONTROL_PLANE_API_KEY", None)
    if result.returncode:
        # The external binary may echo sensitive config or credentials.
        # Never include raw stdout/stderr in a user-visible exception.
        raise SetupError("Local tunnel setup or verification failed; see official tunnel-client doctor")


class FirstRun:
    def __init__(self, paths: SetupPaths | None = None, *,
                 agent_command: list[str] | None = None):
        self.paths = paths or SetupPaths(Path.home())
        self.agent_command = agent_command or [
            sys.executable, "-m", "openremote_agent.server", "--transport", "stdio",
            "--config", str(self.paths.config),
        ]

    def installed(self) -> bool:
        try:
            load_config(self.paths.config)
            return True
        except (OSError, ValueError, KeyError):
            return False

    def prepare_local(self, device_id: str) -> Path:
        if not DEVICE_RE.fullmatch(device_id):
            raise SetupError("Device name must contain 1–64 letters, numbers, _ or -")
        p = self.paths
        for folder in (p.config_dir, p.sandbox, p.state):
            _private_dir(folder)
        if not p.config.exists():
            data = {
                "device_id": device_id,
                "root": str(p.sandbox),
                "state": str(p.state),
                "role": "viewer", "ad_hoc_enabled": False,
                "commands": {}, "workspaces": {}, "peers": {},
            }
            _safe_file(p.config, (json.dumps(data, indent=2) + "\n").encode(), 0o600)
        config = load_config(p.config)
        if config.device_id != device_id:
            raise SetupError("The existing agent belongs to a different device name")
        return p.config

    def store_runtime_key(self, runtime_key: str) -> Path:
        _check_key(runtime_key)
        _private_dir(self.paths.config_dir)
        p = self.paths.key
        if p.exists() or p.is_symlink():
            info = p.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise SetupError("Existing runtime key file has unsafe permissions")
            # Never overwrite a key during first-run retries.
            raise SetupError("A runtime key already exists; it will not be overwritten")
        _safe_file(p, (runtime_key + "\n").encode(), 0o600)
        return p

    def verify_client(self, binary: str) -> Path:
        client = _check_binary(binary)
        _run([str(client), "--version"], 10)
        return client

    def configure_tunnel(self, *, device_id: str, tunnel_id: str,
                         runtime_key: str, binary: str,
                         start_service: bool = True) -> dict:
        if not TUNNEL_RE.fullmatch(tunnel_id):
            raise SetupError("Tunnel ID must have the form tunnel_ followed by 32 hex digits")
        _check_key(runtime_key)
        client = self.verify_client(binary)
        p = self.paths
        if p.service.exists() != p.wrapper.exists():
            raise SetupError("Partially configured service files detected; manual review required")
        self.prepare_local(device_id)
        if p.key.exists():
            info = p.key.lstat()
            if (p.key.is_symlink() or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise SetupError("Existing runtime key has unsafe permissions")
            if not hmac.compare_digest(p.key.read_text().strip(), runtime_key):
                raise SetupError("The existing runtime key differs; no credentials were replaced")
        else:
            self.store_runtime_key(runtime_key)
        # Validate the new agent without exposing credentials.
        _run(self.agent_command + ["--doctor"], 12)
        command = shlex.join(self.agent_command)
        if p.profile.exists() or p.profile.is_symlink():
            info = p.profile.lstat()
            if (p.profile.is_symlink() or not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077
                    or tunnel_id not in p.profile.read_text(encoding="utf-8")):
                raise SetupError("An unrelated or unsafe tunnel profile already exists")
        else:
            # Store no API key inside the profile or command arguments.
            _run([
                str(client), "init", "--sample", "sample_mcp_stdio_local",
                "--profile", PROFILE, "--tunnel-id", tunnel_id,
                "--health-listen-addr", "127.0.0.1:18767",
                "--mcp-command", command,
            ], 35, key=runtime_key)
            if not p.profile.exists():
                raise SetupError("tunnel-client did not create a profile")
            os.chmod(p.profile, 0o600)
        _run([str(client), "doctor", "--profile", PROFILE, "--explain"], 25, key=runtime_key)

        wrapper = (
            "#!/bin/sh\n"
            "set -eu\n"
            "umask 077\n"
            "CONTROL_PLANE_API_KEY=$(cat " + shlex.quote(str(p.key)) + ")\n"
            "export CONTROL_PLANE_API_KEY\n"
            "exec " + shlex.quote(str(client)) + " run --profile " + PROFILE + "\n"
        )
        if p.wrapper.exists():
            if p.wrapper.is_symlink() or p.wrapper.read_text() != wrapper:
                raise SetupError("An unrelated service launcher exists")
        else:
            _safe_file(p.wrapper, wrapper.encode(), 0o700)
        unit = (
            "[Unit]\nDescription=OpenRemote private MCP tunnel\n"
            "After=network-online.target\nWants=network-online.target\n\n"
            "[Service]\nType=simple\n"
            "ExecStart=%h/.local/libexec/openremote-agent-tunnel-start\n"
            "Restart=on-failure\nRestartSec=8\nUMask=0077\n"
            "NoNewPrivileges=true\n\n"
            "[Install]\nWantedBy=default.target\n"
        )
        if p.service.exists():
            if p.service.is_symlink() or p.service.read_text() != unit:
                raise SetupError("An unrelated systemd unit exists")
        else:
            _safe_file(p.service, unit.encode(), 0o600)
        if start_service:
            _run(["/usr/bin/systemctl", "--user", "daemon-reload"], 10)
            _run(["/usr/bin/systemctl", "--user", "enable", "--now",
                  "openremote-agent-tunnel.service"], 20)
        return {
            "configured": True,
            "service_requested": start_service,
            "tunnel_id": tunnel_id,
            "config_path": str(p.config),
        }
