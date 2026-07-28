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
    top_margin: int = 0                # 顶部边距 (0-100, 占显示区域高度百分比)
    notify_startup: bool = True
    notify_login: bool = True
    notify_follow: bool = True
    danmu_speed: int = 50             # 0-100 (滚动速度)
    danmu_area: str = "topHalf"        # fullscreen | topHalf | bottomHalf (scrolling only)
    floating_corner: str = "bottomLeft"  # topLeft | topRight | bottomLeft | bottomRight
    danmu_width: int = 100             # 0-100 弹幕区域宽度百分比
    danmu_height: int = 100            # 0-100 弹幕区域高度百分比
    danmu_opacity: int = 100           # 0-100 不透明度百分比
    font_scale: float = 100.0          # 字号百分比 (100 = 1.0x)
    font_family: str = "Consolas"      # font family name
    truncate_long_messages: bool = True  # truncate code blocks / long messages
    max_message_lines: int = 3           # max text lines before truncation
    # Floating mode specific settings
    floating_dwell_seconds: int = 8      # 3-30 s, how long each floating card stays
    floating_max_items: int = 6          # 1-8, max simultaneous floating cards
    floating_card_scale: float = 1.0    # 浮动卡片宽度相对系数 (0.5-2.0)
    floating_font_scale: float = 100.0   # 浮动卡片字号百分比 (100 = 1.0x)
    display_screen: int = -1             # -1 = primary screen; else index into QApplication.screens()
    legacy_pixels: dict = field(default_factory=dict)  # 旧版绝对px迁移暂存


@dataclass
class Config:
    account: Account = field(default_factory=Account)
    display: Display = field(default_factory=Display)
    hotkey: str = "f9"
    boss_key: str = "f10"
    theme: str = "light"               # dark | light
    autostart: bool = False            # 开机自启


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
        "fontScale": disp["font_scale"],
        "fontFamily": disp["font_family"],
        "truncateLongMessages": disp["truncate_long_messages"],
        "maxMessageLines": disp["max_message_lines"],
        "floatingDwellSeconds": disp["floating_dwell_seconds"],
        "floatingMaxItems": disp["floating_max_items"],
        "floatingCardScale": disp["floating_card_scale"],
        "floatingFontScale": disp["floating_font_scale"],
        "displayScreen": disp["display_screen"],
    }
    # Rename root fields
    d["hotkey"] = d.pop("hotkey", "f9")
    d["bossKey"] = d.pop("boss_key", "f10")
    d["autostart"] = d.get("autostart", False)
    return d


def _coerce_font_scale(disp) -> float:
    """Read fontScale, normalising legacy 0.5-2.0 coefficient to new 0-100 percent."""
    fs = disp.get("fontScale", 100)
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        return 100.0
    # Legacy configs stored a 0.5-2.0 multiplier; treat anything <= 3.0 as old.
    if fs <= 3.0:
        fs = min(100.0, fs * 100.0)
    return max(20.0, min(100.0, fs))


def _coerce_top_margin(disp) -> int:
    """Read top margin percent, normalising legacy topMarginScale coefficient.

    Old configs stored ``topMarginScale`` (0.5-2.0), where ~1.0 meant the small
    baseline offset, plus an absolute-pixel ``topMargin`` that must be ignored
    (it is NOT a percent). New configs store ``topMargin`` directly as 0-100.
    """
    if "topMarginScale" in disp:
        try:
            coeff = float(disp["topMarginScale"])
            # 0.5 -> 0%, 1.0 -> ~10%, 2.0 -> ~30% (old 0.5-2.0 range)
            return max(0, min(100, int(round((coeff - 0.5) * 20.0))))
        except (TypeError, ValueError):
            pass
    if "topMargin" in disp:
        try:
            return max(0, min(100, int(round(float(disp["topMargin"])))))
        except (TypeError, ValueError):
            pass
    return 0


def _coerce_floating_font_scale(disp) -> float:
    """Read floatingFontScale, normalising legacy 0.5-2.0 coefficient to new 0-100 percent."""
    fs = disp.get("floatingFontScale", 100)
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        return 100.0
    if fs <= 3.0:
        fs = min(100.0, fs * 100.0)
    return max(20.0, min(100.0, fs))


def config_from_dict(d: dict) -> Config:
    """Build a Config from a camelCase JSON dict, filling defaults."""
    cfg = Config()
    acc = d.get("account", {})
    cfg.account = Account(
        username=acc.get("username", ""),
        password_enc=acc.get("passwordEnc", ""),
    )
    disp = d.get("display", {})

    # 旧版绝对像素配置 -> 相对系数（迁移暂存，由 App 用主屏基准反推）
    legacy_pixels: dict = {}
    if "fontScale" not in disp:
        for _old_key, _new_key in (
            ("fontSize", "font_size"),
            ("topMargin", "top_margin"),
            ("floatingCardWidth", "floating_card_width"),
            ("floatingFontSize", "floating_font_size"),
        ):
            if _old_key in disp:
                try:
                    legacy_pixels[_new_key] = float(disp[_old_key])
                except (TypeError, ValueError):
                    pass
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
        top_margin=_coerce_top_margin(disp),
        notify_startup=disp.get("notifyStartup", True),
        notify_login=disp.get("notifyLogin", True),
        notify_follow=disp.get("notifyFollow", True),
        danmu_speed=disp.get("danmuSpeed", 50),
        danmu_area=disp.get("danmuArea", "topHalf"),
        floating_corner=disp.get("floatingCorner", "bottomLeft"),
        danmu_width=disp.get("danmuWidth", 100),
        danmu_height=disp.get("danmuHeight", 100),
        danmu_opacity=disp.get("danmuOpacity", 100),
        font_scale=_coerce_font_scale(disp),
        font_family=disp.get("fontFamily", "Consolas"),
        truncate_long_messages=disp.get("truncateLongMessages", True),
        max_message_lines=disp.get("maxMessageLines", 3),
        floating_dwell_seconds=disp.get("floatingDwellSeconds", 8),
        floating_max_items=disp.get("floatingMaxItems", 6),
        floating_card_scale=disp.get("floatingCardScale", 1.0),
        floating_font_scale=_coerce_floating_font_scale(disp),
        display_screen=disp.get("displayScreen", -1),
        legacy_pixels=legacy_pixels,
    )
    cfg.hotkey = d.get("hotkey", "f9")
    cfg.boss_key = d.get("bossKey", "f10")
    cfg.theme = d.get("theme", "light")
    cfg.autostart = d.get("autostart", False)
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


# ── Target screen selection (shared by overlay / input box / notifications) ──

# Index of the screen the overlay should live on. -1 means "follow the OS
# primary monitor" (i.e. QApplication.primaryScreen()). set by main.py from
# Config.display.display_screen. Kept module-level so widgets that don't hold a
# reference to the Config can still resolve the same screen.
_CURRENT_DISPLAY_SCREEN: int = -1


def set_target_screen(index: int) -> None:
    """Tell the shared helper which screen to use (-1 = primary)."""
    global _CURRENT_DISPLAY_SCREEN
    _CURRENT_DISPLAY_SCREEN = index if isinstance(index, int) else -1


def target_screen_geometry(app) -> object:
    """Return the QRect of the configured target screen.

    `app` is a QApplication instance. Falls back to the primary screen on any
    error. PyQt is intentionally not imported at module level here to avoid a
    hard dependency / import cycle.
    """
    from screen_utils import target_geometry
    return target_geometry(app, _CURRENT_DISPLAY_SCREEN)
