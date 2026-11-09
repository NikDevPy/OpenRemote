"""OpenRemote Control Center — bilingual, accessible, low-overhead Linux GUI.

The desktop UI is optional and independent of the MCP tunnel service.
All SSH probes require explicit user interaction. No credentials, command
arguments, process stdout or binary payloads are rendered by the UI.
"""
from __future__ import annotations

import argparse
import socket
from datetime import datetime
from pathlib import Path
import sys
import time

try:
    from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRunnable, QThreadPool, QLockFile, QSettings
    from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QAction, QPalette
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout,
        QLabel, QMainWindow, QMenu, QProgressBar, QPushButton,
        QScrollArea, QStackedWidget, QSystemTrayIcon, QVBoxLayout,
        QWidget, QGraphicsDropShadowEffect,
    )
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
except ImportError as exc:
    raise SystemExit(
        "OpenRemote GUI requires PySide6. Install with: python -m pip install '.[gui]'"
    ) from exc

from openremote_agent import __version__
from .backend import Backend
from .desktop import autostart_enabled, launcher_installed, set_autostart
from .i18n import STRINGS, LANGUAGES, translate


THEMES = {
    "light": {
        "background": "#F7F8FB", "sidebar": "#F0F2F8", "surface": "#FFFFFF",
        "border": "#E8EAF1", "text": "#1C2434", "muted": "#738092",
        "accent": "#7465E9", "accent_soft": "#EEEAFE", "positive": "#18A77A",
        "positive_soft": "#E7F8F2", "warning": "#C48C31",
        "warning_soft": "#FFF5E1", "negative": "#D44F5E",
        "negative_soft": "#FDEDF0", "hover": "#E5E8F1",
        "neutral_soft": "#ECEFF3", "neutral": "#7A8594",
    },
    "dark": {
        "background": "#171C26", "sidebar": "#1D2330", "surface": "#272E3B",
        "border": "#383F4E", "text": "#F3F5F9", "muted": "#B3B9C6",
        "accent": "#ACA2FF", "accent_soft": "#37314F", "positive": "#59D3A6",
        "positive_soft": "#233F39", "warning": "#F3C371",
        "warning_soft": "#43392C", "negative": "#F48B96",
        "negative_soft": "#4A303B", "hover": "#353D4E",
        "neutral_soft": "#353C49", "neutral": "#B3B9C6",
    },
}


def icon() -> QIcon:
    """Use the same vector asset as the installed Linux desktop icon."""
    asset = Path(__file__).parent / "assets" / "openremote.svg"
    if asset.is_file():
        value = QIcon(str(asset))
        if not value.isNull():
            return value
    # Bundled-file fallback for unusual Qt SVG installations.
    pix = QPixmap(72, 72)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#7465E9"))
    p.drawRoundedRect(5, 5, 62, 62, 18, 18)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(18, 24, 18, 18)
    p.drawEllipse(37, 30, 18, 18)
    p.end()
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
            # Never render detailed exceptions; their text can contain sensitive
            # local paths, network endpoints or private configuration values.
            self.signals.error.emit(type(exc).__name__)


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
        elif item.layout():
            clear(item.layout())


def text_label(value: str, style: str = "muted") -> QLabel:
    result = QLabel(value)
    result.setObjectName(style)
    result.setWordWrap(True)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return result


def card(title: str | None = None, *, shadow: bool = True):
    pane = QFrame()
    pane.setObjectName("card")
    layout = QVBoxLayout(pane)
    layout.setContentsMargins(21, 19, 21, 19)
    layout.setSpacing(9)
    if shadow:
        effect = QGraphicsDropShadowEffect(pane)
        effect.setBlurRadius(20)
        effect.setOffset(0, 4)
        effect.setColor(QColor(30, 38, 66, 10))
        pane.setGraphicsEffect(effect)
    if title:
        layout.addWidget(text_label(title, "cardTitle"))
    return pane, layout


class MainWindow(QMainWindow):
    TITLES = (
        ("devices_title", "devices_subtitle"),
        ("activity_title", "activity_subtitle"),
        ("transfers_title", "transfers_subtitle"),
        ("settings_title", "settings_subtitle"),
    )

    def __init__(self, backend: Backend, instance_server=None):
        super().__init__()
        self.backend = backend
        self.instance_server = instance_server
        self.prefs = QSettings("OpenRemote", "ControlCenter")
        preferred = str(self.prefs.value("language", "ru"))
        self.language = preferred if preferred in LANGUAGES else "ru"
        self.dark = self.prefs.value("dark", False, type=bool)
        self.tray = None
        self._closing = False
        self._updating = False
        self._probing = set()
        self._peer_health = {}
        self._snapshot = {}
        self._workers = []
        self._refresh_time = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

        self.setWindowTitle("OpenRemote")
        self.setWindowIcon(icon())
        self.resize(1080, 750)
        self.setMinimumSize(810, 575)
        self._build()
        self.set_theme()
        self._tray()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._periodic_refresh)
        self.timer.start(30000)
        if self.instance_server:
            self.instance_server.newConnection.connect(self._second_instance)
        self.refresh()

    def tr(self, key: str, **args) -> str:
        return translate(self.language, key, **args)

    def _build(self):
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        base = QHBoxLayout(root)
        base.setSpacing(0)
        base.setContentsMargins(0, 0, 0, 0)

        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(224)
        nav = QVBoxLayout(side)
        nav.setContentsMargins(19, 26, 19, 25)
        nav.setSpacing(9)
        brand = QHBoxLayout()
        brand.setSpacing(11)
        mark = QLabel()
        mark.setPixmap(icon().pixmap(40, 40))
        mark.setFixedSize(40, 40)
        brand.addWidget(mark)
        label = text_label("OpenRemote", "brand")
        label.setWordWrap(False)
        brand.addWidget(label)
        brand.addStretch()
        nav.addLayout(brand)
        nav.addSpacing(37)

        self.nav_buttons = []
        for i, glyph in enumerate(("◈", "▤", "⇄", "⚙")):
            button = QPushButton()
            button.setObjectName("nav")
            button.setProperty("glyph", glyph)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(43)
            button.clicked.connect(lambda checked=False, n=i: self.show_page(n))
            nav.addWidget(button)
            self.nav_buttons.append(button)

        nav.addStretch()
        self.side_caption = text_label("", "muted")
        nav.addWidget(self.side_caption)
        self.version_caption = text_label("", "micro")
        nav.addWidget(self.version_caption)
        base.addWidget(side)

        main = QFrame()
        main.setObjectName("main")
        container = QVBoxLayout(main)
        container.setContentsMargins(33, 26, 35, 22)
        container.setSpacing(19)

        header = QHBoxLayout()
        self.header_label = text_label("", "eyebrow")
        header.addWidget(self.header_label)
        header.addStretch()
        self.health_badge = QLabel()
        self.health_badge.setObjectName("healthBadge")
        self.health_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.health_badge.setMinimumHeight(36)
        header.addWidget(self.health_badge)
        self.refresh_button = QPushButton()
        self.refresh_button.setObjectName("secondary")
        self.refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_button.clicked.connect(self.refresh)
        header.addWidget(self.refresh_button)
        container.addLayout(header)

        self.pages = QStackedWidget()
        self.page_layouts = []
        self.page_headings = []
        self.scrollers = []
        for _ in range(4):
            page = QWidget()
            page.setObjectName("page")
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 0, 0, 0)
            page_layout.setSpacing(7)
            title = text_label("", "pageTitle")
            subtitle = text_label("", "muted")
            self.page_headings.append((title, subtitle))
            page_layout.addWidget(title)
            page_layout.addWidget(subtitle)
            page_layout.addSpacing(12)

            scroll = QScrollArea()
            scroll.setObjectName("scroll")
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            inner = QWidget()
            inner.setObjectName("scrollContent")
            body = QVBoxLayout(inner)
            body.setContentsMargins(2, 4, 8, 11)
            body.setSpacing(15)
            body.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(inner)
            page_layout.addWidget(scroll, 1)
            self.page_layouts.append(body)
            self.scrollers.append(scroll)
            self.pages.addWidget(page)
        container.addWidget(self.pages, 1)

        footer = QHBoxLayout()
        self.updated_label = text_label("", "micro")
        footer.addWidget(self.updated_label)
        footer.addStretch()
        self.footer_right = text_label("", "micro")
        footer.addWidget(self.footer_right)
        container.addLayout(footer)
        base.addWidget(main, 1)
        self.show_page(0)
        self._retranslate_static()

    def _retranslate_static(self):
        names = ("devices_nav", "activity_nav", "transfers_nav", "settings_nav")
        for i, name in enumerate(names):
            glyph = self.nav_buttons[i].property("glyph")
            self.nav_buttons[i].setText(f"  {glyph}     {self.tr(name)}")
        for i, (header, subtitle) in enumerate(self.page_headings):
            header.setText(self.tr(self.TITLES[i][0]))
            subtitle.setText(self.tr(self.TITLES[i][1]))
        self.header_label.setText(self.tr("control_center"))
        self.refresh_button.setText("↻  " + self.tr("refresh"))
        self.side_caption.setText(self.tr("private_connection"))
        self.version_caption.setText(self.tr("version", version=__version__))
        self.footer_right.setText(self.tr("private_tunnel"))
        self.updated_label.setText(
            self.tr("last_checked", time=self._refresh_time)
            if self._refresh_time else self.tr("waiting"))
        if self.tray:
            self.tray.setToolTip(self.tr("tray_tooltip"))
            self.action_open.setText(self.tr("tray_open"))
            self.action_refresh.setText(self.tr("tray_refresh"))
            self.action_quit.setText(self.tr("tray_quit"))

    def _tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QApplication.instance().setQuitOnLastWindowClosed(True)
            return
        tray = QSystemTrayIcon(icon(), self)
        menu = QMenu()
        self.action_open = QAction(self.tr("tray_open"), self)
        self.action_open.triggered.connect(self.restore)
        self.action_refresh = QAction(self.tr("tray_refresh"), self)
        self.action_refresh.triggered.connect(self.refresh)
        self.action_quit = QAction(self.tr("tray_quit"), self)
        self.action_quit.triggered.connect(self.quit)
        menu.addAction(self.action_open)
        menu.addAction(self.action_refresh)
        menu.addSeparator()
        menu.addAction(self.action_quit)
        tray.setContextMenu(menu)
        tray.setToolTip(self.tr("tray_tooltip"))
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
        if (self.tray and not self._closing
                and self.windowState() & Qt.WindowState.WindowMinimized):
            QTimer.singleShot(0, self.hide)

    def show_page(self, index: int):
        self.pages.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setChecked(i == index)
        self.render_pages()

    def _periodic_refresh(self):
        # A hidden GUI is essentially idle. Only refresh when somebody is
        # looking at the dashboard; the independent MCP tunnel stays active.
        if self.isVisible():
            self.refresh()

    def set_theme(self):
        c = THEMES["dark" if self.dark else "light"]
        # The former app styled the QScrollArea but left its viewport and
        # scrollContent with the desktop's default dark palette. Explicitly
        # style every surface to avoid light cards on black rectangles.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(c["background"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(c["background"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(c["text"]))
        self.setPalette(palette)
        self.setStyleSheet(f"""
            QWidget#root, QWidget#page, QWidget#scrollContent,
            QFrame#main, QScrollArea#scroll {{
                background-color: {c['background']};
                border: none;
            }}
            QScrollArea#scroll > QWidget, QScrollArea#scroll QWidget#qt_scrollarea_viewport {{
                background-color: {c['background']};
                border: none;
            }}
            QFrame#sidebar {{
                background-color: {c['sidebar']};
                border: none; border-right: 1px solid {c['border']};
            }}
            QLabel {{ color: {c['text']}; font-size: 13px; background: transparent; }}
            QLabel#brand {{ font-size: 17px; font-weight: 700; }}
            QLabel#pageTitle {{ font-size: 31px; font-weight: 700; }}
            QLabel#cardTitle {{ font-size: 15px; font-weight: 700; }}
            QLabel#muted {{ color: {c['muted']}; font-size: 12px; }}
            QLabel#micro {{ color: {c['muted']}; font-size: 11px; }}
            QLabel#eyebrow {{ color: {c['muted']}; font-size: 10px; font-weight: 700; }}
            QLabel#metric {{ font-size: 22px; font-weight: 700; }}
            QLabel#healthBadge {{
                background-color: {c['positive_soft']}; color: {c['positive']};
                padding: 7px 14px; border-radius: 16px; font-weight: 700;
            }}
            QFrame#card {{
                background-color: {c['surface']}; border-radius: 18px;
                border: 1px solid {c['border']};
            }}
            QPushButton {{
                border: 1px solid {c['border']}; border-radius: 11px;
                padding: 10px 13px; background-color: {c['surface']};
                color: {c['text']}; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {c['hover']}; }}
            QPushButton:disabled {{ color: {c['muted']}; opacity: .5; }}
            QPushButton#nav {{
                border: none; text-align: left; padding: 12px 13px;
                background-color: transparent; font-size: 13px;
            }}
            QPushButton#nav:checked {{
                background-color: {c['accent_soft']}; color: {c['accent']};
            }}
            QPushButton#nav:hover:!checked {{ background-color: {c['hover']}; }}
            QCheckBox {{ color: {c['text']}; spacing: 10px; font-size: 13px; }}
            QComboBox {{
                border: 1px solid {c['border']}; border-radius: 11px;
                padding: 9px 13px; min-height: 26px; min-width: 145px;
                background-color: {c['surface']}; color: {c['text']};
            }}
            QComboBox QAbstractItemView {{
                border: 1px solid {c['border']};
                background-color: {c['surface']}; color: {c['text']};
                selection-background-color: {c['accent_soft']};
                selection-color: {c['text']};
            }}
            QProgressBar {{
                background-color: {c['sidebar']}; border: none;
                border-radius: 5px; min-height: 9px; max-height: 9px;
            }}
            QProgressBar::chunk {{
                background-color: {c['accent']}; border-radius: 5px;
            }}
            QScrollBar:vertical {{ background: transparent; width: 7px; }}
            QScrollBar::handle:vertical {{
                background-color: {c['border']}; border-radius: 3px; min-height: 27px;
            }}
        """)
        for scroll in self.scrollers:
            scroll.viewport().setPalette(palette)
            scroll.viewport().setAutoFillBackground(True)
            scroll.widget().setPalette(palette)
            scroll.widget().setAutoFillBackground(True)
            scroll.viewport().setStyleSheet(f"background: {c['background']}; border: none;")
            scroll.widget().setStyleSheet(
                f"QWidget#scrollContent {{ background: {c['background']}; border: none; }}")
        self.render_pages()

    def refresh(self):
        if self._updating:
            return
        self._updating = True
        self.refresh_button.setEnabled(False)
        task = Task(self.backend.snapshot)
        task.signals.result.connect(
            lambda result, t=task: (self._release(t), self._snapshot_ready(result)))
        task.signals.error.connect(
            lambda reason, t=task: (self._release(t), self._snapshot_error(reason)))
        self._workers.append(task)
        self.pool.start(task)

    def _release(self, task):
        if task in self._workers:
            self._workers.remove(task)

    def _snapshot_ready(self, snapshot):
        self._snapshot = snapshot
        self._refresh_time = datetime.now().strftime("%H:%M:%S")
        self._updating = False
        self.refresh_button.setEnabled(True)
        self.updated_label.setText(self.tr("last_checked", time=self._refresh_time))
        if self.isVisible():
            self.render_pages()

    def _snapshot_error(self, reason):
        self._updating = False
        self.refresh_button.setEnabled(True)
        self.health_badge.setText(self.tr("tunnel_offline"))
        self.updated_label.setText(self.tr("error_status", reason=reason))

    def probe(self, peer: str):
        if peer in self._probing:
            return
        self._probing.add(peer)
        self._peer_health[peer] = {"state": "checking", "checked": None}
        self.render_pages()
        task = Task(lambda: self.backend.probe_peer(peer))
        task.signals.result.connect(
            lambda result, t=task: (self._release(t), self._peer_ready(result)))
        task.signals.error.connect(
            lambda reason, n=peer, t=task: (
                self._release(t), self._peer_failed(n, reason)))
        self._workers.append(task)
        self.pool.start(task)

    def _peer_ready(self, info):
        peer = info["device_id"]
        self._probing.discard(peer)
        self._peer_health[peer] = {
            "state": "online" if info["online"] else "offline",
            "checked": time.time(),
        }
        self.render_pages()

    def _peer_failed(self, peer, reason):
        self._probing.discard(peer)
        self._peer_health[peer] = {"state": "offline", "checked": time.time()}
        self.render_pages()

    def render_pages(self):
        if not self._snapshot:
            return
        online = bool(self._snapshot["tunnel_ready"])
        c = THEMES["dark" if self.dark else "light"]
        key = "tunnel_ready" if online else "tunnel_offline"
        status_color = c["positive"] if online else c["warning"]
        soft = c["positive_soft"] if online else c["warning_soft"]
        self.health_badge.setText(("●  " if online else "◌  ") + self.tr(key))
        self.health_badge.setStyleSheet(
            f"background-color: {soft}; color: {status_color}; "
            "padding: 7px 14px; border-radius: 16px; font-weight: 700;")
        index = self.pages.currentIndex()
        body = self.page_layouts[index]
        clear(body)
        if index == 0:
            self._devices(self._snapshot)
        elif index == 1:
            self._jobs(self._snapshot)
        elif index == 2:
            self._transfers(self._snapshot)
        else:
            self._settings(self._snapshot)

    def _status_pill(self, state: str, title: str, object_name: str):
        c = THEMES["dark" if self.dark else "light"]
        tones = {
            "online": (c["positive"], c["positive_soft"]),
            "offline": (c["negative"], c["negative_soft"]),
            "checking": (c["warning"], c["warning_soft"]),
            "configured": (c["neutral"], c["neutral_soft"]),
            "local": (c["positive"], c["positive_soft"]),
        }
        fg, bg = tones.get(state, tones["configured"])
        label = QLabel("●  " + title)
        label.setObjectName(object_name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setMinimumHeight(31)
        label.setStyleSheet(
            f"QLabel {{ color: {fg}; background-color: {bg}; "
            "border-radius: 14px; padding: 5px 12px; font-size: 11px; "
            "font-weight: 700; }")
        label.setAccessibleName(title)
        return label

    def _devices(self, s):
        body = self.page_layouts[0]
        metrics = QGridLayout()
        metrics.setSpacing(13)
        rows = [
            ("gateway", "ready" if s["tunnel_ready"] else "unavailable"),
            ("workspace_count", str(s["workspaces"])),
            ("system_load", s["load"]),
        ]
        for i, (label_key, value) in enumerate(rows):
            pane, layout = card()
            layout.setSpacing(12)
            layout.addWidget(text_label(self.tr(label_key), "eyebrow"))
            layout.addWidget(
                text_label(self.tr(value) if value in ("ready", "unavailable") else value, "metric"))
            metrics.addWidget(pane, 0, i)
        body.addLayout(metrics)
        body.addSpacing(4)
        body.addWidget(text_label(self.tr("registered_devices"), "cardTitle"))

        for name, is_local in [(s["local_id"], True)] + [(x, False) for x in s["peers"]]:
            pane, layout = card()
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel("◆")
            badge.setObjectName("deviceSymbol_" + name)
            badge.setFixedSize(40, 40)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c = THEMES["dark" if self.dark else "light"]
            badge.setStyleSheet(
                f"background: {c['accent_soft']}; color: {c['accent']}; "
                "border-radius: 13px; font-size: 18px;")
            row.addWidget(badge)
            labels = QVBoxLayout()
            labels.setSpacing(3)
            heading = text_label(name, "cardTitle")
            heading.setWordWrap(False)
            labels.addWidget(heading)
            if is_local:
                state = "local" if s["tunnel_ready"] else "offline"
                description = self.tr("local_agent", role=s["role"])
            else:
                data = self._peer_health.get(name, {"state": "configured", "checked": None})
                state = data["state"]
                checked = data.get("checked")
                if state == "online" and checked and (time.time() - checked > 90):
                    state = "configured"  # Never claim current connectivity from stale evidence.
                if checked:
                    checked_time = datetime.fromtimestamp(checked).strftime("%H:%M:%S")
                    description = self.tr("last_verified", time=checked_time) if data["state"] == "online" else (
                        self.tr("probe_failed"))
                else:
                    description = self.tr("probe_started") if state == "checking" else (
                        self.tr("not_probed"))
            labels.addWidget(text_label(description, "muted"))
            row.addLayout(labels)
            row.addStretch()
            label_key = "local" if is_local and state == "local" else state
            pill = self._status_pill(
                state, self.tr(label_key),
                "deviceStatus_" + name)
            row.addWidget(pill)
            layout.addLayout(row)
            if not is_local:
                control = QHBoxLayout()
                control.addSpacing(52)
                action = QPushButton(self.tr("probe_again") if name in self._peer_health
                                     else self.tr("probe_action"))
                action.setObjectName("secondary")
                action.setMinimumWidth(148)
                action.setCursor(Qt.CursorShape.PointingHandCursor)
                action.setEnabled(name not in self._probing)
                action.clicked.connect(lambda checked=False, n=name: self.probe(n))
                control.addWidget(action)
                control.addStretch()
                layout.addLayout(control)
            body.addWidget(pane)
        body.addWidget(text_label(self.tr("probe_note"), "micro"))

    def _jobs(self, s):
        body = self.page_layouts[1]
        jobs = s["jobs"]
        body.addWidget(text_label(self.tr("jobs_count", n=len(jobs)), "cardTitle"))
        if not jobs:
            pane, layout = card(self.tr("jobs_empty"))
            layout.addWidget(text_label(self.tr("jobs_empty_hint")))
            body.addWidget(pane)
        for job in jobs:
            pane, layout = card()
            top = QHBoxLayout()
            job_name = job["label"]
            if job_name == "Unlabelled job":
                job_name = self.tr("job_unlabelled")
            top.addWidget(text_label(job_name, "cardTitle"))
            top.addStretch()
            state = job["status"]
            tone = ("online" if state == "completed" else
                    "checking" if state in ("starting", "running", "cancelling") else "offline")
            label_key = "job_" + state
            name = self.tr(label_key) if label_key in STRINGS[self.language] else state
            top.addWidget(self._status_pill(tone, name, "jobStatus"))
            layout.addLayout(top)
            layout.addWidget(text_label(job["id"][:12] + " · " + self.tr(
                "job_streams", stdout=f'{job["stdout_bytes"]:,} B',
                stderr=f'{job["stderr_bytes"]:,} B')))
            action = QPushButton(self.tr("job_id_copy"))
            action.setObjectName("secondary")
            action.setCursor(Qt.CursorShape.PointingHandCursor)
            action.clicked.connect(lambda checked=False, value=job["id"]:
                                   QApplication.clipboard().setText(value))
            layout.addWidget(action, alignment=Qt.AlignmentFlag.AlignLeft)
            body.addWidget(pane)
        body.addWidget(text_label(self.tr("jobs_remote_note"), "micro"))

    def _transfers(self, s):
        body = self.page_layouts[2]
        pending = s["uploads"]
        body.addWidget(text_label(self.tr("uploads_count", n=len(pending)), "cardTitle"))
        if not pending:
            pane, layout = card(self.tr("uploads_empty"))
            layout.addWidget(text_label(self.tr("uploads_empty_hint")))
            body.addWidget(pane)
        for transfer in pending:
            pane, layout = card(transfer["name"])
            total = transfer["total"]
            received = transfer["received"]
            layout.addWidget(text_label(self.tr(
                "uploads_bytes", received=f"{received:,} B", total=f"{total:,} B")))
            progress = QProgressBar()
            progress.setTextVisible(False)
            progress.setMaximum(100)
            progress.setValue(int(100 * received / total) if total else 100)
            layout.addWidget(progress)
            body.addWidget(pane)
        body.addWidget(text_label(self.tr("uploads_remote_note"), "micro"))

    def _settings(self, s):
        body = self.page_layouts[3]
        pane, layout = card(self.tr("appearance"))
        lang_row = QHBoxLayout()
        lang_row.addWidget(text_label(self.tr("language"), "cardTitle"))
        lang_row.addStretch()
        language_select = QComboBox()
        language_select.setObjectName("languageSelect")
        language_select.addItem("Русский", "ru")
        language_select.addItem("English", "en")
        language_select.setCurrentIndex(0 if self.language == "ru" else 1)
        language_select.currentIndexChanged.connect(
            lambda index, combo=language_select: self._language_change(combo.itemData(index)))
        lang_row.addWidget(language_select)
        layout.addLayout(lang_row)
        theme = QCheckBox(self.tr("dark"))
        theme.setObjectName("darkTheme")
        theme.setChecked(self.dark)
        theme.toggled.connect(self._dark_change)
        layout.addWidget(theme)

        autostart = QCheckBox(self.tr("startup"))
        autostart.setObjectName("autostartToggle")
        autostart.setChecked(autostart_enabled())
        autostart.setEnabled(launcher_installed())
        autostart.toggled.connect(self._startup_change)
        layout.addWidget(autostart)
        if not launcher_installed():
            layout.addWidget(text_label(self.tr("startup_missing"), "micro"))
        layout.addWidget(text_label(self.tr("tray_tip")))
        layout.addWidget(text_label(self.tr("interval_tip"), "micro"))
        body.addWidget(pane)

        pane, layout = card(self.tr("connection"))
        row = QHBoxLayout()
        row.addWidget(text_label(self.tr("agent_role") + ":"))
        row.addStretch()
        row.addWidget(text_label(s["role"], "cardTitle"))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(text_label(self.tr("ad_hoc") + ":"))
        row.addStretch()
        row.addWidget(text_label(
            self.tr("enabled") if s["ad_hoc"] else self.tr("disabled"), "cardTitle"))
        layout.addLayout(row)
        layout.addWidget(text_label(self.tr("memory") + ": " + s["memory"]))
        layout.addWidget(text_label(self.tr("config_description"), "micro"))
        layout.addWidget(text_label(str(self.backend.config_path), "micro"))
        copy = QPushButton(self.tr("copy_config"))
        copy.setObjectName("secondary")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(
            str(self.backend.config_path)))
        layout.addWidget(copy, alignment=Qt.AlignmentFlag.AlignLeft)
        body.addWidget(pane)

        pane, layout = card(self.tr("privacy"))
        layout.addWidget(text_label(self.tr("privacy_tip")))
        body.addWidget(pane)

    def _language_change(self, language: str):
        if language not in LANGUAGES or language == self.language:
            return
        self.language = language
        self.prefs.setValue("language", language)
        self.prefs.sync()
        self._retranslate_static()
        self.render_pages()

    def _dark_change(self, enabled: bool):
        if self.dark == bool(enabled):
            return
        self.dark = bool(enabled)
        self.prefs.setValue("dark", self.dark)
        self.prefs.sync()
        self.set_theme()

    def _startup_change(self, enabled: bool):
        try:
            set_autostart(bool(enabled))
        except (OSError, ValueError, PermissionError):
            # Restore the control to its actual persisted state without
            # an endless signal loop or displaying private filesystem details.
            self.render_pages()
            self.updated_label.setText(self.tr("startup_error"))


def _single_instance(name):
    # QLockFile is the atomic admission gate; QLocalServer merely raises an
    # existing window. It does not expose any MCP administrative capabilities.
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
    QLocalServer.removeServer(name)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        lock.unlock()
        return None, None
    return server, lock



def discover_config(explicit: Path | None = None,
                    home: Path | None = None,
                    host: str | None = None) -> Path:
    """Reuse an existing authorized device config before starting onboarding.

    No file writes. With multiple configs, prefer the current hostname
    rather than accidentally using old test or rollback configurations.
    """
    if explicit is not None:
        return Path(explicit).expanduser().absolute()
    base = (home or Path.home()) / ".config" / "openremote-agent"
    default = base / "agent.json"
    if default.is_file() and not default.is_symlink():
        return default
    local_name = host or socket.gethostname()
    local_name = local_name.split(".", 1)[0]
    named = base / (local_name + ".json")
    if named.is_file() and not named.is_symlink():
        return named
    candidates = [
        x for x in base.glob("*.json")
        if x.name != default.name and not x.is_symlink()
        and "-test" not in x.stem and "-v0." not in x.stem
        and "rollback" not in x.stem
    ]
    if len(candidates) == 1:
        return candidates[0]
    return default

def main(argv=None):
    parser = argparse.ArgumentParser(description="OpenRemote Control Center (optional GUI)")
    parser.add_argument("--config", type=Path, default=None,
                        help="Private agent config (auto-detects hostname.json when omitted)")
    parser.add_argument("--health-url", default="http://127.0.0.1:18767/readyz")
    parser.add_argument("--screenshot", type=Path, default=None,
                        help="Offscreen UI screenshot and exit")
    parser.add_argument("--page", type=int, choices=range(4), default=0,
                        help="For UI testing: dashboard page index 0–3")
    parser.add_argument("--language", choices=LANGUAGES, default=None,
                        help="Override interface language for this session")
    parser.add_argument("--dark", action="store_true", help="Use dark dashboard theme")
    parser.add_argument("--setup", action="store_true",
                        help="Open first-run setup wizard even if a config exists")
    parser.add_argument("--wizard-screenshot", type=Path, default=None,
                        help="Offscreen setup-wizard screenshot and exit")
    parser.add_argument("--wizard-step", type=int, choices=range(6), default=0)
    args = parser.parse_args(argv)
    args.config = discover_config(args.config)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("OpenRemote")
    app.setDesktopFileName("openremote-control-center")
    app.setStyle("Fusion")
    app.setWindowIcon(icon())
    app.setFont(QFont("Noto Sans", 10))
    testing_screenshot = args.screenshot is not None or args.wizard_screenshot is not None
    server, instance_lock = None, None
    if not testing_screenshot:
        import getpass
        server, instance_lock = _single_instance("openremote-gui-" + getpass.getuser())
        if server is None:
            return 0

    def show_dashboard():
        window = MainWindow(Backend(args.config, args.health_url), instance_server=server)
        app.openremote_window = window
        if args.language and args.language != window.language:
            window._language_change(args.language)
        if args.dark:
            window._dark_change(True)
        window.show_page(args.page)
        window.show()
        if args.screenshot is not None:
            def capture():
                window.grab().save(str(args.screenshot))
                window.quit()
            QTimer.singleShot(1250, capture)

    run_wizard = args.setup or args.wizard_screenshot is not None or not args.config.is_file()
    if run_wizard:
        from .wizard import SetupWizard
        from .onboarding import FirstRun, SetupPaths
        # User-installed apps must not depend on the source-checkout layout.
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--agent-stdio", "--config", str(args.config)]
        else:
            command = [
                sys.executable, "-m", "openremote_agent.server",
                "--transport", "stdio", "--config", str(args.config),
            ]
        wizard = SetupWizard(
            FirstRun(SetupPaths(Path.home()), agent_command=command),
            language=args.language or "ru", finished=show_dashboard)
        app.openremote_wizard = wizard
        if args.wizard_step:
            wizard.index = args.wizard_step
            wizard._show_step()
        wizard.show()
        if args.wizard_screenshot is not None:
            def capture_wizard():
                wizard.grab().save(str(args.wizard_screenshot))
                wizard.close()
                app.quit()
            QTimer.singleShot(1350, capture_wizard)
    else:
        show_dashboard()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
