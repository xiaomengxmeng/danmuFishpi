"""Asynchronous image download cache for avatars and inline message images.

Uses QNetworkAccessManager to fetch images in the background without blocking
the render loop. Completed downloads trigger a repaint via the loaded signal.

GIF images are decoded with QMovie into individual frames for animation support.
"""

import logging
from pathlib import Path
import hashlib
from urllib.parse import urlparse, unquote

from PyQt6.QtCore import QObject, pyqtSignal, QUrl, QByteArray, QBuffer
from PyQt6.QtGui import QPixmap, QMovie, QImage
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

logger = logging.getLogger("danmuFishpi.image_cache")

# GIF89a / GIF87a magic bytes
_GIF_MAGIC = (b"GIF89a", b"GIF87a")


def _is_gif(data: bytes) -> bool:
    """Check raw bytes for GIF magic."""
    return data[:6] in _GIF_MAGIC


class AnimatedImage:
    """Holds pre-decoded GIF frames for efficient per-frame rendering.

    Each frame is stored as a QPixmap. ``durations`` gives per-frame display
    time in milliseconds; ``total_duration`` is the sum for one loop.
    """

    def __init__(self, frames: list[QPixmap], durations: list[int]):
        self.frames = frames
        self.durations = durations  # ms per frame
        self.total_duration = sum(durations) if durations else 1

    def current_frame(self, elapsed_ms: int) -> QPixmap | None:
        """Return the QPixmap for the current loop time (loops forever)."""
        if not self.frames:
            return None
        t = elapsed_ms % self.total_duration
        acc = 0
        for i, dur in enumerate(self.durations):
            acc += dur
            if t < acc:
                return self.frames[i]
        return self.frames[-1]


class ImageCache(QObject):
    """Cache for remote images used by the danmu overlay."""

    loaded = pyqtSignal(str)  # URL that just finished loading

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._cache: dict[str, QPixmap] = {}
        self._animated: dict[str, AnimatedImage] = {}
        self._pending: set[str] = set()
        # on-disk cache directory (hidden .cache/images under the package)
        self._cache_dir = (
            Path(__file__).resolve().parent / ".cache" / "images"
        )
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            # If creation fails, fall back to in-memory only
            logger.debug("Could not create disk cache directory; using memory only")
        self._manager.finished.connect(self._on_finished)

    def _cache_path_for_url(self, url: str) -> Path | None:
        """Return a deterministic Path in the disk cache for the given URL.

        Uses SHA256(url) plus the URL's path suffix when available.
        Returns None if disk caching isn't available.
        """
        try:
            if not hasattr(self, "_cache_dir") or self._cache_dir is None:
                return None
            parsed = urlparse(url)
            suffix = Path(unquote(parsed.path)).suffix
            ext = suffix if suffix and len(suffix) <= 8 else ""
            name = hashlib.sha256(url.encode("utf-8")).hexdigest() + ext
            return self._cache_dir / name
        except Exception:
            return None

    def get(self, url: str) -> QPixmap | None:
        """Return cached pixmap or start downloading and return None.

        For animated GIFs this returns the *first frame* as a static fallback
        until ``is_animated`` + ``current_frame`` are used.
        """
        if not url:
            return None

        if url in self._cache:
            return self._cache[url]

        # Try loading from disk cache first
        try:
            path = self._cache_path_for_url(url)
            if path and path.exists():
                raw = path.read_bytes()
                # GIF handling
                if _is_gif(raw):
                    self._decode_gif(url, raw)
                    return self._cache.get(url)

                pixmap = QPixmap()
                if pixmap.loadFromData(QByteArray(raw)):
                    self._cache[url] = pixmap
                    logger.debug(f"Image loaded from disk cache: {url} -> {path}")
                    return pixmap
        except Exception as e:
            logger.debug(f"Disk cache load failed for {url}: {e}")

        if url not in self._pending:
            self._pending.add(url)
            request = QNetworkRequest(QUrl(url))
            request.setHeader(
                QNetworkRequest.KnownHeaders.UserAgentHeader,
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            self._manager.get(request)

        return None

    def is_animated(self, url: str) -> bool:
        """Whether the cached image is a GIF with animation frames."""
        return url in self._animated

    def current_frame(self, url: str, elapsed_ms: int) -> QPixmap | None:
        """Return the current frame pixmap for an animated GIF (loops forever)."""
        anim = self._animated.get(url)
        if anim is None:
            return None
        return anim.current_frame(elapsed_ms)

    def _on_finished(self, reply: QNetworkReply) -> None:
        url = reply.url().toString()
        self._pending.discard(url)

        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                logger.debug(f"Image download failed for {url}: {reply.errorString()}")
                reply.deleteLater()
                return

            data = reply.readAll()
            raw = bytes(data)

            # Persist raw bytes to disk cache if possible
            try:
                path = self._cache_path_for_url(url)
                if path is not None:
                    path.write_bytes(raw)
            except Exception as e:
                logger.debug(f"Failed to write disk cache for {url}: {e}")

            # Detect GIF and decode frames
            if _is_gif(raw):
                self._decode_gif(url, raw)
                self.loaded.emit(url)
                reply.deleteLater()
                return

            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._cache[url] = pixmap
                logger.debug(f"Image loaded: {url} ({pixmap.width()}x{pixmap.height()})")
                self.loaded.emit(url)
            else:
                logger.debug(f"Image decode failed: {url}")

            reply.deleteLater()
        except Exception as e:
            logger.error(f"Image cache _on_finished error for {url}: {e}")
            reply.deleteLater()

    def _decode_gif(self, url: str, data: bytes) -> None:
        """Decode a GIF into individual frames via QMovie."""
        try:
            movie = QMovie()
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            buf = QBuffer()
            buf.setData(QByteArray(data))
            movie.setDevice(buf)

            if not movie.isValid():
                logger.debug(f"GIF decode invalid: {url}")
                # Fallback: try loading as static
                pixmap = QPixmap()
                if pixmap.loadFromData(data):
                    self._cache[url] = pixmap
                return

            movie.jumpToFrame(0)
            frames: list[QPixmap] = []
            durations: list[int] = []

            frame_count = movie.frameCount()
            if frame_count <= 0:
                frame_count = 1

            for i in range(frame_count):
                movie.jumpToFrame(i)
                pm = movie.currentPixmap()
                if not pm.isNull():
                    frames.append(pm.copy())
                # nextFrameDelay returns ms between frames
                delay = movie.nextFrameDelay()
                # GIF specifies delay in 1/100 s units; QMovie returns ms.
                # Treat 0 as 100ms default (as browsers do).
                durations.append(delay if delay > 0 else 100)

            movie.stop()

            if frames:
                # Store first frame in static cache as fallback
                self._cache[url] = frames[0]
                self._animated[url] = AnimatedImage(frames, durations)
                logger.debug(
                    f"GIF loaded: {url} "
                    f"({frames[0].width()}x{frames[0].height()}, "
                    f"{len(frames)} frames, {sum(durations)}ms loop)"
                )
            else:
                logger.debug(f"GIF decode produced no frames: {url}")
        except Exception as e:
            logger.error(f"GIF decode error for {url}: {e}")
            # Fallback: static image
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self._cache[url] = pixmap

