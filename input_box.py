"""Floating input box for sending chat messages.

A minimal bottom input bar without a heavy border, so it doesn't visually
interfere with the danmu overlay behind it.
"""

import logging

import config as cfg_module
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QApplication,
)

logger = logging.getLogger("danmuFishpi.inputbox")


class InputBox(QWidget):
    """A floating, frameless bottom input bar."""

    message_sent = pyqtSignal(str)
    closed = pyqtSignal()

    THEME_DARK = {
        "bg": QColor(13, 17, 23, 230),
        "border": QColor(48, 54, 61),
        "input_bg": QColor(22, 27, 34, 240),
        "input_border": QColor(62, 68, 76),
        "input_border_focus": QColor(88, 166, 255),
        "text_primary": "#e6edf3",
        "text_muted": "#8b949e",
        "accent": "#58a6ff",
        "error": "#f85149",
    }

    THEME_LIGHT = {
        "bg": QColor(255, 255, 255, 230),
        "border": QColor(208, 215, 222),
        "input_bg": QColor(246, 248, 250, 240),
        "input_border": QColor(208, 215, 222),
        "input_border_focus": QColor(9, 105, 218),
        "text_primary": "#1f2328",
        "text_muted": "#656d76",
        "accent": "#0969da",
        "error": "#cf222e",
    }

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self._theme = theme
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(520)
        self._build_ui()
        self.hide()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("发送消息到聊天室 · Enter 发送 · Esc 关闭")
        self.input_field.returnPressed.connect(self._on_send)
        layout.addWidget(self.input_field)

        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("font-size: 12px; color: " + self._t("error") + ";")
        self.lbl_error.setWordWrap(True)
        layout.addWidget(self.lbl_error)

        self._apply_theme()

    def _t(self, key: str) -> str:
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        val = t[key]
        return val if isinstance(val, str) else val.name()

    def _apply_theme(self):
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background: {t['input_bg'].name()};
                border: 1px solid {t['input_border'].name()};
                border-radius: 8px;
                padding: 12px 14px;
                color: {t['text_primary']};
                font-size: 15px;
                selection-background-color: {t['accent']};
            }}
            QLineEdit:focus {{
                border: 1px solid {t['input_border_focus'].name()};
            }}
            QLabel {{
                color: {t['text_muted']};
                background: transparent;
                border: none;
            }}
        """)
        self.lbl_error.setStyleSheet(f"font-size: 12px; color: {t['error']};")

    def set_theme(self, theme: str):
        self._theme = theme
        self._apply_theme()

    def show_at_bottom(self):
        """Center the input box horizontally near the bottom of the configured screen."""
        screen = cfg_module.target_screen_geometry(QApplication.instance())
        # Compute the real size from the layout *before* positioning. On the
        # first show the widget has no valid height yet (only width is fixed),
        # so calling this avoids placing it off-screen where the window manager
        # would re-center it.
        self.adjustSize()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 20
        self.move(x, y)
        self.input_field.clear()
        self.lbl_error.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input_field.setFocus(Qt.FocusReason.PopupFocusReason)

    def hide_input(self):
        if self.isVisible():
            self.hide()
            self.closed.emit()

    def _on_send(self):
        text = self.input_field.text().strip()
        if text:
            self.message_sent.emit(text)
            self.input_field.clear()
            self.hide_input()

    def set_error(self, msg: str):
        self.lbl_error.setText(msg)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide_input()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        painter.setBrush(t["bg"])
        painter.drawRoundedRect(self.rect(), 10, 10)
