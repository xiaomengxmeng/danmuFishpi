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
from dataclasses import dataclass
from typing import Optional, Union

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


@dataclass
class TextBlockLayout:
    """Layout result for one text block."""
    lines: list[str]
    y_offset: float
    height: float
    is_first_text: bool = False   # first text block merges avatar+nickname into line 0


@dataclass
class ImageBlockLayout:
    """Layout result for one image block (urls laid out horizontally)."""
    urls: list[str]
    y_offset: float
    height: float
    width: float                  # total width of the image row


@dataclass
class ScrollLayout:
    """Full layout for a scrolling danmu item, shared by measure + draw."""
    blocks: list[Union[TextBlockLayout, ImageBlockLayout]]
    total_h: float
    content_w: float
    prefix_w: float               # avatar + nickname width on the first line
    text_column_x: float          # left x for text rows / image blocks
    avatar_size: int
    nick_text: str
    padding: int


class DanmuOverlay(QWidget):
    """Transparent always-on-top overlay that renders danmu with QPainter."""

    def __init__(self, engine: DanmuEngine):
        super().__init__()

        self.engine = engine
        engine.set_overlay(self)  # let the engine query item-size estimates

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
        # setPixelSize makes the font size DPI-independent: the same number of
        # physical pixels on every screen. This guarantees QFontMetrics height
        # matches the actual rendered text height inside QPixmap, preventing
        # clipping on mixed-DPI multi-monitor setups (e.g. 125% + 100%).
        self.font = QFont("Microsoft YaHei")
        self.font.setPixelSize(engine.font_size)
        self.font.setBold(True)
        self.font_metrics = QFontMetrics(self.font, self)
        try:
            self._metrics_screen = self.screen()
        except (RuntimeError, AttributeError):
            self._metrics_screen = None

        # Rebuild metrics + clear pixmap cache when moving to a screen with a
        # different DPI (e.g. dragging the overlay or switching display_screen).
        # QWidget.screenChanged is not exposed in PyQt6, so we attach to the
        # underlying QWindow.screenChanged lazily in showEvent (the QWindow is
        # only available after the widget is shown).
        self._win_screen_signal_attached = False
        self._metrics_screen = None  # screen QFontMetrics was built for

        # Theme
        self.theme = THEME_DARK
        self.click_through = True
        self.visible_flag = True  # Toggle danmu visibility
        self.show_outline = True  # Text outline effect
        self.nickname_color: str = ""  # 默认昵称颜色(#RRGGBB)，空=跟随主题默认

        # Animation timer
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self._topmost_timer: QTimer | None = None
        self._topmost_fail_streak = 0

        self._tick_clock = QElapsedTimer()
        self._last_tick_valid = False
        self.last_dt = FRAME_DT

        # Pixmap cache is stored directly on each DanmuItem (item.pixmap).
        # The previous id(item)-keyed dict caused visual duplication when
        # Python reused a garbage-collected item's memory address for a new
        # item, returning the stale pixmap. Per-item storage eliminates this
        # bug and avoids unbounded dict growth.

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

    def _invalidate_pixmaps(self) -> None:
        """Clear cached pixmaps on all active scrolling items.

        Called when images load, config changes, or screen DPI changes —
        anything that would make a previously rendered pixmap stale.
        """
        for item in self.engine.scroll_items:
            item.pixmap = None
        for item in self.engine.queued_items:
            item.pixmap = None

    def _on_image_loaded(self, url: str) -> None:
        """Invalidate scrolling pixmap cache when any image finishes loading."""
        self._invalidate_pixmaps()
        # If the new image is a GIF, start the animation clock
        if self._image_cache.is_animated(url):
            self._start_anim_clock()
            self._has_animated_content = True
        self.update()

    def update_config(self, display_config: dict, theme: str) -> None:
        """Update overlay settings from config."""
        self.engine.update_config(display_config)
        font_family = display_config.get("fontFamily", "Microsoft YaHei") or "Microsoft YaHei"
        self.font = QFont(font_family)
        self.font.setPixelSize(self.engine.font_size)
        self.font.setBold(True)
        self.font_metrics = QFontMetrics(self.font, self)
        try:
            self._metrics_screen = self.screen()
        except (RuntimeError, AttributeError):
            self._metrics_screen = None
        self.theme = THEME_LIGHT if theme == "light" else THEME_DARK
        self.show_outline = display_config.get("showOutline", True)
        self.nickname_color = display_config.get("nicknameColor") or ""
        # Clear pixmap cache (font/size changed)
        self._invalidate_pixmaps()
        self._has_animated_content = False
        self.update()

    def _on_screen_changed(self, _screen) -> None:
        """Rebuild font metrics and clear pixmap cache when the overlay's screen changes.

        Different screens may have different logical DPI. QFontMetrics bound to
        ``self`` uses the overlay's current screen DPI, so it must be rebuilt on
        move; otherwise cached pixmaps sized with the old screen's metrics clip
        the text rendered at the new screen's DPI.
        """
        self._rebuild_metrics_for_current_screen(force=True)

    def _rebuild_metrics_for_current_screen(self, *, force: bool = False) -> None:
        """Rebuild QFontMetrics if the overlay is now on a different screen.

        With ``force=True`` (e.g. the QWindow.screenChanged signal fired) the
        rebuild + pixmap cache clear always happens. Otherwise the rebuild is
        skipped when the screen has not changed, avoiding needless cache churn
        on repeated showEvent calls.
        """
        try:
            current = self.screen()
        except (RuntimeError, AttributeError):
            current = None
        if not force and current is not None and current is self._metrics_screen:
            return
        self.font_metrics = QFontMetrics(self.font, self)
        self._metrics_screen = current
        self._invalidate_pixmaps()
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

    def _nickname_color(self, item) -> QColor:
        """Resolve the nickname color for an item.

        Priority: per-user danmu color (nickname matches the danmu color) >
        configurable default nickname color > theme default (follows the text
        color in simple mode).
        """
        if item is not None and item.color:
            c = QColor(item.color)
            if c.isValid():
                return c
        if self.nickname_color:
            c = QColor(self.nickname_color)
            if c.isValid():
                return c
        return self.theme["text"] if self.engine.simple_mode else self.theme["nickname"]

    def _danmu_text_color(self, item) -> QColor:
        """Resolve the danmu content color for an item.

        A user-configured color (item.color, #RRGGBB) wins when valid;
        otherwise fall back to the red-packet color or the theme default
        text color ("跟随系统").
        """
        if item.color:
            c = QColor(item.color)
            if c.isValid():
                return c
        return self.theme["red_packet"] if item.is_red_packet else self.theme["text"]

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
        self._attach_window_screen_signal()
        # By show() time the widget has reached its target screen (setGeometry
        # runs before show() in the init path). Rebuild metrics for that screen
        # in case it differs from the primary screen the metrics were built on.
        self._rebuild_metrics_for_current_screen()

    def _attach_window_screen_signal(self) -> None:
        """Connect to QWindow.screenChanged once the QWindow exists.

        QWidget.screenChanged is not exposed in PyQt6, so we reach the
        underlying QWindow (available only after show()) and connect there.
        This fires when the overlay moves to a screen with a different DPI.
        """
        if self._win_screen_signal_attached:
            return
        try:
            wh = self.windowHandle()
        except (RuntimeError, AttributeError):
            return
        if wh is None:
            return
        try:
            wh.screenChanged.connect(self._on_screen_changed)
            self._win_screen_signal_attached = True
        except (AttributeError, TypeError) as e:
            logger.debug(f"Could not attach QWindow.screenChanged: {e}")

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

        # Update positions / cleanup per mode
        if self.engine.mode == "scrolling":
            self.engine.update_scrolling()
        elif self.engine.mode == "floating":
            self.engine.cleanup_floating()

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

        max_width = int(self.engine.playfield_width * 0.6)
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
                         color: QColor, font_metrics: QFontMetrics | None = None,
                         line_gap: int = 0) -> None:
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
            cur_y += line_h + line_gap

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
            font.setPixelSize(int(size * 0.5))
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 255, 255)))
            fm = QFontMetrics(font, self)
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

    def _parse_segments(self, content: str) -> list:
        """Return ordered content blocks preserving <img> position in the stream.

        Consecutive <img> tags collapse into one ImageBlock (urls list).
        Text between/around images becomes TextBlock(str). Other HTML tags
        are stripped from text. Returns [] for empty content.

        Examples:
            "hi<img src=u>" -> [TextBlock("hi"), ImageBlock(["u"])]
            "<img src=u>hi" -> [ImageBlock(["u"]), TextBlock("hi")]
            "<img src=a>x<img src=b>" -> [ImageBlock(["a"]), TextBlock("x"), ImageBlock(["b"])]
        """
        import re

        class TextBlock:
            __slots__ = ("text",)
            def __init__(self, text): self.text = text

        class ImageBlock:
            __slots__ = ("urls",)
            def __init__(self, urls): self.urls = urls

        blocks: list = []
        pending_urls: list[str] = []
        pending_text: list[str] = []

        def _flush_text():
            if pending_text:
                t = re.sub(r'<[^>]+>', '', "".join(pending_text)).strip()
                if t:
                    blocks.append(TextBlock(t))
                pending_text.clear()

        def _flush_imgs():
            if pending_urls:
                blocks.append(ImageBlock(list(pending_urls)))
                pending_urls.clear()

        # Walk tokens: <img ...> vs everything else.
        for m in re.finditer(r'(?is)(<img[^>]*>)|([^<]+)', content):
            if m.group(1):  # an <img> tag
                _flush_text()
                src = re.search(r'(?is)src=["\']([^"\']+)["\']', m.group(1))
                if src and src.group(1):
                    pending_urls.append(src.group(1))
            else:  # text run
                _flush_imgs()
                pending_text.append(m.group(2))
        _flush_text()
        _flush_imgs()
        return blocks

    def _layout_scrolling(self, item: DanmuItem) -> ScrollLayout:
        """Compute the full vertical layout of a scrolling danmu item.

        Shared by estimate_scrolling_size() and _get_item_pixmap() so the two
        never drift apart. Layout rules (see spec §4.2):
          - blocks in original message order (images may be above or below text)
          - avatar+nickname merge into the FIRST text block's first line
          - subsequent text lines and all image blocks left-align to text_column_x
        """
        max_width = int(self.engine.playfield_width * 0.6)
        avatar_size = int(self.engine.font_size * 1.4)
        padding = 12
        line_gap = 4
        image_max_h = 80
        image_gap = 6
        prefix_gap = 8  # gap between avatar and nickname, nickname and text

        blocks_in = self._parse_segments(item.content)
        show_images = self._effective_show_image() and item.has_image
        show_avatar = self._effective_show_avatar() and bool(item.nickname)

        # Prefix width (avatar + nickname) on the first line.
        nick_text = ""
        if self.engine.show_nickname and item.nickname:
            nick_text = item.nickname + ": "
        prefix_w = 0
        if show_avatar:
            prefix_w += avatar_size + prefix_gap
        if nick_text:
            prefix_w += self.font_metrics.horizontalAdvance(nick_text) + prefix_gap

        text_column_x = float(padding + prefix_w)
        text_avail_w = max(100, int(max_width - padding * 2 - prefix_w))

        # Truncation cap (total text lines across all text blocks combined).
        max_lines = None
        if self.engine.truncate_long_messages:
            max_lines = self.engine.max_message_lines

        line_h = self.font_metrics.height()

        out_blocks: list = []
        cur_y = 0.0
        text_lines_used = 0
        first_text_seen = False
        longest_w = 0.0

        for b in blocks_in:
            if hasattr(b, "text"):
                # Text block
                raw_lines = self._wrap_text(b.text, self.font_metrics, text_avail_w)
                remaining = None
                if max_lines is not None:
                    remaining = max(0, max_lines - text_lines_used)
                    if remaining == 0:
                        # No more text budget: drop this block entirely.
                        continue
                    lines = raw_lines[:remaining]
                    if len(raw_lines) > remaining:
                        # Replace last kept line with a truncated marker.
                        if lines:
                            lines = lines[:-1] + [lines[-1].rstrip() + " ..."]
                        else:
                            lines = ["..."]
                else:
                    lines = raw_lines
                text_lines_used += len(lines)
                bh = len(lines) * line_h + max(0, len(lines) - 1) * line_gap
                is_first = not first_text_seen
                first_text_seen = True
                out_blocks.append(TextBlockLayout(
                    lines=lines, y_offset=cur_y, height=bh, is_first_text=is_first))
                # Track widest line. All content (text rows + image blocks)
                # starts at text_col_x = padding + prefix_w, so the span from
                # the pixmap's left padding to the content's right edge is
                # prefix_w + content_width for EVERY row (not just the first).
                for i, ln in enumerate(lines):
                    lw = self.font_metrics.horizontalAdvance(ln)
                    longest_w = max(longest_w, prefix_w + lw)
                cur_y += bh + line_gap
            else:
                # Image block
                if not show_images:
                    continue
                urls = b.urls[:3]
                if not urls:
                    continue
                row_w = 0.0
                row_h = 0.0
                measured: list[tuple[str, int, int]] = []
                for url in urls:
                    cached = self._image_cache.get(url)
                    if cached and not cached.isNull():
                        scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                        dw = int(cached.width() * scale)
                        dh = int(cached.height() * scale)
                    else:
                        dw = dh = image_max_h
                    if row_w + dw > text_avail_w and row_w > 0:
                        # would overflow; but we keep single row for simplicity
                        # (matches prior behavior: 3 images in a row max)
                        pass
                    measured.append((url, dw, dh))
                    row_w += dw + image_gap
                    row_h = max(row_h, dh)
                row_w = max(0, row_w - image_gap)  # drop trailing gap
                bh = row_h + padding  # gap below image block
                out_blocks.append(ImageBlockLayout(
                    urls=urls, y_offset=cur_y, height=bh, width=row_w))
                longest_w = max(longest_w, prefix_w + row_w)
                cur_y += bh + line_gap

        # Drop trailing gap, add bottom padding.
        if out_blocks:
            cur_y -= line_gap
        total_h = cur_y + padding  # bottom padding (top handled by draw offset)

        content_w = max(1, int(min(max_width, longest_w + padding * 2)))

        return ScrollLayout(
            blocks=out_blocks, total_h=total_h, content_w=content_w,
            prefix_w=prefix_w, text_column_x=text_column_x,
            avatar_size=avatar_size, nick_text=nick_text, padding=padding,
        )

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

    def estimate_scrolling_size(self, item: DanmuItem) -> tuple[float, float]:
        """Estimate (width, height) of a scrolling item's rendered pixmap.

        Delegates to _layout_scrolling so the estimate is identical to what
        _get_item_pixmap actually draws (no drift -> no overlap).
        """
        lay = self._layout_scrolling(item)
        if not lay.blocks:
            # No measurable content (e.g. empty). Return a minimal size so the
            # engine still places it without blocking tracks forever.
            min_w = max(1, int(self.engine.font_size * 2))
            min_h = self.font_metrics.height()
            item.width = float(min_w)
            item.height = float(min_h)
            return float(min_w), float(min_h)
        item.width = float(lay.content_w)
        item.height = float(lay.total_h)
        return float(lay.content_w), float(lay.total_h)

    def _get_item_pixmap(self, item: DanmuItem) -> QPixmap:
        """Get or create a pre-rendered pixmap for a scrolling item.

        Layout comes from _layout_scrolling (shared with estimate) so the
        drawn pixmap matches the estimated size exactly. Items with animated
        GIFs are redrawn every frame (not cached).
        """
        lay = self._layout_scrolling(item)
        if not lay.blocks or lay.content_w <= 0 or lay.total_h <= 0:
            return QPixmap()

        # Detect animated GIF across all image blocks.
        has_gif = False
        for b in lay.blocks:
            if hasattr(b, "urls"):
                for url in b.urls:
                    if self._image_cache.is_animated(url):
                        has_gif = True
                        break
                if has_gif:
                    break

        if not has_gif:
            if item.pixmap is not None:
                return item.pixmap

        item.width = float(lay.content_w)
        item.height = float(lay.total_h)

        dpr = self.devicePixelRatio()
        target_pm = QPixmap(int(lay.content_w * dpr), int(lay.total_h * dpr))
        target_pm.setDevicePixelRatio(dpr)
        target_pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(target_pm)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            p.setFont(self.font)

            padding = lay.padding
            text_col_x = lay.text_column_x
            line_h = self.font_metrics.height()
            line_gap = 4
            ascent = self.font_metrics.ascent()
            text_color = self._danmu_text_color(item)
            nick_color = self._nickname_color(item)
            show_avatar = self._effective_show_avatar() and bool(item.nickname)

            avatar_drawn = False
            nick_drawn = False

            for b in lay.blocks:
                if hasattr(b, "lines"):  # TextBlockLayout
                    y = b.y_offset
                    for i, ln in enumerate(b.lines):
                        line_y = y + i * (line_h + line_gap) + ascent
                        if b.is_first_text and i == 0:
                            # First text line: draw avatar + nickname + text inline.
                            cur_x = padding
                            if show_avatar and not avatar_drawn:
                                self._draw_avatar(p, cur_x, y + 2,
                                                  lay.avatar_size, item.nickname, item.avatar_url)
                                cur_x += lay.avatar_size + 8
                                avatar_drawn = True
                            if lay.nick_text and not nick_drawn:
                                self._draw_text_with_outline(p, lay.nick_text, cur_x, line_y, nick_color)
                                cur_x += self.font_metrics.horizontalAdvance(lay.nick_text) + 8
                                nick_drawn = True
                            self._draw_text_with_outline(p, ln, text_col_x, line_y, text_color)
                        else:
                            self._draw_text_with_outline(p, ln, text_col_x, line_y, text_color)
                else:  # ImageBlockLayout
                    img_y = b.y_offset
                    img_x = text_col_x
                    for url in b.urls:
                        dw, dh = self._draw_inline_image(p, img_x, img_y, url, 80)
                        img_x += dw + 6
        finally:
            p.end()

        if not has_gif:
            item.pixmap = target_pm
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
        # Coerce to int: PyQt6's drawText(int, int, str) overload rejects floats,
        # and callers may pass float y_offsets from _layout_scrolling.
        x = int(x)
        y = int(y)
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
            # drawPixmap(QPointF, QPixmap) draws the ENTIRE pixmap at the target
            # point, letting Qt handle devicePixelRatio correctly. The previous
            # 3-arg form used QRectF(0,0, w/dpr, h/dpr) as the source rect, but
            # Qt6 expects source rects in device pixels (physical), not logical
            # pixels — so on DPR>1 screens the source rect was too small and the
            # bottom of multi-line text was clipped.
            painter.drawPixmap(QPointF(item.x, item.y), pm)

    def _find_free_y(self, occupied: list[tuple[float, float]], candidate_top: float,
                     card_h: float, h: float, *, from_top: bool, margin: float) -> Optional[float]:
        """Find a top-y at/near candidate_top that avoids overlap with placed
        cards and stays within the screen.

        Returns the adjusted top y, or None if the card cannot fit.
        """
        top = candidate_top
        bottom = top + card_h
        gap = 6  # breathing room between stacked cards

        # Keep within screen bounds; if it doesn't fit there is no room.
        if top < margin or bottom > h - margin:
            return None

        # Nudge away from any overlap, then re-validate against ALL intervals.
        # Re-scanning after each nudge guarantees no cross-card collision is
        # missed (the previous single-pass version could leave residual overlap
        # when a tall card was shifted into another card's band).
        changed = True
        while changed:
            changed = False
            for (y0, y1) in occupied:
                if bottom <= y0 or top >= y1:
                    continue
                if from_top:
                    top = y1 + gap
                    bottom = top + card_h
                else:
                    bottom = y0 - gap
                    top = bottom - card_h
                if top < margin or top + card_h > h - margin:
                    return None
                changed = True
                break  # geometry changed; restart the scan

        return top

    def _paint_floating(self, painter: QPainter) -> None:
        """Paint floating-mode cards at the configured corner.

        Newest items are anchored nearest the corner so they are always visible;
        older items stack outward. A collision check guarantees cards never
        overlap, and once the screen is full the remaining (oldest) items are
        dropped rather than stacked off-screen. Each card fades out in its final
        second.
        """
        margin = 12
        card_w = float(self.engine.floating_card_width)
        card_w = min(card_w, self.width() - margin * 2)
        card_gap = 6
        fade_window = 1.0  # seconds for fade-out at end of life

        items = self.engine.float_items
        if not items:
            return

        now = time.time()
        corner = self.engine.floating_corner
        w = self.width()
        h = self.height()

        is_top = corner in ("topLeft", "topRight")
        is_left = corner in ("topLeft", "bottomLeft")

        # Newest first so the corner-most slot always holds the latest message.
        ordered = sorted(items, key=lambda it: it.add_time, reverse=True)

        occupied: list[tuple[float, float]] = []  # (y_top, y_bottom) intervals

        if is_top:
            y = margin
            for item in ordered:
                layout = self._compute_card_layout(item, card_w, self.engine.floating_font_size)
                card_h = layout["total_h"]
                top_y = self._find_free_y(occupied, y, card_h, h, from_top=True, margin=margin)
                if top_y is None:
                    break  # screen full; drop remaining (older) items
                x = margin if is_left else w - card_w - margin
                self._draw_card_with_fade(painter, item, x, top_y, card_w, layout, now, fade_window)
                occupied.append((top_y, top_y + card_h))
                y = top_y + card_h + card_gap
        else:
            y = h - margin
            for item in ordered:
                layout = self._compute_card_layout(item, card_w, self.engine.floating_font_size)
                card_h = layout["total_h"]
                top_y = self._find_free_y(occupied, y - card_h, card_h, h, from_top=False, margin=margin)
                if top_y is None:
                    break  # screen full; drop remaining (older) items
                x = margin if is_left else w - card_w - margin
                self._draw_card_with_fade(painter, item, x, top_y, card_w, layout, now, fade_window)
                occupied.append((top_y, top_y + card_h))
                y = top_y - card_gap

    def _draw_card_with_fade(self, painter: QPainter, item: DanmuItem,
                             x: float, y: float, w: float, layout: dict,
                             now: float, fade_window: float) -> None:
        """Draw a floating card, applying fade-out during its final second."""
        remaining = self.engine.floating_dwell_seconds - (now - item.add_time)
        card_fade = 1.0
        if remaining < fade_window:
            card_fade = max(0.0, remaining / fade_window)
        if card_fade <= 0.0:
            return
        painter.save()
        painter.setOpacity(painter.opacity() * card_fade)
        self._draw_card(painter, item, x, y, w, layout)
        painter.restore()

    def _compute_card_layout(self, item: DanmuItem, w: float,
                             font_size: int) -> dict:
        """Measure and compute the floating-card layout (vertical, forum-style).

        Vertical stack (top→bottom):
            [ avatar ]
            [ nickname ]
            [ text …  ]
            [ image ][ image ]

        Returns a dict with all geometry metrics and fonts so the card can be
        drawn (and its height measured for stacking) without duplication. The
        height is computed from every segment, so stacked cards never overlap.
        """
        padding = 10
        avatar_size = max(24, int(font_size * 1.2))
        avatar_gap = 6
        nick_gap = 4
        line_gap = 1
        image_max_h = 60
        image_gap = 6

        show_avatar = self._effective_show_avatar() and bool(item.nickname)

        nick_font = QFont(self.font)
        nick_font.setPixelSize(max(8, font_size - 4))
        nick_fm = QFontMetrics(nick_font, self)

        content_font = QFont(self.font)
        content_font.setPixelSize(max(8, font_size))
        content_fm = QFontMetrics(content_font, self)

        text, img_urls = self._parse_content(item.content)
        show_images = self._effective_show_image() and item.has_image and img_urls
        if item.has_image and not self._effective_show_image():
            text = "[图片] " + text

        # Measure content inside card — truncate if setting enabled
        max_lines = None
        if self.engine.truncate_long_messages:
            max_lines = self.engine.max_message_lines
        text_w = max(40, int(w - padding * 2))
        content_lines = self._wrap_text(text, content_fm, text_w, max_lines)
        total_raw = self._wrap_text(text, content_fm, text_w)
        is_truncated = max_lines is not None and len(total_raw) > max_lines
        if is_truncated:
            content_lines.append("...")

        line_h = content_fm.height()
        content_h = len(content_lines) * line_h + (len(content_lines) - 1) * line_gap

        nick_h = nick_fm.height() if (self.engine.show_nickname and item.nickname) else 0

        # Image block height (full card width)
        image_block_h = 0
        have_loaded_images = False
        if show_images:
            row_x = 0
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    have_loaded_images = True
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
                image_block_h += nick_gap  # gap between text and images

        # Total height: every segment stacked vertically + outer padding.
        seg = padding
        if show_avatar:
            seg += avatar_size + avatar_gap
        seg += nick_h
        if nick_h > 0:
            seg += nick_gap
        seg += content_h
        seg += image_block_h
        total_h = seg + padding

        return {
            "padding": padding,
            "avatar_size": avatar_size,
            "avatar_gap": avatar_gap,
            "nick_gap": nick_gap,
            "line_gap": line_gap,
            "image_max_h": image_max_h,
            "image_gap": image_gap,
            "show_avatar": show_avatar,
            "nick_font": nick_font,
            "nick_fm": nick_fm,
            "content_font": content_font,
            "content_fm": content_fm,
            "content_lines": content_lines,
            "content_h": content_h,
            "nick_h": nick_h,
            "image_block_h": image_block_h,
            "text_w": text_w,
            "img_urls": img_urls,
            "show_images": show_images,
            "have_loaded_images": have_loaded_images,
            "total_h": total_h,
        }

    def _draw_card(self, painter: QPainter, item: DanmuItem,
                   x: float, y: float, w: float, layout: dict) -> None:
        """Draw a floating card using a precomputed layout dict (vertical)."""
        padding = layout["padding"]
        avatar_size = layout["avatar_size"]
        avatar_gap = layout["avatar_gap"]
        nick_gap = layout["nick_gap"]
        line_gap = layout["line_gap"]
        image_max_h = layout["image_max_h"]
        image_gap = layout["image_gap"]
        show_avatar = layout["show_avatar"]
        nick_font = layout["nick_font"]
        nick_fm = layout["nick_fm"]
        content_font = layout["content_font"]
        content_fm = layout["content_fm"]
        content_lines = layout["content_lines"]
        content_h = layout["content_h"]
        nick_h = layout["nick_h"]
        image_block_h = layout["image_block_h"]
        text_w = layout["text_w"]
        img_urls = layout["img_urls"]
        show_images = layout["show_images"]
        have_loaded_images = layout["have_loaded_images"]
        total_h = layout["total_h"]

        # Card background/border
        painter.setPen(QPen(self.theme["card_border"]))
        painter.setBrush(self.theme["card_bg"])
        painter.drawRoundedRect(QRectF(x, y, w, total_h), 8, 8)

        content_x = x + padding
        cur_y = y + padding

        # 1. Avatar (top, left-aligned)
        if show_avatar:
            self._draw_avatar(painter, content_x, cur_y, avatar_size, item.nickname, item.avatar_url)
            cur_y += avatar_size + avatar_gap

        # 2. Nickname (below avatar)
        if self.engine.show_nickname and item.nickname:
            painter.setPen(QPen(self._nickname_color(item)))
            painter.setFont(nick_font)
            painter.drawText(int(content_x), int(cur_y + nick_fm.ascent()), item.nickname)
            cur_y += nick_fm.height() + nick_gap

        # 3. Content text (below nickname)
        text_y = cur_y + content_fm.ascent()
        content_color = self._danmu_text_color(item)
        painter.setPen(QPen(content_color))
        painter.setFont(content_font)
        self._draw_text_block(painter, content_lines, content_x, text_y, content_color, content_fm, line_gap)
        cur_y += content_h

        # 4. Images (below text)
        if show_images and have_loaded_images:
            img_x = content_x
            img_y = cur_y
            row_h = 0
            for url in img_urls[:3]:
                cached = self._image_cache.get(url)
                if cached and not cached.isNull():
                    scale = min(image_max_h / cached.height(), image_max_h / cached.width(), 1.0)
                    dw = int(cached.width() * scale)
                    dh = int(cached.height() * scale)
                else:
                    dw = dh = image_max_h
                if img_x + dw > content_x + text_w and img_x > content_x:
                    img_x = content_x
                    img_y += row_h + image_gap
                    row_h = 0
                self._draw_inline_image(painter, img_x, img_y, url, image_max_h)
                img_x += dw + image_gap
                row_h = max(row_h, dh)
            cur_y += image_block_h

        painter.setFont(self.font)

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

        item = self.engine.add_message(msg)
        # Pre-compute the estimated height so the engine can pack scrolling
        # items without overlap even before the pixmap is measured.
        if item is not None and item.height <= 0:
            try:
                _w, item.height = self.estimate_scrolling_size(item)
            except Exception:
                logger.debug("pre-estimate height failed", exc_info=True)
        if self.isVisible() and self.visible_flag:
            self.start_render_loop()

    def clear_all(self) -> None:
        """Clear all danmu."""
        self.engine.clear_all()
        self._has_animated_content = False
        self.update()

    def resizeEvent(self, event):
        """Update engine container size on resize."""
        super().resizeEvent(event)
        self.engine.set_container_size(
            float(self.width()), float(self.height()))
