"""Offscreen (no-network) tests for ImageCache disk persistence."""
import base64
import os
import shutil
import sys

import pytest
from PyQt6.QtWidgets import QApplication

from image_cache import ImageCache, _cache_path

pytest.importorskip("PyQt6")

# 1x1 transparent PNG
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_URL = "http://127.0.0.1:9/avatar.png"


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


@pytest.fixture
def workdir():
    """Workspace-local temp dir (pytest tmp_path uses mkdtemp, denied in sandbox)."""
    d = os.path.join(os.path.dirname(__file__), ".tmp_imgcache")
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_disk_hit_loads_without_network(app, workdir):
    """A file already on disk (real format ext) is decoded without a download."""
    cache = ImageCache(cache_dir=workdir)
    path = _cache_path(workdir, _URL, ".png")
    with open(path, "wb") as f:
        f.write(_PNG)
    pm = cache.get(_URL)
    assert pm is not None and not pm.isNull()
    assert _URL not in cache._pending  # no HTTP request started
    assert _URL in cache._cache


def test_legacy_img_ext_still_found(app, workdir):
    """Old .img-named cache files from earlier versions are still reused."""
    cache = ImageCache(cache_dir=workdir)
    path = _cache_path(workdir, _URL, ".img")
    with open(path, "wb") as f:
        f.write(_PNG)
    assert cache.get(_URL) is not None
    assert _URL not in cache._pending


def test_corrupted_file_is_removed_and_download_started(app, workdir):
    """Garbage bytes -> file deleted, falls through to download."""
    cache = ImageCache(cache_dir=workdir)
    path = _cache_path(workdir, _URL, ".png")
    with open(path, "wb") as f:
        f.write(b"not an image at all")
    pm = cache.get(_URL)
    assert pm is None
    assert not os.path.exists(path)  # corrupted file dropped
    assert _URL in cache._pending    # download scheduled


def test_missing_file_starts_download(app, workdir):
    cache = ImageCache(cache_dir=workdir)
    assert cache.get(_URL) is None
    assert _URL in cache._pending


def test_clear_disk_cache_removes_files(app, workdir):
    cache = ImageCache(cache_dir=workdir)
    with open(_cache_path(workdir, _URL, ".png"), "wb") as f:
        f.write(_PNG)
    cache.clear_disk_cache()
    assert not os.path.exists(_cache_path(workdir, _URL, ".png"))


def test_disk_disabled_degrades_to_memory(app, workdir):
    """When the cache dir cannot be created, get() still works (download path)."""
    blocked = os.path.join(workdir, "blocked")
    with open(blocked, "w") as f:
        f.write("i am a file, not a dir")
    cache = ImageCache(cache_dir=blocked)
    assert cache._disk_enabled is False
    assert cache.get(_URL) is None
    assert _URL in cache._pending