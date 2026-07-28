"""Windows auto-start via registry (HKCU\\...\\Run).

Uses the standard winreg library — zero external dependencies.
"""

import logging
import os
import sys
import winreg

logger = logging.getLogger("danmuFishpi.autostart")

_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "DanmuFishpi"


def _get_command() -> str:
    """Build the command string for the registry Run value.

    - Frozen (PyInstaller EXE): use sys.executable directly.
    - Python dev mode: use pythonw.exe + main.py absolute path.
    """
    if getattr(sys, "frozen", False):
        # EXE mode: sys.executable is the EXE path
        return sys.executable

    # Python mode: prefer pythonw.exe (no console window)
    exe_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(exe_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        python_exe = pythonw
    else:
        python_exe = sys.executable
        logger.warning(
            f"pythonw.exe not found at {pythonw}, falling back to {python_exe}"
        )

    # main.py is in the project root (same dir as this file)
    main_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    return f'"{python_exe}" "{main_py}"'


def is_enabled() -> bool:
    """Check if auto-start is enabled in the registry."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except OSError as e:
        logger.error(f"Failed to query auto-start registry: {e}")
        return False


def enable() -> bool:
    """Enable auto-start by writing to the registry. Returns True on success."""
    try:
        command = _get_command()
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
        logger.info(f"Auto-start enabled: {command}")
        return True
    except OSError as e:
        logger.error(f"Failed to enable auto-start: {e}")
        return False


def disable() -> bool:
    """Disable auto-start by removing the registry value. Returns True on success.

    If the value doesn't exist, this is still considered success.
    """
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _REG_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, _VALUE_NAME)
        logger.info("Auto-start disabled")
        return True
    except FileNotFoundError:
        # Value doesn't exist — treat as success
        logger.info("Auto-start value not found, nothing to remove")
        return True
    except OSError as e:
        logger.error(f"Failed to disable auto-start: {e}")
        return False
