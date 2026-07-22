"""Danmu animation engine: track management and position calculation.

Handles three display modes:
- scrolling: multi-track horizontal scroll (right to left)
- floating: right-side card list (max 15, no timeout)
- bottom: bottom-left bubble list (max 6, 8s timeout)
"""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DanmuItem:
    """A single danmu message being displayed."""
    nickname: str
    avatar_url: str
    content: str          # May contain HTML (e.g. <img> tags)
    has_image: bool

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
    last_item_time: float = 0.0


class DanmuEngine:
    """Manages danmu items and their animation state.

    The engine is mode-agnostic for item storage; the overlay queries
    items and the engine calculates positions for scrolling mode.
    """

    def __init__(self):
        self.mode: str = "scrolling"
        self.font_size: int = 24
        self.danmu_speed: int = 5
        self.danmu_area: str = "fullscreen"
        self.show_avatar: bool = True
        self.show_nickname: bool = True
        self.show_image: bool = True
        self.opacity: int = 100

        self.tracks: list[Track] = []
        self.scroll_items: list[DanmuItem] = []   # Active scrolling items
        self.float_items: list[DanmuItem] = []     # Floating/bottom items
        self.max_float: int = 15
        self.max_bottom: int = 6
        self.bottom_timeout: float = 8.0

        self.container_width: float = 1920.0
        self.container_height: float = 1080.0

    def update_config(self, display_config: dict) -> None:
        """Update engine settings from a display config dict."""
        self.mode = display_config.get("danmuMode", self.mode)
        self.font_size = display_config.get("fontSize", self.font_size)
        self.danmu_speed = display_config.get("danmuSpeed", self.danmu_speed)
        self.danmu_area = display_config.get("danmuArea", self.danmu_area)
        self.show_avatar = display_config.get("showAvatar", self.show_avatar)
        self.show_nickname = display_config.get("showNickname", self.show_nickname)
        self.show_image = display_config.get("showImage", self.show_image)
        self.opacity = display_config.get("danmuOpacity", self.opacity)
        self.init_tracks()

    def set_container_size(self, w: float, h: float) -> None:
        self.container_width = w
        self.container_height = h
        self.init_tracks()

    def init_tracks(self) -> None:
        """Initialize scrolling tracks based on font size and area."""
        line_height = self.font_size * 1.6
        available_height = self.container_height
        start_offset = 0.0

        if self.danmu_area == "topHalf":
            available_height = self.container_height / 2
            start_offset = 0.0
        elif self.danmu_area == "bottomHalf":
            available_height = self.container_height / 2
            start_offset = self.container_height / 2

        track_count = max(5, min(int(available_height / line_height), 20))

        self.tracks = []
        for i in range(track_count):
            self.tracks.append(Track(
                y=start_offset + i * line_height,
                last_item_time=0.0,
            ))

    def find_free_track(self) -> int:
        """Find a free track for a new scrolling item.

        Priority: track unused for >3s, else least-recently-used.
        Returns -1 if no tracks exist.
        """
        if not self.tracks:
            return -1

        now = time.time()
        best_track = -1
        best_time = 0.0

        for i, track in enumerate(self.tracks):
            elapsed = now - track.last_item_time
            if elapsed > 3.0:
                return i
            if elapsed > best_time:
                best_time = elapsed
                best_track = i

        return best_track

    def add_message(self, msg: dict) -> Optional[DanmuItem]:
        """Add a new message to the engine. Returns the created item or None."""
        item = DanmuItem(
            nickname=msg.get("nickname", ""),
            avatar_url=msg.get("avatar_url", ""),
            content=msg.get("content", ""),
            has_image=msg.get("has_image", False),
            add_time=time.time(),
        )

        if self.mode == "scrolling":
            return self._add_scrolling(item)
        elif self.mode == "floating":
            return self._add_floating(item)
        elif self.mode == "bottom":
            return self._add_bottom(item)
        return None

    def _add_scrolling(self, item: DanmuItem) -> Optional[DanmuItem]:
        """Add item to scrolling mode."""
        track_idx = self.find_free_track()
        if track_idx < 0:
            return None

        item.track_index = track_idx
        item.y = self.tracks[track_idx].y
        item.start_time = time.time()
        # Duration formula: 20 - (speed - 1) * 1.5
        item.duration = 20.0 - (self.danmu_speed - 1) * 1.5
        # Start position: right edge of container (x set by overlay after measuring width)
        item.x = self.container_width

        self.tracks[track_idx].last_item_time = time.time()
        self.scroll_items.append(item)
        return item

    def _add_floating(self, item: DanmuItem) -> DanmuItem:
        self.float_items.append(item)
        while len(self.float_items) > self.max_float:
            self.float_items.pop(0)
        return item

    def _add_bottom(self, item: DanmuItem) -> DanmuItem:
        self.float_items.append(item)
        while len(self.float_items) > self.max_bottom:
            self.float_items.pop(0)
        return item

    def update_scrolling(self) -> None:
        """Update positions of scrolling items and remove off-screen ones."""
        now = time.time()
        alive = []
        for item in self.scroll_items:
            if item.width <= 0:
                # Width not yet measured; keep alive
                alive.append(item)
                continue

            elapsed = now - item.start_time
            progress = elapsed / item.duration
            # Move from container_width to -item.width
            total_distance = self.container_width + item.width
            item.x = self.container_width - progress * total_distance

            if item.x + item.width < 0:
                # Off-screen left, remove
                continue
            alive.append(item)

        self.scroll_items = alive

    def cleanup_bottom(self) -> list[DanmuItem]:
        """Remove timed-out bottom items. Returns list of removed items."""
        if self.mode != "bottom":
            return []
        now = time.time()
        removed = []
        alive = []
        for item in self.float_items:
            if now - item.add_time > self.bottom_timeout:
                removed.append(item)
            else:
                alive.append(item)
        self.float_items = alive
        return removed

    def clear_all(self) -> None:
        """Clear all items."""
        self.scroll_items.clear()
        self.float_items.clear()
        for track in self.tracks:
            track.last_item_time = 0.0

    def has_content(self) -> bool:
        """Check if there are any items to render."""
        return len(self.scroll_items) > 0 or len(self.float_items) > 0
