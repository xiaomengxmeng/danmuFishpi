"""Tests for image_cache pure helpers: hashing, paths and LRU pruning (no Qt)."""
import os
import shutil

import pytest

from image_cache import (_hash_url, _cache_path, _find_cache_path, _prune,
                         _sniff_ext, default_cache_dir)


@pytest.fixture
def workdir():
    """Workspace-local temp dir (pytest tmp_path uses mkdtemp, denied in sandbox)."""
    d = os.path.join(os.path.dirname(__file__), ".tmp_imgcache")
    os.makedirs(d, exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_hash_url_deterministic_and_distinct():
    a = _hash_url("https://fishpi.cn/img/a.png")
    b = _hash_url("https://fishpi.cn/img/a.png")
    c = _hash_url("https://fishpi.cn/img/b.png")
    assert a == b
    assert a != c
    assert len(a) == 40  # sha1 hex
    assert a.isalnum()


def test_cache_path_under_dir(workdir):
    url = "https://fishpi.cn/img/a.png?x=1"
    p = _cache_path(workdir, url)
    assert p == os.path.join(workdir, _hash_url(url) + ".img")
    assert _cache_path(workdir, url, ".png") == os.path.join(workdir, _hash_url(url) + ".png")


def test_sniff_ext_from_magic():
    assert _sniff_ext(b"GIF89a") == ".gif"
    assert _sniff_ext(b"\x89PNG\r\n\x1a\nrest") == ".png"
    assert _sniff_ext(b"\xff\xd8\xff\xe0") == ".jpg"
    assert _sniff_ext(b"unknown-bytes") == ".img"


def test_find_cache_path_tries_all_exts(workdir):
    url = "https://fishpi.cn/img/a.png"
    with open(_cache_path(workdir, url, ".png"), "wb") as f:
        f.write(b"x")
    assert _find_cache_path(workdir, url) == _cache_path(workdir, url, ".png")
    assert _find_cache_path(workdir, "https://fishpi.cn/img/missing.png") is None


def test_default_cache_dir_structure(monkeypatch):
    monkeypatch.setenv("APPDATA", "C:\\Users\\test\\AppData\\Roaming")
    d = default_cache_dir()
    assert d.replace("\\", "/").endswith("DanmuFishpi/img_cache")


def test_prune_removes_oldest_until_under_bytes(workdir):
    for name, size, age in (("a.img", 100, 50), ("b.img", 200, 100), ("c.txt", 500, 0)):
        p = os.path.join(workdir, name)
        with open(p, "wb") as f:
            f.write(b"x" * size)
        os.utime(p, (age, age))
    removed = _prune(workdir, 300, max_bytes=250, max_files=1000)
    assert removed == 1
    assert not os.path.exists(os.path.join(workdir, "a.img"))  # oldest evicted
    assert os.path.exists(os.path.join(workdir, "b.img"))
    assert os.path.exists(os.path.join(workdir, "c.txt"))      # non-cache untouched


def test_prune_respects_file_cap_and_handles_real_exts(workdir):
    for name in ("a.png", "b.jpg", "c.gif"):
        with open(os.path.join(workdir, name), "wb") as f:
            f.write(b"x" * 10)
    removed = _prune(workdir, 30, max_bytes=10 ** 9, max_files=2)
    assert removed == 1
    cache_files = [f for f in os.listdir(workdir) if f.endswith((".png", ".jpg", ".gif", ".img"))]
    assert len(cache_files) == 2


def test_prune_missing_dir_returns_zero(workdir):
    assert _prune(os.path.join(workdir, "nope"), 0) == 0