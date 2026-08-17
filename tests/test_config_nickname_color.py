"""Tests for Config nickname_color (default nickname color) field."""
import config


def test_default_nickname_color_empty():
    """Default Config should have an empty nickname_color (follows theme)."""
    cfg = config.Config()
    assert cfg.display.nickname_color == ""


def test_config_to_dict_includes_nickname_color():
    """config_to_dict should include the nicknameColor key."""
    cfg = config.Config()
    cfg.display.nickname_color = "#ff8800"
    d = config.config_to_dict(cfg)
    assert d["display"]["nicknameColor"] == "#ff8800"


def test_config_from_dict_reads_nickname_color():
    """config_from_dict should read and normalise nicknameColor."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {"nicknameColor": "#FF8800"},
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.nickname_color == "#ff8800"


def test_config_from_dict_missing_nickname_color_defaults_empty():
    """Missing nicknameColor should default to an empty string."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {},
    }
    cfg = config.config_from_dict(d)
    assert cfg.display.nickname_color == ""


def test_config_from_dict_invalid_nickname_color_defaults_empty():
    """Invalid colors / non-strings should yield an empty string."""
    for bad in ("not-a-color", "12345", 42, None, ["#ff0000"]):
        d = {
            "account": {"username": "", "passwordEnc": ""},
            "display": {"nicknameColor": bad},
        }
        cfg = config.config_from_dict(d)
        assert cfg.display.nickname_color == "", f"bad value: {bad!r}"


def test_roundtrip_nickname_color():
    """Save and load should preserve nickname_color."""
    cfg = config.Config()
    cfg.display.nickname_color = "#ff8800"
    d = config.config_to_dict(cfg)
    cfg2 = config.config_from_dict(d)
    assert cfg2.display.nickname_color == "#ff8800"
