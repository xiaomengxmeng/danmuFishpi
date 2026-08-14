"""Tests for Config user_colors (per-user danmu colors) field."""
import config


def test_default_user_colors_empty():
    """Default Config should have an empty user_colors map."""
    cfg = config.Config()
    assert cfg.display.user_colors == {}


def test_config_to_dict_includes_user_colors():
    """config_to_dict should include the userColors key."""
    cfg = config.Config()
    cfg.display.user_colors = {"userA": "#ff0000", "userB": "#00ff00"}
    d = config.config_to_dict(cfg)
    assert d["display"]["userColors"] == {"userA": "#ff0000", "userB": "#00ff00"}


def test_config_from_dict_reads_user_colors():
    """config_from_dict should read userColors from the display dict."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {"userColors": {"userA": "#FF0000", "userB": "#00ff00"}},
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.user_colors == {"userA": "#ff0000", "userB": "#00ff00"}


def test_config_from_dict_missing_user_colors_defaults_empty():
    """Missing userColors in dict should default to an empty map."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {},
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.user_colors == {}


def test_config_from_dict_normalises_hex_and_drops_invalid():
    """Invalid colors are dropped; valid ones are normalised to #rrggbb."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {
            "userColors": {
                "userA": "FF0000",        # no '#', upper case -> #ff0000
                "userB": "#00ff00",
                "userC": "not-a-color",   # dropped
                "": "#123456",            # empty key dropped
                "  userD  ": "#abcdef",   # key trimmed
                "userE": 12345,           # non-string dropped
            }
        },
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.user_colors == {
        "userA": "#ff0000",
        "userB": "#00ff00",
        "userD": "#abcdef",
    }


def test_config_from_dict_non_dict_user_colors_defaults_empty():
    """Non-dict userColors values should yield an empty map."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {"userColors": ["userA", "#ff0000"]},
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.user_colors == {}


def test_roundtrip_user_colors():
    """Save and load should preserve user_colors."""
    cfg = config.Config()
    cfg.display.user_colors = {"userA": "#ff0000", "userB": "#00ff00"}
    d = config.config_to_dict(cfg)
    cfg2 = config.config_from_dict(d)
    assert cfg2.display.user_colors == {"userA": "#ff0000", "userB": "#00ff00"}
