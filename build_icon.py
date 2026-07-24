"""Generate ``app.ico`` (the fish logo) for embedding into the packaged exe.

PyInstaller only stores the raw image bytes from the .ico into the exe; Windows
then renders them. Windows Explorer is unreliable with ICO files that contain
only PNG-compressed frames, so we emit a standard, fully BMP-based ICO with
several sizes (16/32/48/256). No third-party deps beyond PyQt6 (already required).
"""

import ctypes
import os
import struct
import sys

# Render headlessly without a visible window.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

from app_icon import render_fish  # noqa: E402


def _rgba_bytes(size: int):
    """Return top-down RGBA pixel bytes (w*h*4) for the fish at ``size``."""
    pm = render_fish(size)
    img = pm.toImage().convertToFormat(
        __import__("PyQt6.QtGui", fromlist=["QImage"]).QImage.Format.Format_RGBA8888
    )
    w, h = img.width(), img.height()
    n = img.sizeInBytes()
    buf = ctypes.create_string_buffer(n)
    ctypes.memmove(buf, int(img.constBits()), n)
    return bytes(buf), w, h


def _dib_from_rgba(rgba: bytes, w: int, h: int) -> bytes:
    """Build a 32bpp BGRA DIB (XOR + zero AND mask) for an icon frame."""
    xor = bytearray()
    stride = w * 4
    # DIB rows are stored bottom-up.
    for y in range(h - 1, -1, -1):
        row = rgba[y * stride:(y + 1) * stride]
        for i in range(0, stride, 4):
            r, g, b, a = row[i], row[i + 1], row[i + 2], row[i + 3]
            xor += bytes((b, g, r, a))
    # AND mask: 1 bpp, zero (transparency handled by the alpha channel).
    and_row_bytes = ((w + 31) // 32) * 4
    and_mask = b"\x00" * (and_row_bytes * h)
    header = struct.pack("<IiiHHIIiiII", 40, w, 2 * h, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + bytes(xor) + and_mask


def _make_ico(frames, out_path: str) -> None:
    with open(out_path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(frames)))
        offset = 6 + 16 * len(frames)
        for rgba, w, h in frames:
            dib = _dib_from_rgba(rgba, w, h)
            bw = w if w < 256 else 0
            bh = h if h < 256 else 0
            f.write(struct.pack("<BBBBHHII", bw, bh, 0, 0, 1, 32, len(dib), offset))
            offset += len(dib)
        for rgba, w, h in frames:
            f.write(_dib_from_rgba(rgba, w, h))


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here)

    app = QApplication(sys.argv)
    frames = []
    for size in (16, 32, 48, 256):
        frames.append(_rgba_bytes(size))

    out = os.path.join(here, "app.ico")
    _make_ico(frames, out)
    print(f"Wrote {out} with sizes 16/32/48/256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
