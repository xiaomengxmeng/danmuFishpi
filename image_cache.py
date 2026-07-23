"""Asynchronous image download cache for avatars and inline message images.

Uses QNetworkAccessManager to fetch images in the background without blocking
the render loop. Completed downloads trigger a repaint via the loaded signal.
"""

import logging

from PyQt6.QtCore import QObject, pyqtSignal, QUrl
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

logger = logging.getLogger("danmuFishpi.image_cache")


class ImageCache(QObject):
    """Cache for remote images used by the danmu overlay."""

    loaded = pyqtSignal(str)  # URL that just finished loading

    def __init__(self, parent=None):
        super().__init__(parent)
        self._manager = QNetworkAccessManager(self)
        self._cache: dict[str, QPixmap] = {}
        self._pending: set[str] = set()
        self._manager.finished.connect(self._on_finished)

    def get(self, url: str) -> QPixmap | None:
        """Return cached pixmap or start downloading and return None."""
        if not url:
            return None

        if url in self._cache:
            return self._cache[url]

        if url not in self._pending:
            self._pending.add(url)
            request = QNetworkRequest(QUrl(url))
            request.setHeader(
                QNetworkRequest.KnownHeaders.UserAgentHeader,
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            )
            self._manager.get(request)

        return None

    def _on_finished(self, reply: QNetworkReply) -> None:
        url = reply.url().toString()
        self._pending.discard(url)

        if reply.error() != QNetworkReply.NetworkError.NoError:
            logger.debug(f"Image download failed for {url}: {reply.errorString()}")
            reply.deleteLater()
            return

        data = reply.readAll()
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self._cache[url] = pixmap
            logger.debug(f"Image loaded: {url} ({pixmap.width()}x{pixmap.height()})")
            self.loaded.emit(url)
        else:
            logger.debug(f"Image decode failed: {url}")

        reply.deleteLater()
