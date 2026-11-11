"""Process boundary between OpenRemote Python venv and Ubuntu's system D-Bus/GI.

The portal worker stays on the same UID and user session. It is not
reachable over TCP or Tailscale. Consent must be approved on the target screen.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


class DesktopClient:
    def __init__(self):
        self.sock = Path(f"/run/user/{os.getuid()}/openremote-desktop.sock")

    def _rpc(self, op: str, **params):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(85)
            conn.connect(str(self.sock))
            message = json.dumps({"op": op, **params}, ensure_ascii=False).encode()
            if len(message) > 8192:
                raise ValueError("Команда рабочего стола слишком длинная")
            conn.sendall(message + b"\n")
            raw = bytearray()
            while b"\n" not in raw:
                block = conn.recv(65536)
                if not block:
                    break
                raw.extend(block)
                if len(raw) > 750000:
                    raise RuntimeError("Ответ рабочего стола превышает 750 КиБ")
        if not raw:
            raise RuntimeError("Графический помощник не ответил")
        answer = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        if not answer.get("ok"):
            error = str(answer.get("message", "Графический доступ отклонён"))
            if answer.get("error") in ("PermissionError", "TimeoutError"):
                raise PermissionError(error)
            raise RuntimeError(error)
        return answer["result"]

    def _start(self):
        if self.sock.is_socket():
            try:
                return self._rpc("status")
            except (ConnectionError, OSError):
                pass
        if sys.platform != "linux" or not Path("/usr/bin/python3").is_file():
            raise RuntimeError("Поддерживается системный Python Ubuntu/Linux")
        program = Path(__file__).with_name("desktop.py")
        with open(os.devnull, "wb") as null:
            subprocess.Popen(
                ["/usr/bin/python3", str(program), "--serve", str(self.sock)],
                stdin=null, stdout=null, stderr=null,
                close_fds=True, start_new_session=True,
                env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin",
                     "LANG": "C.UTF-8"},
            )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            try:
                return self._rpc("status")
            except (ConnectionError, OSError):
                time.sleep(.08)
        raise RuntimeError("Графический помощник не открыл локальный Unix-сокет")

    def status(self):
        try:
            return self._rpc("status")
        except (ConnectionError, OSError):
            return {"connected": False, "input_granted": False,
                    "native_width": 0, "native_height": 0,
                    "image_width": 0, "image_height": 0,
                    "backend": "xdg-desktop-portal"}

    def connect(self, enable_input: bool):
        self._start()
        return self._rpc("connect", input=bool(enable_input))

    def screenshot(self, max_width=1280):
        return self._rpc("screenshot", max_width=max_width)

    def click(self, x, y, button="left"):
        return self._rpc("click", x=x, y=y, button=button)

    def move(self, x, y):
        return self._rpc("move", x=x, y=y)

    def drag(self, x, y, to_x, to_y, steps=12):
        return self._rpc("drag", x=x, y=y, to_x=to_x, to_y=to_y, steps=steps)

    def scroll(self, x, y, steps):
        return self._rpc("scroll", x=x, y=y, steps=steps)

    def key(self, name):
        return self._rpc("key", name=name)

    def type_text(self, text):
        return self._rpc("type_text", text=text)

    def disconnect(self):
        try:
            return self._rpc("disconnect")
        except (ConnectionError, OSError):
            return {"disconnected": True}
