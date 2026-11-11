"""Owner-approved Wayland desktop via XDG RemoteDesktop or ScreenCast portal.

No X11 bypass, no root/uinput, no network listener. Consent is granted by the
desktop owner through the configured system portal. Sessions are process-local and
close when the MCP process stops or when desktop_disconnect is invoked.
"""
from __future__ import annotations

import base64
import configparser
import os
import site
from pathlib import Path
import secrets
import shutil
import stat
import threading
import time


def _system_dbus_gi():
    """Use distro's system desktop bindings without modifying the agent venv."""
    try:
        import dbus
        import gi
    except ImportError:
        for location in ("/usr/lib/python3/dist-packages", "/usr/lib64/python3/dist-packages"):
            if Path(location).is_dir():
                site.addsitedir(location)
        import dbus
        import gi
    return dbus, gi


KEYSYMS = {
    "enter": 0xff0d, "return": 0xff0d, "esc": 0xff1b, "escape": 0xff1b,
    "tab": 0xff09, "backspace": 0xff08, "delete": 0xffff,
    "up": 0xff52, "down": 0xff54, "left": 0xff51, "right": 0xff53,
    "home": 0xff50, "end": 0xff57, "pageup": 0xff55, "pagedown": 0xff56,
    "space": 0x20, "f5": 0xffc2,
}
BUTTONS = {"left": 0x110, "right": 0x111, "middle": 0x112}


def require_labwc_chooser():
    """Fail closed unless labwc uses an explicitly interactive wlr chooser.

    xdg-desktop-portal-wlr can otherwise fall back to an arbitrary monitor.
    The dedicated labwc config is deliberately narrow; the system must also
    be tested to verify that its portal actually loads this configuration.
    """
    cfg = Path.home() / ".config/xdg-desktop-portal-wlr/labwc"
    try:
        s = cfg.lstat()
        if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                or stat.S_IMODE(s.st_mode) & 0o022):
            raise PermissionError("Небезопасная конфигурация выбора экрана")
        parser = configparser.ConfigParser(interpolation=None)
        with cfg.open(encoding="utf-8") as source:
            parser.read_file(source)
        if (parser.get("screencast", "chooser_type", fallback="") != "simple"
                or parser.get("screencast", "chooser_cmd", fallback="")
                != "/usr/bin/slurp -f %o -or"
                or not shutil.which("/usr/bin/slurp")):
            raise PermissionError("Требуется интерактивный выбор экрана labwc")
    except (OSError, configparser.Error) as exc:
        raise PermissionError("Не настроен интерактивный выбор экрана labwc") from exc


def session_environment() -> dict[str, str]:
    """Only inherit display access from a process with the current user's UID."""
    keys = ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "XDG_CURRENT_DESKTOP")
    uid = os.getuid()
    runtime = Path(f"/run/user/{uid}")
    if not runtime.is_dir() or runtime.stat().st_uid != uid:
        raise RuntimeError("Нет пользовательской графической сессии")
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != uid:
                continue
            raw = (proc / "environ").read_bytes()
            env = dict(piece.decode("utf-8", "replace").split("=", 1)
                       for piece in raw.split(b"\0") if b"=" in piece)
            if (env.get("XDG_RUNTIME_DIR") == str(runtime)
                    and env.get("WAYLAND_DISPLAY")
                    and (runtime / env["WAYLAND_DISPLAY"]).is_socket()):
                result = {k: env[k] for k in keys if k in env}
                result["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime}/bus"
                return result
        except (OSError, ValueError):
            continue
    raise RuntimeError("Нет активного пользовательского Wayland-дисплея")


class DesktopPortal:
    """One portal consent session; serializes DBus and video calls."""

    def __init__(self):
        self.lock = threading.RLock()
        self.bus = None
        self.remote = None
        self.screencast = None
        self.session = None
        self.stream = None
        self.native_size = None
        self.image_size = None
        self.input_granted = False
        self.last_use = 0.0
        self.desktop_name = ""

    def _setup(self):
        if self.bus is not None:
            return
        dbus, gi = _system_dbus_gi()
        from dbus.mainloop.glib import DBusGMainLoop
        DBusGMainLoop(set_as_default=True)
        # This is a user-owned local session bus, not a public endpoint.
        env = session_environment()
        self.desktop_name = env.get("XDG_CURRENT_DESKTOP", "")
        os.environ.update(env)
        self.bus = dbus.SessionBus()
        obj = self.bus.get_object("org.freedesktop.portal.Desktop",
                                  "/org/freedesktop/portal/desktop")
        self.remote = dbus.Interface(obj, "org.freedesktop.portal.RemoteDesktop")
        self.screencast = dbus.Interface(obj, "org.freedesktop.portal.ScreenCast")

    def _request(self, iface, method: str, args: tuple, *, timeout: float = 50,
                 options: dict | None = None) -> dict:
        dbus, gi = _system_dbus_gi()
        from gi.repository import GLib
        token = "openremote" + secrets.token_hex(8)
        params = dbus.Dictionary({"handle_token": dbus.String(token),
                                  **(options or {})}, signature="sv")
        result = []
        expected = None

        def response(code, data, _path=None):
            if _path == expected:
                result.append((int(code), data))

        match = self.bus.add_signal_receiver(
            response, signal_name="Response",
            dbus_interface="org.freedesktop.portal.Request",
            path_keyword="_path",
        )
        try:
            handle = getattr(iface, method)(*args, params)
            expected = str(handle)
            deadline = time.monotonic() + timeout
            context = GLib.MainContext.default()
            while time.monotonic() < deadline and not result:
                while context.pending():
                    context.iteration(False)
                time.sleep(0.025)
            if not result:
                try:
                    dbus.Interface(
                        self.bus.get_object("org.freedesktop.portal.Desktop", expected),
                        "org.freedesktop.portal.Request",
                    ).Close()
                except Exception:
                    pass
                raise TimeoutError("Портал не получил разрешение за отведённое время")
            code, data = result[0]
            if code != 0:
                raise PermissionError("Владелец отклонил разрешение экрана/управления")
            return data
        finally:
            match.remove()

    def connect(self, enable_input: bool) -> dict:
        dbus, gi = _system_dbus_gi()
        with self.lock:
            if self.session:
                return self.status()
            self._setup()
            try:
                if "labwc" in self.desktop_name.lower().split(":"):
                    require_labwc_chooser()
                # wlroots' standard portal implements ScreenCast but not
                # RemoteDesktop. A read-only session must not require or request
                # input devices. Input sessions still require RemoteDesktop and
                # never silently fall back to any other injection mechanism.
                session_portal = self.remote if enable_input else self.screencast
                created = self._request(
                    session_portal, "CreateSession", (),
                    options={"session_handle_token": dbus.String(
                        "or_session" + secrets.token_hex(8))},
                    timeout=12,
                )
                self.session = str(created["session_handle"])
                if enable_input:
                    self._request(
                        self.remote, "SelectDevices",
                        (dbus.ObjectPath(self.session),),
                        options={"types": dbus.UInt32(3)},
                        timeout=12,
                    )
                self._request(
                    self.screencast, "SelectSources",
                    (dbus.ObjectPath(self.session),),
                    options={"types": dbus.UInt32(1), "multiple": dbus.Boolean(False),
                             "cursor_mode": dbus.UInt32(2)},
                    timeout=12,
                )
                started = self._request(
                    session_portal, "Start", (dbus.ObjectPath(self.session), dbus.String("")),
                    timeout=55,
                )
                streams = started.get("streams", [])
                if not streams:
                    raise RuntimeError("Портал не предоставил поток экрана")
                node, meta = streams[0]
                self.stream = int(node)
                native = meta.get("size", (0, 0))
                self.native_size = (int(native[0]), int(native[1]))
                granted = int(started.get("devices", 0))
                self.input_granted = (granted & 3) == 3 and enable_input
                self.last_use = time.monotonic()
                return self.status()
            except BaseException:
                self.disconnect()
                raise

    def status(self) -> dict:
        return {
            "connected": bool(self.session and self.stream is not None),
            "input_granted": bool(self.input_granted),
            "native_width": self.native_size[0] if self.native_size else 0,
            "native_height": self.native_size[1] if self.native_size else 0,
            "image_width": self.image_size[0] if self.image_size else 0,
            "image_height": self.image_size[1] if self.image_size else 0,
            "backend": "xdg-desktop-portal",
        }

    def _require(self, *, input_required=False):
        if not self.session or self.stream is None:
            raise PermissionError("Сначала подтвердите desktop_connect в системном портале")
        if input_required and not self.input_granted:
            raise PermissionError("Системный портал не выдал разрешение на управление")
        if time.monotonic() - self.last_use > 900:
            self.disconnect()
            raise PermissionError("Сессия закрыта после 15 минут бездействия")
        self.last_use = time.monotonic()

    def screenshot(self, max_width=1280) -> dict:
        with self.lock:
            self._require()
            if type(max_width) is not int or not 320 <= max_width <= 1920:
                raise ValueError("max_width: от 320 до 1920")
            dbus, gi = _system_dbus_gi()
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            Gst.init(None)
            fdref = self.screencast.OpenPipeWireRemote(
                dbus.ObjectPath(self.session), dbus.Dictionary({}, signature="sv"))
            fd = fdref.take()
            pipeline = None
            try:
                nw, nh = self.native_size
                if nw <= 0 or nh <= 0:
                    raise RuntimeError("Портал не вернул размер экрана")
                target_width = min(max_width, nw)
                target_height = max(1, round(nh * target_width / nw))
                pipeline = Gst.parse_launch(
                    f"pipewiresrc fd={fd} path={self.stream} do-timestamp=true "
                    "! videoconvert ! videoscale "
                    f"! video/x-raw,width={target_width},height={target_height},pixel-aspect-ratio=1/1 "
                    "! jpegenc quality=65 ! appsink name=sink sync=false "
                    "max-buffers=1 drop=true"
                )
                sink = pipeline.get_by_name("sink")
                pipeline.set_state(Gst.State.PLAYING)
                sample = sink.emit("try-pull-sample", 7 * Gst.SECOND)
                if sample is None:
                    raise RuntimeError("PipeWire не вернул кадр за 7 секунд")
                caps = sample.get_caps().get_structure(0)
                width, height = caps.get_value("width"), caps.get_value("height")
                buffer = sample.get_buffer()
                ok, mapped = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    raise RuntimeError("Не удалось прочесть кадр PipeWire")
                try:
                    data = bytes(mapped.data)
                finally:
                    buffer.unmap(mapped)
                if len(data) > 512000:
                    raise RuntimeError("Кадр превышает допустимый размер")
                self.image_size = int(width), int(height)
                return {"mime_type": "image/jpeg", "image_base64": base64.b64encode(data).decode(),
                        "width": int(width), "height": int(height),
                        "native_width": self.native_size[0],
                        "native_height": self.native_size[1]}
            finally:
                if pipeline is not None:
                    pipeline.set_state(Gst.State.NULL)
                os.close(fd)

    def _button(self, button: int, down: bool):
        import dbus
        self.remote.NotifyPointerButton(dbus.ObjectPath(self.session),
                                        dbus.Dictionary({}, signature="sv"),
                                        dbus.Int32(button), dbus.UInt32(1 if down else 0))

    def _move_pixels(self, x: int, y: int) -> None:
        import dbus
        if type(x) is not int or type(y) is not int:
            raise ValueError("Координаты должны быть целыми пикселями")
        w, h = self.image_size or self.native_size
        if not 0 <= x < w or not 0 <= y < h:
            raise ValueError("Координаты за пределами экрана")
        nw, nh = self.native_size
        self.remote.NotifyPointerMotionAbsolute(
            dbus.ObjectPath(self.session), dbus.Dictionary({}, signature="sv"),
            dbus.UInt32(self.stream), dbus.Double(x * nw / w),
            dbus.Double(y * nh / h))

    def move(self, x: int, y: int) -> dict:
        with self.lock:
            self._require(input_required=True)
            self._move_pixels(x, y)
            return {"moved": True, "x": x, "y": y}

    def click(self, x: int, y: int, button: str = "left") -> dict:
        with self.lock:
            self._require(input_required=True)
            if button not in BUTTONS:
                raise ValueError("Кнопка должна быть left/right/middle")
            self._move_pixels(x, y)
            self._button(BUTTONS[button], True)
            self._button(BUTTONS[button], False)
            return {"clicked": True, "x": x, "y": y, "button": button}

    def drag(self, x: int, y: int, to_x: int, to_y: int, steps: int = 12) -> dict:
        with self.lock:
            self._require(input_required=True)
            if type(steps) is not int or not 2 <= steps <= 40:
                raise ValueError("Число шагов перемещения от 2 до 40")
            self._move_pixels(x, y)
            # Prevalidate end point before holding down any button.
            self._move_pixels(to_x, to_y)
            self._move_pixels(x, y)
            self._button(BUTTONS["left"], True)
            try:
                for i in range(1, steps + 1):
                    self._move_pixels(round(x + (to_x-x)*i/steps),
                                      round(y + (to_y-y)*i/steps))
                    time.sleep(.02)
            finally:
                self._button(BUTTONS["left"], False)
            return {"dragged": True, "from_x": x, "from_y": y,
                    "to_x": to_x, "to_y": to_y}

    def scroll(self, x: int, y: int, steps: int) -> dict:
        with self.lock:
            self._require(input_required=True)
            if type(steps) is not int or steps == 0 or abs(steps) > 15:
                raise ValueError("Число шагов скролла от -15 до 15, кроме 0")
            import dbus
            self._move_pixels(x, y)
            self.remote.NotifyPointerAxisDiscrete(
                dbus.ObjectPath(self.session), dbus.Dictionary({}, signature="sv"),
                dbus.UInt32(0), dbus.Int32(steps))
            return {"scrolled": True, "steps": steps}

    def _keysym(self, sym: int):
        import dbus
        self.remote.NotifyKeyboardKeysym(dbus.ObjectPath(self.session),
                                         dbus.Dictionary({}, signature="sv"),
                                         dbus.Int32(sym), dbus.UInt32(1))
        self.remote.NotifyKeyboardKeysym(dbus.ObjectPath(self.session),
                                         dbus.Dictionary({}, signature="sv"),
                                         dbus.Int32(sym), dbus.UInt32(0))

    def key(self, name: str) -> dict:
        with self.lock:
            self._require(input_required=True)
            key = KEYSYMS.get(name.lower()) if isinstance(name, str) else None
            if key is None:
                raise ValueError("Недопустимая клавиша")
            self._keysym(key)
            return {"pressed": name}

    def type_text(self, text: str) -> dict:
        with self.lock:
            self._require(input_required=True)
            if not isinstance(text, str) or len(text) > 256 or any(ord(c) < 32 and c not in "\n\t" for c in text):
                raise ValueError("Максимум 256 печатных символов")
            for char in text:
                keysym = (0xff0d if char == "\n" else 0xff09 if char == "\t" else
                          ord(char) if ord(char) < 0x100 else 0x01000000 | ord(char))
                self._keysym(keysym)
            return {"typed": len(text)}

    def disconnect(self) -> dict:
        with self.lock:
            if self.session and self.bus:
                try:
                    dbus, gi = _system_dbus_gi()
                    obj = self.bus.get_object("org.freedesktop.portal.Desktop", self.session)
                    dbus.Interface(obj, "org.freedesktop.portal.Session").Close()
                except Exception:
                    pass
            self.session = self.stream = self.native_size = self.image_size = None
            self.input_granted = False
            return {"disconnected": True}

def serve(socket_path: str):
    """Run portal calls in system Python; unix socket accepts same UID only."""
    import json
    import socket
    import struct
    import stat

    target = Path(socket_path)
    runtime = Path(f"/run/user/{os.getuid()}")
    if target.parent != runtime or target.name != "openremote-desktop.sock":
        raise ValueError("Сокет допускается только в XDG_RUNTIME_DIR пользователя")
    if target.exists() or target.is_symlink():
        if target.is_symlink() or target.stat().st_uid != os.getuid() or not target.is_socket():
            raise PermissionError("Нельзя использовать чужой или неправильный сокет")
        try:
            with socket.socket(socket.AF_UNIX) as candidate:
                candidate.settimeout(.4)
                candidate.connect(str(target))
            raise RuntimeError("Процесс графического доступа уже работает")
        except (ConnectionRefusedError, TimeoutError, FileNotFoundError):
            target.unlink()

    portal = DesktopPortal()
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(target))
    os.chmod(target, 0o600)
    server.listen(4)
    server.settimeout(4)
    last_request = time.monotonic()
    close_now = False
    try:
        while not close_now and time.monotonic() - last_request < 900:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                continue
            with connection:
                connection.settimeout(90)
                creds = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                _pid, uid, _gid = struct.unpack("3i", creds)
                if uid != os.getuid():
                    continue
                payload = b""
                while b"\n" not in payload and len(payload) < 8192:
                    part = connection.recv(4096)
                    if not part:
                        break
                    payload += part
                last_request = time.monotonic()
                try:
                    request = json.loads(payload.decode().split("\n")[0])
                    operation = request.get("op")
                    if operation == "status":
                        result = portal.status()
                    elif operation == "connect":
                        result = portal.connect(bool(request.get("input", False)))
                    elif operation == "screenshot":
                        result = portal.screenshot(request.get("max_width", 1280))
                    elif operation == "click":
                        result = portal.click(request["x"], request["y"], request.get("button", "left"))
                    elif operation == "move":
                        result = portal.move(request["x"], request["y"])
                    elif operation == "drag":
                        result = portal.drag(request["x"], request["y"], request["to_x"],
                                             request["to_y"], request.get("steps", 12))
                    elif operation == "scroll":
                        result = portal.scroll(request["x"], request["y"], request["steps"])
                    elif operation == "key":
                        result = portal.key(request["name"])
                    elif operation == "type_text":
                        result = portal.type_text(request["text"])
                    elif operation == "disconnect":
                        result = portal.disconnect()
                        close_now = True
                    else:
                        raise ValueError("Неизвестная операция графического агента")
                    message = {"ok": True, "result": result}
                except (Exception,) as error:
                    message = {"ok": False, "error": type(error).__name__,
                               "message": str(error)[:260]}
                connection.sendall((json.dumps(message, ensure_ascii=False) + "\n").encode())
    finally:
        portal.disconnect()
        server.close()
        try:
            if target.is_socket() and target.stat().st_uid == os.getuid():
                target.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] != "--serve":
        raise SystemExit("Usage: desktop.py --serve /run/user/UID/openremote-desktop.sock")
    serve(sys.argv[2])
