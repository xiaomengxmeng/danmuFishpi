"""Win32 HWND_TOPMOST reassert and overlay extended styles.

Adapted from danmuai/app/win32_overlay_zorder.py to keep the overlay visible
above exclusive-fullscreen games and to restore z-order after Alt+Tab.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

logger = logging.getLogger("danmuFishpi.win32_overlay")

if sys.platform == "win32":
    _GWL_EXSTYLE = -20
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_TRANSPARENT = 0x00000020
    _HWND_TOPMOST = wintypes.HWND(-1)
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040
    _GWL_STYLE = -16
    _WS_CAPTION = 0x00C00000

    _SetWindowPos = ctypes.windll.user32.SetWindowPos
    _GetForegroundWindow = ctypes.windll.user32.GetForegroundWindow
    _GetWindowRect = ctypes.windll.user32.GetWindowRect
    try:
        _SetWindowLong = ctypes.windll.user32.SetWindowLongPtrW
        _GetWindowLong = ctypes.windll.user32.GetWindowLongPtrW
    except AttributeError:
        _SetWindowLong = ctypes.windll.user32.SetWindowLongW
        _GetWindowLong = ctypes.windll.user32.GetWindowLongW

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]


def apply_overlay_exstyles(hwnd: int, *, click_through: bool = True) -> None:
    """Apply WS_EX_LAYERED + optional WS_EX_TRANSPARENT to the native HWND."""
    if sys.platform != "win32" or not hwnd:
        return
    try:
        ex_style = _GetWindowLong(hwnd, _GWL_EXSTYLE)
        if click_through:
            new_style = ex_style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT
        else:
            new_style = (ex_style | _WS_EX_LAYERED) & ~_WS_EX_TRANSPARENT
        _SetWindowLong(hwnd, _GWL_EXSTYLE, new_style)
    except Exception as e:
        logger.error(f"apply_overlay_exstyles failed: {e}")


def reassert_hwnd_topmost(hwnd: int) -> bool:
    """Restore HWND_TOPMOST without moving/resizing/activating the window."""
    if sys.platform != "win32" or not hwnd:
        return True
    try:
        result = _SetWindowPos(
            hwnd,
            _HWND_TOPMOST,
            0,
            0,
            0,
            0,
            _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_SHOWWINDOW,
        )
        return bool(result)
    except Exception as e:
        logger.error(f"reassert_hwnd_topmost failed: {e}")
        return False


def get_foreground_hwnd() -> int:
    if sys.platform != "win32":
        return 0
    try:
        return int(_GetForegroundWindow())
    except Exception:
        return 0


def _read_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if sys.platform != "win32" or not hwnd:
        return None
    rect = _RECT()
    if not _GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def probe_exclusive_fullscreen_risk(
    *,
    overlay_hwnd: int,
    screen_x: int,
    screen_y: int,
    screen_w: int,
    screen_h: int,
    own_hwnds: tuple[int, ...] = (),
    foreground_hwnd: int | None = None,
) -> bool:
    """Heuristic: foreground window nearly covers the target screen and has no caption."""
    if sys.platform != "win32" or not overlay_hwnd or screen_w <= 0 or screen_h <= 0:
        return False
    try:
        fg = int(foreground_hwnd) if foreground_hwnd is not None else int(_GetForegroundWindow())
        if not fg:
            return False
        skip = {int(h) for h in own_hwnds if h}
        skip.add(int(overlay_hwnd))
        if fg in skip:
            return False
        bounds = _read_window_rect(fg)
        if bounds is None:
            return False
        left, top, right, bottom = bounds
        fg_w = right - left
        fg_h = bottom - top
        if fg_w < int(screen_w * 0.95) or fg_h < int(screen_h * 0.95):
            return False
        if abs(left - screen_x) > 8 or abs(top - screen_y) > 8:
            return False
        style = int(_GetWindowLong(fg, _GWL_STYLE))
        if style & _WS_CAPTION:
            return False
        return True
    except Exception:
        return False
