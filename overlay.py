"""PyQt6 transparent always-on-top danmu overlay.

Uses Qt.WA_TranslucentBackground for reliable transparency (no DirectComposition
issues like Wails/WebView2). Renders danmu with QPainter at 60fps.

Click-through is achieved via Win32 WS_EX_LAYERED | WS_EX_TRANSPARENT,
applied after show() when the HWND is available. Can be toggled on/off
for settings panel interaction.
"""

import ctypes
import logging
import sys
import time

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QPointF
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import QElapsedTimer

from danmu_engine import DanmuEngine, DanmuItem

logger = logging.getLogger("danmuFishpi.overlay")

# Win32 constants for click-through
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

_user32 = ctypes.windll.user32
_GetWindowLongW = _user32.GetWindowLongW
_SetWindowLongW = _user32.SetWindowLongW

# Theme colors
THEME_DARK = {
    "text": QColor(230, 237, 243),       # #e6edf3
    "nickname": QColor(88, 166, 255),    # #58a6ff
    "card_bg": QColor(13, 17, 23, 235),  # rgba(13,17,23,0.92)
    "card_border": QColor(48, 54, 61),   # #30363d
    "outline": QColor(0, 0, 0, 200),
}

THEME_LIGHT = {
    "text": QColor(31, 35, 40),          # #1f2328
    "nickname": QColor(9, 105, 218),     # #0969da
    "card_bg": QColor(255, 255, 255, 235),
    "card_border": QColor(208, 215, 222),
    "outline": QColor(255, 255, 255, 200),
}

FRAME_DT = 1.0 / 60.0
INTERVAL_MS = 16
DT_CAP = 0.1


def apply_click_through(hwnd: int, enable: bool) -> None:
    """Toggle WS_EX_TRANSPARENT on the window's extended style."""
    if not hwnd:
        return
    ex_style = _GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enable:
        ex_style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    else:
        ex_style &= ~WS_EX_TRANSPARENT
        ex_style |= WS_EX_LAYERED  # Keep layered for transparency
    _SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)


class DanmuOverlay(QWidget):
    """Transparent always-on-top overlay that renders danmu with QPainter."""

    def __init__(self, engine: DanmuEngine):
        super().__init__()

        self.engine = engine

        # Window flags: frameless, always-on-top, tool window (no taskbar),
        # bypass window manager to reduce focus stealing
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )

        # Critical transparency attributes
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setStyleSheet("background: transparent;")

        # Font setup
        self.font = QFont("Microsoft YaHei", engine.font_size)
        self.font.setBold(True)
        self.font_metrics = QFontMetrics(self.font)

        # Theme
        self.theme = THEME_DARK
        self.click_through = True
        self.visible_flag = True  # Toggle danmu visibility

        # Animation timer
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)

        self._tick_clock = QElapsedTimer()
        self._last_tick_valid = False
        self.last_dt = FRAME_DT

        # Pre-rendered pixmaps cache for scrolling items
        self._pixmaps: dict[int, QPixmap] = {}  # id(item) -> QPixmap

    def update_config(self, display_config: dict, theme: str) -> None:
        """Update overlay settings from config."""
        self.engine.update_config(display_config)
        font_family = display_config.get("fontFamily", "Microsoft YaHei") or "Microsoft YaHei"
        self.font = QFont(font_family, self.engine.font_size)
        self.font.setBold(True)
        self.font_metrics = QFontMetrics(self.font)
        self.theme = THEME_LIGHT if theme == "light" else THEME_DARK
        # Clear pixmap cache (font/size changed)
        self._pixmaps.clear()
        self.update()

    def set_click_through(self, enable: bool) -> None:
        """Toggle mouse click-through."""
        self.click_through = enable
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, enable)
        try:
            hwnd = int(self.winId())
            apply_click_through(hwnd, enable)
        except (RuntimeError, ValueError, TypeError):
            pass

    def toggle_visibility(self) -> None:
        """Toggle danmu visibility on/off."""
        self.visible_flag = not self.visible_flag
        if self.visible_flag:
            self.show()
            self.start_render_loop()
        else:
            self.stop_render_loop()
            self.hide()

    def showEvent(self, event):
        """Apply Win32 click-through after window is shown (HWND available)."""
        super().showEvent(event)
        # Apply click-through now that we have a HWND
        try:
            hwnd = int(self.winId())
            if hwnd:
                apply_click_through(hwnd, self.click_through)
        except (RuntimeError, ValueError, TypeError):
            # Retry on next event
            QTimer.singleShot(100, lambda: self._retry_click_through())

    def _retry_click_through(self):
        try:
            hwnd = int(self.winId())
            if hwnd:
                apply_click_through(hwnd, self.click_through)
        except (RuntimeError, ValueError, TypeError):
            pass

    def start_render_loop(self) -> None:
        """Start the 60fps render timer."""
        if not self.isVisible():
            return
        self._last_tick_valid = False
        if not self.timer.isActive():
            self.timer.start(INTERVAL_MS)
        self._tick()

    def stop_render_loop(self) -> None:
        """Stop the render timer."""
        self.timer.stop()
        self._last_tick_valid = False

    def _tick(self) -> None:
        """Animation tick: update positions and request repaint."""
        if not self.isVisible() or not self.visible_flag:
            return

        # Calculate delta time
        if not self._last_tick_valid:
            self._tick_clock.start()
            self._last_tick_valid = True
            self.last_dt = FRAME_DT
        else:
            dt = self._tick_clock.restart() / 1000.0
            self.last_dt = min(dt if dt > 0 else FRAME_DT, DT_CAP)

        # Update scrolling positions
        if self.engine.mode == "scrolling":
            self.engine.update_scrolling()

        # Trigger repaint
        self.update()

    def paintEvent(self, event):
        """Render all danmu items."""
        if not self.visible_flag:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # Apply opacity
        opacity = self.engine.opacity / 100.0
        painter.setOpacity(opacity)

        if self.engine.mode == "scrolling":
            self._paint_scrolling(painter)
        elif self.engine.mode == "floating":
            self._paint_floating(painter)
        elif self.engine.mode == "bottom":
            self._paint_bottom(painter)

        painter.end()

    def _measure_item_width(self, item: DanmuItem) -> float:
        """Measure total width of a danmu item (avatar + nickname + content)."""
        w = 16  # padding

        if self.engine.show_avatar and item.nickname:
            w += int(self.engine.font_size * 1.4) + 8

        if self.engine.show_nickname and item.nickname:
            nick = item.nickname + ": "
            w += self.font_metrics.horizontalAdvance(nick)
            w += 8  # gap

        # Content width (strip HTML for measurement)
        content = self._strip_html(item.content)
        if item.has_image and not self.engine.show_image:
            content = "[图片] " + content
        w += self.font_metrics.horizontalAdvance(content)
        return w + 16  # padding

    def _strip_html(self, text: str) -> str:
        """Strip HTML tags for text measurement."""
        import re
        return re.sub(r'<[^>]+>', '', text).strip()

    def _avatar_color(self, nickname: str) -> QColor:
        """Generate a consistent avatar background color from nickname."""
        hue = abs(hash(nickname)) % 360
        return QColor.fromHsv(hue, 180, 220)

    def _draw_avatar(self, painter: QPainter, x: float, y: float, size: int,
                     nickname: str) -> None:
        """Draw a circular avatar placeholder with the first letter."""
        color = self._avatar_color(nickname)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(int(x), int(y), size, size)

        letter = nickname[0].upper() if nickname else "?"
        font = QFont(self.font)
        font.setPointSize(int(size * 0.5))
        painter.setFont(font)
        painter.setPen(QPen(QColor(255, 255, 255)))
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(letter)
        th = fm.ascent()
        painter.drawText(
            int(x + (size - tw) / 2),
            int(y + th + (size - th) / 2 - 2),
            letter,
        )
        painter.setFont(self.font)

    def _get_item_pixmap(self, item: DanmuItem) -> QPixmap:
        """Get or create a pre-rendered pixmap for a scrolling item."""
        item_id = id(item)
        if item_id in self._pixmaps:
            return self._pixmaps[item_id]

        # Max width is 60% of container width
        max_width = int(self.engine.container_width * 0.6)
        avatar_size = int(self.engine.font_size * 1.4)
        padding = 12
        line_gap = 4

        # Prepare prefix size
        prefix_w = 0
        if self.engine.show_avatar and item.nickname:
            prefix_w += avatar_size + 8
        nick_text = ""
        if self.engine.show_nickname and item.nickname:
            nick_text = item.nickname + ": "
            prefix_w += self.font_metrics.horizontalAdvance(nick_text) + 8

        # Content width
        content = self._strip_html(item.content)
        if item.has_image and not self.engine.show_image:
            content = "[图片] " + content

        # Decide layout: if total width fits, single line; otherwise wrap content
        content_single_w = self.font_metrics.horizontalAdvance(content)
        total_single_w = prefix_w + content_single_w + padding * 2

        if total_single_w <= max_width:
            # Single line
            width = max(1, total_single_w)
            height = int(self.font_metrics.height() + 10)
            lines = 1
        else:
            # Wrap content into multiple lines (max 2 lines for scrolling)
            available_w = max(100, max_width - prefix_w - padding * 2)
            flags = int(Qt.TextFlag.TextWordWrap)
            br = self.font_metrics.boundingRect(
                QRect(0, 0, available_w, 1000), flags, content)
            line_h = self.font_metrics.height()
            max_h = line_h * 2 + line_gap  # limit to 2 lines visually
            lines = max(1, min(2, (br.height() + line_h - 1) // line_h))
            width = max_width
            height = int(line_h * lines + line_gap * (lines - 1) + 10)

        if width <= 0 or height <= 0:
            return QPixmap()

        dpr = self.devicePixelRatio()
        pm = QPixmap(int(width * dpr), int(height * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        p.setFont(self.font)

        text_y = self.font_metrics.ascent() + 5
        text_x = padding

        # Draw avatar
        if self.engine.show_avatar and item.nickname:
            self._draw_avatar(p, text_x, 5, avatar_size, item.nickname)
            text_x += avatar_size + 8

        # Draw nickname
        if self.engine.show_nickname and item.nickname:
            self._draw_text_with_outline(p, nick_text, text_x, text_y,
                                         self.theme["nickname"])
            text_x += self.font_metrics.horizontalAdvance(nick_text) + 8

        # Draw content (single line or wrapped)
        if lines == 1:
            self._draw_text_with_outline(p, content, text_x, text_y,
                                         self.theme["text"])
        else:
            available_w = max(100, width - text_x - padding)
            content_rect = QRect(text_x, 5, available_w, height - 10)
            self._draw_text_wrapped(p, content, content_rect)

        p.end()

        # Set item dimensions
        item.width = width
        item.height = height
        self._pixmaps[item_id] = pm
        return pm

    def _draw_text_wrapped(self, painter: QPainter, text: str,
                           rect: QRect) -> None:
        """Draw wrapped text with outline."""
        outline_pen = QPen(self.theme["outline"])
        outline_pen.setWidth(2)
        painter.setPen(outline_pen)
        flags = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (1, 1), (-1, 1), (1, -1)]:
            painter.drawText(rect.translated(dx, dy), flags, text)
        painter.setPen(QPen(self.theme["text"]))
        painter.drawText(rect, flags, text)

    def _draw_text_with_outline(self, painter: QPainter, text: str,
                                 x: int, y: int, color: QColor) -> None:
        """Draw text with a dark outline for readability on any background."""
        outline_pen = QPen(self.theme["outline"])
        outline_pen.setWidth(3)
        painter.setPen(outline_pen)
        # Draw outline by offsetting text in 8 directions
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (1, 1), (-1, 1), (1, -1)]:
            painter.drawText(x + dx, y + dy, text)
        # Draw main text
        painter.setPen(QPen(color))
        painter.drawText(x, y, text)

    def _paint_scrolling(self, painter: QPainter) -> None:
        """Paint scrolling mode: items moving right to left."""
        for item in self.engine.scroll_items:
            pm = self._get_item_pixmap(item)
            if pm.isNull():
                continue
            dpr = pm.devicePixelRatio()
            painter.drawPixmap(
                QPointF(item.x, item.y),
                pm,
                QRectF(0, 0, pm.width() / dpr, pm.height() / dpr),
            )

    def _paint_floating(self, painter: QPainter) -> None:
        """Paint floating mode: right-side card list."""
        card_w = 280
        card_h = 0
        card_padding = 10
        card_gap = 6
        max_items = self.engine.max_float

        items = self.engine.float_items[-max_items:]
        if not items:
            return

        # Draw from bottom up (newest at bottom)
        y = self.height() - card_padding
        for item in reversed(items):
            card_h = self._draw_card(painter, item,
                                     self.width() - card_w - card_padding,
                                     y - 60, card_w, 60)
            y -= card_h + card_gap
            if y < 0:
                break

    def _paint_bottom(self, painter: QPainter) -> None:
        """Paint bottom mode: bottom-left bubble list."""
        card_padding = 10
        card_gap = 4
        max_w = 600
        max_items = self.engine.max_bottom

        items = self.engine.float_items[-max_items:]
        if not items:
            return

        y = self.height() - card_padding
        for item in reversed(items):
            bubble_h = 40
            self._draw_bubble(painter, item,
                              card_padding, y - bubble_h,
                              min(max_w, self.width() - 2 * card_padding),
                              bubble_h)
            y -= bubble_h + card_gap
            if y < 0:
                break

    def _draw_card(self, painter: QPainter, item: DanmuItem,
                    x: float, y: float, w: float, h: float) -> float:
        """Draw a floating card (right-side list). Returns actual height used."""
        padding = 10
        avatar_size = 28
        gap = 8
        line_gap = 2

        # Prepare fonts
        nick_font = QFont(self.font)
        nick_font.setPointSize(max(8, self.engine.font_size - 6))
        nick_fm = QFontMetrics(nick_font)

        content_font = QFont(self.font)
        content_font.setPointSize(max(9, self.engine.font_size - 4))
        content_fm = QFontMetrics(content_font)

        content = self._strip_html(item.content)
        if item.has_image and not self.engine.show_image:
            content = "[图片] " + content

        # Measure content inside card with word wrap
        text_w = w - padding * 2 - (avatar_size + gap if self.engine.show_avatar else 0)
        text_w = max(40, text_w)
        flags = int(Qt.TextFlag.TextWordWrap)
        content_br = content_fm.boundingRect(QRect(0, 0, int(text_w), 1000), flags, content)
        content_h = content_br.height()

        nick_h = nick_fm.height() if self.engine.show_nickname and item.nickname else 0
        total_h = padding * 2 + nick_h + (line_gap if nick_h else 0) + content_h
        total_h = max(total_h, avatar_size + padding * 2)

        # Draw card background/border
        painter.setPen(QPen(self.theme["card_border"]))
        painter.setBrush(self.theme["card_bg"])
        painter.drawRoundedRect(QRectF(x, y, w, total_h), 8, 8)

        # Draw avatar
        text_x = x + padding
        if self.engine.show_avatar and item.nickname:
            self._draw_avatar(painter, text_x, y + padding + (total_h - padding * 2 - avatar_size) / 2,
                              avatar_size, item.nickname)
            text_x += avatar_size + gap

        # Draw nickname
        text_y = y + padding + nick_fm.ascent()
        if self.engine.show_nickname and item.nickname:
            painter.setPen(QPen(self.theme["nickname"]))
            painter.setFont(nick_font)
            painter.drawText(int(text_x), int(text_y), item.nickname)

        # Draw content (wrapped)
        text_y += nick_h + line_gap
        painter.setPen(QPen(self.theme["text"]))
        painter.setFont(content_font)
        content_rect = QRect(int(text_x), int(text_y), int(text_w), int(total_h - text_y - padding))
        painter.drawText(content_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, content)

        painter.setFont(self.font)
        return total_h

    def _draw_bubble(self, painter: QPainter, item: DanmuItem,
                      x: float, y: float, w: float, h: float) -> float:
        """Draw a bottom bubble. Returns actual height used."""
        padding = 10
        avatar_size = 24
        gap = 8

        nick_font = QFont(self.font)
        nick_font.setPointSize(max(8, self.engine.font_size - 6))
        nick_fm = QFontMetrics(nick_font)

        content_font = QFont(self.font)
        content_font.setPointSize(max(9, self.engine.font_size - 4))
        content_fm = QFontMetrics(content_font)

        content = self._strip_html(item.content)
        if item.has_image and not self.engine.show_image:
            content = "[图片] " + content

        text_w = w - padding * 2 - (avatar_size + gap if self.engine.show_avatar else 0)
        text_w = max(40, text_w)
        flags = int(Qt.TextFlag.TextWordWrap)
        content_br = content_fm.boundingRect(QRect(0, 0, int(text_w), 1000), flags, content)
        content_h = content_br.height()

        nick_h = nick_fm.height() if self.engine.show_nickname and item.nickname else 0
        total_h = padding * 2 + nick_h + 2 + content_h
        total_h = max(total_h, avatar_size + padding * 2)

        painter.setPen(QPen(self.theme["card_border"]))
        painter.setBrush(self.theme["card_bg"])
        painter.drawRoundedRect(QRectF(x, y, w, total_h), 12, 12)

        text_x = x + padding
        if self.engine.show_avatar and item.nickname:
            self._draw_avatar(painter, text_x, y + padding + (total_h - padding * 2 - avatar_size) / 2,
                              avatar_size, item.nickname)
            text_x += avatar_size + gap

        text_y = y + padding + nick_fm.ascent()
        if self.engine.show_nickname and item.nickname:
            painter.setPen(QPen(self.theme["nickname"]))
            painter.setFont(nick_font)
            painter.drawText(int(text_x), int(text_y), item.nickname)

        text_y += nick_h + 2
        painter.setPen(QPen(self.theme["text"]))
        painter.setFont(content_font)
        content_rect = QRect(int(text_x), int(text_y), int(text_w), int(total_h - text_y - padding))
        painter.drawText(content_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, content)

        painter.setFont(self.font)
        return total_h

    def add_message(self, msg: dict) -> None:
        """Add a new message to the engine and ensure render loop is running."""
        self.engine.add_message(msg)
        if self.isVisible() and self.visible_flag:
            self.start_render_loop()

    def clear_all(self) -> None:
        """Clear all danmu."""
        self.engine.clear_all()
        self._pixmaps.clear()
        self.update()

    def resizeEvent(self, event):
        """Update engine container size on resize."""
        super().resizeEvent(event)
        self.engine.set_container_size(
            float(self.width()), float(self.height()))
