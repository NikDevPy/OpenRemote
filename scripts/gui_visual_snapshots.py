"""Create privacy-safe, repeatable screenshots for GUI design QA.

Never loads real user configuration, keys, SSH peers or remote data. Statuses
are illustrative and are not evidence that a real device is online.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import tempfile
import time


class DemoBackend:
    config_path = Path("/home/user/.config/openremote-agent/agent.json")

    def snapshot(self):
        return {
            "local_id": "workstation",
            "role": "admin",
            "ad_hoc": True,
            "peers": ["remote-pc"],
            "workspaces": 2,
            "tunnel_ready": True,
            "load": "1.13 / 24 cores",
            "memory": "11.2 / 32.0 GiB",
            "jobs": [{"id": "a" * 32, "label": "Example task", "status": "completed",
                      "stdout_bytes": 784, "stderr_bytes": 0}],
            "uploads": [],
            "timestamp": time.time(),
        }

    def probe_peer(self, name):
        raise RuntimeError("This is an illustrative mock; no real SSH requests")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1400)
    parser.add_argument("--height", type=int, default=960)
    args = parser.parse_args()
    if not (900 <= args.width <= 3000 and 610 <= args.height <= 2000):
        parser.error("Preview size outside supported bounds")

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Discard changes to QSettings. Screenshots are deterministic by design.
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from openremote_gui.app import MainWindow
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    output = args.output
    output.mkdir(parents=True, exist_ok=True)
    window = MainWindow(DemoBackend())
    window.resize(args.width, args.height)
    window.show()

    sequence = (
        ("ru", False, 0, "ru-devices.png"),
        ("ru", False, 1, "ru-activity.png"),
        ("ru", False, 2, "ru-transfers.png"),
        ("ru", False, 3, "ru-settings.png"),
        ("en", False, 0, "en-devices.png"),
        ("en", False, 3, "en-settings.png"),
        ("en", True, 0, "en-devices-dark.png"),
        ("en", True, 3, "en-settings-dark.png"),
    )

    def capture_step(index=0):
        if index >= len(sequence):
            window.quit()
            return
        lang, dark, page, filename = sequence[index]
        window.language = lang
        window._retranslate_static()
        window.dark = dark
        window.set_theme()
        window.show_page(page)
        # Deferred Qt layout/paint events must settle before capturing.
        def capture():
            app.processEvents()
            window.grab().save(str(output / filename))
            QTimer.singleShot(0, lambda: capture_step(index + 1))
        QTimer.singleShot(180, capture)

    def initialize():
        window._snapshot = DemoBackend().snapshot()
        window._peer_ready({"device_id": "remote-pc", "online": True})
        capture_step()

    QTimer.singleShot(700, initialize)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
