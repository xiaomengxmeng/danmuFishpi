"""Global hotkey registration using the `keyboard` library.

Supports ctrl/shift/alt/win modifiers + a main key.
Fires callback on key release (matching the original Go behavior).
"""

import logging
import threading
from typing import Callable, Optional

import keyboard

logger = logging.getLogger("danmuFishpi.hotkey")


class HotkeyManager:
    """Manages a single global hotkey registration."""

    def __init__(self):
        self._current_hotkey: Optional[str] = None
        self._callback: Optional[Callable] = None
        self._lock = threading.Lock()
        self._registered = False

    def register(self, hotkey_str: str, callback: Callable) -> bool:
        """Register a global hotkey. Unregisters any previous one first.

        Args:
            hotkey_str: e.g. "ctrl+enter"
            callback: Called when the hotkey is released.

        Returns True on success, False on failure.
        """
        with self._lock:
            self.unregister()

            try:
                keyboard.add_hotkey(hotkey_str, callback,
                                    suppress=False, trigger_on_release=True)
                self._current_hotkey = hotkey_str
                self._callback = callback
                self._registered = True
                logger.info(f"Registered hotkey: {hotkey_str}")
                return True
            except Exception as e:
                logger.error(f"Failed to register hotkey '{hotkey_str}': {e}")
                return False

    def unregister(self) -> None:
        """Unregister the current hotkey if any."""
        if self._registered and self._current_hotkey:
            try:
                keyboard.remove_hotkey(self._current_hotkey)
            except Exception:
                pass
            self._registered = False
            self._current_hotkey = None
            self._callback = None
