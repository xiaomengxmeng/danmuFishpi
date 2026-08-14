"""Tests for per-user danmu color resolution in DanmuEngine (Qt-free)."""
from danmu_engine import DanmuEngine


def _msg(user_id: str = "", nickname: str = "", content: str = "hi") -> dict:
    return {
        "user_id": user_id,
        "nickname": nickname,
        "avatar_url": "",
        "content": content,
        "has_image": False,
        "is_red_packet": False,
    }


def test_no_user_colors_defaults_none():
    """Without userColors, items carry no custom color."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {}})
    item = engine.add_message(_msg(user_id="userA", nickname="用户A"))
    assert item is not None
    assert item.color is None


def test_matched_user_id_gets_color():
    """A message from a configured user_id gets its color."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {"userA": "#ff0000"}})
    item = engine.add_message(_msg(user_id="userA", nickname="用户A"))
    assert item.color == "#ff0000"


def test_matched_nickname_fallback_gets_color():
    """Missing user_id falls back to nickname lookup."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {"用户B": "#00ff00"}})
    item = engine.add_message(_msg(user_id="", nickname="用户B"))
    assert item.color == "#00ff00"


def test_unmatched_user_gets_no_color():
    """Unconfigured users get no custom color (follows theme default)."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {"userA": "#ff0000"}})
    item = engine.add_message(_msg(user_id="userC", nickname="用户C"))
    assert item.color is None


def test_user_id_takes_precedence_over_nickname():
    """user_id lookup wins when both user_id and nickname are configured."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {"userA": "#ff0000", "用户A": "#00ff00"}})
    item = engine.add_message(_msg(user_id="userA", nickname="用户A"))
    assert item.color == "#ff0000"


def test_update_config_replaces_user_colors():
    """A later update_config replaces the user color map."""
    engine = DanmuEngine()
    engine.update_config({"userColors": {"userA": "#ff0000"}})
    engine.update_config({"userColors": {"userB": "#00ff00"}})
    assert engine.add_message(_msg(user_id="userA")).color is None
    assert engine.add_message(_msg(user_id="userB")).color == "#00ff00"
