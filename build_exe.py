"""Build a standalone executable with PyInstaller."""

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
        "--name", "弹幕鱼排",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--hidden-import", "PyQt6.sip",
        "--hidden-import", "PyQt6.QtCore",
        "--hidden-import", "PyQt6.QtGui",
        "--hidden-import", "PyQt6.QtWidgets",
        "--hidden-import", "websocket",
        "--hidden-import", "httpx",
        "--hidden-import", "keyboard",
        "main.py",
    ]

    print("Building executable...")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"\nBuild complete. Executable: {os.path.join(here, 'dist', '弹幕鱼排.exe')}")


if __name__ == "__main__":
    main()
