"""Danmu animation engine: track management and position calculation.

Handles two display modes:
- scrolling: multi-track horizontal scroll (right to left)
- floating: corner card list with per-message dwell timeout + fade-out
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("danmuFishpi.engine")


@dataclass
class DanmuItem:
    """A single danmu message being displayed."""
    nickname: str
    avatar_url: str
    content: str          # May contain HTML (e.g. <img> tags)
    has_image: bool
    is_red_packet: bool = False
    color: Optional[str] = None   # 用户自定义弹幕颜色 (#RRGGBB)，None = 跟随主题默认色

    # Scrolling mode fields
    track_index: int = -1
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0     # Pre-measured total width
    height: float = 0.0
    start_time: float = 0.0
    duration: float = 14.0  # seconds

    # Floating/bottom mode fields
    add_time: float = 0.0
    pixmap = None           # Cached pre-rendered pixmap (set by overlay)


@dataclass
class Track:
    """A horizontal lane for scrolling danmu."""
    y: float = 0.0


class DanmuEngine:
    """Manages danmu items and their animation state.

    The engine is mode-agnostic for item storage; the overlay queries
    items and the engine calculates positions for scrolling mode.
    """

    def __init__(self):
        self.mode: str = "scrolling"
        self.font_size: int = 24
        self.font_family: str = "Microsoft YaHei"
        self.danmu_speed: int = 5
        self.danmu_area: str = "fullscreen"
        self.danmu_width: int = 100
        self.danmu_height: int = 100
        self.floating_corner: str = "topRight"
        self.show_avatar: bool = True
        self.show_nickname: bool = True
        self.show_image: bool = True
        self.simple_mode: bool = False
        self.truncate_long_messages: bool = True
        self.max_message_lines: int = 3
        self.opacity: int = 100
        self.top_margin: int = 0
        self.user_colors: dict[str, str] = {}  # 用户ID -> 弹幕颜色(#RRGGBB)

        # Floating mode settings
        self.floating_dwell_seconds: float = 8.0   # lifetime of each card (s)
        self.floating_max_items: int = 3           # max simultaneous cards
        self.floating_card_width: int = 240        # card width (px)
        self.floating_font_size: int = 16          # card font size (px)

        self.tracks: list[Track] = []
        self.scroll_items: list[DanmuItem] = []   # Active scrolling items
        self.float_items: list[DanmuItem] = []     # Floating items
        self.queued_items: list[DanmuItem] = []    # FIFO: waiting for a free track
        self.max_float: int = 30                   # hard cap to bound memory

        # Uniform scrolling speed (px/s); recomputed by update_config from danmu_speed.
        self._px_speed: float = 150.0

        self.overlay = None  # set by overlay; used to estimate item height

        self.container_width: float = 1920.0
        self.container_height: float = 1080.0

        # Scrolling playfield (computed from width/height/area/top-margin %)
        self.playfield_left: float = 0.0
        self.playfield_top: float = 0.0
        self.playfield_width: float = 1920.0
        self.playfield_height: float = 1080.0

    def set_overlay(self, overlay) -> None:
        """Wire the overlay so the engine can query item-height estimates."""
        self.overlay = overlay

    def update_config(self, display_config: dict) -> None:
        """Update engine settings from a display config dict."""
        self.mode = display_config.get("danmuMode", self.mode)
        self.font_size = display_config.get("fontSize", self.font_size)
        self.font_family = display_config.get("fontFamily", self.font_family)
        self.danmu_speed = display_config.get("danmuSpeed", self.danmu_speed)
        self.danmu_area = display_config.get("danmuArea", self.danmu_area)
        self.danmu_width = display_config.get("danmuWidth", self.danmu_width)
        self.danmu_height = display_config.get("danmuHeight", self.danmu_height)
        self.floating_corner = display_config.get("floatingCorner", self.floating_corner)
        self.show_avatar = display_config.get("showAvatar", self.show_avatar)
        self.show_nickname = display_config.get("showNickname", self.show_nickname)
        self.show_image = display_config.get("showImage", self.show_image)
        self.simple_mode = display_config.get("simpleMode", self.simple_mode)
        self.truncate_long_messages = display_config.get("truncateLongMessages", self.truncate_long_messages)
        self.max_message_lines = display_config.get("maxMessageLines", self.max_message_lines)
        self.opacity = display_config.get("danmuOpacity", self.opacity)
        self.top_margin = display_config.get("topMargin", self.top_margin)
        user_colors = display_config.get("userColors")
        if isinstance(user_colors, dict):
            self.user_colors = user_colors

        self.floating_dwell_seconds = display_config.get("floatingDwellSeconds", self.floating_dwell_seconds)
        self.floating_max_items = display_config.get("floatingMaxItems", self.floating_max_items)
        self.floating_card_width = display_config.get("floatingCardWidth", self.floating_card_width)
        self.floating_font_size = display_config.get("floatingFontSize", self.floating_font_size)
        self.max_float = max(self.floating_max_items, 8)

        # Uniform pixel speed from danmu_speed (0-100) -> 80..220 px/s.
        # Default danmu_speed=50 -> 150 px/s (close to prior feel).
        speed_pct = max(0, min(100, self.danmu_speed)) / 100.0
        self._px_speed = 80.0 + speed_pct * 140.0

        self.init_tracks()

    def set_container_size(self, w: float, h: float) -> None:
        self.container_width = w
        self.container_height = h
        self.init_tracks()

    # Area presets: (base_top_ratio, base_height_ratio) of the full container.
    _AREA_PRESETS = {
        "fullscreen": (0.0, 1.0),
        "top25":      (0.0, 0.25),
        "topHalf":    (0.0, 0.5),
        "bottomHalf": (0.5, 0.5),
        "bottom25":   (0.75, 0.25),
    }

    def _compute_playfield(self) -> None:
        """Recompute the scrolling playfield rectangle (container pixels).

        The playfield is the sub-region of the container in which scrolling
        danmu live. It is derived from:
          - danmu_area  : coarse vertical band (full / 25% / 50% top or bottom)
          - danmu_width : playfield width as % of container width (centered)
          - danmu_height: playfield height as % of the area band height
          - top_margin  : extra downward offset as % of container height
        """
        C_W = self.container_width
        C_H = self.container_height
        base_top_r, base_h_r = self._AREA_PRESETS.get(self.danmu_area, (0.0, 1.0))

        width_pct = max(1.0, min(100.0, self.danmu_width)) / 100.0
        height_pct = max(1.0, min(100.0, self.danmu_height)) / 100.0
        top_pct = max(0.0, min(100.0, self.top_margin)) / 100.0

        band_top = C_H * base_top_r
        band_h = C_H * base_h_r

        # Top margin is a % of the *band* height, so it only nudges the
        # playfield within the area band and can never push it off-screen.
        pf_height = band_h * height_pct
        pf_top = band_top + band_h * top_pct
        pf_width = C_W * width_pct
        pf_left = (C_W - pf_width) / 2.0

        # Keep the playfield within the container bounds and guarantee a
        # minimally usable height so danmu never disappear entirely.
        min_height = max(8.0, self.font_size * 1.6)
        if pf_height < min_height:
            pf_height = min_height
        if pf_top + pf_height > C_H:
            pf_top = max(0.0, C_H - pf_height)
        pf_width = max(1.0, min(pf_width, C_W))
        pf_left = (C_W - pf_width) / 2.0

        self.playfield_left = pf_left
        self.playfield_top = pf_top
        self.playfield_width = pf_width
        self.playfield_height = pf_height

    def init_tracks(self) -> None:
        """Initialize scrolling tracks based on font size and playfield."""
        self._compute_playfield()
        line_height = self.font_size * 1.6
        available_height = self.playfield_height
        track_count = max(1, min(int(available_height / line_height), 40))

        self.tracks = []
        for i in range(track_count):
            self.tracks.append(Track(y=self.playfield_top + i * line_height))

    def _estimate_size(self, item: DanmuItem) -> tuple[float, float]:
        """Estimate a scrolling item's (width, height) before it is measured.

        Uses the overlay's measurement (mirrors the real pixmap size) when
        available; otherwise falls back to a single-line size.
        """
        if self.overlay is not None:
            try:
                return self.overlay.estimate_scrolling_size(item)
            except Exception:
                logger.debug("size estimate failed; using fallback", exc_info=True)
        return float(self.playfield_width * 0.5), self.font_size * 1.6

    def _overlaps(self, rx: float, ry: float, rw: float, rh: float) -> bool:
        """Return True if rect (rx,ry,rw,rh) overlaps any active scrolling item.

        Unmeasured items (width/height <= 0) are treated as occupying the full
        container, so they correctly block placement instead of being skipped.
        """
        for it in self.scroll_items:
            iw = it.width if it.width > 0 else self.container_width
            ih = it.height if it.height > 0 else self.font_size * 1.6
            if (rx < it.x + iw and rx + rw > it.x
                    and ry < it.y + ih and ry + rh > it.y):
                return True
        return False

    def find_free_y(self, item: DanmuItem) -> Optional[float]:
        """Find a Y for a new scrolling item with zero 2D overlap.

        All scrolling items move left at the same speed, so their relative
        horizontal positions are constant for all time. A new item therefore
        only ever overlaps an existing one if they overlap at the moment it is
        placed (entering at the right edge). We scan Y top-down and pick the
        first band whose entry rect does not intersect any existing item. This
        guarantees no overlap regardless of how tall/multi-line the item is.

        Returns None when no free band exists at the entry edge; the caller
        decides whether to enqueue or drop the item.
        """
        est_w, est_h = self._estimate_size(item)
        gap = max(6.0, self.font_size * 0.3)
        step = max(8.0, self.font_size * 0.6)

        # Entry rect: item enters at the right edge of the playfield and
        # extends left by est_w. Inflate by `gap` for breathing room.
        playfield_right = self.playfield_left + self.playfield_width
        entry_left = playfield_right - (est_w + gap)
        bottom_limit = self.playfield_top + self.playfield_height

        y = float(self.playfield_top)
        while y + est_h <= bottom_limit:
            if not self._overlaps(entry_left, y - gap / 2, est_w + gap, est_h + gap):
                return y
            y += step

        # No free band at the entry edge: caller decides enqueue vs drop.
        return None

    def add_message(self, msg: dict) -> Optional[DanmuItem]:
        """Add a new message to the engine. Returns the created item or None."""
        item = DanmuItem(
            nickname=msg.get("nickname", ""),
            avatar_url=msg.get("avatar_url", ""),
            content=msg.get("content", ""),
            has_image=msg.get("has_image", False),
            is_red_packet=msg.get("is_red_packet", False),
            add_time=time.time(),
        )
        # 彩色弹幕：按 user_id（缺省回退 nickname）查用户自定义颜色
        uid = msg.get("user_id") or msg.get("nickname", "")
        if uid:
            item.color = self.user_colors.get(uid)

        logger.info(f"Engine add message from={item.nickname}: {item.content[:40]}")

        if self.mode == "floating":
            return self._add_floating(item)
        # scrolling (and any unknown mode) falls back to scrolling
        return self._add_scrolling(item)

    def _add_scrolling(self, item: DanmuItem) -> Optional[DanmuItem]:
        """Add item to scrolling mode; enqueue if no free track."""
        y = self.find_free_y(item)
        if y is None:
            self.queued_items.append(item)
            return item
        item.track_index = int(y)
        item.y = y
        item.start_time = time.time()
        # Duration from uniform speed: distance / v.
        item.duration = (self.playfield_width + item.width) / self._px_speed
        # Start position: right edge of the playfield.
        item.x = self.playfield_left + self.playfield_width
        self.scroll_items.append(item)
        return item

    def _add_floating(self, item: DanmuItem) -> DanmuItem:
        self.float_items.append(item)
        while len(self.float_items) > self.max_float:
            self.float_items.pop(0)
        return item

    def update_scrolling(self) -> None:
        """Update positions of scrolling items (uniform px/s) and remove off-screen.

        Then backfill the queue: any track freed this tick pulls the next
        queued item in (FIFO).
        """
        now = time.time()
        v = self._px_speed
        playfield_right = self.playfield_left + self.playfield_width
        alive = []
        for item in self.scroll_items:
            if item.width <= 0:
                # Pixmap not measured yet (e.g. image still loading). Hold the
                # start time so the item enters smoothly once width is known.
                item.start_time = now
                alive.append(item)
                continue

            elapsed = now - item.start_time
            # Uniform speed: x advances left by v*elapsed.
            item.x = playfield_right - v * elapsed

            if item.x + item.width < self.playfield_left:
                continue  # off-screen left, drop
            alive.append(item)

        self.scroll_items = alive
        self._backfill_queue()

    def _backfill_queue(self) -> None:
        """Pull queued items into freed tracks (FIFO). Stops at first failure."""
        if not self.queued_items:
            return
        now = time.time()
        playfield_right = self.playfield_left + self.playfield_width
        i = 0
        n = len(self.queued_items)
        while i < n:
            item = self.queued_items[i]
            item.start_time = now
            y = self.find_free_y(item)
            if y is None:
                break  # screen still full; this and rest stay queued
            item.track_index = int(y)
            item.y = y
            item.x = playfield_right
            item.duration = (self.playfield_width + item.width) / self._px_speed
            self.scroll_items.append(item)
            i += 1
        # Keep the failing item (at index i) and all unprocessed items after it.
        self.queued_items = self.queued_items[i:]

    def cleanup_floating(self) -> list[DanmuItem]:
        """Remove timed-out floating items and trim to max_items.

        Returns the list of removed items (useful for debugging/fade hooks).
        """
        if self.mode != "floating":
            return []
        now = time.time()
        removed: list[DanmuItem] = []
        alive: list[DanmuItem] = []
        for item in self.float_items:
            if now - item.add_time > self.floating_dwell_seconds:
                removed.append(item)
            else:
                alive.append(item)
        # Keep only the newest max_items (tail of the list)
        if len(alive) > self.floating_max_items:
            excess = len(alive) - self.floating_max_items
            removed.extend(alive[:excess])
            alive = alive[excess:]
        self.float_items = alive
        return removed

    def clear_all(self) -> None:
        """Clear all items (including queued)."""
        self.scroll_items.clear()
        self.float_items.clear()
        self.queued_items.clear()

    def has_content(self) -> bool:
        """Check if there are any items to render."""
        return len(self.scroll_items) > 0 or len(self.float_items) > 0
