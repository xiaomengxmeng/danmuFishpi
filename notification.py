"""Silent notification popup widget (top-right, no Windows sound)."""

import logging

from PyQt6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication, QGraphicsOpacityEffect

logger = logging.getLogger("danmuFishpi.notification")


class NotificationPopup(QWidget):
    """A single top-right notification toast."""

    THEME_DARK = {
        "bg": QColor(13, 17, 23, 230),
        "border": QColor(48, 54, 61),
        "title": "#e6edf3",
        "body": "#c9d1d9",
    }
    THEME_LIGHT = {
        "bg": QColor(255, 255, 255, 235),
        "border": QColor(208, 215, 222),
        "title": "#1f2328",
        "body": "#656d76",
    }

    def __init__(self, title: str, body: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(280)
        self._theme = theme
        self._build_ui(title, body)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._start_fade)
        self._timer.setSingleShot(True)
        self._timer.start(2500)

    def _build_ui(self, title: str, body: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        self.setStyleSheet(f"""
            QLabel {{
                background: transparent;
                border: none;
            }}
            QLabel#title {{
                color: {t['title']};
                font-size: 13px;
                font-weight: bold;
            }}
            QLabel#body {{
                color: {t['body']};
                font-size: 12px;
            }}
        """)

        lbl_title = QLabel(title)
        lbl_title.setObjectName("title")
        lbl_title.setWordWrap(True)
        layout.addWidget(lbl_title)

        lbl_body = QLabel(body)
        lbl_body.setObjectName("body")
        lbl_body.setWordWrap(True)
        layout.addWidget(lbl_body)

    def show_at(self, pos: QPoint):
        self.move(pos)
        self.show()
        self.raise_()

    def _start_fade(self):
        self._anim = QPropertyAnimation(self._opacity, b"opacity")
        self._anim.setDuration(400)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.finished.connect(self.close)
        self._anim.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        painter.setBrush(t["bg"])
        painter.drawRoundedRect(self.rect(), 8, 8)


class NotificationManager:
    """Manages a queue of silent top-right notifications."""

    def __init__(self, theme: str = "dark"):
        self.theme = theme
        self._active: list[NotificationPopup] = []
        self._max_visible = 3
        self._gap = 8
        self._margin = (16, 16)  # right, top

    def set_theme(self, theme: str):
        self.theme = theme

    def show(self, title: str, body: str):
        """Show a notification if screen is available."""
        try:
            popup = NotificationPopup(title, body, self.theme)
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.x() + screen.width() - popup.width() - self._margin[0]
            y = screen.y() + self._margin[1]
            for p in self._active[-self._max_visible:]:
                y += p.height() + self._gap
            popup.show_at(QPoint(x, y))
            # Capture popup and manager by default arg to avoid closure issues
            # after the popup is destroyed.
            popup.destroyed.connect(lambda _p=popup, _mgr=self: _mgr._remove(_p))
            self._active.append(popup)
            # Keep only max visible + a small queue
            if len(self._active) > self._max_visible + 2:
                old = self._active.pop(0)
                old.close()
        except Exception as e:
            logger.error(f"Failed to show notification: {e}")

    def _remove(self, popup: NotificationPopup):
        if popup in self._active:
            self._active.remove(popup)
