"""Nacre's icon: an M3 Expressive scalloped 'cookie' with an iridescent sheen, a nautilus spiral and a pearl.
Drawn from the live Material You scheme (window icon), and exportable as SVG for the launcher."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient

LOBES, AMP = 9, 0.065


def cookie(cx, cy, r, n=360):
    pts = []
    for i in range(n):
        th = math.tau * i / n
        rr = r * (1 + AMP * math.cos(LOBES * th + math.pi / 2))
        pts.append((cx + rr * math.cos(th), cy + rr * math.sin(th)))
    return pts


def spiral(cx, cy, r_out, turns=2.15, n=220):
    """Logarithmic (nautilus) spiral, outer end at the top-right, winding inward."""
    b = math.log(r_out / (r_out * 0.085)) / (math.tau * turns)
    pts = []
    for i in range(n + 1):
        th = math.tau * turns * i / n
        r = r_out * math.exp(-b * th)
        a = -math.pi / 4 + th
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _path(pts, close=False):
    p = QPainterPath(QPointF(*pts[0]))
    for x, y in pts[1:]:
        p.lineTo(x, y)
    if close:
        p.closeSubpath()
    return p


def paint(p, size, c1, c2, fg, pearl):
    s = size
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = _path(cookie(s / 2, s / 2, s * 0.42), True)
    g = QLinearGradient(0, 0, s, s)
    g.setColorAt(0, c1)
    g.setColorAt(0.55, QColor.fromRgbF(*[(a + b) / 2 for a, b in zip(c1.getRgbF(), c2.getRgbF())]))
    g.setColorAt(1, c2)
    p.fillPath(body, g)
    sheen = QRadialGradient(s * 0.3, s * 0.25, s * 0.55)          # mother-of-pearl highlight
    sheen.setColorAt(0, QColor(255, 255, 255, 70))
    sheen.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(body, sheen)
    p.setPen(QPen(fg, max(1.2, s * 0.055), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawPath(_path(spiral(s / 2, s / 2 + s * 0.01, s * 0.27)))
    p.setPen(Qt.PenStyle.NoPen)
    pr = s * 0.065
    rg = QRadialGradient(s / 2 - pr * 0.35, s / 2 - pr * 0.35, pr * 1.4)
    rg.setColorAt(0, QColor("white"))
    rg.setColorAt(1, pearl)
    p.setBrush(rg)
    p.drawEllipse(QRectF(s / 2 - pr, s / 2 - pr + s * 0.01, 2 * pr, 2 * pr))


def pixmap(size, scheme):
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    paint(p, size, scheme["primary"], scheme["tertiary"], scheme["onPrimary"], scheme["primaryContainer"])
    p.end()
    return pm


def app_icon(scheme):
    ic = QIcon()
    for s in (16, 24, 32, 48, 64, 128, 256):
        ic.addPixmap(pixmap(s, scheme))
    return ic


def svg(c1="#6750A4", c2="#7D5260", fg="#FFFFFF", pearl="#EADDFF", size=256):
    s = size
    pts = lambda ps: " ".join(f"{x:.2f},{y:.2f}" for x, y in ps)
    pr = s * 0.065
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {s} {s}" width="{s}" height="{s}">
  <defs>
    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{c1}"/><stop offset="1" stop-color="{c2}"/></linearGradient>
    <radialGradient id="sheen" cx="0.3" cy="0.25" r="0.55"><stop offset="0" stop-color="#fff" stop-opacity=".28"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
    <radialGradient id="pearl" cx="0.35" cy="0.35" r="0.7"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="{pearl}"/></radialGradient>
  </defs>
  <polygon points="{pts(cookie(s / 2, s / 2, s * 0.42))}" fill="url(#g)"/>
  <polygon points="{pts(cookie(s / 2, s / 2, s * 0.42))}" fill="url(#sheen)"/>
  <polyline points="{pts(spiral(s / 2, s / 2 + s * 0.01, s * 0.27))}" fill="none" stroke="{fg}" stroke-width="{s * 0.055:.2f}" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="{s / 2}" cy="{s / 2 + s * 0.01}" r="{pr:.2f}" fill="url(#pearl)"/>
</svg>
"""
