"""OpenRemote Control Center — optional PySide6 desktop/tray UI.

No credentials are displayed; no SSH polling occurs until the user probes a
device. The backend tunnel agent continues independently of this window.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

try:
    from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRunnable, QThreadPool, QLockFile
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QDesktopServices, QAction
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QMainWindow, QMenu, QMessageBox, QProgressBar, QPushButton,
        QScrollArea, QSizePolicy, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
        QWidget, QGraphicsDropShadowEffect,
    )
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
except ImportError as exc:
    raise SystemExit(
        "OpenRemote GUI requires PySide6. Install with: python -m pip install '.[gui]'"
    ) from exc

from openremote_agent import __version__
from .backend import Backend


THEMES = {
    "light": {
        "background": "#F5F5F8", "sidebar": "#EEF0F5", "surface": "#FFFFFF",
        "border": "#E7E8ED", "text": "#1C2029", "muted": "#818592",
        "accent": "#6864E8", "accent_soft": "#EEEAFE", "positive": "#24B985",
        "warning": "#E8A63C", "negative": "#E36E76", "hover": "#E2E3EC",
    },
    "dark": {
        "background": "#17191F", "sidebar": "#202229", "surface": "#282B34",
        "border": "#343844", "text": "#F5F5F8", "muted": "#A0A4B2",
        "accent": "#A69CFF", "accent_soft": "#34314A", "positive": "#5ED1A6",
        "warning": "#F5C76F", "negative": "#FA8A90", "hover": "#393D47",
    },
}


def icon() -> QIcon:
    pix = QPixmap(72, 72)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#756EF0"))
    painter.drawRoundedRect(5, 5, 62, 62, 18, 18)
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawEllipse(17, 24, 17, 17)
    painter.drawEllipse(38, 31, 17, 17)
    painter.setPen(QColor("#FFFFFF"))
    painter.drawLine(31, 33, 43, 37)
    painter.end()
    return QIcon(pix)


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)


class Task(QRunnable):
    def __init__(self, func):
        super().__init__()
        self.func = func
        self.signals = WorkerSignals()

    def run(self):
        try:
            self.signals.result.emit(self.func())
        except Exception as exc:
            # GUI errors contain only error type, not private CLI or config data.
            self.signals.error.emit(type(exc).__name__)


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout():
            clear(item.layout())


def line(text: str, style: str = "muted") -> QLabel:
    label = QLabel(text)
    label.setObjectName(style)
    label.setWordWrap(True)
    return label


def card(title: str | None = None):
    pane = QFrame()
    pane.setObjectName("card")
    layout = QVBoxLayout(pane)
    layout.setContentsMargins(23, 21, 23, 21)
    layout.setSpacing(10)
    shadow = QGraphicsDropShadowEffect(pane)
    shadow.setBlurRadius(18)
    shadow.setOffset(0, 4)
    shadow.setColor(QColor(15, 20, 40, 12))
    pane.setGraphicsEffect(shadow)
    if title:
        label = line(title, "cardTitle")
        layout.addWidget(label)
    return pane, layout


class MainWindow(QMainWindow):
    def __init__(self, backend: Backend, instance_server=None):
        super().__init__()
        self.backend = backend
        self.instance_server = instance_server
        self.tray = None
        self._closing = False
        self._updating = False
        self._probing = set()
        self._peer_health = {}
        self._snapshot = {}
        self._workers = []
        self.dark = False
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.setWindowTitle("OpenRemote — Control Center")
        self.setWindowIcon(icon())
        self.resize(1050, 720)
        self.setMinimumSize(840, 590)
        self._build()
        self.set_theme()
        self._tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(30000)
        if self.instance_server:
            self.instance_server.newConnection.connect(self._second_instance)
        self.refresh()

    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        base = QHBoxLayout(root)
        base.setSpacing(0)
        base.setContentsMargins(0, 0, 0, 0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(227)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(21, 28, 19, 26)
        nav.setSpacing(11)
        brand = QHBoxLayout()
        self.mark = QLabel()
        self.mark.setPixmap(icon().pixmap(36, 36))
        brand.addWidget(self.mark)
        brand_title = QLabel("OpenRemote")
        brand_title.setObjectName("brand")
        brand.addWidget(brand_title)
        brand.addStretch()
        nav.addLayout(brand)
        nav.addSpacing(31)
        self.nav_buttons = []
        names = [("◈", "Devices"), ("▤", "Activity"), ("⇄", "Transfers"), ("⚙", "Settings")]
        for index, (glyph, title) in enumerate(names):
            button = QPushButton(f"  {glyph}     {title}")
            button.setObjectName("nav")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, n=index: self.show_page(n))
            nav.addWidget(button)
            self.nav_buttons.append(button)
        nav.addStretch()
        self.side_caption = line("Private connection\nControlled by you", "muted")
        nav.addWidget(self.side_caption)
        nav.addWidget(line("v" + __version__, "micro"))
        base.addWidget(side)

        main = QFrame()
        main.setObjectName("main")
        main_layout = QVBoxLayout(main)
        main_layout.setContentsMargins(36, 26, 38, 25)
        main_layout.setSpacing(22)

        header = QHBoxLayout()
        header.addWidget(line("CONTROL CENTER", "eyebrow"))
        header.addStretch()
        self.health_badge = QLabel("◌ Checking tunnel")
        self.health_badge.setObjectName("badge")
        header.addWidget(self.health_badge)
        self.refresh_button = QPushButton("↻  Refresh")
        self.refresh_button.setObjectName("secondary")
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        main_layout.addLayout(header)

        self.pages = QStackedWidget()
        self.page_layouts = []
        for title, subtitle in [
            ("Your devices", "Trusted computers. One private connection."),
            ("Activity", "Background tasks without the terminal clutter."),
            ("Transfers", "Bounded, verified file movement."),
            ("Preferences", "Simple controls. Local-first by design."),
        ]:
            frame = QWidget()
            vl = QVBoxLayout(frame)
            vl.setContentsMargins(0, 0, 0, 0)
            vl.setSpacing(7)
            vl.addWidget(line(title, "pageTitle"))
            vl.addWidget(line(subtitle))
            vl.addSpacing(16)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setObjectName("scroll")
            inner = QWidget()
            body = QVBoxLayout(inner)
            body.setContentsMargins(2, 6, 8, 12)
            body.setSpacing(16)
            body.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(inner)
            vl.addWidget(scroll, 1)
            self.page_layouts.append(body)
            self.pages.addWidget(frame)
        main_layout.addWidget(self.pages)
        footer = QHBoxLayout()
        self.updated_label = line("Waiting for local data…", "micro")
        footer.addWidget(self.updated_label)
        footer.addStretch()
        footer.addWidget(line("Private tunnel", "micro"))
        main_layout.addLayout(footer)
        base.addWidget(main, 1)
        self.show_page(0)

    def _tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QApplication.instance().setQuitOnLastWindowClosed(True)
            return
        tray = QSystemTrayIcon(icon(), self)
        tray.setToolTip("OpenRemote — private connection")
        menu = QMenu()
        action_open = QAction("Show OpenRemote", self)
        action_open.triggered.connect(self.restore)
        action_refresh = QAction("Refresh status", self)
        action_refresh.triggered.connect(self.refresh)
        action_quit = QAction("Quit OpenRemote UI", self)
        action_quit.triggered.connect(self.quit)
        menu.addAction(action_open)
        menu.addAction(action_refresh)
        menu.addSeparator()
        menu.addAction(action_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._tray_activated)
        tray.show()
        self.tray = tray
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore()

    def restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.refresh()

    def _second_instance(self):
        while self.instance_server.hasPendingConnections():
            socket = self.instance_server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
        self.restore()

    def quit(self):
        self._closing = True
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if not self._closing and self.tray:
            event.ignore()
            self.hide()
        else:
            super().closeEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if (self.tray and not self._closing and
                self.windowState() & Qt.WindowState.WindowMinimized):
            QTimer.singleShot(0, self.hide)

    def show_page(self, index):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)

    def set_theme(self):
        c = THEMES["dark" if self.dark else "light"]
        self.setStyleSheet(f"""
            QWidget#root, QFrame#main, QScrollArea#scroll {{
                background: {c['background']};
            }}
            QFrame#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
            QLabel {{ color: {c['text']}; font-size: 13px; background: transparent; }}
            QLabel#brand {{ font-size: 17px; font-weight: 700; }}
            QLabel#pageTitle {{ font-size: 31px; font-weight: 700; }}
            QLabel#cardTitle {{ font-size: 16px; font-weight: 650; }}
            QLabel#muted {{ color: {c['muted']}; font-size: 13px; }}
            QLabel#micro {{ color: {c['muted']}; font-size: 11px; }}
            QLabel#eyebrow {{ color: {c['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 2px; }}
            QLabel#metric {{ font-size: 23px; font-weight: 700; }}
            QLabel#badge {{ background: {c['accent_soft']}; color: {c['accent']}; padding: 9px 15px; border-radius: 16px; font-weight: 700; }}
            QFrame#card {{ background: {c['surface']}; border-radius: 19px; border: 1px solid {c['border']}; }}
            QFrame#item {{ background: {c['surface']}; border-radius: 14px; border: 1px solid {c['border']}; }}
            QPushButton {{ border-radius: 12px; padding: 11px 14px; background: {c['surface']}; color: {c['text']}; font-weight: 600; border: 1px solid {c['border']}; }}
            QPushButton:hover {{ background: {c['hover']}; }}
            QPushButton#nav {{ border: none; text-align: left; padding: 14px 13px; background: transparent; font-size: 13px; }}
            QPushButton#nav:checked {{ background: {c['accent_soft']}; color: {c['accent']}; }}
            QPushButton#secondary {{ background: {c['surface']}; }}
            QPushButton#primary {{ background: {c['accent']}; color: {c['surface']}; border: none; }}
            QCheckBox {{ color: {c['text']}; spacing: 10px; font-size: 13px; }}
            QProgressBar {{ background: {c['sidebar']}; border-radius: 5px; height: 9px; border: none; text-align: center; }}
            QProgressBar::chunk {{ background: {c['accent']}; border-radius: 5px; }}
            QScrollBar:vertical {{ background: transparent; width: 7px; }}
            QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 3px; min-height: 27px; }}
        """)
        self.render_pages()

    def refresh(self):
        if self._updating:
            return
        self._updating = True
        self.refresh_button.setEnabled(False)
        task = Task(self.backend.snapshot)
        task.signals.result.connect(lambda result, t=task: (self._release(t), self._snapshot_ready(result)))
        task.signals.error.connect(lambda error, t=task: (self._release(t), self._snapshot_error(error)))
        self._workers.append(task)
        self.pool.start(task)

    def _release(self, task):
        if task in self._workers:
            self._workers.remove(task)

    def _snapshot_ready(self, snapshot):
        self._snapshot = snapshot
        self._updating = False
        self.refresh_button.setEnabled(True)
        self.updated_label.setText("Last checked  " + datetime.now().strftime("%H:%M:%S"))
        self.render_pages()

    def _snapshot_error(self, reason):
        self._updating = False
        self.refresh_button.setEnabled(True)
        self.health_badge.setText("●  Local status unavailable")
        self.updated_label.setText("Unable to read local configuration: " + reason)

    def probe(self, peer):
        if peer in self._probing:
            return
        self._probing.add(peer)
        self._peer_health[peer] = ("checking", "Checking agent via SSH…")
        self.render_pages()
        task = Task(lambda: self.backend.probe_peer(peer))
        task.signals.result.connect(lambda result, t=task: (self._release(t), self._peer_ready(result)))
        task.signals.error.connect(lambda error, n=peer, t=task: (self._release(t), self._peer_failed(n, error)))
        self._workers.append(task)
        self.pool.start(task)

    def _peer_ready(self, info):
        peer = info["device_id"]
        self._probing.discard(peer)
        self._peer_health[peer] = ("online" if info["online"] else "offline", info["detail"])
        self.render_pages()

    def _peer_failed(self, peer, reason):
        self._probing.discard(peer)
        self._peer_health[peer] = ("offline", "Probe error: " + reason)
        self.render_pages()

    def render_pages(self):
        s = self._snapshot
        if not s:
            return
        c = THEMES["dark" if self.dark else "light"]
        online = bool(s["tunnel_ready"])
        self.health_badge.setText("●  Tunnel ready" if online else "◌  Tunnel not ready")
        self.health_badge.setStyleSheet(
            f"color: {c['positive'] if online else c['warning']};")
        for layout in self.page_layouts:
            clear(layout)
        self._devices(s)
        self._jobs(s)
        self._transfers(s)
        self._settings(s)

    def _devices(self, s):
        body = self.page_layouts[0]
        metrics = QGridLayout()
        metrics.setSpacing(13)
        for i, (title, value) in enumerate([
            ("CONNECTED GATEWAY", "Ready" if s["tunnel_ready"] else "Offline"),
            ("WORKSPACES", str(s["workspaces"])),
            ("SYSTEM LOAD", s["load"]),
        ]):
            pane, layout = card()
            layout.addWidget(line(title, "eyebrow"))
            layout.addWidget(line(value, "metric"))
            metrics.addWidget(pane, 0, i)
        body.addLayout(metrics)
        body.addWidget(line("Registered devices", "cardTitle"))
        for name, is_local in [(s["local_id"], True)] + [(x, False) for x in s["peers"]]:
            pane, layout = card()
            top = QHBoxLayout()
            symbol = QLabel("●")
            palette = THEMES["dark" if self.dark else "light"]
            color = palette["positive"] if is_local else palette["muted"]
            symbol.setStyleSheet(f"font-size: 17px; color: {color};")
            top.addWidget(symbol)
            title = line(name, "cardTitle")
            top.addWidget(title)
            top.addStretch()
            if is_local:
                description = "Local agent • " + s["role"]
                status = "Local"
            else:
                status, description = self._peer_health.get(
                    name, ("configured", "Not probed yet"))
            top.addWidget(line(status.upper(), "eyebrow"))
            layout.addLayout(top)
            layout.addWidget(line(description))
            if not is_local:
                button = QPushButton("Check connection")
                button.setObjectName("secondary")
                button.setEnabled(name not in self._probing)
                button.clicked.connect(lambda checked=False, n=name: self.probe(n))
                layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
            body.addWidget(pane)
        body.addWidget(line(
            "Peers are not polled automatically. Use Check connection to verify a remote agent.",
            "micro"))

    def _jobs(self, s):
        body = self.page_layouts[1]
        jobs = s["jobs"]
        body.addWidget(line(f"{len(jobs)} recent local jobs", "cardTitle"))
        if not jobs:
            pane, layout = card("No background jobs yet")
            layout.addWidget(line("Start a job from ChatGPT. It will appear here without further setup."))
            body.addWidget(pane)
        for job in jobs:
            pane, layout = card()
            top = QHBoxLayout()
            top.addWidget(line(job["label"], "cardTitle"))
            top.addStretch()
            top.addWidget(line(job["status"].upper(), "eyebrow"))
            layout.addLayout(top)
            layout.addWidget(line(
                f"{job['id'][:12]}  ·  stdout {job['stdout_bytes']:,} B  ·  "
                f"stderr {job['stderr_bytes']:,} B"))
            button = QPushButton("Copy job ID")
            button.clicked.connect(
                lambda checked=False, value=job["id"]:
                QApplication.clipboard().setText(value))
            layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
            body.addWidget(pane)
        body.addWidget(line(
            "Jobs started on SSH peers are stored on those peers, not in this gateway's local history.",
            "micro"))

    def _transfers(self, s):
        body = self.page_layouts[2]
        pending = s["uploads"]
        body.addWidget(line(f"{len(pending)} pending local transfers", "cardTitle"))
        if not pending:
            pane, layout = card("All caught up")
            layout.addWidget(line("No resumable uploads are currently pending on this gateway."))
            body.addWidget(pane)
        for transfer in pending:
            pane, layout = card(transfer["name"])
            size = transfer["total"]
            received = transfer["received"]
            layout.addWidget(line(f"{received:,} / {size:,} bytes"))
            bar = QProgressBar()
            bar.setTextVisible(False)
            bar.setMaximum(100)
            bar.setValue(int(100 * received / size) if size else 100)
            layout.addWidget(bar)
            body.addWidget(pane)
        body.addWidget(line(
            "Remote-device transfers live on the remote machine. No file bytes are displayed here.",
            "micro"))

    def _settings(self, s):
        body = self.page_layouts[3]
        pane, layout = card("Connection")
        layout.addWidget(line("Agent role: " + s["role"]))
        layout.addWidget(line("Dynamic commands: " + (
            "Owner enabled" if s["ad_hoc"] else "Disabled")))
        layout.addWidget(line("Gateway memory: " + s["memory"]))
        layout.addWidget(line("Private configuration (never displayed as contents):"))
        layout.addWidget(line(str(self.backend.config_path), "micro"))
        btn = QPushButton("Copy config path")
        btn.clicked.connect(
            lambda: QApplication.clipboard().setText(str(self.backend.config_path)))
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignLeft)
        body.addWidget(pane)
        pane, layout = card("Appearance and behavior")
        toggle = QCheckBox("Dark appearance")
        toggle.setChecked(self.dark)
        toggle.toggled.connect(self._dark_change)
        layout.addWidget(toggle)
        layout.addWidget(line(
            "Closing or minimizing hides the window in the tray when your desktop supports it. "
            "The MCP tunnel is a separate background service and stays available even when you quit this UI."))
        layout.addWidget(line(
            "The dashboard refreshes local data every 30 seconds and probes SSH only on request.",
            "micro"))
        body.addWidget(pane)
        pane, layout = card("Privacy")
        layout.addWidget(line(
            "No external analytics, API secrets, shell history or command arguments "
            "are collected by the dashboard."))
        body.addWidget(pane)

    def _dark_change(self, enabled):
        self.dark = bool(enabled)
        self.set_theme()


def _single_instance(name):
    # QLockFile is the atomic admission gate. QLocalServer is only used to
    # raise an existing window, never to grant extra MCP access.
    cache = Path.home() / ".cache" / "openremote"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = QLockFile(str(cache / "gui-instance.lock"))
    if not lock.tryLock(0):
        socket = QLocalSocket()
        socket.connectToServer(name)
        if socket.waitForConnected(500):
            socket.write(b"SHOW")
            socket.flush()
            socket.waitForBytesWritten(500)
            socket.disconnectFromServer()
        return None, None
    QLocalServer.removeServer(name)  # safe: process lock is held
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        lock.unlock()
        return None, None
    return server, lock

def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenRemote desktop dashboard (optional)")
    parser.add_argument("--config", type=Path,
                        default=Path.home() / ".config/openremote-agent/agent.json")
    parser.add_argument("--health-url", default="http://127.0.0.1:18767/readyz")
    parser.add_argument("--screenshot", type=Path, default=None,
                        help="Offscreen development screenshot and exit")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("OpenRemote")
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))
    server, instance_lock = None, None
    if args.screenshot is None:
        import getpass
        server, instance_lock = _single_instance("openremote-gui-" + getpass.getuser())
        if server is None:
            return 0  # The existing instance was signalled, or startup was denied.
    window = MainWindow(Backend(args.config, args.health_url), instance_server=server)
    window.show()
    if args.screenshot is not None:
        # CI/offscreen visual snapshot, without requiring a real tray.
        def capture():
            window.grab().save(str(args.screenshot))
            window.quit()
        QTimer.singleShot(1700, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
