"""Floating input box for sending chat messages.

Matches the original Wails input-box-overlay design: a centered,
semi-transparent, blurred bar at the bottom of the screen.
"""

import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QApplication,
)

logger = logging.getLogger("danmuFishpi.inputbox")


class InputBox(QWidget):
    """A floating, frameless input box for sending chatroom messages."""

    message_sent = pyqtSignal(str)
    closed = pyqtSignal()

    # Theme colors matching the overlay/settings
    THEME_DARK = {
        "bg": "rgba(13, 17, 23, 0.92)",
        "border": "rgba(255, 255, 255, 0.08)",
        "input_bg": "rgba(255, 255, 255, 0.04)",
        "text_primary": "#e6edf3",
        "text_muted": "#6e7681",
        "accent": "#58a6ff",
        "error": "#f85149",
    }

    THEME_LIGHT = {
        "bg": "rgba(255, 255, 255, 0.92)",
        "border": "rgba(0, 0, 0, 0.12)",
        "input_bg": "rgba(0, 0, 0, 0.04)",
        "text_primary": "#1f2328",
        "text_muted": "#8c959f",
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
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(420)
        self._build_ui()
        self._apply_theme()
        self.hide()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.input_field = QLineEdit()
        self.input_field.setPlaceholderText("输入消息发送到聊天室...")
        self.input_field.returnPressed.connect(self._on_send)
        layout.addWidget(self.input_field)

        bottom = QHBoxLayout()
        self.lbl_error = QLabel("")
        self.lbl_error.setStyleSheet("font-size: 12px;")
        self.lbl_error.setWordWrap(True)
        bottom.addWidget(self.lbl_error, stretch=1)

        self.lbl_hint = QLabel("Enter 发送 · Esc 关闭")
        self.lbl_hint.setStyleSheet("font-size: 11px;")
        self.lbl_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom.addWidget(self.lbl_hint)
        layout.addLayout(bottom)

    def _apply_theme(self):
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        self.setStyleSheet(f"""
            QWidget {{
                background: transparent;
                border: none;
            }}
            QLineEdit {{
                background: {t['input_bg']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 10px 14px;
                color: {t['text_primary']};
                font-size: 14px;
                selection-background-color: {t['accent']};
            }}
            QLineEdit:focus {{
                border: 1px solid {t['accent']};
            }}
            QLabel {{
                color: {t['text_muted']};
                background: transparent;
                border: none;
            }}
        """)

    def set_theme(self, theme: str):
        self._theme = theme
        self._apply_theme()

    def show_at_bottom(self):
        """Center the input box horizontally near the bottom of the primary screen."""
        screen = QApplication.primaryScreen().availableGeometry()
        x = screen.x() + (screen.width() - self.width()) // 2
        y = screen.y() + screen.height() - self.height() - 60
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
        # Parse rgba string for QColor
        painter.setBrush(QColor(t["bg"]))
        painter.drawRoundedRect(self.rect(), 10, 10)
