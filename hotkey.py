"""Global hotkey registration.

Primary implementation uses the `keyboard` library (low-level Windows hook),
which is more reliable than RegisterHotKey for arbitrary combinations and
works even when the Qt native event filter misses messages.
RegisterHotKey is kept as a fallback.
"""

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

logger = logging.getLogger("danmuFishpi.hotkey")

# Win32 constants for fallback RegisterHotKey
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_F1 = 0x70
VK_ESCAPE = 0x1B


def _normalize_hotkey(hotkey_str: str) -> str:
    """Normalize e.g. 'alt + space' -> 'alt+space'."""
    return "+".join(p.strip().lower() for p in hotkey_str.split("+") if p.strip())


class HotkeyManager(QObject):
    """Manages a single global hotkey."""

    triggered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_hotkey: Optional[str] = None
        self._callback: Optional[Callable] = None
        self._keyboard_hook = None
        self._hotkey_id: int = 1
        self._lock = threading.Lock()

    def register(self, hotkey_str: str, callback: Callable) -> bool:
        """Register a global hotkey. Unregisters any previous one first."""
        self.unregister()
        normalized = _normalize_hotkey(hotkey_str)
        if not normalized:
            logger.error(f"Empty hotkey string")
            return False

        self._callback = callback

        # Primary: keyboard library low-level hook
        try:
            import keyboard

            def _hook_callback():
                # keyboard calls this from its listener thread; emit signal so
                # the receiver runs on the Qt main thread.
                logger.info(f"keyboard hook triggered: {normalized}")
                print(f"[danmuFishpi] keyboard hook triggered: {normalized}", flush=True)
                self.triggered.emit()

            self._keyboard_hook = keyboard.add_hotkey(normalized, _hook_callback)
            self._current_hotkey = normalized
            logger.info(f"Registered hotkey via keyboard hook: {normalized}")
            print(f"[danmuFishpi] Registered hotkey (keyboard hook): {normalized}", flush=True)
            self.triggered.connect(self._on_triggered)
            return True
        except Exception as e:
            logger.warning(f"keyboard hook failed for '{normalized}': {e}; falling back to RegisterHotKey")

        # Fallback: Win32 RegisterHotKey + native event filter
        return self._register_win32(normalized, callback)

    def _register_win32(self, hotkey_str: str, callback: Callable) -> bool:
        try:
            modifiers, vk = self._parse_hotkey_win32(hotkey_str)
        except Exception as e:
            logger.error(f"Failed to parse hotkey '{hotkey_str}': {e}")
            return False

        self._hotkey_id = (_kernel32.GetCurrentProcessId() % 0x7FFF) + 100
        result = _user32.RegisterHotKey(None, self._hotkey_id, modifiers, vk)
        if not result:
            err = _kernel32.GetLastError()
            logger.error(f"RegisterHotKey failed for '{hotkey_str}' (error {err})")
            print(f"[danmuFishpi] RegisterHotKey failed for '{hotkey_str}' (error {err})", flush=True)
            return False

        self._current_hotkey = hotkey_str
        self._callback = callback
        logger.info(f"Registered hotkey via RegisterHotKey: {hotkey_str}")
        print(f"[danmuFishpi] Registered hotkey (RegisterHotKey): {hotkey_str}", flush=True)
        return True

    def unregister(self) -> None:
        """Unregister the current hotkey if any."""
        try:
            self.triggered.disconnect(self._on_triggered)
        except Exception:
            pass

        with self._lock:
            if self._keyboard_hook is not None:
                try:
                    import keyboard
                    keyboard.remove_hotkey(self._keyboard_hook)
                except Exception as e:
                    logger.warning(f"Failed to remove keyboard hook: {e}")
                self._keyboard_hook = None

            if self._current_hotkey and self._hotkey_id:
                try:
                    _user32.UnregisterHotKey(None, self._hotkey_id)
                except Exception:
                    pass

            self._current_hotkey = None
            self._callback = None

    def install_filter(self):
        """No-op for keyboard-hook primary implementation.

        Kept for API compatibility with previous RegisterHotKey-based code.
        """
        pass

    def _on_triggered(self):
        hk = self._current_hotkey or "未知"
        logger.info(f"Hotkey triggered, invoking callback ({hk})")
        print(f"[danmuFishpi] 热键触发: {hk}", flush=True)
        if self._callback:
            try:
                self._callback()
            except Exception as e:
                logger.error(f"Hotkey callback error: {e}")
                print(f"[danmuFishpi] 热键回调错误: {e}", flush=True)

    def _parse_hotkey_win32(self, hotkey_str: str):
        """Parse 'ctrl+shift+a' into (modifiers, vk) for RegisterHotKey."""
        parts = [p.strip().lower() for p in hotkey_str.split("+") if p.strip()]
        modifiers = 0
        main_key = None
        for p in parts:
            if p in ("ctrl", "control"):
                modifiers |= MOD_CONTROL
            elif p == "shift":
                modifiers |= MOD_SHIFT
            elif p in ("alt", "menu"):
                modifiers |= MOD_ALT
            elif p in ("win", "windows", "meta", "command"):
                modifiers |= MOD_WIN
            else:
                main_key = p
        if not main_key:
            raise ValueError(f"No main key in hotkey '{hotkey_str}'")
        vk = self._key_to_vk(main_key)
        return modifiers, vk

    def _key_to_vk(self, key: str) -> int:
        key = key.lower()
        if key.startswith("f") and key[1:].isdigit():
            n = int(key[1:])
            if 1 <= n <= 24:
                return VK_F1 + n - 1
        named = {
            "enter": VK_RETURN, "return": VK_RETURN,
            "space": VK_SPACE, " ": VK_SPACE,
            "esc": VK_ESCAPE, "escape": VK_ESCAPE,
            "tab": 0x09,
            "backspace": 0x08,
            "delete": 0x2E, "del": 0x2E,
            "insert": 0x2D, "ins": 0x2D,
            "home": 0x24, "end": 0x23,
            "pageup": 0x21, "pgup": 0x21,
            "pagedown": 0x22, "pgdn": 0x22,
            "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
        }
        if key in named:
            return named[key]
        if len(key) == 1:
            code = ord(key.upper())
            if (0x30 <= code <= 0x39) or (0x41 <= code <= 0x5A):
                return code
        raise ValueError(f"Unsupported hotkey key: '{key}'")
