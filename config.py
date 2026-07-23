"""Configuration management for danmuFishpi.

Stores config as JSON at %APPDATA%/DanmuFishpi/config.json.
Password is encrypted with Windows DPAPI (via ctypes) and base64-encoded.
"""

import base64
import json
import os
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict, field
from typing import Optional


# ── DPAPI encryption (Windows only) ──────────────────────────────

_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32

class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def dpapi_encrypt(plaintext: str) -> str:
    """Encrypt a string with Windows DPAPI, return base64-encoded ciphertext."""
    if not plaintext:
        return ""
    data = plaintext.encode("utf-8")
    blob_in = DATA_BLOB(len(data), ctypes.cast(
        ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not _crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise OSError("DPAPI CryptProtectData failed")
    try:
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        _kernel32.LocalFree(blob_out.pbData)


def dpapi_decrypt(ciphertext_b64: str) -> str:
    """Decrypt a base64-encoded DPAPI ciphertext."""
    if not ciphertext_b64:
        return ""
    encrypted = base64.b64decode(ciphertext_b64)
    blob_in = DATA_BLOB(len(encrypted), ctypes.cast(
        ctypes.c_char_p(encrypted), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0,
            ctypes.byref(blob_out)):
        raise OSError("DPAPI CryptUnprotectData failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")
    finally:
        _kernel32.LocalFree(blob_out.pbData)


# ── Config dataclasses ───────────────────────────────────────────

@dataclass
class Account:
    username: str = ""
    password_enc: str = ""


@dataclass
class Display:
    danmu_mode: str = "scrolling"      # scrolling | floating
    show_avatar: bool = True
    show_nickname: bool = True
    show_image: bool = True
    show_red_packet: bool = True
    blocked_user_ids: list[str] = field(default_factory=list)
    followed_user_ids: list[str] = field(default_factory=list)
    play_sound: bool = False           # deprecated, kept for compatibility
    show_outline: bool = True          # text outline on danmu
    simple_mode: bool = False          # minimal visual style
    top_margin: int = 21               # 0-300 px
    notify_startup: bool = True
    notify_login: bool = True
    notify_follow: bool = True
    danmu_speed: int = 10              # 1-10
    danmu_area: str = "topHalf"        # fullscreen | topHalf | bottomHalf (scrolling only)
    floating_corner: str = "bottomLeft"  # topLeft | topRight | bottomLeft | bottomRight
    danmu_width: int = 52              # 30-100 percentage
    danmu_height: int = 40             # 30-100 percentage
    danmu_opacity: int = 100           # 30-100 percentage
    font_size: int = 10                # 8-48 px
    font_family: str = "Consolas"      # font family name
    truncate_long_messages: bool = True  # truncate code blocks / long messages
    max_message_lines: int = 3           # max text lines before truncation
    # Floating mode specific settings
    floating_dwell_seconds: int = 8      # 3-30 s, how long each floating card stays
    floating_max_items: int = 6          # 1-8, max simultaneous floating cards
    floating_card_width: int = 225       # 160-520 px, floating card width
    floating_font_size: int = 10         # 8-48 px, floating card font size


@dataclass
class Config:
    account: Account = field(default_factory=Account)
    display: Display = field(default_factory=Display)
    hotkey: str = "f9"
    boss_key: str = "f10"
    theme: str = "light"               # dark | light


# ── JSON (de)serialisation with camelCase keys ───────────────────

def _to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def config_to_dict(cfg: Config) -> dict:
    """Convert Config to the camelCase JSON dict matching the Go format."""
    d = asdict(cfg)
    # Rename account fields
    d["account"] = {
        "username": d["account"]["username"],
        "passwordEnc": d["account"]["password_enc"],
    }
    # Rename display fields
    disp = d["display"]
    d["display"] = {
        "danmuMode": disp["danmu_mode"],
        "showAvatar": disp["show_avatar"],
        "showNickname": disp["show_nickname"],
        "showImage": disp["show_image"],
        "showRedPacket": disp["show_red_packet"],
        "blockedUserIds": disp["blocked_user_ids"],
        "followedUserIds": disp["followed_user_ids"],
        "playSound": disp["play_sound"],
        "showOutline": disp["show_outline"],
        "simpleMode": disp["simple_mode"],
        "topMargin": disp["top_margin"],
        "notifyStartup": disp["notify_startup"],
        "notifyLogin": disp["notify_login"],
        "notifyFollow": disp["notify_follow"],
        "danmuSpeed": disp["danmu_speed"],
        "danmuArea": disp["danmu_area"],
        "floatingCorner": disp["floating_corner"],
        "danmuWidth": disp["danmu_width"],
        "danmuHeight": disp["danmu_height"],
        "danmuOpacity": disp["danmu_opacity"],
        "fontSize": disp["font_size"],
        "fontFamily": disp["font_family"],
        "truncateLongMessages": disp["truncate_long_messages"],
        "maxMessageLines": disp["max_message_lines"],
        "floatingDwellSeconds": disp["floating_dwell_seconds"],
        "floatingMaxItems": disp["floating_max_items"],
        "floatingCardWidth": disp["floating_card_width"],
        "floatingFontSize": disp["floating_font_size"],
    }
    # Rename root fields
    d["hotkey"] = d.pop("hotkey", "f9")
    d["bossKey"] = d.pop("boss_key", "f10")
    return d


def config_from_dict(d: dict) -> Config:
    """Build a Config from a camelCase JSON dict, filling defaults."""
    cfg = Config()
    acc = d.get("account", {})
    cfg.account = Account(
        username=acc.get("username", ""),
        password_enc=acc.get("passwordEnc", ""),
    )
    disp = d.get("display", {})
    danmu_mode_raw = disp.get("danmuMode", "scrolling")
    if danmu_mode_raw not in ("scrolling", "floating"):
        danmu_mode_raw = "scrolling"  # fallback for legacy "bottom" mode
    cfg.display = Display(
        danmu_mode=danmu_mode_raw,
        show_avatar=disp.get("showAvatar", True),
        show_nickname=disp.get("showNickname", True),
        show_image=disp.get("showImage", True),
        show_red_packet=disp.get("showRedPacket", True),
        blocked_user_ids=disp.get("blockedUserIds", []),
        followed_user_ids=disp.get("followedUserIds", []),
        play_sound=disp.get("playSound", False),
        show_outline=disp.get("showOutline", True),
        simple_mode=disp.get("simpleMode", False),
        top_margin=disp.get("topMargin", 21),
        notify_startup=disp.get("notifyStartup", True),
        notify_login=disp.get("notifyLogin", True),
        notify_follow=disp.get("notifyFollow", True),
        danmu_speed=disp.get("danmuSpeed", 10),
        danmu_area=disp.get("danmuArea", "topHalf"),
        floating_corner=disp.get("floatingCorner", "bottomLeft"),
        danmu_width=disp.get("danmuWidth", 52),
        danmu_height=disp.get("danmuHeight", 40),
        danmu_opacity=disp.get("danmuOpacity", 100),
        font_size=disp.get("fontSize", 10),
        font_family=disp.get("fontFamily", "Consolas"),
        truncate_long_messages=disp.get("truncateLongMessages", True),
        max_message_lines=disp.get("maxMessageLines", 3),
        floating_dwell_seconds=disp.get("floatingDwellSeconds", 8),
        floating_max_items=disp.get("floatingMaxItems", 6),
        floating_card_width=disp.get("floatingCardWidth", 225),
        floating_font_size=disp.get("floatingFontSize", 10),
    )
    cfg.hotkey = d.get("hotkey", "f9")
    cfg.boss_key = d.get("bossKey", "f10")
    cfg.theme = d.get("theme", "light")
    return cfg


# ── Load / Save ──────────────────────────────────────────────────

def default_config_path() -> str:
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    return os.path.join(appdata, "DanmuFishpi", "config.json")


def load(path: Optional[str] = None) -> Config:
    """Load config from disk. Returns defaults on any error."""
    if path is None:
        path = default_config_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        d = json.loads(text)
        return config_from_dict(d)
    except FileNotFoundError:
        return Config()
    except (json.JSONDecodeError, OSError):
        # Backup corrupted file
        try:
            os.rename(path, path + ".corrupted")
        except OSError:
            pass
        return Config()


def save(cfg: Config, path: Optional[str] = None) -> None:
    if path is None:
        path = default_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    d = config_to_dict(cfg)
    text = json.dumps(d, indent=2, ensure_ascii=False)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
