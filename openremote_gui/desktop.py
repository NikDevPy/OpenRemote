"""User-only Linux desktop launcher, icon and opt-in session autostart.

Never writes system-level files or starts/restarts the MCP tunnel. Only the
authenticated OS user's XDG application/icon/autostart locations are used.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import stat
import sys
import tempfile

APP_ID = "openremote-control-center"
APP_MARKER = "X-OpenRemote-ControlCenter=true"


def _home_path(*segments: str) -> Path:
    return Path.home().joinpath(*segments)


def _atomic_file(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symbolic link")
    if path.exists() and path.stat().st_uid != os.getuid():
        raise PermissionError("File is not owned by this user")
    fd, temporary = tempfile.mkstemp(prefix="." + APP_ID + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _paths() -> dict[str, Path]:
    return {
        "launcher": _home_path(".local", "bin", APP_ID),
        "desktop": _home_path(".local", "share", "applications", APP_ID + ".desktop"),
        "icon": _home_path(".local", "share", "icons", "hicolor",
                           "scalable", "apps", APP_ID + ".svg"),
        "raster": _home_path(".local", "share", "icons", "hicolor",
                             "256x256", "apps", APP_ID + ".png"),
        "autostart": _home_path(".config", "autostart", APP_ID + ".desktop"),
    }


def install_desktop(config_path: Path, python_executable: str | Path = sys.executable) -> dict:
    if sys.platform != "linux":
        raise OSError("Linux-only desktop integration")
    config_path = Path(config_path).expanduser().absolute()
    if not config_path.is_file():
        raise FileNotFoundError("OpenRemote agent configuration not found")
    python_executable = Path(python_executable).absolute()
    if not python_executable.is_file():
        raise FileNotFoundError("Python executable not found")
    icon_source = Path(__file__).parent / "assets" / "openremote.svg"
    if not icon_source.is_file():
        raise FileNotFoundError("Packaged OpenRemote icon is missing")

    paths = _paths()
    script = (
        "#!/bin/sh\n"
        "exec " + shlex.quote(str(python_executable)) +
        " -m openremote_gui.app --config " +
        shlex.quote(str(config_path)) + ' "$@"\n'
    )
    _atomic_file(paths["launcher"], script.encode("utf-8"), 0o755)

    desktop_text = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Version=1.0\n"
        "Name=OpenRemote\n"
        "Name[ru]=OpenRemote — Центр управления\n"
        "Comment=Private device control center\n"
        "Comment[ru]=Панель управления подключёнными устройствами\n"
        "Exec=" + str(paths["launcher"]) + "\n"
        "Icon=" + APP_ID + "\n"
        "Terminal=false\n"
        "Categories=Network;Utility;\n"
        "StartupNotify=true\n"
        "StartupWMClass=OpenRemote\n"
        + APP_MARKER + "\n"
    )
    _atomic_file(paths["desktop"], desktop_text.encode("utf-8"), 0o644)
    _atomic_file(paths["icon"], icon_source.read_bytes(), 0o644)
    # Qt SVG renderer generates a real 256x256 PNG for docks that do not
    # consume icon themes' SVG entries. No font files are embedded.
    try:
        from PySide6.QtCore import QByteArray
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer

        renderer = QSvgRenderer(QByteArray(icon_source.read_bytes()))
        if renderer.isValid():
            image = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            png_path = paths["raster"]
            png_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".openremote-icon-", suffix=".png",
                                       dir=png_path.parent)
            os.close(fd)
            try:
                if not image.save(tmp, "PNG"):
                    raise OSError("Could not render PNG icon")
                os.chmod(tmp, 0o644)
                os.replace(tmp, png_path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
    except ImportError:
        pass  # SVG remains the standards-compliant application icon.

    return {key: str(value) for key, value in paths.items()}


def launcher_installed() -> bool:
    paths = _paths()
    try:
        for desktop in (paths["desktop"],
                        Path("/usr/share/applications") / (APP_ID + ".desktop")):
            if not desktop.is_symlink() and desktop.is_file():
                if APP_MARKER in desktop.read_text(encoding="utf-8"):
                    return True
        return False
    except (OSError, ValueError):
        return False


def autostart_enabled() -> bool:
    path = _paths()["autostart"]
    try:
        return (not path.is_symlink() and path.is_file()
                and APP_MARKER in path.read_text(encoding="utf-8")
                and "X-GNOME-Autostart-enabled=true" in path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False


def set_autostart(enabled: bool) -> None:
    path = _paths()["autostart"]
    if not enabled:
        if path.exists():
            if not autostart_enabled():
                raise ValueError("Refusing to delete a non-OpenRemote autostart entry")
            path.unlink()
        return
    if not launcher_installed():
        raise FileNotFoundError("Install the desktop launcher first")
    location = _paths()["desktop"]
    if not location.is_file():
        location = Path("/usr/share/applications") / (APP_ID + ".desktop")
    if location.is_symlink() or not location.is_file():
        raise FileNotFoundError("Recognized desktop launcher is missing")
    source = location.read_text(encoding="utf-8")
    if APP_MARKER not in source:
        raise ValueError("Unrecognized desktop launcher")
    _atomic_file(path, (source + "X-GNOME-Autostart-enabled=true\n").encode(), 0o644)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install Linux desktop integration")
    parser.add_argument("--config", type=Path, required=True,
                        help="Path to existing, private OpenRemote agent config")
    parser.add_argument("--autostart", action="store_true",
                        help="Also enable GUI autostart at graphical login")
    options = parser.parse_args(argv)
    locations = install_desktop(options.config)
    if options.autostart:
        set_autostart(True)
    print("OpenRemote desktop launcher installed:", locations["desktop"])
    print("Application icon installed:", locations["icon"])
    print("Autostart:", "enabled" if autostart_enabled() else "disabled")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
