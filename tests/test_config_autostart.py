"""Tests for Config autostart field serialization."""
import config


def test_default_autostart_false():
    """Default Config should have autostart=False."""
    cfg = config.Config()
    assert cfg.autostart is False


def test_config_to_dict_includes_autostart():
    """config_to_dict should include autostart key."""
    cfg = config.Config()
    cfg.autostart = True
    d = config.config_to_dict(cfg)
    assert d["autostart"] is True


def test_config_from_dict_reads_autostart():
    """config_from_dict should read autostart from dict."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {},
        "autostart": True,
    }
    cfg = config.config_from_dict(d)
    assert cfg.autostart is True


def test_config_from_dict_missing_autostart_defaults_false():
    """Missing autostart in dict should default to False."""
    d = {
        "account": {"username": "", "passwordEnc": ""},
        "display": {},
    }
    cfg = config.config_from_dict(d)
    assert cfg.autostart is False


def test_roundtrip_autostart():
    """Save and load should preserve autostart value."""
    cfg = config.Config()
    cfg.autostart = True
    d = config.config_to_dict(cfg)
    cfg2 = config.config_from_dict(d)
    assert cfg2.autostart is True
