"""Settings dialog for danmuFishpi.

PyQt6-based settings panel styled to match the original GitHub-style drawer.
"""

import logging
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QLineEdit,
    QPushButton, QSlider, QCheckBox, QButtonGroup, QComboBox, QMessageBox,
    QFrame, QSizePolicy,
)

from config import Config, dpapi_encrypt, dpapi_decrypt

logger = logging.getLogger("danmuFishpi.settings")


class SettingsDialog(QDialog):
    """GitHub-style settings dialog with account, display, and hotkey tabs."""

    login_requested = pyqtSignal(str, str)       # username, password
    logout_requested = pyqtSignal()
    config_saved = pyqtSignal(dict)              # display config dict
    send_message_requested = pyqtSignal(str)     # message content (optional)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._logged_in = bool(config.account.username and config.account.password_enc)
        self._connected = False
        self._current_tab = "account"

        self.setWindowTitle("弹幕鱼排 - 设置")
        self.setFixedWidth(380)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.WindowCloseButtonHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowStaysOnTopHint
        )

        self._apply_theme()
        self._build_ui()
        self._update_login_state()
        self._populate_form()

    # ── Theme / Styling ─────────────────────────────────────────

    def _apply_theme(self):
        is_light = self.config.theme == "light"
        c = {
            "bg_drawer": "#ffffff" if is_light else "#0d1117",
            "bg_input": "#f6f8fa" if is_light else "#161b22",
            "bg_input_hover": "#f3f4f6" if is_light else "#1f242c",
            "border": "#d0d7de" if is_light else "#30363d",
            "border_active": "#0969da" if is_light else "#58a6ff",
            "text_primary": "#1f2328" if is_light else "#e6edf3",
            "text_secondary": "#656d76" if is_light else "#8b949e",
            "text_muted": "#8c959f" if is_light else "#6e7681",
            "accent": "#0969da" if is_light else "#58a6ff",
            "accent_bg": "rgba(9,105,218,0.12)" if is_light else "rgba(88,166,255,0.15)",
            "success": "#1f883d" if is_light else "#238636",
            "success_border": "#2da44e" if is_light else "#2ea043",
            "danger": "#cf222e" if is_light else "#f85149",
        }

        self.setStyleSheet(f"""
            QDialog {{
                background: {c['bg_drawer']};
                color: {c['text_primary']};
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
            }}
            #header {{
                background: transparent;
                border-bottom: 1px solid {c['border']};
            }}
            #titleLabel {{
                color: {c['text_primary']};
                font-size: 14px;
                font-weight: 600;
            }}
            #closeBtn {{
                background: transparent;
                border: none;
                color: {c['text_muted']};
                font-size: 16px;
                padding: 4px 8px;
            }}
            #closeBtn:hover {{
                color: {c['text_primary']};
            }}
            #tabBar {{
                background: transparent;
                border-bottom: 1px solid {c['border']};
            }}
            #tabBar QPushButton {{
                background: transparent;
                border: none;
                border-bottom: 2px solid transparent;
                color: {c['text_muted']};
                padding: 10px;
                font-size: 12px;
                letter-spacing: 1px;
            }}
            #tabBar QPushButton:hover {{
                color: {c['text_secondary']};
            }}
            #tabBar QPushButton:checked, #tabBar QPushButton[active="true"] {{
                color: {c['accent']};
                border-bottom-color: {c['accent']};
                font-weight: 600;
            }}
            #contentArea {{
                background: transparent;
            }}
            QLabel[class="sectionLabel"] {{
                color: {c['text_secondary']};
                font-size: 11px;
                text-transform: uppercase;
                letter-spacing: 1.5px;
                padding-top: 4px;
                padding-bottom: 10px;
            }}
            QLabel[class="fieldLabel"] {{
                color: {c['text_muted']};
                font-size: 11px;
                padding-bottom: 4px;
            }}
            QLineEdit, QComboBox {{
                background: {c['bg_input']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                padding: 8px 12px;
                color: {c['text_primary']};
                font-size: 13px;
                selection-background-color: {c['accent']};
            }}
            QLineEdit:focus, QComboBox:focus {{
                border: 1px solid {c['accent']};
            }}
            QLineEdit::placeholder {{
                color: {c['text_muted']};
            }}
            QPushButton#primaryBtn {{
                background: {c['success']};
                border: 1px solid {c['success_border']};
                border-radius: 6px;
                color: #ffffff;
                padding: 8px;
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#primaryBtn:hover {{
                opacity: 0.9;
            }}
            QPushButton#primaryBtn:disabled {{
                background: {c['bg_input']};
                color: {c['text_muted']};
                border-color: {c['border']};
            }}
            QPushButton#secondaryBtn {{
                background: {c['bg_input']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                color: {c['text_primary']};
                padding: 8px;
                font-size: 13px;
            }}
            QPushButton#secondaryBtn:hover {{
                background: {c['bg_input_hover']};
            }}
            QPushButton#groupItem {{
                background: {c['bg_input']};
                border: 1px solid {c['border']};
                border-radius: 4px;
                color: {c['text_muted']};
                padding: 6px 8px;
                font-size: 11px;
            }}
            QPushButton#groupItem:hover {{
                color: {c['text_secondary']};
            }}
            QPushButton#groupItem[active="true"] {{
                background: {c['accent_bg']};
                border-color: {c['border_active']};
                color: {c['accent']};
            }}
            QCheckBox {{
                color: {c['text_primary']};
                spacing: 8px;
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border-radius: 3px;
                border: 1px solid {c['border']};
                background: {c['bg_input']};
            }}
            QCheckBox::indicator:checked {{
                background: {c['success']};
                border-color: {c['success_border']};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {c['bg_input']};
                border-radius: 2px;
            }}
            QSlider::sub-page:horizontal {{
                background: {c['accent']};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {c['accent']};
                width: 12px;
                height: 12px;
                border-radius: 6px;
                margin: -4px 0;
                border: 2px solid {c['bg_drawer']};
            }}
            #footer {{
                background: transparent;
                border-top: 1px solid {c['border']};
            }}
            #connDot {{
                color: {c['danger']};
            }}
            #connDot[connected="true"] {{
                color: #2ea043;
            }}
            #connText {{
                color: {c['text_muted']};
                font-size: 11px;
            }}
            #connText[connected="true"] {{
                color: #2ea043;
            }}
            #versionText {{
                color: {c['text_muted']};
                font-size: 11px;
            }}
        """)

    # ── UI Construction ─────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setObjectName("header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 10, 10, 10)
        title = QLabel("弹幕鱼排")
        title.setObjectName("titleLabel")
        dot = QLabel("●")
        dot.setStyleSheet("color: #58a6ff; font-size: 8px;")
        header_layout.addWidget(dot)
        header_layout.addWidget(title)
        header_layout.addStretch()
        close_btn = QPushButton("✕")
        close_btn.setObjectName("closeBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.clicked.connect(self.reject)
        header_layout.addWidget(close_btn)
        layout.addWidget(header)

        # Tab Bar
        tab_bar = QWidget()
        tab_bar.setObjectName("tabBar")
        tab_layout = QHBoxLayout(tab_bar)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        self.tab_buttons = {}
        for tab_id, label in [("account", "账号"), ("display", "显示"), ("hotkey", "热键")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, t=tab_id: self._switch_tab(t))
            tab_layout.addWidget(btn)
            self.tab_buttons[tab_id] = btn
        layout.addWidget(tab_bar)

        # Content Area
        self.content_area = QWidget()
        self.content_area.setObjectName("contentArea")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(18, 18, 18, 18)
        self.content_layout.setSpacing(0)
        layout.addWidget(self.content_area, stretch=1)

        # Build panels
        self._build_account_panel()
        self._build_display_panel()
        self._build_hotkey_tab()

        # Footer
        footer = QWidget()
        footer.setObjectName("footer")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(14, 8, 14, 8)
        self.lbl_conn_dot = QLabel("●")
        self.lbl_conn_dot.setObjectName("connDot")
        self.lbl_conn_text = QLabel("未连接")
        self.lbl_conn_text.setObjectName("connText")
        footer_layout.addWidget(self.lbl_conn_dot)
        footer_layout.addWidget(self.lbl_conn_text)
        footer_layout.addStretch()
        version = QLabel("v1.0")
        version.setObjectName("versionText")
        footer_layout.addWidget(version)
        layout.addWidget(footer)

        # Show first tab
        self._switch_tab("account")

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("class", "sectionLabel")
        return lbl

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setProperty("class", "fieldLabel")
        return lbl

    def _group_button(self, text: str, group_id: str, value: str, group: QButtonGroup) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("groupItem")
        btn.setCheckable(True)
        btn.setAutoExclusive(True)
        btn.setProperty("group", group_id)
        btn.setProperty("value", value)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        group.addButton(btn)
        return btn

    def _build_account_panel(self):
        self.panel_account = QWidget()
        layout = QVBoxLayout(self.panel_account)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._section_label("摸鱼派账号"))

        layout.addWidget(self._field_label("用户名"))
        self.input_username = QLineEdit()
        self.input_username.setPlaceholderText("输入用户名或邮箱")
        layout.addWidget(self.input_username)

        layout.addWidget(self._field_label("密码"))
        self.input_password = QLineEdit()
        self.input_password.setPlaceholderText("••••••••")
        self.input_password.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(self.input_password)

        self.btn_login = QPushButton("登 录")
        self.btn_login.setObjectName("primaryBtn")
        self.btn_login.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_login.clicked.connect(self._on_login)
        layout.addWidget(self.btn_login)

        self.btn_logout = QPushButton("退出登录")
        self.btn_logout.setObjectName("secondaryBtn")
        self.btn_logout.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_logout.clicked.connect(self._on_logout)
        layout.addWidget(self.btn_logout)

        self.lbl_login_status = QLabel("未登录")
        self.lbl_login_status.setStyleSheet("color: #f85149; font-size: 12px; margin-top: 4px;")
        layout.addWidget(self.lbl_login_status)

        layout.addSpacing(16)
        layout.addWidget(self._section_label("主题"))
        theme_group = QButtonGroup(self)
        theme_layout = QHBoxLayout()
        theme_layout.setSpacing(4)
        self.btn_theme_dark = self._group_button("黑夜", "theme", "dark", theme_group)
        self.btn_theme_light = self._group_button("白天", "theme", "light", theme_group)
        theme_layout.addWidget(self.btn_theme_dark)
        theme_layout.addWidget(self.btn_theme_light)
        layout.addLayout(theme_layout)

        # Wire theme buttons directly save
        for btn in (self.btn_theme_dark, self.btn_theme_light):
            btn.clicked.connect(self._on_theme_changed)

        layout.addStretch()

    def _build_display_panel(self):
        self.panel_display = QWidget()
        layout = QVBoxLayout(self.panel_display)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Mode
        layout.addWidget(self._section_label("弹幕模式"))
        self.mode_group = QButtonGroup(self)
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(4)
        self.btn_mode_scroll = self._group_button("滚动", "mode", "scrolling", self.mode_group)
        self.btn_mode_float = self._group_button("悬浮", "mode", "floating", self.mode_group)
        self.btn_mode_bottom = self._group_button("底部", "mode", "bottom", self.mode_group)
        mode_layout.addWidget(self.btn_mode_scroll)
        mode_layout.addWidget(self.btn_mode_float)
        mode_layout.addWidget(self.btn_mode_bottom)
        layout.addLayout(mode_layout)

        # Options
        layout.addWidget(self._section_label("显示元素"))
        options_layout = QHBoxLayout()
        options_layout.setSpacing(4)
        self.chk_avatar = QCheckBox("头像")
        self.chk_nickname = QCheckBox("昵称")
        self.chk_image = QCheckBox("图片")
        self.chk_red_packet = QCheckBox("红包")
        options_layout.addWidget(self.chk_avatar)
        options_layout.addWidget(self.chk_nickname)
        options_layout.addWidget(self.chk_image)
        options_layout.addWidget(self.chk_red_packet)
        layout.addLayout(options_layout)

        # Area
        layout.addWidget(self._section_label("弹幕区域"))
        self.combo_area = QComboBox()
        self.combo_area.addItems(["全屏", "上半屏", "下半屏"])
        layout.addWidget(self.combo_area)

        # Sliders
        layout.addWidget(self._section_label("参数调节"))
        self.slider_speed = self._make_slider(1, 10, self.config.display.danmu_speed)
        layout.addLayout(self._slider_row("速度", self.slider_speed, self.config.display.danmu_speed, ""))

        self.slider_width = self._make_slider(30, 100, self.config.display.danmu_width)
        layout.addLayout(self._slider_row("宽度", self.slider_width, self.config.display.danmu_width, "%"))

        self.slider_height = self._make_slider(30, 100, self.config.display.danmu_height)
        layout.addLayout(self._slider_row("高度", self.slider_height, self.config.display.danmu_height, "%"))

        self.slider_opacity = self._make_slider(30, 100, self.config.display.danmu_opacity)
        layout.addLayout(self._slider_row("不透明度", self.slider_opacity, self.config.display.danmu_opacity, "%"))

        self.slider_font = self._make_slider(12, 48, self.config.display.font_size)
        layout.addLayout(self._slider_row("字号", self.slider_font, self.config.display.font_size, "px"))

        layout.addWidget(self._section_label("字体"))
        self.combo_font = QComboBox()
        self.combo_font.setEditable(True)
        self.combo_font.addItems([
            "Microsoft YaHei",
            "SimHei",
            "SimSun",
            "PingFang SC",
            "Noto Sans CJK SC",
            "Arial",
            "Segoe UI",
            "Consolas",
            "monospace",
        ])
        layout.addWidget(self.combo_font)

        # Live-apply display changes
        for btn in (self.btn_mode_scroll, self.btn_mode_float, self.btn_mode_bottom):
            btn.clicked.connect(self._emit_config_save)
        self.chk_avatar.stateChanged.connect(self._emit_config_save)
        self.chk_nickname.stateChanged.connect(self._emit_config_save)
        self.chk_image.stateChanged.connect(self._emit_config_save)
        self.chk_red_packet.stateChanged.connect(self._emit_config_save)
        self.combo_area.currentIndexChanged.connect(self._emit_config_save)
        self.combo_font.currentTextChanged.connect(self._emit_config_save)
        self.slider_speed.valueChanged.connect(self._emit_config_save)
        self.slider_width.valueChanged.connect(self._emit_config_save)
        self.slider_height.valueChanged.connect(self._emit_config_save)
        self.slider_opacity.valueChanged.connect(self._emit_config_save)
        self.slider_font.valueChanged.connect(self._emit_config_save)

        layout.addStretch()

    def _build_hotkey_tab(self):
        self.panel_hotkey = QWidget()
        layout = QVBoxLayout(self.panel_hotkey)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._section_label("全局快捷键"))
        layout.addWidget(self._field_label("打开输入框快捷键"))
        self.input_hotkey = QLineEdit()
        self.input_hotkey.setPlaceholderText("例如: ctrl+shift+enter")
        layout.addWidget(self.input_hotkey)

        hint = QLabel("按下快捷键可在任何位置打开消息输入框。如果设置后无效，说明该组合键已被其他程序占用，请换一个。输入文字后按 Enter 发送，按 Esc 关闭。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8b949e; font-size: 11px; line-height: 1.6;")
        layout.addWidget(hint)

        save_btn = QPushButton("保存热键")
        save_btn.setObjectName("primaryBtn")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.clicked.connect(self._on_save_hotkey)
        layout.addWidget(save_btn)

        layout.addStretch()

    def _make_slider(self, min_val, max_val, val):
        s = QSlider(Qt.Orientation.Horizontal)
        s.setMinimum(min_val)
        s.setMaximum(max_val)
        s.setValue(val)
        return s

    def _slider_row(self, label_text, slider, val, suffix):
        row = QVBoxLayout()
        row.setSpacing(4)
        header = QHBoxLayout()
        lbl_name = QLabel(label_text)
        lbl_name.setStyleSheet("font-size: 12px;")
        lbl_value = QLabel(f"{val}{suffix}")
        lbl_value.setStyleSheet("color: #58a6ff; font-size: 12px;")
        header.addWidget(lbl_name)
        header.addStretch()
        header.addWidget(lbl_value)
        row.addLayout(header)
        row.addWidget(slider)
        slider.valueChanged.connect(lambda v, l=lbl_value, s=suffix: l.setText(f"{v}{s}"))
        return row

    # ── Tab Switching ───────────────────────────────────────────

    def _switch_tab(self, tab_id: str):
        self._current_tab = tab_id
        for tid, btn in self.tab_buttons.items():
            btn.setChecked(tid == tab_id)
            btn.setProperty("active", "true" if tid == tab_id else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        # Replace content
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)

        if tab_id == "account":
            self.content_layout.addWidget(self.panel_account)
        elif tab_id == "display":
            self.content_layout.addWidget(self.panel_display)
        elif tab_id == "hotkey":
            self.content_layout.addWidget(self.panel_hotkey)

    # ── Form Population ─────────────────────────────────────────

    def _populate_form(self):
        self.input_username.setText(self.config.account.username or "")

        mode = self.config.display.danmu_mode
        self.btn_mode_scroll.setChecked(mode == "scrolling")
        self.btn_mode_float.setChecked(mode == "floating")
        self.btn_mode_bottom.setChecked(mode == "bottom")

        self.chk_avatar.setChecked(self.config.display.show_avatar)
        self.chk_nickname.setChecked(self.config.display.show_nickname)
        self.chk_image.setChecked(self.config.display.show_image)
        self.chk_red_packet.setChecked(self.config.display.show_red_packet)

        area_map = {"fullscreen": 0, "topHalf": 1, "bottomHalf": 2}
        self.combo_area.setCurrentIndex(area_map.get(self.config.display.danmu_area, 0))

        self.slider_speed.setValue(self.config.display.danmu_speed)
        self.slider_width.setValue(self.config.display.danmu_width)
        self.slider_height.setValue(self.config.display.danmu_height)
        self.slider_opacity.setValue(self.config.display.danmu_opacity)
        self.slider_font.setValue(self.config.display.font_size)

        idx = self.combo_font.findText(self.config.display.font_family)
        if idx >= 0:
            self.combo_font.setCurrentIndex(idx)
        else:
            self.combo_font.setCurrentText(self.config.display.font_family)

        theme = self.config.theme
        self.btn_theme_dark.setChecked(theme == "dark")
        self.btn_theme_light.setChecked(theme == "light")

        self.input_hotkey.setText(self.config.hotkey or "ctrl+enter")

    # ── Event Handlers ──────────────────────────────────────────

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

    def _on_theme_changed(self):
        theme = "dark" if self.btn_theme_dark.isChecked() else "light"
        self.config.theme = theme
        self._apply_theme()
        self._emit_config_save()

    def _on_save_display(self):
        self._emit_config_save()
        QMessageBox.information(self, "保存成功", "显示设置已保存")

    def _on_save_hotkey(self):
        self.config.hotkey = self.input_hotkey.text().strip() or "ctrl+enter"
        self._emit_config_save()
        QMessageBox.information(self, "保存成功", "快捷键已保存，下次生效")

    def _emit_config_save(self):
        mode = "scrolling"
        if self.btn_mode_float.isChecked():
            mode = "floating"
        elif self.btn_mode_bottom.isChecked():
            mode = "bottom"

        area_map = {0: "fullscreen", 1: "topHalf", 2: "bottomHalf"}
        area = area_map.get(self.combo_area.currentIndex(), "fullscreen")

        display_config = {
            "danmuMode": mode,
            "showAvatar": self.chk_avatar.isChecked(),
            "showNickname": self.chk_nickname.isChecked(),
            "showImage": self.chk_image.isChecked(),
            "showRedPacket": self.chk_red_packet.isChecked(),
            "danmuSpeed": self.slider_speed.value(),
            "danmuArea": area,
            "danmuWidth": self.slider_width.value(),
            "danmuHeight": self.slider_height.value(),
            "danmuOpacity": self.slider_opacity.value(),
            "fontSize": self.slider_font.value(),
            "fontFamily": self.combo_font.currentText().strip() or "Microsoft YaHei",
        }
        self.config_saved.emit(display_config)

    # ── Public State Updates ────────────────────────────────────

    def _update_login_state(self):
        if self._logged_in:
            self.lbl_login_status.setText(f"已登录: {self.config.account.username}")
            self.lbl_login_status.setStyleSheet("color: #238636; font-size: 12px; margin-top: 4px;")
            self.btn_login.setEnabled(False)
            self.btn_login.setText("已登录")
            self.btn_logout.setEnabled(True)
        else:
            self.lbl_login_status.setText("未登录")
            self.lbl_login_status.setStyleSheet("color: #f85149; font-size: 12px; margin-top: 4px;")
            self.btn_login.setEnabled(True)
            self.btn_login.setText("登 录")
            self.btn_logout.setEnabled(False)

    def on_login_success(self, username: str):
        self._logged_in = True
        self.config.account.username = username
        self._update_login_state()

    def on_login_failed(self, error: str):
        self._logged_in = False
        self._update_login_state()
        self.btn_login.setText("登 录")
        self.btn_login.setEnabled(True)
        QMessageBox.warning(self, "登录失败", error)

    def on_logout(self):
        self._logged_in = False
        self.config.account.username = ""
        self.input_password.clear()
        self._update_login_state()

    def set_connected(self, connected: bool):
        self._connected = connected
        self.lbl_conn_dot.setProperty("connected", "true" if connected else "false")
        self.lbl_conn_text.setProperty("connected", "true" if connected else "false")
        self.lbl_conn_text.setText("已连接" if connected else "未连接")
        self.lbl_conn_dot.style().unpolish(self.lbl_conn_dot)
        self.lbl_conn_dot.style().polish(self.lbl_conn_dot)
        self.lbl_conn_text.style().unpolish(self.lbl_conn_text)
        self.lbl_conn_text.style().polish(self.lbl_conn_text)

    def update_config(self, config: Config):
        self.config = config
        self._apply_theme()
        self._populate_form()

    def keyPressEvent(self, event):
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
