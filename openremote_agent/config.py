"""Конфигурация агента. Секреты не хранятся в JSON."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re

ROLES = {"viewer": 0, "operator": 1, "admin": 2}
DEVICE_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")
SSH_TARGET_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,199}\Z")
SSH_HOST_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,199}\Z")

# Не наследуем список запретов RDC: привилегированные исполняемые файлы
# допускаются лишь в явной политике администратора и с локальным разрешением.
SENSITIVE_BINARIES = {
    "sudo", "su", "doas", "pkexec", "mount", "umount", "dd",
    "fdisk", "parted", "diskpart", "iptables", "ip6tables", "nft",
    "reboot", "shutdown", "halt", "poweroff", "passwd", "usermod",
    "useradd", "adduser", "groupadd", "chsh", "visudo", "systemctl",
    "service", "mkfs", "sh", "bash", "dash", "zsh", "fish", "env",
}



@dataclass(frozen=True)
class CommandSpec:
    argv: tuple[str, ...]
    timeout: float = 10.0
    risk: str = "standard"
    summary: str = ""

    def __post_init__(self) -> None:
        if not self.argv or not os.path.isabs(self.argv[0]):
            raise ValueError("Команда должна содержать абсолютный путь исполняемого файла")
        if len(self.argv) > 32 or sum(len(a) for a in self.argv) > 4096:
            raise ValueError("Превышен лимит аргументов")
        if not math.isfinite(self.timeout) or self.timeout < 0.2 or self.timeout > 60:
            raise ValueError("Таймаут допускается от 0.2 до 60 секунд")
        if self.risk not in {"standard", "privileged"}:
            raise ValueError("risk: standard или privileged")
        if not isinstance(self.summary, str) or len(self.summary) > 300:
            raise ValueError("Описание команды слишком длинное")
        if os.path.basename(self.argv[0]) in SENSITIVE_BINARIES or os.path.basename(self.argv[0]).startswith("mkfs."):
            if self.risk != "privileged":
                raise ValueError("Системная команда допускается только с risk=privileged")
        if os.path.basename(self.argv[0]) == "sudo" and "-n" not in self.argv[1:]:
            raise ValueError("sudo должен использовать -n и отдельное ограниченное правило sudoers")
        if any("\x00" in arg for arg in self.argv):
            raise ValueError("NUL в аргументах недопустим")


@dataclass(frozen=True)
class SSHPeer:
    """Только указанный владельцем SSH-хост и MCP процесс (не пользовательский argv)."""
    target: str
    host_name: str
    python: str
    config_path: str

    def __post_init__(self) -> None:
        if not SSH_TARGET_RE.fullmatch(self.target) or not SSH_HOST_RE.fullmatch(self.host_name):
            raise ValueError("Недопустимый SSH target/HostName")
        for path in (self.python, self.config_path):
            if (not isinstance(path, str) or not path.startswith("/") or
                    "\x00" in path or "\n" in path or "\r" in path):
                raise ValueError("Нужен абсолютный путь к Python и удалённому конфигу")


@dataclass(frozen=True)
class Config:
    device_id: str
    root: Path
    state: Path
    role: str = "viewer"
    commands: dict[str, CommandSpec] = field(default_factory=dict)
    peers: dict[str, SSHPeer] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not DEVICE_RE.fullmatch(self.device_id):
            raise ValueError("Недопустимый device_id")
        if self.role not in ROLES:
            raise ValueError("Роль должна быть viewer, operator или admin")
        if not self.root.is_absolute() or not self.state.is_absolute():
            raise ValueError("root и state должны быть абсолютными путями")
        if self.root == self.state or self.root in self.state.parents or self.state in self.root.parents:
            raise ValueError("Аудит и разрешённые файлы должны находиться в разных каталогах")
        for name in self.commands:
            if not DEVICE_RE.fullmatch(name):
                raise ValueError("Недопустимый идентификатор команды")
        for name, peer in self.peers.items():
            if not DEVICE_RE.fullmatch(name) or name == self.device_id or not isinstance(peer, SSHPeer):
                raise ValueError("Недопустимый идентификатор доверенного SSH-устройства")


def load_config(path: Path) -> Config:
    path = path.absolute()
    st = path.lstat()
    if not path.is_file() or path.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o022:
        raise ValueError("Конфигурация должна принадлежать пользователю и не допускать чужой записи")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"device_id", "root", "state", "role", "commands", "peers"}:
        raise ValueError("Неизвестные ключи конфигурации")
    entries = data.get("commands", {})
    if not isinstance(entries, dict):
        raise ValueError("commands должен быть объектом")
    commands = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) - {"argv", "timeout", "risk", "summary"}:
            raise ValueError(f"Некорректная команда: {name}")
        argv = entry.get("argv")
        if not isinstance(argv, list) or not all(isinstance(arg, str) for arg in argv):
            raise ValueError(f"Некорректные argv: {name}")
        commands[name] = CommandSpec(
            tuple(argv), float(entry.get("timeout", 10)),
            risk=entry.get("risk", "standard"), summary=entry.get("summary", ""),
        )
    entries = data.get("peers", {})
    if not isinstance(entries, dict):
        raise ValueError("peers должен быть объектом")
    peers = {}
    for name, entry in entries.items():
        if not isinstance(entry, dict) or set(entry) != {"target", "host_name", "python", "config_path"}:
            raise ValueError(f"Неправильная запись устройства: {name}")
        peers[name] = SSHPeer(**entry)
    return Config(
        device_id=data["device_id"], root=Path(data["root"]),
        state=Path(data["state"]), role=data.get("role", "viewer"),
        commands=commands, peers=peers,
    )
