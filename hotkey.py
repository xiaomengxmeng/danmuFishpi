"""Global hotkey registration using the Win32 RegisterHotKey API.

Works without admin privileges and integrates with the Qt event loop via
QAbstractNativeEventFilter.
"""

import ctypes
import logging
from ctypes import wintypes
from typing import Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QAbstractNativeEventFilter

logger = logging.getLogger("danmuFishpi.hotkey")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Win32 constants
WM_HOTKEY = 0x0312

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_F1 = 0x70
VK_ESCAPE = 0x1B


class NativeHotkeyFilter(QObject, QAbstractNativeEventFilter):
    """Native event filter that listens for WM_HOTKEY messages."""

    triggered = pyqtSignal()

    def nativeEventFilter(self, event_type, message):
        et = event_type.toString() if hasattr(event_type, "toString") else str(event_type)
        # Log native messages that might be hotkey events
        if et not in ("windows_timer_MSG",):
            logger.debug(f"nativeEventFilter event_type={et}")
        if et in ("windows_generic_MSG", "windows_dispatcher_MSG"):
            try:
                msg_ptr = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG))
                msg = msg_ptr.contents
            except Exception:
                return False, 0
            if msg.message == WM_HOTKEY:
                logger.info(f"WM_HOTKEY received (id={msg.wParam})")
                print(f"[danmuFishpi] WM_HOTKEY received id={msg.wParam}", flush=True)
                self.triggered.emit()
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    """Manages a single global hotkey via RegisterHotKey."""

    triggered = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_hotkey: Optional[str] = None
        self._callback: Optional[Callable] = None
        self._hotkey_id: int = 1
        self._filter = NativeHotkeyFilter()
        self._filter.triggered.connect(self._on_triggered)

    def _parse_hotkey(self, hotkey_str: str):
        """Parse 'ctrl+shift+a' into (modifiers, vk)."""
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
        # Function keys
        if key.startswith("f") and key[1:].isdigit():
            n = int(key[1:])
            if 1 <= n <= 24:
                return VK_F1 + n - 1
        # Named keys
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
        # Letters and digits
        if len(key) == 1:
            code = ord(key.upper())
            if (0x30 <= code <= 0x39) or (0x41 <= code <= 0x5A):
                return code
        raise ValueError(f"Unsupported hotkey key: '{key}'")

    def register(self, hotkey_str: str, callback: Callable) -> bool:
        """Register a global hotkey. Unregisters any previous one first."""
        self.unregister()

        try:
            modifiers, vk = self._parse_hotkey(hotkey_str)
        except Exception as e:
            logger.error(f"Failed to parse hotkey '{hotkey_str}': {e}")
            return False

        # Use a unique hotkey id based on current process id to avoid collisions
        self._hotkey_id = (kernel32.GetCurrentProcessId() % 0x7FFF) + 100
        result = user32.RegisterHotKey(None, self._hotkey_id, modifiers, vk)
        if not result:
            err = kernel32.GetLastError()
            logger.error(f"RegisterHotKey failed for '{hotkey_str}' (error {err})")
            return False

        self._current_hotkey = hotkey_str
        self._callback = callback
        logger.info(f"Registered hotkey: {hotkey_str}")
        return True

    def unregister(self) -> None:
        """Unregister the current hotkey if any."""
        if self._current_hotkey:
            try:
                user32.UnregisterHotKey(None, self._hotkey_id)
            except Exception:
                pass
            self._current_hotkey = None
            self._callback = None

    def install_filter(self):
        """Install the native event filter (call from the Qt main thread)."""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.installNativeEventFilter(self._filter)

    def _on_triggered(self):
        logger.info("Hotkey triggered, invoking callback")
        print(f"[danmuFishpi] 热键触发: {self._current_hotkey}", flush=True)
        if self._callback:
            try:
                self._callback()
            except Exception as e:
                logger.error(f"Hotkey callback error: {e}")
                print(f"[danmuFishpi] 热键回调错误: {e}", flush=True)
