"""Settings dialog for danmuFishpi.

PyQt6-based settings panel with tabs for account, display, and hotkey.
Styled with GitHub dark theme to match the original design.
"""

import logging
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QSlider, QCheckBox, QRadioButton,
    QButtonGroup, QGroupBox, QComboBox, QMessageBox, QFrame,
)

from config import Config, Display, dpapi_encrypt, dpapi_decrypt

logger = logging.getLogger("danmuFishpi.settings")

# GitHub dark theme stylesheet
DARK_STYLE = """
QDialog { background: #0d1117; color: #e6edf3; }
QTabWidget::pane { border: 1px solid #30363d; background: #0d1117; }
QTabBar::tab {
    background: #161b22; color: #8b949e; padding: 8px 16px;
    border: 1px solid #30363d; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #0d1117; color: #e6edf3; border-bottom: 2px solid #58a6ff; }
QLabel { color: #e6edf3; }
QLineEdit {
    background: #161b22; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 6px; padding: 6px 10px;
}
QLineEdit:focus { border: 1px solid #58a6ff; }
QPushButton {
    background: #238636; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 16px; font-weight: bold;
}
QPushButton:hover { background: #2ea043; }
QPushButton:pressed { background: #1a7f37; }
QPushButton:disabled { background: #21262d; color: #6e7681; }
QPushButton[danger="true"] { background: #da3633; }
QPushButton[danger="true"]:hover { background: #f85149; }
QSlider::groove:horizontal {
    height: 6px; background: #21262d; border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #58a6ff; width: 16px; height: 16px;
    margin: -5px 0; border-radius: 8px;
}
QSlider::sub-page:horizontal { background: #58a6ff; border-radius: 3px; }
QCheckBox { color: #e6edf3; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 1px solid #30363d; background: #161b22;
}
QCheckBox::indicator:checked { background: #238636; border: 1px solid #238636; }
QRadioButton { color: #e6edf3; spacing: 8px; }
QRadioButton::indicator {
    width: 16px; height: 16px; border-radius: 8px;
    border: 1px solid #30363d; background: #161b22;
}
QRadioButton::indicator:checked { background: #58a6ff; border: 2px solid #0d1117; }
QGroupBox {
    border: 1px solid #30363d; border-radius: 8px;
    margin-top: 12px; padding-top: 12px; color: #8b949e;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QComboBox {
    background: #161b22; color: #e6edf3; border: 1px solid #30363d;
    border-radius: 6px; padding: 6px 10px;
}
"""

LIGHT_STYLE = """
QDialog { background: #ffffff; color: #1f2328; }
QTabWidget::pane { border: 1px solid #d0d7de; background: #ffffff; }
QTabBar::tab {
    background: #f6f8fa; color: #57606a; padding: 8px 16px;
    border: 1px solid #d0d7de; border-bottom: none;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #ffffff; color: #1f2328; border-bottom: 2px solid #0969da; }
QLabel { color: #1f2328; }
QLineEdit {
    background: #f6f8fa; color: #1f2328; border: 1px solid #d0d7de;
    border-radius: 6px; padding: 6px 10px;
}
QLineEdit:focus { border: 1px solid #0969da; }
QPushButton {
    background: #1f883d; color: #ffffff; border: none;
    border-radius: 6px; padding: 8px 16px; font-weight: bold;
}
QPushButton:hover { background: #1a7f37; }
QPushButton:disabled { background: #e9ecef; color: #6e7681; }
QSlider::groove:horizontal { height: 6px; background: #e9ecef; border-radius: 3px; }
QSlider::handle:horizontal {
    background: #0969da; width: 16px; height: 16px;
    margin: -5px 0; border-radius: 8px;
}
QSlider::sub-page:horizontal { background: #0969da; border-radius: 3px; }
QCheckBox { color: #1f2328; spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 1px solid #d0d7de; background: #f6f8fa;
}
QCheckBox::indicator:checked { background: #1f883d; border: 1px solid #1f883d; }
QRadioButton { color: #1f2328; spacing: 8px; }
QGroupBox { border: 1px solid #d0d7de; border-radius: 8px; margin-top: 12px; padding-top: 12px; }
QComboBox {
    background: #f6f8fa; color: #1f2328; border: 1px solid #d0d7de;
    border-radius: 6px; padding: 6px 10px;
}
"""


class SettingsDialog(QDialog):
    """Settings dialog with account, display, and hotkey tabs."""

    # Signals
    login_requested = pyqtSignal(str, str)       # username, password
    logout_requested = pyqtSignal()
    config_saved = pyqtSignal(dict)               # display config dict
    send_message_requested = pyqtSignal(str)      # message content

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._logged_in = bool(config.account.username)
        self._apply_theme()
        self.setWindowTitle("弹幕鱼排 - 设置")
        self.setFixedWidth(420)
        self._build_ui()
        self._update_login_state()

    def _apply_theme(self):
        if self.config.theme == "light":
            self.setStyleSheet(LIGHT_STYLE)
        else:
            self.setStyleSheet(DARK_STYLE)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self._build_account_tab()
        self._build_display_tab()
        self._build_hotkey_tab()

        # Send message bar (always visible)
        msg_frame = QFrame()
        msg_layout = QHBoxLayout(msg_frame)
        msg_layout.setContentsMargins(0, 0, 0, 0)
        self.msg_input = QLineEdit()
        self.msg_input.setPlaceholderText("发送消息到聊天室...")
        self.msg_input.returnPressed.connect(self._on_send_message)
        self.btn_send = QPushButton("发送")
        self.btn_send.clicked.connect(self._on_send_message)
        msg_layout.addWidget(self.msg_input)
        msg_layout.addWidget(self.btn_send)
        layout.addWidget(msg_frame)

    def _build_account_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(12)

        self.input_username = QLineEdit(self.config.account.username)
        self.input_username.setPlaceholderText("用户名或邮箱")
        self.input_password = QLineEdit()
        self.input_password.setPlaceholderText("密码")
        self.input_password.setEchoMode(QLineEdit.EchoMode.Password)

        layout.addRow("用户名:", self.input_username)
        layout.addRow("密码:", self.input_password)

        btn_layout = QHBoxLayout()
        self.btn_login = QPushButton("登录")
        self.btn_login.clicked.connect(self._on_login)
        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setProperty("danger", True)
        self.btn_logout.clicked.connect(self._on_logout)
        btn_layout.addWidget(self.btn_login)
        btn_layout.addWidget(self.btn_logout)
        layout.addRow(btn_layout)

        self.lbl_login_status = QLabel("未登录")
        layout.addRow(self.lbl_login_status)

        self.tabs.addTab(tab, "账号")

    def _build_display_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(12)

        # Mode selection
        mode_group = QGroupBox("弹幕模式")
        mode_layout = QHBoxLayout(mode_group)
        self.mode_group = QButtonGroup(self)
        self.mode_radios = {}
        for mode, label in [("scrolling", "横向滚动"),
                            ("floating", "侧边悬浮"),
                            ("bottom", "底部聊天")]:
            radio = QRadioButton(label)
            if self.config.display.danmu_mode == mode:
                radio.setChecked(True)
            self.mode_group.addButton(radio)
            mode_layout.addWidget(radio)
            self.mode_radios[mode] = radio
        layout.addWidget(mode_group)

        # Sliders
        sliders_group = QGroupBox("显示参数")
        sliders_layout = QFormLayout(sliders_group)

        self.slider_speed = self._make_slider(1, 10, self.config.display.danmu_speed)
        self.lbl_speed = QLabel(str(self.config.display.danmu_speed))
        self.slider_speed.valueChanged.connect(
            lambda v: self.lbl_speed.setText(str(v)))
        sliders_layout.addRow("速度:", self._slider_row(self.slider_speed, self.lbl_speed))

        self.slider_width = self._make_slider(30, 100, self.config.display.danmu_width)
        self.lbl_width = QLabel(f"{self.config.display.danmu_width}%")
        self.slider_width.valueChanged.connect(
            lambda v: self.lbl_width.setText(f"{v}%"))
        sliders_layout.addRow("宽度:", self._slider_row(self.slider_width, self.lbl_width))

        self.slider_height = self._make_slider(30, 100, self.config.display.danmu_height)
        self.lbl_height = QLabel(f"{self.config.display.danmu_height}%")
        self.slider_height.valueChanged.connect(
            lambda v: self.lbl_height.setText(f"{v}%"))
        sliders_layout.addRow("高度:", self._slider_row(self.slider_height, self.lbl_height))

        self.slider_opacity = self._make_slider(30, 100, self.config.display.danmu_opacity)
        self.lbl_opacity = QLabel(f"{self.config.display.danmu_opacity}%")
        self.slider_opacity.valueChanged.connect(
            lambda v: self.lbl_opacity.setText(f"{v}%"))
        sliders_layout.addRow("不透明度:", self._slider_row(self.slider_opacity, self.lbl_opacity))

        self.slider_font = self._make_slider(12, 48, self.config.display.font_size)
        self.lbl_font = QLabel(f"{self.config.display.font_size}px")
        self.slider_font.valueChanged.connect(
            lambda v: self.lbl_font.setText(f"{v}px"))
        sliders_layout.addRow("字号:", self._slider_row(self.slider_font, self.lbl_font))

        layout.addWidget(sliders_group)

        # Checkboxes
        options_group = QGroupBox("显示选项")
        options_layout = QVBoxLayout(options_group)
        self.chk_avatar = QCheckBox("显示头像")
        self.chk_avatar.setChecked(self.config.display.show_avatar)
        self.chk_nickname = QCheckBox("显示昵称")
        self.chk_nickname.setChecked(self.config.display.show_nickname)
        self.chk_image = QCheckBox("显示图片")
        self.chk_image.setChecked(self.config.display.show_image)
        options_layout.addWidget(self.chk_avatar)
        options_layout.addWidget(self.chk_nickname)
        options_layout.addWidget(self.chk_image)
        layout.addWidget(options_group)

        # Area selection
        area_group = QGroupBox("显示区域")
        area_layout = QHBoxLayout(area_group)
        self.area_group = QButtonGroup(self)
        self.area_radios = {}
        for area, label in [("fullscreen", "全屏"),
                            ("topHalf", "上半屏"),
                            ("bottomHalf", "下半屏")]:
            radio = QRadioButton(label)
            if self.config.display.danmu_area == area:
                radio.setChecked(True)
            self.area_group.addButton(radio)
            area_layout.addWidget(radio)
            self.area_radios[area] = radio
        layout.addWidget(area_group)

        # Save button
        self.btn_save_display = QPushButton("保存显示设置")
        self.btn_save_display.clicked.connect(self._on_save_display)
        layout.addWidget(self.btn_save_display)

        layout.addStretch()
        self.tabs.addTab(tab, "显示")

    def _build_hotkey_tab(self):
        tab = QWidget()
        layout = QFormLayout(tab)
        layout.setSpacing(12)

        self.input_hotkey = QLineEdit(self.config.hotkey)
        self.input_hotkey.setPlaceholderText("例如: ctrl+enter")
        layout.addRow("全局热键:", self.input_hotkey)

        lbl_hint = QLabel("热键用于打开/关闭消息输入框\n支持的修饰键: ctrl, shift, alt, win")
        lbl_hint.setWordWrap(True)
        layout.addRow(lbl_hint)

        self.btn_save_hotkey = QPushButton("保存热键")
        self.btn_save_hotkey.clicked.connect(self._on_save_hotkey)
        layout.addRow(self.btn_save_hotkey)

        self.tabs.addTab(tab, "热键")

    def _make_slider(self, min_val, max_val, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(min_val)
        s.setMaximum(max_val)
        s.setValue(val)
        return s

    def _slider_row(self, slider, label):
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slider, stretch=1)
        layout.addWidget(label)
        return w

    def _on_login(self):
        username = self.input_username.text().strip()
        password = self.input_password.text()
        if not username or not password:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        self.btn_login.setEnabled(False)
        self.btn_login.setText("登录中...")
        self.login_requested.emit(username, password)

    def _on_logout(self):
        self.logout_requested.emit()

    def _on_send_message(self):
        text = self.msg_input.text().strip()
        if text:
            self.send_message_requested.emit(text)
            self.msg_input.clear()

    def _on_save_display(self):
        # Get selected mode
        mode = "scrolling"
        for m, radio in self.mode_radios.items():
            if radio.isChecked():
                mode = m
                break

        area = "fullscreen"
        for a, radio in self.area_radios.items():
            if radio.isChecked():
                area = a
                break

        display_config = {
            "danmuMode": mode,
            "showAvatar": self.chk_avatar.isChecked(),
            "showNickname": self.chk_nickname.isChecked(),
            "showImage": self.chk_image.isChecked(),
            "danmuSpeed": self.slider_speed.value(),
            "danmuArea": area,
            "danmuWidth": self.slider_width.value(),
            "danmuHeight": self.slider_height.value(),
            "danmuOpacity": self.slider_opacity.value(),
            "fontSize": self.slider_font.value(),
        }
        self.config_saved.emit(display_config)

    def _on_save_hotkey(self):
        hotkey = self.input_hotkey.text().strip()
        if hotkey:
            self.config.hotkey = hotkey
            self.config_saved.emit({
                "danmuMode": self.config.display.danmu_mode,
                "showAvatar": self.config.display.show_avatar,
                "showNickname": self.config.display.show_nickname,
                "showImage": self.config.display.show_image,
                "danmuSpeed": self.slider_speed.value(),
                "danmuArea": self.config.display.danmu_area,
                "danmuWidth": self.slider_width.value(),
                "danmuHeight": self.slider_height.value(),
                "danmuOpacity": self.slider_opacity.value(),
                "fontSize": self.slider_font.value(),
            })

    def _update_login_state(self):
        if self._logged_in:
            self.lbl_login_status.setText(f"已登录: {self.config.account.username}")
            self.lbl_login_status.setStyleSheet("color: #238636;")
            self.btn_login.setEnabled(False)
            self.btn_logout.setEnabled(True)
        else:
            self.lbl_login_status.setText("未登录")
            self.lbl_login_status.setStyleSheet("color: #da3633;")
            self.btn_login.setEnabled(True)
            self.btn_logout.setEnabled(False)

    def on_login_success(self, username: str):
        self._logged_in = True
        self.config.account.username = username
        self._update_login_state()
        self.btn_login.setText("登录")
        self.btn_login.setEnabled(False)

    def on_login_failed(self, error: str):
        self._logged_in = False
        self._update_login_state()
        self.btn_login.setText("登录")
        self.btn_login.setEnabled(True)
        QMessageBox.warning(self, "登录失败", error)

    def on_logout(self):
        self._logged_in = False
        self.config.account.username = ""
        self._update_login_state()
        self.input_password.clear()

    def keyPressEvent(self, event):
        """Capture key combinations for hotkey input."""
        if self.input_hotkey.hasFocus() and self.input_hotkey.text() == "":
            modifiers = []
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                modifiers.append("ctrl")
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                modifiers.append("shift")
            if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                modifiers.append("alt")
            if event.modifiers() & Qt.KeyboardModifier.MetaModifier:
                modifiers.append("win")

            key = event.key()
            if key not in (Qt.Key.Key_Control, Qt.Key.Key_Shift,
                          Qt.Key.Key_Alt, Qt.Key.Key_Meta,
                          Qt.Key.Key_unknown, 0):
                key_map = {
                    Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter",
                    Qt.Key.Key_Space: "space",
                }
                key_name = key_map.get(key, chr(key).lower() if key < 256 else "")
                if key_name:
                    self.input_hotkey.setText("+".join(modifiers + [key_name]))
                    return
        super().keyPressEvent(event)

    def update_config(self, config: Config):
        """Update the dialog with new config values."""
        self.config = config
        self._apply_theme()
        # Update display tab values
        for mode, radio in self.mode_radios.items():
            radio.setChecked(config.display.danmu_mode == mode)
        self.slider_speed.setValue(config.display.danmu_speed)
        self.slider_width.setValue(config.display.danmu_width)
        self.slider_height.setValue(config.display.danmu_height)
        self.slider_opacity.setValue(config.display.danmu_opacity)
        self.slider_font.setValue(config.display.font_size)
        self.chk_avatar.setChecked(config.display.show_avatar)
        self.chk_nickname.setChecked(config.display.show_nickname)
        self.chk_image.setChecked(config.display.show_image)
        for area, radio in self.area_radios.items():
            radio.setChecked(config.display.danmu_area == area)
        self.input_hotkey.setText(config.hotkey)
