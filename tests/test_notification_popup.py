"""Tests for the enhanced notification popup (特别关注 accent toast)."""
import sys

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from notification import NotificationPopup, NotificationManager

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication(sys.argv)
    yield a


def _click(popup):
    ev = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    popup.mousePressEvent(ev)


def test_default_popup_unchanged(app):
    """Default construction keeps the original behaviour (no accent)."""
    p = NotificationPopup("弹幕鱼排", "登录成功")
    assert p._timer.interval() == 2500
    assert p.width() == 280
    assert not hasattr(p, "lbl_avatar")
    # title label should not carry the star prefix
    from PyQt6.QtWidgets import QLabel
    title = p.findChild(QLabel, "title")
    assert title is not None
    assert not title.text().startswith("⭐")
    p.close()


def test_accent_popup_duration_and_star(app):
    """Accent popups use the given duration and a star prefix."""
    p = NotificationPopup("特别关注", "内容", accent=True, duration_ms=8000)
    assert p._timer.interval() == 8000
    assert p.width() == 300
    from PyQt6.QtWidgets import QLabel
    title = p.findChild(QLabel, "title")
    assert title is not None
    assert title.text().startswith("⭐ ")
    p.close()


def test_avatar_placeholder_without_image_cache(app):
    """avatar_url without image_cache falls back to a letter placeholder."""
    p = NotificationPopup("特别关注", "内容", avatar_url="https://x/a.png",
                          avatar_nickname="小明", image_cache=None, accent=True)
    from PyQt6.QtWidgets import QLabel
    lbl = p.findChild(QLabel, "avatar")
    assert lbl is not None
    assert lbl.text() == "小"  # first char of nickname
    p.close()


def test_click_dismisses(app):
    """mousePressEvent starts the fade-out immediately."""
    p = NotificationPopup("特别关注", "内容", accent=True, duration_ms=8000)
    _click(p)
    assert getattr(p, "_fading", False) is True
    assert hasattr(p, "_anim")
    p.close()


def test_manager_prunes_accent_to_max(app):
    """At most _MAX_ACCENT accent toasts stay on screen."""
    mgr = NotificationManager(theme="dark", image_cache=None)
    for i in range(4):
        mgr.show("特别关注", f"第{i}条", accent=True, duration_ms=60000)
    accents = [p for p in mgr._active if getattr(p, "_accent", False)]
    assert len(accents) <= mgr._MAX_ACCENT
    for p in list(mgr._active):
        p.close()
