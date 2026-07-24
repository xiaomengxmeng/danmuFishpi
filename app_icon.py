"""Shared fish icon renderer.

The same programmatic fish is used for the system tray and for the
packaged executable icon, so they never drift apart. The original artwork
was designed at 64px; :func:`render_fish` scales it to any size.
"""

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPolygonF


def render_fish(size: int = 256) -> QPixmap:
    """Return a transparent pixmap with the fish logo drawn at ``size`` px."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 64.0  # original art was laid out on a 64x64 canvas

    # Body
    p.setBrush(QColor(88, 166, 255))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(int(16 * s), int(20 * s), int(36 * s), int(24 * s))

    # Tail
    poly = QPolygonF([
        QPointF(50 * s, 32 * s),
        QPointF(60 * s, 22 * s),
        QPointF(60 * s, 42 * s),
    ])
    p.drawPolygon(poly)

    # Eye
    p.setBrush(QColor(255, 255, 255))
    p.drawEllipse(int(26 * s), int(26 * s), int(6 * s), int(6 * s))
    p.setBrush(QColor(0, 0, 0))
    p.drawEllipse(int(28 * s), int(28 * s), int(3 * s), int(3 * s))
    p.end()
    return pm
