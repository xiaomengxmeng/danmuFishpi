"""Tests for ImageCache bounded-memory behavior (offscreen, no network)."""
import base64
import os
import shutil
import sys

import pytest
from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

import image_cache as ic
from image_cache import ImageCache, AnimatedImage, _cache_path, _scaled_size

pytest.importorskip("PyQt6")

_URL = "http://127.0.0.1:9/img.png"
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


@pytest.fixture
def workdir():
    """Workspace-local temp dir (pytest tmp_path uses mkdtemp, denied in sandbox)."""
    d = os.path.join(os.path.dirname(__file__), ".tmp_imgcache_mem")
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _big_png_bytes(w=2000, h=1000) -> bytes:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0xFFFF0000)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def test_scaled_size_math():
    assert _scaled_size(100, 50) == (100, 50)      # small: unchanged
    assert _scaled_size(2000, 1000) == (512, 256)  # largest side capped
    assert _scaled_size(1000, 2000) == (256, 512)
    assert _scaled_size(600, 600, max_dim=100) == (100, 100)


def test_decode_static_downscales_large_images(app):
    cache = ImageCache(cache_dir="unused-not-created")  # disk disabled path
    pm = cache._decode_static(_URL, _big_png_bytes())
    assert pm is not None and not pm.isNull()
    assert max(pm.width(), pm.height()) <= ic._MAX_DECODE_DIM
    # small image unchanged (longest side below the cap)
    pm2 = cache._decode_static(_URL, _PNG)
    assert pm2 is not None and pm2.width() <= 512


def test_memory_item_cap_evicts_oldest(app, workdir, monkeypatch):
    monkeypatch.setattr(ic, "_MAX_MEM_ITEMS", 3)
    monkeypatch.setattr(ic, "_MAX_MEM_BYTES", 10 ** 9)
    cache = ImageCache(cache_dir=workdir)
    for i in range(5):
        pm = QPixmap(16, 16)
        pm.fill(0xFF000000)
        cache._store_static(f"{_URL}{i}", pm)
    assert len(cache._cache) == 3
    # oldest two URLs evicted from memory
    assert f"{_URL}0" not in cache._cache
    assert f"{_URL}1" not in cache._cache
    assert f"{_URL}4" in cache._cache
    assert cache._mem_bytes >= 0


def test_evicted_url_reloads_from_disk_without_network(app, workdir, monkeypatch):
    monkeypatch.setattr(ic, "_MAX_MEM_ITEMS", 2)
    monkeypatch.setattr(ic, "_MAX_MEM_BYTES", 10 ** 9)
    cache = ImageCache(cache_dir=workdir)
    # pre-seed a disk entry
    with open(_cache_path(workdir, _URL, ".png"), "wb") as f:
        f.write(_PNG)
    # load it (memory), then push it out of memory with newer images
    assert cache.get(_URL) is not None
    assert _URL in cache._cache
    for i in range(4):
        pm = QPixmap(16, 16)
        pm.fill(0xFF000000)
        cache._store_static(f"{_URL}x{i}", pm)
    assert _URL not in cache._cache  # evicted from memory
    # re-get: served from disk, no download scheduled
    assert cache.get(_URL) is not None
    assert _URL in cache._cache
    assert _URL not in cache._pending


def test_gif_count_cap(app, workdir, monkeypatch):
    monkeypatch.setattr(ic, "_MAX_MEM_GIFS", 2)
    monkeypatch.setattr(ic, "_MAX_MEM_ITEMS", 100)
    monkeypatch.setattr(ic, "_MAX_MEM_BYTES", 10 ** 9)
    cache = ImageCache(cache_dir=workdir)
    for i in range(4):
        frames = [QPixmap(8, 8), QPixmap(8, 8)]
        anim = AnimatedImage(frames, [100, 100])
        cache._store_gif(f"{_URL}g{i}", frames[0], anim)
    assert len(cache._animated) == 2
    assert f"{_URL}g0" not in cache._animated
    assert f"{_URL}g3" in cache._animated
    # evicted GIF also removed from the static cache
    assert f"{_URL}g0" not in cache._cache


def test_get_refreshes_lru_recency(app, workdir, monkeypatch):
    monkeypatch.setattr(ic, "_MAX_MEM_ITEMS", 2)
    monkeypatch.setattr(ic, "_MAX_MEM_BYTES", 10 ** 9)
    cache = ImageCache(cache_dir=workdir)
    pm = QPixmap(8, 8)
    cache._store_static("a", pm)
    cache._store_static("b", pm)
    # touch "a" so it becomes the most recent
    cache.get("a")
    cache._store_static("c", pm)
    assert "a" in cache._cache
    assert "b" not in cache._cache  # b is now the oldest