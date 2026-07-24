"""Build a standalone executable with PyInstaller."""

import os
import subprocess
import sys


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    # Generate the fish icon (app.ico) before building.
    import build_icon
    if build_icon.main() != 0:
        raise RuntimeError("Failed to generate app.ico")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name", "弹幕鱼排",
        "--onefile",
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", "app.ico",
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

    print("Building executable...")
    print(" ".join(cmd))
    subprocess.run(cmd, check=True)
    print(f"\nBuild complete. Executable: {os.path.join(here, 'dist', '弹幕鱼排.exe')}")


if __name__ == "__main__":
    main()
