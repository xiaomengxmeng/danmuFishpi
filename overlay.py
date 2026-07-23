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

from PyQt6.QtCore import Qt, QTimer, QRect, QRectF, QPointF, QElapsedTimer
from PyQt6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap, QBrush,
)
from PyQt6.QtWidgets import QWidget, QApplication

from danmu_engine import DanmuEngine, DanmuItem
from image_cache import ImageCache
from win32_overlay import (
    apply_overlay_exstyles,
    reassert_hwnd_topmost,
    probe_exclusive_fullscreen_risk,
)

logger = logging.getLogger("danmuFishpi.overlay")

# Theme colors
THEME_DARK = {
    "text": QColor(230, 237, 243),       # #e6edf3
    "nickname": QColor(88, 166, 255),    # #58a6ff
    "red_packet": QColor(255, 107, 107), # #ff6b6b
    "card_bg": QColor(13, 17, 23, 235),  # rgba(13,17,23,0.92)
    "card_border": QColor(48, 54, 61),   # #30363d
    "outline": QColor(0, 0, 0, 200),
}

THEME_LIGHT = {
    "text": QColor(31, 35, 40),          # #1f2328
    "nickname": QColor(9, 105, 218),     # #0969da
    "red_packet": QColor(218, 54, 51),   # #da3633
    "card_bg": QColor(255, 255, 255, 235),
    "card_border": QColor(208, 215, 222),
    "outline": QColor(255, 255, 255, 200),
}

FRAME_DT = 1.0 / 60.0
INTERVAL_MS = 16
DT_CAP = 0.1


def apply_click_through(hwnd: int, enable: bool) -> None:
    """Toggle WS_EX_TRANSPARENT on the window's extended style."""
    apply_overlay_exstyles(hwnd, click_through=enable)


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
            | Qt.WindowType.BypassWindowManagerHint
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
        self.show_outline = True  # Text outline effect

        # Animation timer
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._topmost_timer: QTimer | None = None
        self._topmost_fail_streak = 0

        self._tick_clock = QElapsedTimer()
        self._last_tick_valid = False
        self.last_dt = FRAME_DT

        # Pre-rendered pixmaps cache for scrolling items
        self._pixmaps: dict[int, QPixmap] = {}  # id(item) -> QPixmap

        # Async image cache for avatars and inline images
        self._image_cache = ImageCache(self)
        self._image_cache.loaded.connect(self._on_image_loaded)

        # Animation clock for GIF playback (starts when first animated image loads)
        self._anim_clock = QElapsedTimer()
        self._anim_clock_started = False
        self._has_animated_content = False  # True when any visible GIF is playing

    def _start_anim_clock(self) -> None:
        """Lazily start the animation clock on first GIF load."""
        if not self._anim_clock_started:
            self._anim_clock.start()
            self._anim_clock_started = True

    def _on_image_loaded(self, url: str) -> None:
        """Invalidate scrolling pixmap cache when any image finishes loading."""
        self._pixmaps.clear()
        # If the new image is a GIF, start the animation clock
        if self._image_cache.is_animated(url):
            self._start_anim_clock()
            self._has_animated_content = True
        self.update()

    def update_config(self, display_config: dict, theme: str) -> None:
        """Update overlay settings from config."""
        self.engine.update_config(display_config)
        font_family = display_config.get("fontFamily", "Microsoft YaHei") or "Microsoft YaHei"
        self.font = QFont(font_family, self.engine.font_size)
        self.font.setBold(True)
        self.font_metrics = QFontMetrics(self.font)
        self.theme = THEME_LIGHT if theme == "light" else THEME_DARK
        self.show_outline = display_config.get("showOutline", True)
        # Clear pixmap cache (font/size changed)
        self._pixmaps.clear()
        self._has_animated_content = False
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

    def _effective_show_avatar(self) -> bool:
        return self.engine.show_avatar and not self.engine.simple_mode

    def _effective_show_image(self) -> bool:
        return self.engine.show_image and not self.engine.simple_mode

    def _nickname_color(self) -> QColor:
        return self.theme["text"] if self.engine.simple_mode else self.theme["nickname"]

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
        self._apply_win32_exstyles(_defer_attempt=0)

    def _apply_win32_exstyles(self, *, _defer_attempt: int = 0) -> None:
        """Apply WS_EX_LAYERED + WS_EX_TRANSPARENT; retry if HWND not ready."""
        try:
            hwnd = int(self.winId())
        except (RuntimeError, ValueError, TypeError):
            return
        if not hwnd:
            try:
                still_visible = self.isVisible()
            except (RuntimeError, ValueError, TypeError):
                return
            if _defer_attempt < 3 and still_visible:
                QTimer.singleShot(
                    100,
                    lambda attempt=_defer_attempt + 1: self._apply_win32_exstyles(
                        _defer_attempt=attempt
                    ),
                )
            return
        apply_overlay_exstyles(hwnd, click_through=self.click_through)

    def start_render_loop(self) -> None:
        """Start the 60fps render timer and topmost reassert timer."""
        if not self.isVisible():
            return
        self._last_tick_valid = False
        if not self.timer.isActive():
            self.timer.start(INTERVAL_MS)
        if not getattr(self, "_topmost_timer", None):
            self._topmost_timer = QTimer(self)
            self._topmost_timer.timeout.connect(self._reassert_topmost)
            self._topmost_timer.start(200)
        self._tick()

    def stop_render_loop(self) -> None:
        """Stop the render timer and topmost reassert timer."""
        self.timer.stop()
        self._last_tick_valid = False
        if getattr(self, "_topmost_timer", None):
            self._topmost_timer.stop()

    def _reassert_topmost(self, *, log: bool = False) -> None:
        """Periodically restore HWND_TOPMOST to stay above fullscreen games."""
        if not self.isVisible():
            return
        self.raise_()
        try:
            hwnd = int(self.winId())
        except (RuntimeError, ValueError, TypeError):
            return
        success = reassert_hwnd_topmost(hwnd)
        if success:
            self._topmost_fail_streak = 0
            if log:
                logger.info("Overlay HWND_TOPMOST reasserted")
        else:
            self._topmost_fail_streak = getattr(self, "_topmost_fail_streak", 0) + 1
            if self._topmost_fail_streak == 3:
                logger.warning(
                    "topmost reassert failed 3 times; overlay may be blocked "
                    "by exclusive fullscreen or another topmost window"
                )
        # Periodically probe for exclusive fullscreen to help diagnostics
        if not getattr(self, "_last_probe_at", 0) or time.time() - self._last_probe_at > 10:
            self._probe_fullscreen_risk()
            self._last_probe_at = time.time()

    def force_topmost(self) -> None:
        """Manual trigger to force overlay back to topmost (e.g. tray action)."""
        logger.info("Force overlay topmost")
        self.show()
        self.raise_()
        self.activateWindow()
        self._reassert_topmost(log=True)

    def _probe_fullscreen_risk(self) -> bool:
        """Log whether a foreground exclusive-fullscreen window is detected."""
        try:
            hwnd = int(self.winId())
        except (RuntimeError, ValueError, TypeError):
            return False
        geom = self.screen().geometry()
        risk = probe_exclusive_fullscreen_risk(
            overlay_hwnd=hwnd,
            screen_x=geom.x(),
            screen_y=geom.y(),
            screen_w=geom.width(),
            screen_h=geom.height(),
            own_hwnds=(hwnd,),
        )
        if risk:
            logger.warning(
                "Detected a foreground exclusive-fullscreen window; "
                "overlay may not be visible. Switch the game to borderless/windowed fullscreen."
            )
        return risk

    def _tick(self) -> None:
        """Animation tick: update positions and request repaint.

        When animated GIF content is present the pixmap cache for those items
        is skipped and the render loop must continue even when no scrolling
        items are moving (e.g. floating/bottom mode) so frames advance.
        """
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

        # Always trigger repaint when animated content is present
        # so GIF frames advance smoothly.
        self.update()

    def paintEvent(self, event):
        """Render all danmu items."""
        if not self.visible_flag:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        try:
            # Apply opacity
            opacity = self.engine.opacity / 100.0
            painter.setOpacity(opacity)

            if self.engine.mode == "scrolling":
                self._paint_scrolling(painter)
            elif self.engine.mode == "floating":
                self._paint_floating(painter)
            elif self.engine.mode == "bottom":
                self._paint_bottom(painter)
        except Exception as e:
            logger.error(f"paintEvent error: {e}", exc_info=True)
        finally:
            painter.end()

    def _measure_item_width(self, item: DanmuItem) -> float:
        """Measure total width of a danmu item (avatar + nickname + content).

        For multi-line text the width is determined by the longest line,
        which is already limited by max_width (0.6 * container).
        """
        w = 16  # padding

        if self._effective_show_avatar() and item.nickname:
            w += int(self.engine.font_size * 1.4) + 8

        if self.engine.show_nickname and item.nickname:
            nick = item.nickname + ": "
            w += self.font_metrics.horizontalAdvance(nick)
            w += 8  # gap

        # Content width: strip HTML, then use the longest line after wrapping
        # (capped to max_width, same as _get_item_pixmap)
        content = self._strip_html(item.content)
        if item.has_image and not self._effective_show_image():
            content = "[图片] " + content

        max_width = int(self.engine.container_width * 0.6)
        prefix_w = w
        available_text_w = max(100, max_width - prefix_w)
        text_lines = self._wrap_text(content, self.font_metrics, available_text_w)
        longest_line_w = max(
            (self.font_metrics.horizontalAdvance(line) for line in text_lines),
            default=0,
        )
        w = prefix_w + longest_line_w

        # Add extra safety margin for outline/antialiasing so the last
        # character is not clipped.
        return min(w + 24, max_width + 24)  # padding + outline safety margin

    def _strip_html(self, text: str) -> str:
        """Strip HTML tags for text measurement."""
        import re
        return re.sub(r'<[^>]+>', '', text).strip()

    def _wrap_text(self, text: str, fm: QFontMetrics, max_width: int,
                   max_lines: int | None = None) -> list[str]:
        """Wrap text into visual lines, preserving explicit newlines.

        Uses Qt's word+anywhere wrap so long unbroken strings (code, URLs)
        are also broken.  Hard line breaks (\\n) are kept as separate lines.
        """
        if not text:
            return [""]

        flags = Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextWrapAnywhere
        line_h = fm.height()
        raw_lines = []

        for hard_line in text.split("\n"):
            if not hard_line:
                raw_lines.append("")
                continue
            # Measure the number of visual lines Qt thinks this needs.
            br = fm.boundingRect(QRect(0, 0, max_width, 100000), flags, hard_line)
            line_count = max(1, (br.height() + line_h - 1) // line_h)
            if line_count == 1:
                raw_lines.append(hard_line)
            else:
                # Walk forward measuring substrings until we exceed max_width.
                start = 0
                while start < len(hard_line):
                    # Binary search for the longest prefix that fits.
                    lo, hi = 1, min(len(hard_line) - start, 500)
                    best = 1
                    while lo <= hi:
                        mid = (lo + hi) // 2
                        seg = hard_line[start:start + mid]
                        if fm.horizontalAdvance(seg) <= max_width:
                            best = mid
                            lo = mid + 1
                        else:
                            hi = mid - 1
                    segment = hard_line[start:start + best]
                    if not segment:
                        segment = hard_line[start:start + 1]
                    raw_lines.append(segment)
                    start += len(segment)

        if max_lines is not None:
            raw_lines = raw_lines[:max_lines]
        return raw_lines if raw_lines else [""]

    def _draw_text_block(self, painter: QPainter, lines: list[str], x: float, y: float,
                         color: QColor, font_metrics: QFontMetrics | None = None) -> None:
        """Draw a list of text lines with outline, preserving hard line breaks."""
        fm = font_metrics or self.font_metrics
        line_h = fm.height()
        pen = QPen(color)
        cur_y = y
        for line in lines:
            if self.show_outline:
                # Draw outline
                outline_pen = QPen(self.theme["outline"])
                outline_pen.setWidth(1)
                painter.setPen(outline_pen)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1, -1), (1, 1), (-1, 1), (1, -1)]:
                    painter.drawText(int(x + dx), int(cur_y + dy), line)
            # Draw text
            painter.setPen(pen)
            painter.drawText(int(x), int(cur_y), line)
            cur_y += line_h

    def _avatar_color(self, nickname: str) -> QColor:
        """Generate a consistent avatar background color from nickname."""
        hue = abs(hash(nickname)) % 360
        return QColor.fromHsv(hue, 180, 220)

    def _draw_avatar(self, painter: QPainter, x: float, y: float, size: int,
                     nickname: str, avatar_url: str = "") -> None:
        """Draw a circular avatar. Uses cached image if available, else placeholder.

        For animated GIFs the current frame is drawn (loops forever).
        """
        pm = None
        if avatar_url and self._image_cache.is_animated(avatar_url) and self._anim_clock_started:
            pm = self._image_cache.current_frame(avatar_url, self._anim_clock.elapsed())
        elif avatar_url:
            pm = self._image_cache.get(avatar_url)

        if pm and not pm.isNull():
            # Clip to circle and draw scaled image
            path = QPainterPath()
            path.addEllipse(int(x), int(y), size, size)
            painter.setClipPath(path)
            try:
                scaled = pm.scaled(
                    size, size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                # Center the scaled image in the circle
                dx = (size - scaled.width()) // 2
                dy = (size - scaled.height()) // 2
                painter.drawPixmap(int(x + dx), int(y + dy), scaled)
            finally:
                painter.setClipping(False)
        else:
            # Placeholder circle with first letter
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

    def _parse_content(self, content: str) -> tuple[str, list[str]]:
        """Extract plain text and image URLs from message content.

        Returns (text_without_img_tags, list_of_image_src_urls).
        """
        import re
        img_urls = []

        def _collect_img(m):
            tag = m.group(0)
            src_match = re.search(r'(?is)src=["\']([^"\']+)["\']', tag)
            if src_match:
                url = src_match.group(1)
                if url:
                    img_urls.append(url)
            return ""

        text = re.sub(r"(?is)<img[^>]*>", _collect_img, content)
        text = re.sub(r'<[^>]+>', '', text).strip()
        return text, img_urls

    def _draw_inline_image(self, painter: QPainter, x: float, y: float,
                           url: str, max_size: int) -> tuple[int, int]:
        """Draw a scaled inline image with rounded corners (web-style).

        Uses a clip path for rounded corners. Adds a subtle border to match
        typical web image style. Animated GIFs advance every frame.
        """
        pm = None
        if self._image_cache.is_animated(url) and self._anim_clock_started:
            pm = self._image_cache.current_frame(url, self._anim_clock.elapsed())
        else:
            pm = self._image_cache.get(url)

        if not pm or pm.isNull():
            # Placeholder while loading
            painter.setPen(QPen(self.theme["card_border"]))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(int(x), int(y), max_size, max_size, 6, 6)
            painter.setPen(QPen(self.theme["text"]))
            painter.drawText(int(x + 8), int(y + max_size // 2), "[图片]")
            return max_size, max_size

        src_w, src_h = pm.width(), pm.height()
        if src_w <= 0 or src_h <= 0:
            return 0, 0

        # Scale to fit within max_size, with a small margin so images don't
        # feel cramped against borders (like web lightbox behaviour).
        scale = min(max_size / src_w, max_size / src_h, 0.95)
        dst_w = max(1, int(src_w * scale))
        dst_h = max(1, int(src_h * scale))

        scaled = pm.scaled(
            dst_w, dst_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        # Clip to rounded rect for web-style corners
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(x, y, dst_w, dst_h), 6, 6)
        painter.setClipPath(clip_path)
        painter.drawPixmap(int(x), int(y), scaled)
        painter.setClipping(False)

        # Subtle border overlay (matches typical web image border)
        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(x, y, dst_w, dst_h), 6, 6)
        return dst_w, dst_h

    def _get_item_pixmap(self, item: DanmuItem) -> QPixmap:
        """Get or create a pre-rendered pixmap for a scrolling item.

        Items containing animated GIFs are NOT cached — they are redrawn every
        frame so the GIF animation plays. The pixmap cache is skipped for them.
        """
        # Parse text and images first to detect animated content
        text, img_urls = self._parse_content(item.content)
        show_images = self._effective_show_image() and item.has_image and img_urls

        has_gif = False
        if show_images:
            for url in img_urls[:3]:
                if self._image_cache.is_animated(url):
                    has_gif = True
                    break

        # Don't cache items with animated GIFs — they need per-frame redraw
        if not has_gif:
            item_id = id(item)
            if item_id in self._pixmaps:
                return self._pixmaps[item_id]

        max_width = int(self.engine.container_width * 0.6)
        avatar_size = int(self.engine.font_size * 1.4)
        padding = 12
        line_gap = 4
        image_max_h = 80
        image_gap = 6

        if item.has_image and not self._effective_show_image():
            text = "[图片] " + text

        # Prefix size (avatar + nickname)
        prefix_w = 0
        if self._effective_show_avatar() and item.nickname:
            prefix_w += avatar_size + 8
        nick_text = ""
        if self.engine.show_nickname and item.nickname:
            nick_text = item.nickname + ": "
            prefix_w += self.font_metrics.horizontalAdvance(nick_text) + 8

        # Text layout — truncate if setting enabled
        max_lines = None
        if self.engine.truncate_long_messages:
            max_lines = self.engine.max_message_lines
        available_text_w = max(100, max_width - prefix_w - padding * 2)
        text_lines = self._wrap_text(text, self.font_metrics, available_text_w, max_lines)
        # If truncated, add an ellipsis hint
        total_raw_lines = self._wrap_text(text, self.font_metrics, available_text_w)
        is_truncated = max_lines is not None and len(total_raw_lines) > max_lines
        if is_truncated:
            text_lines.append("...")
        text_height = len(text_lines) * self.font_metrics.height() + (len(text_lines) - 1) * line_gap + 10

        longest_line_w = max(
            (self.font_metrics.horizontalAdvance(line) for line in text_lines),
            default=0,
        )
        width = max(1, prefix_w + longest_line_w + padding * 2)
        width = min(width, max_width)

        # Image block (placed below text)
        image_block_h = 0
        if show_images:
            row_x = 0
            row_h = 0
            for url in img_urls[:3]:  # up to 3 images
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if row_x + dw > width - padding * 2 and row_x > 0:
                    image_block_h += row_h + image_gap
                    row_x = 0
                    row_h = 0
                row_x += dw + image_gap
                row_h = max(row_h, dh)
            image_block_h += row_h
            if image_block_h > 0:
                image_block_h += padding  # gap between text and images

        height = text_height + image_block_h

        if width <= 0 or height <= 0:
            return QPixmap()

        # Update item dimensions
        item.width = width
        item.height = height

        dpr = self.devicePixelRatio()
        target_pm = QPixmap(int(width * dpr), int(height * dpr))
        target_pm.setDevicePixelRatio(dpr)
        target_pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(target_pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            p.setFont(self.font)

            text_y = self.font_metrics.ascent() + 5
            text_x = padding

            # Draw avatar
            if self._effective_show_avatar() and item.nickname:
                self._draw_avatar(p, text_x, 5, avatar_size, item.nickname, item.avatar_url)
                text_x += avatar_size + 8

            # Draw nickname
            if self.engine.show_nickname and item.nickname:
                self._draw_text_with_outline(p, nick_text, text_x, text_y,
                                             self._nickname_color())
                text_x += self.font_metrics.horizontalAdvance(nick_text) + 8

            # Draw content (preserving hard line breaks)
            text_color = self.theme["red_packet"] if item.is_red_packet else self.theme["text"]
            self._draw_text_block(p, text_lines, text_x, text_y, text_color)

            # Draw images below text
            if show_images:
                img_x = padding
                img_y = text_height
                row_h = 0
                for url in img_urls[:3]:
                    cached = self._image_cache.get(url)
                    if cached and not cached.isNull():
                        scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                        dw = int(cached.width() * scale)
                        dh = int(cached.height() * scale)
                    else:
                        dw = dh = image_max_h
                    if img_x + dw > width - padding and img_x > padding:
                        img_x = padding
                        img_y += row_h + image_gap
                        row_h = 0
                    self._draw_inline_image(p, img_x, img_y, url, image_max_h)
                    img_x += dw + image_gap
                    row_h = max(row_h, dh)
        finally:
            p.end()

        # Only cache non-animated items
        if not has_gif:
            self._pixmaps[id(item)] = target_pm
        return target_pm

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
        if self.show_outline:
            outline_pen = QPen(self.theme["outline"])
            outline_pen.setWidth(2)
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
        image_max_h = 80
        image_gap = 6

        # Prepare fonts
        nick_font = QFont(self.font)
        nick_font.setPointSize(max(8, self.engine.font_size - 6))
        nick_fm = QFontMetrics(nick_font)

        content_font = QFont(self.font)
        content_font.setPointSize(max(9, self.engine.font_size - 4))
        content_fm = QFontMetrics(content_font)

        text, img_urls = self._parse_content(item.content)
        show_images = self._effective_show_image() and item.has_image and img_urls
        if item.has_image and not self._effective_show_image():
            text = "[图片] " + text

        # Measure content inside card — truncate if setting enabled
        max_lines = None
        if self.engine.truncate_long_messages:
            max_lines = self.engine.max_message_lines
        text_w = w - padding * 2 - (avatar_size + gap if self._effective_show_avatar() else 0)
        text_w = max(40, text_w)
        content_lines = self._wrap_text(text, content_fm, int(text_w), max_lines)
        total_raw = self._wrap_text(text, content_fm, int(text_w))
        if max_lines is not None and len(total_raw) > max_lines:
            content_lines.append("...")
        line_h = content_fm.height()
        content_h = len(content_lines) * line_h + (len(content_lines) - 1) * 2

        nick_h = nick_fm.height() if self.engine.show_nickname and item.nickname else 0
        total_h = padding * 2 + nick_h + (line_gap if nick_h else 0) + content_h

        # Add image block height
        image_block_h = 0
        if show_images:
            row_x = 0
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if row_x + dw > text_w and row_x > 0:
                    image_block_h += row_h + image_gap
                    row_x = 0
                    row_h = 0
                row_x += dw + image_gap
                row_h = max(row_h, dh)
            image_block_h += row_h
            if image_block_h > 0:
                image_block_h += padding
            total_h += image_block_h

        total_h = max(total_h, avatar_size + padding * 2)

        # Draw card background/border
        painter.setPen(QPen(self.theme["card_border"]))
        painter.setBrush(self.theme["card_bg"])
        painter.drawRoundedRect(QRectF(x, y, w, total_h), 8, 8)

        # Draw avatar
        text_x = x + padding
        if self._effective_show_avatar() and item.nickname:
            self._draw_avatar(painter, text_x, y + padding + (total_h - padding * 2 - avatar_size) / 2,
                              avatar_size, item.nickname, item.avatar_url)
            text_x += avatar_size + gap

        # Draw nickname
        text_y = y + padding + nick_fm.ascent()
        if self.engine.show_nickname and item.nickname:
            painter.setPen(QPen(self._nickname_color()))
            painter.setFont(nick_font)
            painter.drawText(int(text_x), int(text_y), item.nickname)

        # Draw content (wrapped, preserving hard line breaks)
        text_y += nick_h + line_gap
        painter.setPen(QPen(self.theme["text"]))
        painter.setFont(content_font)
        content_color = self.theme["red_packet"] if item.is_red_packet else self.theme["text"]
        self._draw_text_block(painter, content_lines, text_x, text_y, content_color, content_fm)

        # Draw images below text
        if show_images:
            img_x = text_x
            img_y = text_y + content_h + padding
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if img_x + dw > text_x + text_w and img_x > text_x:
                    img_x = text_x
                    img_y += row_h + image_gap
                    row_h = 0
                self._draw_inline_image(painter, img_x, img_y, url, image_max_h)
                img_x += dw + image_gap
                row_h = max(row_h, dh)

        painter.setFont(self.font)
        return total_h

    def _draw_bubble(self, painter: QPainter, item: DanmuItem,
                      x: float, y: float, w: float, h: float) -> float:
        """Draw a bottom bubble. Returns actual height used."""
        padding = 10
        avatar_size = 24
        gap = 8
        image_max_h = 80
        image_gap = 6

        nick_font = QFont(self.font)
        nick_font.setPointSize(max(8, self.engine.font_size - 6))
        nick_fm = QFontMetrics(nick_font)

        content_font = QFont(self.font)
        content_font.setPointSize(max(9, self.engine.font_size - 4))
        content_fm = QFontMetrics(content_font)

        text, img_urls = self._parse_content(item.content)
        show_images = self._effective_show_image() and item.has_image and img_urls
        if item.has_image and not self._effective_show_image():
            text = "[图片] " + text

        max_lines = None
        if self.engine.truncate_long_messages:
            max_lines = self.engine.max_message_lines
        text_w = w - padding * 2 - (avatar_size + gap if self._effective_show_avatar() else 0)
        text_w = max(40, text_w)
        content_lines = self._wrap_text(text, content_fm, int(text_w), max_lines)
        total_raw = self._wrap_text(text, content_fm, int(text_w))
        if max_lines is not None and len(total_raw) > max_lines:
            content_lines.append("...")
        line_h = content_fm.height()
        content_h = len(content_lines) * line_h + (len(content_lines) - 1) * 2

        nick_h = nick_fm.height() if self.engine.show_nickname and item.nickname else 0
        total_h = padding * 2 + nick_h + 2 + content_h

        # Add image block height
        image_block_h = 0
        if show_images:
            row_x = 0
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if row_x + dw > text_w and row_x > 0:
                    image_block_h += row_h + image_gap
                    row_x = 0
                    row_h = 0
                row_x += dw + image_gap
                row_h = max(row_h, dh)
            image_block_h += row_h
            if image_block_h > 0:
                image_block_h += padding
            total_h += image_block_h

        total_h = max(total_h, avatar_size + padding * 2)

        painter.setPen(QPen(self.theme["card_border"]))
        painter.setBrush(self.theme["card_bg"])
        painter.drawRoundedRect(QRectF(x, y, w, total_h), 12, 12)

        text_x = x + padding
        if self._effective_show_avatar() and item.nickname:
            self._draw_avatar(painter, text_x, y + padding + (total_h - padding * 2 - avatar_size) / 2,
                              avatar_size, item.nickname, item.avatar_url)
            text_x += avatar_size + gap

        text_y = y + padding + nick_fm.ascent()
        if self.engine.show_nickname and item.nickname:
            painter.setPen(QPen(self._nickname_color()))
            painter.setFont(nick_font)
            painter.drawText(int(text_x), int(text_y), item.nickname)

        text_y += nick_h + 2
        painter.setPen(QPen(self.theme["text"]))
        painter.setFont(content_font)
        content_color = self.theme["red_packet"] if item.is_red_packet else self.theme["text"]
        self._draw_text_block(painter, content_lines, text_x, text_y, content_color, content_fm)

        # Draw images below text
        if show_images:
            img_x = text_x
            img_y = text_y + content_h + padding
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if img_x + dw > text_x + text_w and img_x > text_x:
                    img_x = text_x
                    img_y += row_h + image_gap
                    row_h = 0
                self._draw_inline_image(painter, img_x, img_y, url, image_max_h)
                img_x += dw + image_gap
                row_h = max(row_h, dh)

        painter.setFont(self.font)
        return total_h

    def add_message(self, msg: dict) -> None:
        """Add a new message to the engine and ensure render loop is running."""
        # Prefetch images so they are already loaded when the danmu becomes visible.
        avatar_url = msg.get("avatar_url", "")
        if avatar_url:
            self._image_cache.get(avatar_url)
        content = msg.get("content", "")
        if "<img" in content:
            _, img_urls = self._parse_content(content)
            for url in img_urls:
                self._image_cache.get(url)

        self.engine.add_message(msg)
        if self.isVisible() and self.visible_flag:
            self.start_render_loop()

    def clear_all(self) -> None:
        """Clear all danmu."""
        self.engine.clear_all()
        self._pixmaps.clear()
        self._has_animated_content = False
        self.update()

    def resizeEvent(self, event):
        """Update engine container size on resize."""
        super().resizeEvent(event)
        self.engine.set_container_size(
            float(self.width()), float(self.height()))
