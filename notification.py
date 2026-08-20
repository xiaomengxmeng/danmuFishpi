"""Silent notification popup widget (top-right, no Windows sound)."""

import logging

import config as cfg_module
from PyQt6.QtCore import Qt, QTimer, QPoint, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QApplication, QGraphicsOpacityEffect

logger = logging.getLogger("danmuFishpi.notification")

_AVATAR_SIZE = 36


def _circular_pixmap(pm: QPixmap, size: int) -> QPixmap:
    """Crop a pixmap to a circle of the given size (antialiased)."""
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addEllipse(0, 0, size, size)
    p.setClipPath(path)
    p.drawPixmap(0, 0, size, size, pm)
    p.end()
    return out


class NotificationPopup(QWidget):
    """A single top-right notification toast.

    Extended for followed-user ("特别关注") notifications: optional avatar,
    accent styling (gold border + ⭐ title), longer duration and click-to-dismiss.
    All new parameters are optional and default to the original behaviour.
    """

    THEME_DARK = {
        "bg": QColor(13, 17, 23, 230),
        "border": QColor(48, 54, 61),
        "title": "#e6edf3",
        "body": "#c9d1d9",
        "accent_border": QColor(210, 153, 34),   # #d29922
    }
    THEME_LIGHT = {
        "bg": QColor(255, 255, 255, 235),
        "border": QColor(208, 215, 222),
        "title": "#1f2328",
        "body": "#656d76",
        "accent_border": QColor(154, 103, 0),    # #9a6700
    }

    def __init__(self, title: str, body: str, theme: str = "dark", parent=None,
                 *, avatar_url: str = "", avatar_nickname: str = "",
                 image_cache=None, accent: bool = False,
                 duration_ms: int = 2500):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._theme = theme
        self._accent = accent
        self._avatar_url = avatar_url or ""
        self._avatar_nickname = avatar_nickname or ""
        self._image_cache = image_cache
        self._avatar_pixmap: QPixmap | None = None
        self.setFixedWidth(300 if accent else 280)
        self._build_ui(title, body)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._start_fade)
        self._timer.setSingleShot(True)
        self._timer.start(duration_ms)
        self._load_avatar()

    # ── UI ──────────────────────────────────────────────────────

    def _build_ui(self, title: str, body: str):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)

        display_title = ("⭐ " + title) if self._accent else title

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

        lbl_title = QLabel(display_title)
        lbl_title.setObjectName("title")
        lbl_title.setWordWrap(True)

        lbl_body = QLabel(body)
        lbl_body.setObjectName("body")
        lbl_body.setWordWrap(True)

        if self._avatar_url:
            row = QHBoxLayout()
            row.setSpacing(10)
            self.lbl_avatar = QLabel()
            self.lbl_avatar.setObjectName("avatar")
            self.lbl_avatar.setFixedSize(_AVATAR_SIZE, _AVATAR_SIZE)
            row.addWidget(self.lbl_avatar, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(4)
            col.addWidget(lbl_title)
            col.addWidget(lbl_body)
            row.addLayout(col, 1)
            layout.addLayout(row)
        else:
            layout.addWidget(lbl_title)
            layout.addWidget(lbl_body)

    # ── Avatar ──────────────────────────────────────────────────

    def _load_avatar(self):
        """Kick off avatar loading; show a letter placeholder meanwhile."""
        if not self._avatar_url:
            return
        if self._image_cache is None:
            self._show_avatar_placeholder()
            return
        try:
            pm = self._image_cache.get(self._avatar_url)
        except Exception:
            pm = None
        if pm is not None and not pm.isNull():
            self._avatar_pixmap = pm
            self._update_avatar()
        else:
            self._show_avatar_placeholder()
            try:
                self._image_cache.loaded.connect(self._on_image_loaded)
            except Exception as e:
                logger.debug(f"avatar loaded connect failed: {e}")

    def _on_image_loaded(self, url: str):
        """Repaint the avatar once its image arrives (if still alive)."""
        if url != self._avatar_url or self._image_cache is None:
            return
        try:
            pm = self._image_cache.get(url)
        except Exception:
            pm = None
        if pm is not None and not pm.isNull():
            self._avatar_pixmap = pm
            self._update_avatar()

    def _update_avatar(self):
        if not hasattr(self, "lbl_avatar"):
            return
        if self._avatar_pixmap is not None:
            size = _AVATAR_SIZE
            scaled = self._avatar_pixmap.scaled(
                size, size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.lbl_avatar.setPixmap(_circular_pixmap(scaled, size))
        else:
            self._show_avatar_placeholder()

    def _show_avatar_placeholder(self):
        if not hasattr(self, "lbl_avatar"):
            return
        nickname = self._avatar_nickname or "?"
        color = QColor.fromHsv(abs(hash(nickname)) % 360, 180, 220).name()
        letter = nickname[0].upper() if nickname else "?"
        self.lbl_avatar.setStyleSheet(
            f"background-color: {color}; color: #ffffff; border-radius: {_AVATAR_SIZE // 2}px;"
            f"font-weight: bold; font-size: 16px;"
        )
        self.lbl_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_avatar.setText(letter)

    # ── Interaction / paint ─────────────────────────────────────

    def show_at(self, pos: QPoint):
        self.move(pos)
        self.show()
        self.raise_()

    def mousePressEvent(self, event):
        """Click to dismiss immediately."""
        self._start_fade()
        super().mousePressEvent(event)

    def _start_fade(self):
        if getattr(self, "_fading", False):
            return
        self._fading = True
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
        t = self.THEME_LIGHT if self._theme == "light" else self.THEME_DARK
        if self._accent:
            painter.setPen(QPen(t["accent_border"], 2))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(t["bg"])
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)


class NotificationManager:
    """Manages a queue of silent top-right notifications."""

    # Max simultaneous followed-user (accent) notifications.
    _MAX_ACCENT = 2

    def __init__(self, theme: str = "dark", image_cache=None):
        self.theme = theme
        self._image_cache = image_cache
        self._active: list[NotificationPopup] = []
        self._max_visible = 3
        self._gap = 8
        self._margin = (16, 16)  # right, top

    def set_theme(self, theme: str):
        self.theme = theme

    def show(self, title: str, body: str, *,
             avatar_url: str = "", avatar_nickname: str = "",
             accent: bool = False, duration_ms: int = 2500):
        """Show a notification if screen is available."""
        try:
            popup = NotificationPopup(
                title, body, self.theme,
                avatar_url=avatar_url,
                avatar_nickname=avatar_nickname,
                image_cache=self._image_cache,
                accent=accent,
                duration_ms=duration_ms,
            )
            screen = cfg_module.target_screen_geometry(QApplication.instance())
            x = screen.x() + screen.width() - popup.width() - self._margin[0]
            y = screen.y() + self._margin[1]
            for p in self._active[-self._max_visible:]:
                y += p.height() + self._gap
            popup.show_at(QPoint(x, y))
            # Capture popup and manager by default arg to avoid closure issues
            # after the popup is destroyed.
            popup.destroyed.connect(lambda _p=popup, _mgr=self: _mgr._remove(_p))
            self._active.append(popup)
            self._prune_accent()
            # Keep only max visible + a small queue
            if len(self._active) > self._max_visible + 2:
                old = self._active.pop(0)
                old.close()
        except Exception as e:
            logger.error(f"Failed to show notification: {e}")

    def _prune_accent(self):
        """Keep at most _MAX_ACCENT followed-user toasts on screen."""
        accents = [p for p in self._active if getattr(p, "_accent", False)]
        while len(accents) > self._MAX_ACCENT:
            old = accents.pop(0)
            if old in self._active:
                self._active.remove(old)
            old.close()

    def _remove(self, popup: NotificationPopup):
        if popup in self._active:
            self._active.remove(popup)
