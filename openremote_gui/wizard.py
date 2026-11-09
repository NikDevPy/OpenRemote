"""Guided first-run wizard for private OpenRemote Secure MCP Tunnel.

All OpenAI authentication happens in the user's own browser. This GUI never
requests or handles an OpenAI account password. An owner-provided restricted
runtime key is stored locally with 0600 permissions only on explicit Apply.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil
import socket
import sys

from PySide6.QtCore import Qt, QRectF, QTimer, QObject, Signal, QRunnable, QThreadPool, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPen, QBrush, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QStackedWidget, QTextEdit, QVBoxLayout, QWidget,
)

from .onboarding import (
    FirstRun, SetupError, SetupPaths, OFFICIAL_ACCOUNT, OFFICIAL_TUNNELS,
    OFFICIAL_KEYS, OFFICIAL_CLIENT, OFFICIAL_CHATGPT, OFFICIAL_GUIDE,
)


COPY = {
    "ru": {
        "welcome": "Ваш компьютер. Ваш ChatGPT.",
        "welcome_sub": "Один раз подключите OpenRemote — и управляйте своими устройствами из ChatGPT.",
        "account": "Создайте аккаунт OpenAI",
        "account_sub": "Вход и регистрация выполняются только на официальном сайте. Пароль никогда не вводится в OpenRemote.",
        "tunnel": "Создайте защищённый туннель",
        "tunnel_sub": "Туннель позволяет ChatGPT обращаться к вашему устройству без открытых портов.",
        "credentials": "Подключите туннель",
        "credentials_sub": "Вставьте Tunnel ID и ограниченный runtime API key. Данные сохраняются только на этом компьютере.",
        "activate": "Настройка устройства",
        "activate_sub": "Проверим агента, создадим защищённую конфигурацию и включим фоновую службу.",
        "plugin": "Подключите OpenRemote в ChatGPT",
        "plugin_sub": "Последний шаг — создать приватный плагин с вашим туннелем.",
        "back": "Назад",
        "next": "Продолжить",
        "finish": "Открыть панель",
        "step": "ШАГ {current} ИЗ {total}",
        "diagram_device": "ВАШ ПК",
        "diagram_cloud": "ЗАЩИЩЁННЫЙ\nТУННЕЛЬ",
        "diagram_chat": "CHATGPT",
        "welcome_info": "Ваши ключи остаются локально. OpenRemote не просит ваш пароль от OpenAI и не открывает входящие порты.",
        "open_account": "Открыть регистрацию OpenAI",
        "account_note": "Уже есть аккаунт? Перейдите дальше. В настройках Platform вам потребуются права Tunnels Read + Manage.",
        "open_tunnel": "Открыть управление туннелями",
        "tunnel_help": "Создайте Tunnel, свяжите нужную организацию Platform и рабочее пространство ChatGPT. Скопируйте его идентификатор.",
        "open_guide": "Пошаговая инструкция OpenAI",
        "tunnel_id": "Tunnel ID",
        "tunnel_id_hint": "tunnel_ + 32 шестнадцатеричных символа",
        "runtime_key": "Runtime API key",
        "runtime_key_hint": "Секретный ключ, не ваш пароль от аккаунта",
        "show_key": "Показать ключ",
        "open_keys": "Создать runtime API key",
        "binary": "Файл tunnel-client",
        "binary_placeholder": "Укажите официальный исполняемый файл tunnel-client",
        "browse": "Выбрать файл…",
        "download_client": "Скачать tunnel-client",
        "credentials_note": "Не вставляйте ключ в чат, GitHub или общие настройки. Ключ запишется в защищённый файл только при настройке.",
        "device_name": "Имя этого устройства",
        "device_name_hint": "Например, workstation",
        "install_now": "Настроить и запустить туннель",
        "installing": "Настраиваем туннель. Не закрывайте окно.",
        "install_done": "Конфигурация создана, служба запущена.",
        "install_fail": "Настройка не завершена: {error}",
        "install_note": "Начальная роль — viewer, без произвольных команд. Расширенный доступ можно включить позже.",
        "chatgpt_steps": "В ChatGPT откройте Plugins → «+» → Add custom MCP server → Tunnel.\nВыберите созданный туннель или вставьте его ID. Проверьте настройки и установите приватный плагин.",
        "open_chatgpt": "Открыть ChatGPT",
        "copy_id": "Скопировать Tunnel ID",
        "plugin_note": "ChatGPT может попросить разрешение на создание приватного MCP-плагина. OpenRemote не может устанавливать его без вашего подтверждения.",
        "configured": "Туннель настроен",
        "not_configured": "Сначала завершите настройку",
        "invalid": "Проверьте Tunnel ID, ключ, устройство и файл tunnel-client.",
        "client_unavailable": "Официальный tunnel-client отсутствует. Загрузите его перед настройкой.",
        "header": "НАСТРОЙКА OPENREMOTE",
        "offline": "Никаких открытых портов • Локальные секреты",
    },
    "en": {
        "welcome": "Your computer. Your ChatGPT.",
        "welcome_sub": "Connect OpenRemote once, then control your trusted devices from ChatGPT.",
        "account": "Create an OpenAI account",
        "account_sub": "Sign-in takes place on OpenAI's official site. OpenRemote never asks for your account password.",
        "tunnel": "Create a secure tunnel",
        "tunnel_sub": "The tunnel connects ChatGPT to your device without exposing inbound ports.",
        "credentials": "Connect your tunnel",
        "credentials_sub": "Enter your Tunnel ID and restricted runtime API key. Credentials stay on this device.",
        "activate": "Set up this computer",
        "activate_sub": "Verify the agent, create local private configuration, and enable the background service.",
        "plugin": "Connect OpenRemote in ChatGPT",
        "plugin_sub": "Finally, create your private ChatGPT plugin using this tunnel.",
        "back": "Back",
        "next": "Continue",
        "finish": "Open dashboard",
        "step": "STEP {current} OF {total}",
        "diagram_device": "YOUR PC",
        "diagram_cloud": "SECURE\nTUNNEL",
        "diagram_chat": "CHATGPT",
        "welcome_info": "Your credentials stay local. OpenRemote never asks for your OpenAI password or opens inbound ports.",
        "open_account": "Open OpenAI registration",
        "account_note": "Already have an account? Continue. You need Tunnels Read + Manage to create a tunnel.",
        "open_tunnel": "Open Platform tunnel settings",
        "tunnel_help": "Create a Tunnel and associate it with the Platform organization and the right ChatGPT workspace. Copy its ID.",
        "open_guide": "Official OpenAI setup guide",
        "tunnel_id": "Tunnel ID",
        "tunnel_id_hint": "tunnel_ followed by 32 hexadecimal characters",
        "runtime_key": "Runtime API key",
        "runtime_key_hint": "A restricted key, not your OpenAI account password",
        "show_key": "Show key",
        "open_keys": "Create a runtime API key",
        "binary": "tunnel-client executable",
        "binary_placeholder": "Select the official tunnel-client executable",
        "browse": "Browse…",
        "download_client": "Download tunnel-client",
        "credentials_note": "Never paste the key into ChatGPT, GitHub or shared logs. It is stored privately only when setup starts.",
        "device_name": "Name of this device",
        "device_name_hint": "For example, workstation",
        "install_now": "Configure and start tunnel",
        "installing": "Configuring the private tunnel. Keep this window open.",
        "install_done": "Configuration created and service started.",
        "install_fail": "Setup did not complete: {error}",
        "install_note": "Starts as viewer without arbitrary command execution. Grant additional access later.",
        "chatgpt_steps": "In ChatGPT, open Plugins → '+' → Add custom MCP server → Tunnel.\nSelect your tunnel or paste its ID, review security, and create a private plugin.",
        "open_chatgpt": "Open ChatGPT",
        "copy_id": "Copy Tunnel ID",
        "plugin_note": "ChatGPT may ask you to authorize this private MCP plugin. OpenRemote cannot install it without your action.",
        "configured": "Tunnel configured",
        "not_configured": "Complete setup first",
        "invalid": "Check the Tunnel ID, key, device name and tunnel-client file.",
        "client_unavailable": "Official tunnel-client not found. Download it before proceeding.",
        "header": "OPENREMOTE SETUP",
        "offline": "No public ports • Local-only secrets",
    },
}

assert set(COPY["ru"]) == set(COPY["en"])


class Diagram(QWidget):
    """Scale-aware diagram with all labels constrained within their cards."""

    def __init__(self, parent=None, active=1):
        super().__init__(parent)
        self.active = active
        self.setMinimumHeight(188)
        self.setMaximumHeight(220)
        self.language = "ru"
        self.setAccessibleName("OpenRemote: computer, secure tunnel, ChatGPT")

    def geometry_for(self, width: float):
        """Return three non-overlapping node rectangles and connector segments."""
        w = max(float(width), 550.0)
        mid_width = min(192.0, max(170.0, w * .235))
        end_width = min(150.0, max(116.0, w * .19))
        centers = (w * .16, w * .5, w * .84)
        box_height = 108.0
        y = (self.height() - box_height) / 2.0
        boxes = [
            QRectF(centers[0] - end_width/2, y, end_width, box_height),
            QRectF(centers[1] - mid_width/2, y - 5, mid_width, box_height + 10),
            QRectF(centers[2] - end_width/2, y, end_width, box_height),
        ]
        return boxes

    def font_for_label(self, label: str, available_width: float, painter: QPainter):
        font = QFont(painter.font())
        font.setWeight(QFont.Weight.DemiBold)
        # Text is measured, never blindly painted outside its rounded card.
        for size in range(15, 9, -1):
            font.setPixelSize(size)
            from PySide6.QtGui import QFontMetrics
            metrics = QFontMetrics(font)
            if all(metrics.horizontalAdvance(line) <= available_width
                   for line in label.splitlines()):
                return font
        font.setPixelSize(10)
        return font

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        boxes = self.geometry_for(self.width())
        cy = float(self.height()) / 2
        painter.setPen(QPen(QColor("#C9C4ED"), 2, Qt.PenStyle.DashLine))
        painter.drawLine(int(boxes[0].right() + 10), int(cy),
                         int(boxes[1].left() - 10), int(cy))
        painter.drawLine(int(boxes[1].right() + 10), int(cy),
                         int(boxes[2].left() - 10), int(cy))
        captions = [COPY[self.language][key] for key in
                    ("diagram_device", "diagram_cloud", "diagram_chat")]
        for i, rect in enumerate(boxes):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#756AE7") if i == self.active
                                   else QColor("#EEEAFB")))
            painter.drawRoundedRect(rect, 24, 24)
            color = QColor("#FFFFFF" if i == self.active else "#4F478F")
            painter.setPen(QPen(color))
            text_rect = rect.adjusted(12, 8, -12, -8)
            painter.setFont(self.font_for_label(captions[i], text_rect.width(), painter))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter,
                             captions[i])
        painter.end()


class ResultSignals(QObject):
    success = Signal(object)
    error = Signal(str)


class SetupTask(QRunnable):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback
        self.signals = ResultSignals()

    def run(self):
        try:
            self.signals.success.emit(self.callback())
        except SetupError as exc:
            self.signals.error.emit(str(exc))
        except Exception:
            self.signals.error.emit("Unexpected local setup error")


class SetupWizard(QDialog):
    """All steps are optional until the explicit 'Configure' action."""
    STEPS = ("welcome", "account", "tunnel", "credentials", "activate", "plugin")

    def __init__(self, manager: FirstRun, *, language="ru", finished=None):
        super().__init__()
        self.manager = manager
        self.language = language if language in COPY else "ru"
        self.index = 0
        self.configured = False
        self.pending = False
        self.finished_callback = finished
        self._tasks = []
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setWindowTitle("OpenRemote — Setup")
        self.setMinimumSize(820, 660)
        self.resize(980, 720)
        self._build()
        self._show_step()

    def t(self, key, **args):
        return COPY[self.language][key].format(**args)

    def _button(self, label, callback, *, primary=False):
        button = QPushButton(label)
        button.setMinimumHeight(41)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setObjectName("primary")
        button.clicked.connect(callback)
        return button

    def _open(self, url):
        QDesktopServices.openUrl(QUrl(url))

    def _build(self):
        self.setStyleSheet("""
            QDialog, QWidget#panel, QStackedWidget { background: #F7F8FC; color: #212438; }
            QLabel { color: #262C40; font-size: 13px; background: transparent; }
            QLabel#title { font-size: 29px; font-weight: 800; }
            QLabel#subtitle { color: #70788D; font-size: 14px; }
            QLabel#tag { color: #7C71E9; font-size: 12px; font-weight: 800; }
            QLabel#note { color: #727B90; font-size: 12px; }
            QFrame#whiteCard { background: #FFFFFF; border: 1px solid #E5E8F0; border-radius: 20px; }
            QLabel#illustration { background: #F0EDFF; border-radius: 14px; color: #685CE4; }
            QPushButton { background: white; color: #2B2F42; font-size: 13px;
                border: 1px solid #E2E5EE; border-radius: 11px; padding: 10px 18px; font-weight: 650; }
            QPushButton:hover { background: #F0EEFF; }
            QPushButton:disabled { color: #A1A7B2; background: #ECEEF3; }
            QPushButton#primary { background: #7367E9; color: white; border: none; }
            QPushButton#primary:hover { background: #5D51D4; }
            QLineEdit { border: 1px solid #DBDFEA; background: #FFFFFF; padding: 11px 13px;
                border-radius: 10px; color: #283048; font-size: 13px; }
            QLineEdit:focus { border: 2px solid #8378ED; }
            QProgressBar { border: none; background: #E9EAF2; height: 7px;
                border-radius: 3px; text-align: center; }
            QProgressBar::chunk { background: #8075EC; border-radius: 3px; }
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(44, 30, 44, 30)
        root.setSpacing(13)
        header = QHBoxLayout()
        brand = QLabel("◆  OpenRemote")
        brand.setStyleSheet("font-size: 18px; font-weight: 800;")
        header.addWidget(brand)
        header.addStretch()
        self.progress_name = QLabel()
        self.progress_name.setObjectName("tag")
        header.addWidget(self.progress_name)
        root.addLayout(header)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setMaximum(6)
        root.addWidget(self.progress)
        root.addSpacing(18)
        self.title = QLabel()
        self.title.setObjectName("title")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("subtitle")
        self.subtitle.setWordWrap(True)
        root.addWidget(self.title)
        root.addWidget(self.subtitle)
        root.addSpacing(12)
        self.stack = QStackedWidget()
        self.stack.setObjectName("panel")
        self._welcome()
        self._account()
        self._tunnel()
        self._credentials()
        self._activate()
        self._plugin()
        root.addWidget(self.stack, 1)
        root.addSpacing(6)
        navigation = QHBoxLayout()
        self.back = self._button(self.t("back"), self.previous)
        self.next = self._button(self.t("next"), self.advance, primary=True)
        navigation.addWidget(self.back)
        navigation.addStretch()
        navigation.addWidget(self.next)
        root.addLayout(navigation)

    def _pane(self):
        page = QWidget()
        page.setObjectName("panel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(14)
        self.stack.addWidget(page)
        return layout

    def _notice(self, layout, content):
        note = QLabel(content)
        note.setObjectName("note")
        note.setWordWrap(True)
        layout.addWidget(note)

    def _welcome(self):
        layout = self._pane()
        self.diagram = Diagram()
        self.diagram.language = self.language
        layout.addWidget(self.diagram)
        self._notice(layout, self.t("welcome_info"))
        layout.addStretch()

    def _account(self):
        layout = self._pane()
        illustration = Diagram(active=0)
        illustration.language = self.language
        layout.addWidget(illustration)
        layout.addWidget(self._button(self.t("open_account"),
                                      lambda: self._open(OFFICIAL_ACCOUNT)))
        self._notice(layout, self.t("account_note"))
        layout.addStretch()

    def _tunnel(self):
        layout = self._pane()
        illustration = Diagram(active=1)
        illustration.language = self.language
        layout.addWidget(illustration)
        layout.addWidget(self._button(self.t("open_tunnel"),
                                      lambda: self._open(OFFICIAL_TUNNELS)))
        self._notice(layout, self.t("tunnel_help"))
        layout.addWidget(self._button(self.t("open_guide"),
                                      lambda: self._open(OFFICIAL_GUIDE)))
        layout.addStretch()

    def _credentials(self):
        layout = self._pane()
        label = QLabel(self.t("tunnel_id"))
        layout.addWidget(label)
        self.tunnel = QLineEdit()
        self.tunnel.setPlaceholderText(self.t("tunnel_id_hint"))
        self.tunnel.setMaxLength(39)
        layout.addWidget(self.tunnel)
        layout.addWidget(QLabel(self.t("runtime_key")))
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key.setPlaceholderText(self.t("runtime_key_hint"))
        layout.addWidget(self.api_key)
        self.show_secret = QCheckBox(self.t("show_key"))
        self.show_secret.toggled.connect(
            lambda enabled: self.api_key.setEchoMode(
                QLineEdit.EchoMode.Normal if enabled else QLineEdit.EchoMode.Password))
        layout.addWidget(self.show_secret)
        layout.addWidget(self._button(self.t("open_keys"),
                                      lambda: self._open(OFFICIAL_KEYS)))
        layout.addWidget(QLabel(self.t("binary")))
        choice = QHBoxLayout()
        self.binary = QLineEdit()
        self.binary.setPlaceholderText(self.t("binary_placeholder"))
        auto = shutil.which("tunnel-client")
        if auto:
            self.binary.setText(auto)
        choice.addWidget(self.binary, 1)
        choice.addWidget(self._button(self.t("browse"), self.browse))
        layout.addLayout(choice)
        layout.addWidget(self._button(self.t("download_client"),
                                      lambda: self._open(OFFICIAL_CLIENT)))
        self._notice(layout, self.t("credentials_note"))
        layout.addStretch()

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, self.t("binary"))
        if path:
            self.binary.setText(path)

    def _activate(self):
        layout = self._pane()
        illustration = Diagram(active=0)
        illustration.language = self.language
        layout.addWidget(illustration)
        layout.addWidget(QLabel(self.t("device_name")))
        self.device_name = QLineEdit()
        hostname = re.sub(r"[^a-zA-Z0-9_-]", "-", socket.gethostname())[:64].strip("-")
        self.device_name.setText(hostname or "my-computer")
        self.device_name.setPlaceholderText(self.t("device_name_hint"))
        layout.addWidget(self.device_name)
        self._notice(layout, self.t("install_note"))
        self.apply = self._button(self.t("install_now"), self.configure, primary=True)
        layout.addWidget(self.apply)
        self.setup_message = QLabel()
        self.setup_message.setWordWrap(True)
        layout.addWidget(self.setup_message)
        layout.addStretch()

    def _plugin(self):
        layout = self._pane()
        illustration = Diagram(active=2)
        illustration.language = self.language
        layout.addWidget(illustration)
        info = QLabel(self.t("chatgpt_steps"))
        info.setWordWrap(True)
        layout.addWidget(info)
        layout.addWidget(self._button(self.t("open_chatgpt"),
                                      lambda: self._open(OFFICIAL_CHATGPT)))
        layout.addWidget(self._button(self.t("copy_id"),
                                      lambda: QApplication.clipboard().setText(self.tunnel.text())))
        self._notice(layout, self.t("plugin_note"))
        layout.addStretch()

    def _show_step(self):
        self.stack.setCurrentIndex(self.index)
        title = self.STEPS[self.index]
        self.title.setText(self.t(title))
        self.subtitle.setText(self.t(title + "_sub"))
        self.progress_name.setText(self.t("step", current=self.index + 1, total=6))
        self.progress.setValue(self.index + 1)
        self.back.setEnabled(self.index > 0 and not self.pending)
        self.next.setText(self.t("finish") if self.index == 5 else self.t("next"))
        self.next.setEnabled(not self.pending and (self.index != 4 or self.configured))

    def previous(self):
        if self.index > 0 and not self.pending:
            self.index -= 1
            self._show_step()

    def advance(self):
        if self.pending:
            return
        if self.index == 3:
            if (not re.fullmatch(r"tunnel_[0-9a-fA-F]{32}", self.tunnel.text()) or
                    len(self.api_key.text()) < 15 or
                    not Path(self.binary.text()).expanduser().is_file()):
                self.setup_message.setText(self.t("invalid"))
                QMessageBox.warning(self, "OpenRemote", self.t("invalid"))
                return
        if self.index == 4 and not self.configured:
            return
        if self.index == 5:
            if self.finished_callback:
                self.finished_callback()
            self.close()
            return
        self.index += 1
        self._show_step()

    def configure(self):
        if self.pending:
            return
        key = self.api_key.text()
        tunnel_id = self.tunnel.text()
        device_id = self.device_name.text()
        binary = self.binary.text()
        self.api_key.clear()  # Never persist secrets in the GUI or Qt settings.
        self.pending = True
        self.apply.setEnabled(False)
        self.setup_message.setText(self.t("installing"))
        self._show_step()
        task = SetupTask(lambda: self.manager.configure_tunnel(
            device_id=device_id, tunnel_id=tunnel_id,
            runtime_key=key, binary=binary, start_service=True,
        ))
        task.signals.success.connect(lambda result: self._configured(result, task))
        task.signals.error.connect(lambda error: self._error(error, task))
        self._tasks.append(task)
        self.pool.start(task)

    def _configured(self, result, task):
        self.pending = False
        self.configured = bool(result["configured"])
        self.setup_message.setText(self.t("install_done"))
        self.apply.setEnabled(False)
        if task in self._tasks:
            self._tasks.remove(task)
        self._show_step()

    def _error(self, reason, task):
        self.pending = False
        self.apply.setEnabled(True)
        self.setup_message.setText(self.t("install_fail", error=reason))
        if task in self._tasks:
            self._tasks.remove(task)
        self._show_step()
