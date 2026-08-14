"""Settings dialog for danmuFishpi.

PyQt6-based settings panel styled to match the original GitHub-style drawer.
"""

import logging
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal, QRectF, QTimer, QEvent
from PyQt6.QtGui import QFont, QPainter, QColor, QPen, QBrush
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QLineEdit,
    QPushButton, QSlider, QCheckBox, QButtonGroup, QComboBox, QMessageBox,
    QFrame, QSizePolicy, QTextEdit, QScrollArea, QColorDialog,
)

from config import Config, dpapi_encrypt, dpapi_decrypt
from version import APP_NAME, APP_VERSION

logger = logging.getLogger("danmuFishpi.settings")


class ToggleSwitch(QWidget):
    """Custom iOS-style toggle switch widget.

    Draws a pill-shaped toggle with a circular knob that slides left/right.
    Uses QTimer-based tween for smooth animation.
    """

    toggled = pyqtSignal(bool)
    _ANIM_DURATION = 150  # ms

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._progress = 1.0 if checked else 0.0  # 0.0=off, 1.0=on
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._anim_tick)
        self._anim_from = self._progress
        self._anim_to = self._progress
        self._anim_start_ms = 0
        self.setFixedSize(44, 24)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool):
        self._checked = checked
        target = 1.0 if checked else 0.0
        self._anim_timer.stop()
        self._progress = target
        self._anim_from = target
        self._anim_to = target
        self.update()

    def _start_anim(self, to_on: bool):
        self._anim_timer.stop()
        self._anim_from = self._progress
        self._anim_to = 1.0 if to_on else 0.0
        self._anim_start_ms = QTimer().remainingTime()  # dummy call to get a timestamp
        from PyQt6.QtCore import QElapsedTimer
        self._anim_elapsed = QElapsedTimer()
        self._anim_elapsed.start()
        self._anim_timer.start(16)

    def _anim_tick(self):
        elapsed = self._anim_elapsed.elapsed()
        if elapsed >= self._ANIM_DURATION:
            self._anim_timer.stop()
            self._progress = self._anim_to
        else:
            t = elapsed / self._ANIM_DURATION
            # Ease-out cubic
            t = 1.0 - (1.0 - t) ** 3
            self._progress = self._anim_from + (self._anim_to - self._anim_from) * t
        self.update()

    def mousePressEvent(self, event):
        self._checked = not self._checked
        self._start_anim(self._checked)
        self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        r = h / 2.0

        # Interpolate track color based on progress
        r_on, g_on, b_on = 35, 134, 54      # checked green (#238636)
        r_off, g_off, b_off = 74, 79, 87    # unchecked gray (#4a4f57)
        p = self._progress
        r_color = int(r_off + (r_on - r_off) * p)
        g_color = int(g_off + (g_on - g_off) * p)
        b_color = int(b_off + (b_on - b_off) * p)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(r_color, g_color, b_color)))
        painter.drawRoundedRect(QRectF(0, 0, w, h), r, r)

        # Knob
        knob_margin = 2
        knob_size = h - knob_margin * 2
        max_x = w - knob_size - knob_margin
        knob_x = knob_margin + (max_x - knob_margin) * self._progress
        knob_y = knob_margin

        # Shadow
        painter.setBrush(QBrush(QColor(0, 0, 0, 40)))
        painter.drawEllipse(QRectF(knob_x + 0.5, knob_y + 0.5, knob_size, knob_size))

        # Knob
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawEllipse(QRectF(knob_x, knob_y, knob_size, knob_size))

        painter.end()


class SettingsDialog(QDialog):
    """GitHub-style settings dialog with account, display, and hotkey tabs."""

    login_requested = pyqtSignal(str, str, str)  # username, password, mfa_code
    logout_requested = pyqtSignal()
    config_saved = pyqtSignal(dict)              # display config dict
    send_message_requested = pyqtSignal(str)     # message content (optional)

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._logged_in = bool(config.account.username and config.account.password_enc)
        self._connected = False
        self._current_tab = "account"

        self.setWindowTitle(f"{APP_NAME} - 设置 v{APP_VERSION}")
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

        # 焦点离开设置面板时自动隐藏（恢复弹幕覆盖层穿透）
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.setInterval(200)
        self._auto_hide_timer.timeout.connect(self._try_auto_hide)

        # 跟踪下拉列表打开数量，避免展开下拉（如显示器选择）时误隐藏
        self._popup_open_count = 0
        for cb in self.findChildren(QComboBox):
            cb.aboutToShowPopup.connect(self._on_combo_popup_show)
            cb.aboutToHidePopup.connect(self._on_combo_popup_hide)

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
            "text_secondary": "#656d76" if is_light else "#9ca3af",
            "text_muted": "#8c959f" if is_light else "#b0b8c4",
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
            /* 强制所有子控件继承主文字色（Qt 样式表不自动继承 color）。
               更具体的选择器（如 #primaryBtn、#hintLabel、fieldLabel 等）会覆盖此默认值。 */
            * {{
                color: {c['text_primary']};
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
            QScrollArea#scrollArea {{
                background: transparent;
                border: none;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: {c['border']};
                border-radius: 4px;
                min-height: 24px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {c['text_muted']};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
                background: none;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
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
            QTextEdit {{
                background: {c['bg_input']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                padding: 8px 12px;
                color: {c['text_primary']};
                font-size: 13px;
                selection-background-color: {c['accent']};
            }}
            QTextEdit:focus {{
                border: 1px solid {c['accent']};
            }}
            QLabel#hintLabel {{
                color: {c['text_muted']};
                font-size: 11px;
                padding-bottom: 4px;
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
            /* Toggle switches are custom-painted via ToggleSwitch widget */
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
            /* 下拉弹出列表（Popup）的深浅主题样式 */
            QComboBox QAbstractItemView {{
                background: {c['bg_input']};
                color: {c['text_primary']};
                border: 1px solid {c['border']};
                border-radius: 6px;
                selection-background-color: {c['accent']};
                selection-color: #ffffff;
                outline: 0;
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 10px;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {c['accent']};
                color: #ffffff;
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
        title = QLabel(APP_NAME)
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
        for tab_id, label in [("account", "账号"), ("display", "显示"), ("hotkey", "热键"), ("block_follow", "屏蔽/关注"), ("user_colors", "弹幕颜色")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda checked, t=tab_id: self._switch_tab(t))
            tab_layout.addWidget(btn)
            self.tab_buttons[tab_id] = btn
        layout.addWidget(tab_bar)

        # Content Area (scrollable)
        self.content_area = QWidget()
        self.content_area.setObjectName("contentArea")
        self.content_layout = QVBoxLayout(self.content_area)
        self.content_layout.setContentsMargins(18, 18, 18, 18)
        self.content_layout.setSpacing(0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("scrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setWidget(self.content_area)
        layout.addWidget(self.scroll_area, stretch=1)

        # Build panels
        self._build_account_panel()
        self._build_display_panel()
        self._build_hotkey_tab()
        self._build_block_follow_panel()
        self._build_user_colors_panel()

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
        version = QLabel(f"v{APP_VERSION}")
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

        # 2FA 验证码区域（默认隐藏，仅在服务器要求时显示）
        self.lbl_mfa_hint = QLabel("🔒 账号已开启两步验证，请输入验证码后重新登录")
        self.lbl_mfa_hint.setStyleSheet(
            "color: #d29922; font-size: 11px; margin-top: 6px; padding: 6px 8px;"
            "background: rgba(210, 153, 34, 0.08); border-radius: 4px;"
        )
        self.lbl_mfa_hint.setWordWrap(True)
        self.lbl_mfa_hint.hide()
        layout.addWidget(self.lbl_mfa_hint)

        self.lbl_mfa_field = self._field_label("两步验证码")
        self.lbl_mfa_field.hide()
        layout.addWidget(self.lbl_mfa_field)
        self.input_mfa = QLineEdit()
        self.input_mfa.setPlaceholderText("6 位数字验证码")
        self.input_mfa.setMaxLength(6)
        self.input_mfa.setEchoMode(QLineEdit.EchoMode.Password)
        self.input_mfa.hide()
        self.input_mfa.returnPressed.connect(self._on_login)
        layout.addWidget(self.input_mfa)

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
        layout.addWidget(self._section_label("系统"))

        # 开机自启开关
        import autostart as autostart_module
        autostart_enabled = autostart_module.is_enabled()
        row_autostart, self.chk_autostart = self._toggle_row("开机自启", autostart_enabled)
        self.chk_autostart.toggled.connect(self._on_autostart_toggled)
        layout.addWidget(row_autostart)

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

    def _toggle_row(self, label_text: str, initial: bool) -> tuple[QWidget, ToggleSwitch]:
        """Create a row with a label and toggle switch."""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)
        lbl = QLabel(label_text)
        lbl.setProperty("class", "fieldLabel")
        toggle = ToggleSwitch(initial, self)
        row_layout.addWidget(lbl)
        row_layout.addStretch()
        row_layout.addWidget(toggle)
        return row, toggle

    def _build_display_panel(self):
        self.panel_display = QWidget()
        layout = QVBoxLayout(self.panel_display)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── Display Elements ──
        layout.addWidget(self._section_label("显示元素"))
        elements_grid = QVBoxLayout()
        elements_grid.setSpacing(6)
        elements_grid.setContentsMargins(0, 0, 0, 0)

        row_avatar, self.chk_avatar = self._toggle_row("头像", True)
        elements_grid.addWidget(row_avatar)
        row_nick, self.chk_nickname = self._toggle_row("昵称", True)
        elements_grid.addWidget(row_nick)
        row_img, self.chk_image = self._toggle_row("图片", True)
        elements_grid.addWidget(row_img)
        row_rp, self.chk_red_packet = self._toggle_row("红包", True)
        elements_grid.addWidget(row_rp)
        row_outline, self.chk_outline = self._toggle_row("文字描边", True)
        elements_grid.addWidget(row_outline)
        row_simple, self.chk_simple_mode = self._toggle_row("简约模式", False)
        elements_grid.addWidget(row_simple)
        row_truncate, self.chk_truncate = self._toggle_row("截断超长消息", True)
        elements_grid.addWidget(row_truncate)

        layout.addLayout(elements_grid)

        # ── Display Screen ──
        layout.addWidget(self._section_label("显示屏幕"))
        screen_layout = QHBoxLayout()
        screen_layout.setContentsMargins(0, 0, 0, 0)
        screen_layout.setSpacing(8)
        self.combo_screen = QComboBox()
        self.combo_screen.setObjectName("combo")
        self.combo_screen.addItem("主显示器 (跟随系统)", -1)
        self.combo_screen.setMinimumWidth(260)
        self.combo_screen.currentIndexChanged.connect(self._emit_config_save)
        screen_layout.addWidget(self.combo_screen)
        screen_layout.addStretch(1)
        layout.addLayout(screen_layout)

        # ── Notification ──
        layout.addSpacing(6)
        layout.addWidget(self._section_label("通知"))
        notify_grid = QVBoxLayout()
        notify_grid.setSpacing(6)
        notify_grid.setContentsMargins(0, 0, 0, 0)
        row_notify_startup, self.chk_notify_startup = self._toggle_row("启动提示", True)
        notify_grid.addWidget(row_notify_startup)
        row_notify_login, self.chk_notify_login = self._toggle_row("登录提示", True)
        notify_grid.addWidget(row_notify_login)
        row_notify_follow, self.chk_notify_follow = self._toggle_row("特别关注提示", True)
        notify_grid.addWidget(row_notify_follow)
        layout.addLayout(notify_grid)

        # Mode selector
        layout.addWidget(self._section_label("弹幕模式"))
        mode_group = QButtonGroup(self)
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(4)
        self.btn_mode_scrolling = self._group_button("滚动", "mode", "scrolling", mode_group)
        self.btn_mode_floating = self._group_button("浮动", "mode", "floating", mode_group)
        mode_layout.addWidget(self.btn_mode_scrolling)
        mode_layout.addWidget(self.btn_mode_floating)
        layout.addLayout(mode_layout)
        for btn in (self.btn_mode_scrolling, self.btn_mode_floating):
            btn.clicked.connect(self._on_mode_changed)

        # Scrolling-only settings container
        self.scrolling_widget = QWidget()
        scroll_layout = QVBoxLayout(self.scrolling_widget)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)
        scroll_layout.addWidget(self._section_label("滚动区域"))
        self.combo_area = QComboBox()
        self.combo_area.addItems(["全屏", "上25%", "上半屏", "下半屏", "下25%"])
        self.combo_area.setObjectName("combo")
        scroll_layout.addWidget(self.combo_area)
        self.combo_area.currentIndexChanged.connect(self._emit_config_save)
        layout.addWidget(self.scrolling_widget)

        # Floating-only settings container
        self.floating_widget = QWidget()
        float_layout = QVBoxLayout(self.floating_widget)
        float_layout.setContentsMargins(0, 0, 0, 0)
        float_layout.setSpacing(10)

        float_layout.addWidget(self._section_label("浮动弹幕位置"))
        corner_group = QButtonGroup(self)
        self.btn_corner_tr = self._group_button("右上", "corner", "topRight", corner_group)
        self.btn_corner_tl = self._group_button("左上", "corner", "topLeft", corner_group)
        self.btn_corner_br = self._group_button("右下", "corner", "bottomRight", corner_group)
        self.btn_corner_bl = self._group_button("左下", "corner", "bottomLeft", corner_group)
        corner_row = QHBoxLayout()
        corner_row.setContentsMargins(0, 0, 0, 0)
        corner_row.setSpacing(4)
        for b in (self.btn_corner_tr, self.btn_corner_tl, self.btn_corner_br, self.btn_corner_bl):
            corner_row.addWidget(b)
            b.clicked.connect(self._emit_config_save)
        float_layout.addLayout(corner_row)

        self.slider_dwell = self._make_slider(3, 30, self.config.display.floating_dwell_seconds)
        float_layout.addLayout(self._slider_row("停留时间", self.slider_dwell, self.config.display.floating_dwell_seconds, "s"))
        self.slider_max_items = self._make_slider(1, 8, self.config.display.floating_max_items)
        float_layout.addLayout(self._slider_row("最大条数", self.slider_max_items, self.config.display.floating_max_items, ""))
        self.slider_card_width = self._make_slider(50, 200, int(self.config.display.floating_card_scale * 100))
        float_layout.addLayout(self._slider_row("卡片宽度系数", self.slider_card_width, int(self.config.display.floating_card_scale * 100), "%"))
        self.slider_card_font = self._make_slider(0, 100, int(self.config.display.floating_font_scale))
        float_layout.addLayout(self._slider_row("卡片字号", self.slider_card_font, int(self.config.display.floating_font_scale), "%"))
        self.slider_dwell.valueChanged.connect(self._emit_config_save)
        self.slider_max_items.valueChanged.connect(self._emit_config_save)
        self.slider_card_width.valueChanged.connect(self._emit_config_save)
        self.slider_card_font.valueChanged.connect(self._emit_config_save)
        layout.addWidget(self.floating_widget)

        # Sliders
        layout.addWidget(self._section_label("参数调节"))
        self.slider_speed = self._make_slider(0, 100, self.config.display.danmu_speed)
        layout.addLayout(self._slider_row("速度", self.slider_speed, self.config.display.danmu_speed, ""))

        self.slider_opacity = self._make_slider(0, 100, self.config.display.danmu_opacity)
        layout.addLayout(self._slider_row("不透明度", self.slider_opacity, self.config.display.danmu_opacity, "%"))

        self.slider_font = self._make_slider(0, 100, int(self.config.display.font_scale))
        layout.addLayout(self._slider_row("字号系数", self.slider_font, int(self.config.display.font_scale), "%"))

        self.slider_top_margin = self._make_slider(0, 100, int(self.config.display.top_margin))
        layout.addLayout(self._slider_row("顶部边距", self.slider_top_margin, int(self.config.display.top_margin), "%"))

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

        # Test message sender
        layout.addSpacing(16)
        layout.addWidget(self._section_label("测试发送"))
        self.input_test_message = QLineEdit()
        self.input_test_message.setPlaceholderText("输入测试消息，按 Enter 或点击发送")
        layout.addWidget(self.input_test_message)

        self.btn_send_test = QPushButton("发 送")
        self.btn_send_test.setObjectName("primaryBtn")
        self.btn_send_test.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send_test.clicked.connect(self._on_send_test_message)
        self.input_test_message.returnPressed.connect(self._on_send_test_message)
        layout.addWidget(self.btn_send_test)

        # Live-apply display changes
        self.chk_avatar.toggled.connect(self._emit_config_save)
        self.chk_nickname.toggled.connect(self._emit_config_save)
        self.chk_image.toggled.connect(self._emit_config_save)
        self.chk_red_packet.toggled.connect(self._emit_config_save)
        self.chk_outline.toggled.connect(self._emit_config_save)
        self.chk_simple_mode.toggled.connect(self._emit_config_save)
        self.chk_truncate.toggled.connect(self._emit_config_save)
        self.chk_notify_startup.toggled.connect(self._emit_config_save)
        self.chk_notify_login.toggled.connect(self._emit_config_save)
        self.chk_notify_follow.toggled.connect(self._emit_config_save)
        self.combo_area.currentIndexChanged.connect(self._emit_config_save)
        self.combo_font.currentTextChanged.connect(self._emit_config_save)
        self.slider_speed.valueChanged.connect(self._emit_config_save)
        self.slider_opacity.valueChanged.connect(self._emit_config_save)
        self.slider_font.valueChanged.connect(self._emit_config_save)
        self.slider_top_margin.valueChanged.connect(self._emit_config_save)

        layout.addStretch()

    def _build_hotkey_tab(self):
        self.panel_hotkey = QWidget()
        layout = QVBoxLayout(self.panel_hotkey)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(self._section_label("全局快捷键"))
        layout.addWidget(self._field_label("打开输入框快捷键"))
        self.input_hotkey = QLineEdit()
        self.input_hotkey.setPlaceholderText("例如: f9 或 mouse4")
        layout.addWidget(self.input_hotkey)

        self.lbl_active_hotkey = QLabel("当前生效热键: 未注册")
        self.lbl_active_hotkey.setObjectName("hintLabel")
        layout.addWidget(self.lbl_active_hotkey)

        layout.addWidget(self._field_label("老板键（隐藏/显示弹幕）"))
        self.input_boss_key = QLineEdit()
        self.input_boss_key.setPlaceholderText("例如: f10")
        layout.addWidget(self.input_boss_key)

        hint = QLabel("支持单键（如 f9、mouse4）和组合键（如 ctrl+shift+a）。若设置无效，说明该按键已被其他程序占用，请更换。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
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
        lbl_name.setProperty("class", "fieldLabel")  # inherit theme color
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
        elif tab_id == "block_follow":
            self.content_layout.addWidget(self.panel_block_follow)
        elif tab_id == "user_colors":
            self.content_layout.addWidget(self.panel_user_colors)

    # ── Form Population ─────────────────────────────────────────

    def _populate_form(self):
        self.input_username.setText(self.config.account.username or "")

        self.chk_avatar.setChecked(self.config.display.show_avatar)
        self.chk_nickname.setChecked(self.config.display.show_nickname)
        self.chk_image.setChecked(self.config.display.show_image)
        self.chk_red_packet.setChecked(self.config.display.show_red_packet)
        self.chk_outline.setChecked(self.config.display.show_outline)
        self.chk_simple_mode.setChecked(self.config.display.simple_mode)
        self.chk_truncate.setChecked(self.config.display.truncate_long_messages)
        self.chk_notify_startup.setChecked(self.config.display.notify_startup)
        self.chk_notify_login.setChecked(self.config.display.notify_login)
        self.chk_notify_follow.setChecked(self.config.display.notify_follow)

        self.slider_speed.setValue(self.config.display.danmu_speed)
        area_map = {"fullscreen": 0, "top25": 1, "topHalf": 2, "bottomHalf": 3, "bottom25": 4}
        self.combo_area.setCurrentIndex(area_map.get(self.config.display.danmu_area, 0))

        self._refresh_screen_list()

        self.slider_speed.setValue(self.config.display.danmu_speed)
        self.slider_opacity.setValue(self.config.display.danmu_opacity)
        self.slider_font.setValue(int(self.config.display.font_scale))
        self.slider_top_margin.setValue(int(self.config.display.top_margin))
        self.slider_dwell.setValue(self.config.display.floating_dwell_seconds)
        self.slider_max_items.setValue(self.config.display.floating_max_items)
        self.slider_card_width.setValue(int(self.config.display.floating_card_scale * 100))
        self.slider_card_font.setValue(int(self.config.display.floating_font_scale))

        idx = self.combo_font.findText(self.config.display.font_family)
        if idx >= 0:
            self.combo_font.setCurrentIndex(idx)
        else:
            self.combo_font.setCurrentText(self.config.display.font_family)

        self.text_blocked_ids.setPlainText("\n".join(self.config.display.blocked_user_ids))
        self.text_followed_ids.setPlainText("\n".join(self.config.display.followed_user_ids))

        theme = self.config.theme
        self.btn_theme_dark.setChecked(theme == "dark")
        self.btn_theme_light.setChecked(theme == "light")

        # Mode
        mode = self.config.display.danmu_mode
        self.btn_mode_scrolling.setChecked(mode == "scrolling")
        self.btn_mode_floating.setChecked(mode == "floating")
        self.floating_widget.setVisible(mode == "floating")
        self.scrolling_widget.setVisible(mode == "scrolling")

        # Corner
        corner = self.config.display.floating_corner
        btn_map = {"topRight": self.btn_corner_tr, "topLeft": self.btn_corner_tl,
                    "bottomRight": self.btn_corner_br, "bottomLeft": self.btn_corner_bl}
        btn = btn_map.get(corner, self.btn_corner_tr)
        btn.setChecked(True)

        self.input_hotkey.setText(self.config.hotkey or "f9")
        self.input_boss_key.setText(self.config.boss_key or "f10")

        # 弹幕颜色行（重新构建，避免重复连接信号）
        while self.user_colors_layout.count():
            item = self.user_colors_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.user_color_rows.clear()
        for uid, color in (self.config.display.user_colors or {}).items():
            self._add_user_color_row(uid=uid, color=color)
        if not self.user_color_rows:
            self._add_user_color_row()

        # 自启开关状态以注册表实际状态为准
        import autostart as autostart_module
        self.chk_autostart.setChecked(autostart_module.is_enabled())

    # ── Event Handlers ──────────────────────────────────────────

    def _on_login(self):
        username = self.input_username.text().strip()
        password = self.input_password.text()
        if not username or not password:
            QMessageBox.warning(self, "提示", "请输入用户名和密码")
            return
        mfa_code = self.input_mfa.text().strip() if self.input_mfa.isVisible() else ""
        if self.input_mfa.isVisible() and not mfa_code:
            QMessageBox.warning(self, "提示", "请输入两步验证码")
            return
        self.btn_login.setEnabled(False)
        self.btn_login.setText("登录中...")
        self.login_requested.emit(username, password, mfa_code)

    def _on_logout(self):
        self.logout_requested.emit()

    def _on_autostart_toggled(self, checked: bool):
        """Handle auto-start toggle. Operate registry first, save config only on success."""
        import autostart as autostart_module
        if checked:
            if autostart_module.enable():
                self._emit_config_save()
            else:
                # Rollback toggle, don't save config
                self.chk_autostart.setChecked(False)
                QMessageBox.warning(self, "设置失败", "设置开机自启失败，可能是权限不足")
        else:
            if autostart_module.disable():
                self._emit_config_save()
            else:
                # Rollback toggle, don't save config
                self.chk_autostart.setChecked(True)
                QMessageBox.warning(self, "取消失败", "取消开机自启失败")

    def _on_theme_changed(self):
        theme = "dark" if self.btn_theme_dark.isChecked() else "light"
        self.config.theme = theme
        self._apply_theme()
        self._emit_config_save()

    def _on_mode_changed(self):
        """Show/hide floating/scrolling widgets based on selected mode."""
        is_floating = self.btn_mode_floating.isChecked()
        self.floating_widget.setVisible(is_floating)
        self.scrolling_widget.setVisible(not is_floating)
        self._emit_config_save()

    def _on_save_display(self):
        self._emit_config_save()
        QMessageBox.information(self, "保存成功", "显示设置已保存")

    def _on_save_hotkey(self):
        self.config.hotkey = self.input_hotkey.text().strip() or "f9"
        self.config.boss_key = self.input_boss_key.text().strip() or "f10"
        self._emit_config_save()
        QMessageBox.information(self, "保存成功", "快捷键已保存，下次生效")

    def _on_send_test_message(self):
        text = self.input_test_message.text().strip()
        if not text:
            return
        self.send_message_requested.emit(text)
        self.input_test_message.clear()

    def _build_block_follow_panel(self):
        """Build the block/follow list management tab."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Block list
        layout.addWidget(self._section_label("屏蔽用户 ID"))
        hint_block = QLabel("每行一个用户 ID，保存后立即生效")
        hint_block.setObjectName("hintLabel")
        layout.addWidget(hint_block)
        self.text_blocked_ids = QTextEdit()
        self.text_blocked_ids.setPlaceholderText("user1\nuser2")
        layout.addWidget(self.text_blocked_ids)

        # Follow list
        layout.addWidget(self._section_label("特别关注用户 ID"))
        hint_follow = QLabel("每行一个用户 ID，收到消息时会弹出托盘提醒")
        hint_follow.setObjectName("hintLabel")
        layout.addWidget(hint_follow)
        self.text_followed_ids = QTextEdit()
        self.text_followed_ids.setPlaceholderText("friend1\nfriend2")
        layout.addWidget(self.text_followed_ids)

        # Save button
        self.btn_save_block_follow = QPushButton("保存")
        self.btn_save_block_follow.setObjectName("primaryBtn")
        self.btn_save_block_follow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save_block_follow.clicked.connect(self._emit_config_save)
        layout.addWidget(self.btn_save_block_follow)

        layout.addStretch()
        self.panel_block_follow = panel

    # ── 弹幕颜色（按用户设置） ────────────────────────────────

    def _build_user_colors_panel(self):
        """Build the per-user danmu color management tab."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        layout.addWidget(self._section_label("弹幕颜色"))
        hint = QLabel("为指定用户设置弹幕颜色；其余用户弹幕颜色跟随主题默认。\n用户 ID 填写鱼排用户名（userName），修改后立即生效。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.user_color_rows = []  # [{"edit": QLineEdit, "btn": QPushButton, "row": QWidget}]
        self.user_colors_layout = QVBoxLayout()
        self.user_colors_layout.setSpacing(8)
        layout.addLayout(self.user_colors_layout)

        btn_add = QPushButton("＋ 添加用户")
        btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_add.clicked.connect(lambda: self._add_user_color_row())
        layout.addWidget(btn_add)

        layout.addStretch()
        self.panel_user_colors = panel

    def _default_user_color(self) -> str:
        return "#1f2328" if self.config.theme == "light" else "#e6edf3"

    def _add_user_color_row(self, uid: str = "", color: str = ""):
        """Append one user-color row; returns nothing (stored in self)."""
        if not color:
            color = self._default_user_color()
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(8)

        edit = QLineEdit()
        edit.setPlaceholderText("用户 ID")
        edit.setText(uid)
        edit.setMinimumWidth(160)
        edit.textChanged.connect(self._emit_config_save)
        row_layout.addWidget(edit, stretch=1)

        btn = QPushButton()
        btn.setFixedSize(48, 28)
        btn.setToolTip("点击选择弹幕颜色")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._style_color_button(btn, color)
        btn.clicked.connect(lambda: self._pick_user_color(btn))
        row_layout.addWidget(btn)

        btn_del = QPushButton("删除")
        btn_del.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_del.clicked.connect(lambda: self._remove_user_color_row(row))
        row_layout.addWidget(btn_del)

        self.user_colors_layout.addWidget(row)
        self.user_color_rows.append({"edit": edit, "btn": btn, "row": row})

    def _style_color_button(self, btn: QPushButton, hex_color: str):
        btn.setProperty("color", hex_color)
        btn.setStyleSheet(
            f"background-color: {hex_color}; border: 1px solid #555555; border-radius: 4px;"
        )

    def _pick_user_color(self, btn: QPushButton):
        current = str(btn.property("color") or self._default_user_color())
        color = QColorDialog.getColor(QColor(current), self, "选择弹幕颜色")
        if color.isValid():
            self._style_color_button(btn, color.name())  # #rrggbb
            self._emit_config_save()

    def _remove_user_color_row(self, row: QWidget):
        for i, rec in enumerate(self.user_color_rows):
            if rec["row"] is row:
                self.user_color_rows.pop(i)
                break
        self.user_colors_layout.removeWidget(row)
        row.deleteLater()
        self._emit_config_save()

    def _collect_user_colors(self) -> dict:
        """Collect valid {user_id: #rrggbb} pairs from the color rows."""
        out: dict[str, str] = {}
        for rec in self.user_color_rows:
            uid = rec["edit"].text().strip()
            color = str(rec["btn"].property("color") or "").strip()
            if uid and color:
                out[uid] = color
        return out

    def _emit_config_save(self):
        area_map = {0: "fullscreen", 1: "top25", 2: "topHalf", 3: "bottomHalf", 4: "bottom25"}
        area = area_map.get(self.combo_area.currentIndex(), "fullscreen")

        # Determine mode
        mode = "floating" if self.btn_mode_floating.isChecked() else "scrolling"

        # Determine floating corner
        corner = "topRight"
        if self.btn_corner_tl.isChecked():
            corner = "topLeft"
        elif self.btn_corner_br.isChecked():
            corner = "bottomRight"
        elif self.btn_corner_bl.isChecked():
            corner = "bottomLeft"

        blocked_ids = [s.strip() for s in self.text_blocked_ids.toPlainText().splitlines() if s.strip()]
        followed_ids = [s.strip() for s in self.text_followed_ids.toPlainText().splitlines() if s.strip()]
        user_colors = self._collect_user_colors()

        display_config = {
            "danmuMode": mode,
            "floatingCorner": corner,
            "showAvatar": self.chk_avatar.isChecked(),
            "showNickname": self.chk_nickname.isChecked(),
            "showImage": self.chk_image.isChecked(),
            "showRedPacket": self.chk_red_packet.isChecked(),
            "showOutline": self.chk_outline.isChecked(),
            "simpleMode": self.chk_simple_mode.isChecked(),
            "truncateLongMessages": self.chk_truncate.isChecked(),
            "maxMessageLines": 3,
            "floatingDwellSeconds": self.slider_dwell.value(),
            "floatingMaxItems": self.slider_max_items.value(),
            "floatingCardScale": self.slider_card_width.value() / 100.0,
            "floatingFontScale": self.slider_card_font.value(),
            "topMargin": self.slider_top_margin.value(),
            "notifyStartup": self.chk_notify_startup.isChecked(),
            "notifyLogin": self.chk_notify_login.isChecked(),
            "notifyFollow": self.chk_notify_follow.isChecked(),
            "blockedUserIds": blocked_ids,
            "followedUserIds": followed_ids,
            "danmuSpeed": self.slider_speed.value(),
            "danmuArea": area,
            "danmuWidth": 100,
            "danmuHeight": 100,
            "danmuOpacity": self.slider_opacity.value(),
            "fontScale": self.slider_font.value(),
            "fontFamily": self.combo_font.currentText().strip() or "Microsoft YaHei",
            "displayScreen": self.combo_screen.currentData(),
            "autostart": self.chk_autostart.isChecked(),
            "userColors": user_colors,
        }
        self.config_saved.emit(display_config)

    def _refresh_screen_list(self):
        """重新枚举所有显示器并填充下拉框，同时恢复当前选中项。

        每次打开设置窗口（showEvent）以及配置回填（_populate_form）时调用，
        以正确处理运行中热插拔显示器的场景。
        """
        cb = self.combo_screen
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("主显示器 (跟随系统)", -1)
        try:
            import screen_utils
            for i, mon in enumerate(screen_utils.list_monitors()):
                label = mon.get("name") or f"显示器 {i + 1}"
                if mon.get("is_primary"):
                    label += " (主)"
                cb.addItem(f"显示器 {i + 1}: {label}", i)
        except Exception as e:
            logger.error(f"枚举显示器失败: {e}")
        # 恢复当前选择
        target = self.config.display.display_screen
        sel = 0
        for i in range(cb.count()):
            if cb.itemData(i) == target:
                sel = i
                break
        cb.setCurrentIndex(sel)
        cb.blockSignals(False)

    def showEvent(self, event):
        # 每次显示都重新枚举，捕捉运行中新接入/移除的显示器。
        self._refresh_screen_list()
        super().showEvent(event)

    def changeEvent(self, event):
        # 窗口失去激活（焦点离开）时，延迟一小段时间后尝试自动隐藏，
        # 避免点击面板内控件触发的瞬时失焦造成误隐藏。
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._auto_hide_timer.stop()
            else:
                self._auto_hide_timer.start()
        super().changeEvent(event)

    def _on_combo_popup_show(self):
        self._popup_open_count += 1

    def _on_combo_popup_hide(self):
        self._popup_open_count = max(0, self._popup_open_count - 1)

    def _try_auto_hide(self):
        # 若窗口重新激活（兜底，避免瞬时无焦点误判），取消隐藏
        if self.isActiveWindow():
            return
        # 下拉列表（如显示器选择）展开时不隐藏
        if self._popup_open_count > 0:
            return
        # 焦点仍在面板内（含子控件）则取消隐藏
        fw = QApplication.focusWidget()
        if fw is not None and (self.isAncestorOf(fw) or fw is self):
            return
        # 下拉列表、弹窗或模态对话框打开时不隐藏
        if QApplication.activePopupWidget() is not None:
            return
        if QApplication.activeModalWidget() is not None:
            return
        self.hide()
        # 复用 finished 信号，恢复弹幕覆盖层的鼠标穿透
        self.finished.emit(self.result())

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
        self.lbl_mfa_hint.hide()
        self.lbl_mfa_field.hide()
        self.input_mfa.hide()
        self.input_mfa.clear()
        self._update_login_state()

    def on_login_failed(self, error: str, need_mfa: bool = False):
        self._logged_in = False
        self._update_login_state()
        self.btn_login.setText("登 录")
        self.btn_login.setEnabled(True)
        if need_mfa:
            self.lbl_mfa_hint.show()
            self.lbl_mfa_field.show()
            self.input_mfa.show()
            self.input_mfa.clear()
            self.input_mfa.setFocus()
        else:
            self.lbl_mfa_hint.hide()
            self.lbl_mfa_field.hide()
            self.input_mfa.hide()
            self.input_mfa.clear()
            QMessageBox.warning(self, "登录失败", error)

    def show_mfa_prompt(self, username: str, password: str = ""):
        """Show the login form with MFA fields visible.

        Called when auto-login failed due to 2FA and the user opens
        settings. Pre-fills username and (optionally) password so the
        user only needs to enter the verification code.
        """
        self._logged_in = False
        self._update_login_state()
        self.input_username.setText(username)
        if password:
            self.input_password.setText(password)
        self.btn_login.setText("登 录")
        self.btn_login.setEnabled(True)
        self.lbl_mfa_hint.show()
        self.lbl_mfa_field.show()
        self.input_mfa.show()
        self.input_mfa.clear()
        if password:
            self.input_mfa.setFocus()
        else:
            self.input_password.setFocus()

    def on_logout(self):
        self._logged_in = False
        self.config.account.username = ""
        self.input_password.clear()
        self.lbl_mfa_hint.hide()
        self.lbl_mfa_field.hide()
        self.input_mfa.hide()
        self.input_mfa.clear()
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

    def set_active_hotkey(self, hotkey: str):
        self.active_hotkey = hotkey or "未注册"
        if hasattr(self, "lbl_active_hotkey"):
            self.lbl_active_hotkey.setText(f"当前生效热键: {self.active_hotkey}")

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
