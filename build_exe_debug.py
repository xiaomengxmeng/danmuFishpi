"""Build a console executable for debugging (keeps terminal output visible)."""

import os
import subprocess
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name", "弹幕鱼排-debug",
        "--onefile",
        "--console",
        "--noconfirm",
        "--clean",
        "--hidden-import", "PyQt6.sip",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
        "--hidden-import", "PyQt6.QtNetwork",
        "--hidden-import", "websocket",
        "--hidden-import", "httpx",
        "--hidden-import", "certifi",
        "--hidden-import", "idna",
        "--hidden-import", "keyboard",
        "--hidden-import", "mouse",
        "main.py",
    ]

    print("Building debug executable...")
    subprocess.run(cmd, check=True)
    print(f"\nDebug build complete: {os.path.join(here, 'dist', '弹幕鱼排-debug.exe')}")


if __name__ == "__main__":
    main()
