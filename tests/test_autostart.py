"""Tests for autostart module (registry-based auto-start)."""
import autostart
from unittest.mock import patch, MagicMock
import winreg


REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "DanmuFishpi"


def test_enable_writes_registry():
    """enable() should write a value to the registry."""
    mock_key = MagicMock()
    with patch("winreg.OpenKey", return_value=mock_key):
        with patch("winreg.SetValueEx") as mock_set:
            with patch("winreg.CloseKey"):
                result = autostart.enable()
    assert result is True
    mock_set.assert_called_once()
    args = mock_set.call_args
    assert args[0][1] == VALUE_NAME


def test_disable_deletes_registry():
    """disable() should delete the registry value."""
    mock_key = MagicMock()
    with patch("winreg.OpenKey", return_value=mock_key):
        with patch("winreg.DeleteValue") as mock_del:
            with patch("winreg.CloseKey"):
                result = autostart.disable()
    assert result is True
    mock_del.assert_called_once()


def test_disable_nonexistent_returns_true():
    """disable() should return True even if value doesn't exist (FileNotFoundError)."""
    mock_key = MagicMock()
    with patch("winreg.OpenKey", return_value=mock_key):
        with patch("winreg.DeleteValue", side_effect=FileNotFoundError):
            with patch("winreg.CloseKey"):
                result = autostart.disable()
    assert result is True


def test_is_enabled_true():
    """is_enabled() returns True when registry value exists."""
    mock_key = MagicMock()
    with patch("winreg.OpenKey", return_value=mock_key):
        with patch("winreg.QueryValueEx", return_value=("some command", winreg.REG_SZ)):
            with patch("winreg.CloseKey"):
                result = autostart.is_enabled()
    assert result is True


def test_is_enabled_false():
    """is_enabled() returns False when registry value doesn't exist."""
    with patch("winreg.OpenKey", side_effect=FileNotFoundError):
        result = autostart.is_enabled()
    assert result is False


def test_get_command_frozen_exe():
    """When frozen (PyInstaller), command should be sys.executable."""
    with patch.object(autostart.sys, "frozen", True, create=True):
        with patch.object(autostart.sys, "executable", "C:\\path\\to\\app.exe"):
            cmd = autostart._get_command()
    assert cmd == "C:\\path\\to\\app.exe"


def test_get_command_python_mode():
    """In Python mode, command should be 'pythonw.exe' 'main.py'."""
    with patch.object(autostart.sys, "frozen", False, create=True):
        with patch.object(autostart.sys, "executable", "C:\\Python310\\python.exe"):
            with patch("os.path.exists", return_value=True):
                cmd = autostart._get_command()
    assert "pythonw.exe" in cmd
    assert "main.py" in cmd
