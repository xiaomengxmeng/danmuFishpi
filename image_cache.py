"""Asynchronous image download cache for avatars and inline message images.

Uses QNetworkAccessManager to fetch images in the background without blocking
the render loop. Completed downloads are persisted to a disk cache
(%APPDATA%/DanmuFishpi/img_cache/) so restarts and reconnects do not
re-download the same images.

Memory is bounded: decoded pixmaps are kept in a small LRU (the disk cache is
the real backing store, so evicted images re-decode from disk on demand).
Large images are decoded downscaled (longest side <= 512px) since the overlay
only ever renders them at ~80px; the disk cache keeps the original bytes.
GIF frames are decoded via QMovie for animation and capped in count.

GIF images are decoded with QMovie into individual frames for animation support.

Disk cache: file name = sha1(url).<real-ext>, LRU eviction by mtime, capped at
800 MB / 50,000 files. Any disk failure degrades gracefully to memory-only.
"""

import hashlib
import logging
import os
from collections import OrderedDict

from PyQt6.QtCore import (QObject, pyqtSignal, QUrl, QByteArray, QBuffer, QSize, QIODevice)
from PyQt6.QtGui import QPixmap, QMovie, QImage, QImageReader
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

logger = logging.getLogger("danmuFishpi.image_cache")

# GIF89a / GIF87a magic bytes
_GIF_MAGIC = (b"GIF89a", b"GIF87a")

# Disk cache limits. 800 MB total; the file cap is a sanity bound against
# directory bloat (800MB / ~30KB average image ~ 27k files).
_MAX_CACHE_BYTES = 800 * 1024 * 1024
_MAX_CACHE_FILES = 50000

# In-memory decoded cache limits (the disk cache is the backing store, so
# evicted images simply re-decode from disk on demand - no re-download).
_MAX_MEM_ITEMS = 300       # max decoded static pixmaps held in memory
_MAX_MEM_BYTES = 256 * 1024 * 1024  # approx decoded bytes cap
_MAX_MEM_GIFS = 20         # max animated GIFs held in memory

# Decode downscale: the overlay renders inline images at ~80px (2x DPR even
# smaller than this), so decoding at full resolution wastes memory. Decoded
# images are capped at this longest side; the disk keeps original bytes.
_MAX_DECODE_DIM = 512

# All cache file suffixes (files store raw image bytes; the extension is
# cosmetic and reflects the real format so they can be opened directly).
_CACHE_EXTS = (".png", ".jpg", ".gif", ".img")

_CACHE_SUBDIR = os.path.join("DanmuFishpi", "img_cache")


def _hash_url(url: str) -> str:
    """Stable hex filename key for a URL (pure hex, no path traversal)."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def default_cache_dir() -> str:
    """Default disk cache directory under %APPDATA%."""
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(appdata, _CACHE_SUBDIR)


def _scaled_size(w: int, h: int, max_dim: int = _MAX_DECODE_DIM) -> tuple[int, int]:
    """Proportionally scale (w, h) so the longest side <= max_dim (no upscale)."""
    longest = max(w, h)
    if longest <= max_dim:
        return w, h
    scale = max_dim / longest
    return max(1, int(round(w * scale))), max(1, int(round(h * scale)))


def _pixmap_bytes(pm: QPixmap) -> int:
    """Approximate decoded memory of a QPixmap (ARGB32, includes DPR)."""
    try:
        dpr = pm.devicePixelRatio() or 1.0
    except Exception:
        dpr = 1.0
    return int(pm.width() * pm.height() * 4 * dpr)


def _cache_path(cache_dir: str, url: str, ext: str = ".img") -> str:
    """Disk file path for a URL inside the cache directory.

    Files store raw image bytes (PNG/JPEG/GIF); QPixmap.loadFromData detects
    the real format from the bytes, so the extension is cosmetic. Persisting
    uses the real format extension so cached files are clearly not disc
    images and can be opened directly.
    """
    return os.path.join(cache_dir, _hash_url(url) + ext)


def _sniff_ext(raw: bytes) -> str:
    """Best-effort extension from the image magic bytes."""
    if raw[:6] in _GIF_MAGIC:
        return ".gif"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if raw[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return ".img"


def _find_cache_path(cache_dir: str, url: str) -> str | None:
    """Return the existing disk path for a URL (any known extension)."""
    base = os.path.join(cache_dir, _hash_url(url))
    for ext in _CACHE_EXTS:
        p = base + ext
        if os.path.exists(p):
            return p
    return None


def _prune(cache_dir: str, total_bytes: int,
           max_bytes: int = _MAX_CACHE_BYTES,
           max_files: int = _MAX_CACHE_FILES) -> int:
    """Evict oldest cache files (by mtime) until under the caps.

    Returns the number of files removed. Missing/unreadable entries are
    skipped; non-cache files are never touched.
    """
    entries = []
    try:
        for name in os.listdir(cache_dir):
            if not name.endswith(_CACHE_EXTS):
                continue
            p = os.path.join(cache_dir, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, p))
    except OSError:
        return 0

    entries.sort(key=lambda e: e[0])  # oldest mtime first
    removed = 0
    total = total_bytes
    count = len(entries)
    for _mtime, size, p in entries:
        if total <= max_bytes and count <= max_files:
            break
        try:
            os.remove(p)
            removed += 1
            total -= size
            count -= 1
        except OSError:
            pass
    return removed


def _is_gif(data: bytes) -> bool:
    """Check raw bytes for GIF magic."""
    return data[:6] in _GIF_MAGIC


class AnimatedImage:
    """Holds pre-decoded GIF frames for efficient per-frame rendering.

    Each frame is stored as a QPixmap. durations gives per-frame display
    time in milliseconds; total_duration is the sum for one loop.
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

    def __init__(self, parent=None, cache_dir: str | None = None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        # Ordered by recency: hit/store moves to end, eviction pops the front.
        self._cache: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._animated: "OrderedDict[str, AnimatedImage]" = OrderedDict()
        self._pending: set[str] = set()
        self._mem_bytes = 0  # approx decoded bytes currently in memory
        self._manager.finished.connect(self._on_finished)

        # Disk cache
        self._cache_dir = cache_dir or default_cache_dir()
        self._disk_enabled = True
        self._total_bytes: int | None = None  # lazily scanned
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"图片磁盘缓存不可用，降级为纯内存缓存: {e}")
            self._disk_enabled = False

    # ---- Memory cache helpers ----

    def _store_static(self, url: str, pixmap: QPixmap) -> None:
        """Insert a decoded static pixmap into the bounded memory cache."""
        self._cache[url] = pixmap
        self._cache.move_to_end(url)
        self._mem_bytes += _pixmap_bytes(pixmap)
        self._evict_memory()

    def _store_gif(self, url: str, first_frame: QPixmap,
                   anim: AnimatedImage) -> None:
        """Insert a decoded GIF (first frame + animation) into memory."""
        self._cache[url] = first_frame
        self._cache.move_to_end(url)
        self._animated[url] = anim
        self._animated.move_to_end(url)
        self._mem_bytes += _pixmap_bytes(first_frame)
        for f in anim.frames:
            self._mem_bytes += _pixmap_bytes(f)
        self._evict_memory()

    def _evict_memory(self) -> None:
        """Evict oldest decoded images from memory until under the caps.

        The disk cache is untouched: evicted URLs re-decode from disk on the
        next get() (no network request).
        """
        while (len(self._cache) > _MAX_MEM_ITEMS
               or len(self._animated) > _MAX_MEM_GIFS
               or self._mem_bytes > _MAX_MEM_BYTES):
            if not self._cache:
                break
            url, pm = self._cache.popitem(last=False)
            self._mem_bytes -= _pixmap_bytes(pm)
            if url in self._animated:
                anim = self._animated.pop(url)
                for f in anim.frames:
                    self._mem_bytes -= _pixmap_bytes(f)

    def _decode_static(self, url: str, raw: bytes) -> QPixmap | None:
        """Decode raw bytes into a QPixmap, downscaling large images.

        Reads the header size first (no full decode) so setScaledSize can cap
        the longest side at _MAX_DECODE_DIM. Falls back to QPixmap.loadFromData
        for formats QImageReader cannot handle.
        """
        buf = QBuffer()
        buf.setData(QByteArray(raw))
        buf.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buf)
        reader.setAutoTransform(True)
        try:
            size = reader.size()
        except Exception:
            size = QSize()
        if size.isValid() and size.width() > 0 and size.height() > 0:
            w, h = _scaled_size(size.width(), size.height())
            if (w, h) != (size.width(), size.height()):
                reader.setScaledSize(QSize(w, h))
            image = reader.read()
            if not image.isNull():
                return QPixmap.fromImage(image)
            logger.debug(f"QImageReader decode failed for {url}, falling back")
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(raw)):
            return pixmap
        return None

    # ---- Disk cache helpers ----

    def _scan_total_bytes(self) -> int:
        total = 0
        try:
            for name in os.listdir(self._cache_dir):
                if not name.endswith(_CACHE_EXTS):
                    continue
                try:
                    total += os.path.getsize(os.path.join(self._cache_dir, name))
                except OSError:
                    pass
        except OSError:
            pass
        return total

    def _load_from_disk(self, url: str) -> bool:
        """Load raw bytes from disk and decode into the memory cache.

        Returns True on success. Corrupted files are removed so the caller
        falls through to a fresh download.
        """
        if not self._disk_enabled:
            return False
        path = _find_cache_path(self._cache_dir, url)
        if path is None:
            return False
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if _is_gif(raw):
                self._decode_gif(url, raw)
            else:
                pm = self._decode_static(url, raw)
                if pm is None:
                    try:
                        os.remove(path)  # corrupted: drop and re-download
                    except OSError:
                        pass
                    return False
                self._store_static(url, pm)
            # Touch mtime so LRU keeps recently used entries.
            try:
                os.utime(path, None)
            except OSError:
                pass
            logger.debug(f"Image from disk cache: {url}")
            return True
        except OSError as e:
            logger.debug(f"磁盘缓存读取失败 {url}: {e}")
            return False

    def _persist(self, url: str, raw: bytes) -> None:
        """Atomically write raw bytes to disk, evicting when over the cap.

        The file is named with the real image format extension so cached
        images can be opened directly. Sibling files with other extensions
        for the same URL (e.g. legacy .img entries) are removed.
        """
        if not self._disk_enabled:
            return
        ext = _sniff_ext(raw)
        path = _cache_path(self._cache_dir, url, ext)
        try:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, path)
            for other in _CACHE_EXTS:
                if other != ext:
                    sibling = _cache_path(self._cache_dir, url, other)
                    try:
                        if os.path.exists(sibling):
                            os.remove(sibling)
                    except OSError:
                        pass
            if self._total_bytes is None:
                self._total_bytes = self._scan_total_bytes()
            self._total_bytes += len(raw)
            if self._total_bytes > _MAX_CACHE_BYTES:
                removed = _prune(self._cache_dir, self._total_bytes)
                if removed:
                    self._total_bytes = None  # re-scan lazily after pruning
        except OSError as e:
            logger.warning(f"图片磁盘缓存写入失败（降级为纯内存缓存）: {e}")
            self._disk_enabled = False
            self._total_bytes = None

    # ---- Public API ----

    def get(self, url: str) -> QPixmap | None:
        """Return cached pixmap (memory or disk) or start downloading.

        For animated GIFs this returns the *first frame* as a static fallback
        until is_animated + current_frame are used.
        """
        if not url:
            return None

        if url in self._cache:
            self._cache.move_to_end(url)  # refresh LRU recency
            return self._cache[url]

        if self._load_from_disk(url):
            return self._cache.get(url)

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

    def clear_disk_cache(self) -> None:
        """Delete all cached image files (memory cache untouched)."""
        if not self._disk_enabled:
            return
        try:
            for name in os.listdir(self._cache_dir):
                if name.endswith(_CACHE_EXTS) or name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(self._cache_dir, name))
                    except OSError:
                        pass
            self._total_bytes = None
        except OSError as e:
            logger.warning(f"清空磁盘缓存失败: {e}")

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

            # Detect GIF and decode frames
            if _is_gif(raw):
                self._decode_gif(url, raw)
                self._persist(url, raw)
                self.loaded.emit(url)
                reply.deleteLater()
                return

            pm = self._decode_static(url, raw)
            if pm is not None:
                self._store_static(url, pm)
                self._persist(url, raw)
                logger.debug(f"Image loaded: {url} ({pm.width()}x{pm.height()})")
                self.loaded.emit(url)
            else:
                logger.debug(f"Image decode failed: {url}")

            reply.deleteLater()
        except Exception as e:
            logger.error(f"Image cache _on_finished error for {url}: {e}")
            reply.deleteLater()

    def _decode_gif(self, url: str, data: bytes) -> None:
        """Decode a GIF into individual frames via QMovie (bounded memory)."""
        try:
            movie = QMovie()
            movie.setCacheMode(QMovie.CacheMode.CacheAll)
            buf = QBuffer()
            buf.setData(QByteArray(data))
            movie.setDevice(buf)

            if not movie.isValid():
                logger.debug(f"GIF decode invalid: {url}")
                # Fallback: try loading as static
                pm = self._decode_static(url, data)
                if pm is not None:
                    self._store_static(url, pm)
                return

            # Peek intrinsic size (frame 0) to choose a downscaled decode size.
            movie.jumpToFrame(0)
            intrinsic = movie.currentImage().size()
            if intrinsic.isValid() and intrinsic.width() > 0:
                w, h = _scaled_size(intrinsic.width(), intrinsic.height())
                if (w, h) != (intrinsic.width(), intrinsic.height()):
                    movie.setScaledSize(QSize(w, h))
                    movie.jumpToFrame(0)  # re-read frame 0 at scaled size

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
                self._store_gif(url, frames[0], AnimatedImage(frames, durations))
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
            pm = self._decode_static(url, data)
            if pm is not None:
                self._store_static(url, pm)