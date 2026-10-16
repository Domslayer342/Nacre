"""Material 3 Expressive widget kit (custom painted, theme-animated)."""
import math
import time

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPalette, QPen, QRegion
from PyQt6.QtWidgets import (QAbstractButton, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QLineEdit,
                             QSizePolicy, QVBoxLayout, QWidget)

from .theme import ACCEL, DECEL, EMPHASIZED, SPRING, STANDARD, alpha, animate, draw_icon, font, mix, theme

AA = QPainter.RenderHint.Antialiasing
NOPEN = Qt.PenStyle.NoPen


def guard(owner, fn):
    """Wrap a slot connected to a long-lived signal so it's skipped once `owner` has been deleted."""
    return lambda *a: None if sip.isdeleted(owner) else fn(*a)


def painter(w):
    p = QPainter(w)
    p.setRenderHint(AA)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    p.setPen(NOPEN)
    return p


def rounded(rect, tl, tr=None, br=None, bl=None):
    """Rounded rect path with independent corner radii."""
    r = QRectF(rect)
    lim = min(r.width(), r.height()) / 2
    tl, tr, br, bl = (min(lim, max(0.0, tl if v is None else v)) for v in (tl, tr, br, bl))
    path = QPainterPath()
    path.moveTo(r.left() + tl, r.top())
    path.arcTo(r.right() - 2 * tr, r.top(), 2 * tr, 2 * tr, 90, -90)
    path.arcTo(r.right() - 2 * br, r.bottom() - 2 * br, 2 * br, 2 * br, 0, -90)
    path.arcTo(r.left(), r.bottom() - 2 * bl, 2 * bl, 2 * bl, -90, -90)
    path.arcTo(r.left(), r.top(), 2 * tl, 2 * tl, 180, -90)
    path.closeSubpath()
    return path


def wavy_arc(center, radius, a0, a1, amp, waves, phase):
    """Path along a circle from angle a0 to a1 (radians, clockwise from 12 o'clock) with a sine ripple."""
    path = QPainterPath()
    steps = max(8, int(abs(a1 - a0) * radius / 1.5))
    for i in range(steps + 1):
        th = a0 + (a1 - a0) * i / steps
        edge = min(1.0, (th - a0) * radius / 6, (a1 - th) * radius / 6 + 0.2)     # taper the ends
        r = radius + amp * max(0.0, edge) * math.sin(th * waves - phase)
        pt = QPointF(center.x() + r * math.sin(th), center.y() - r * math.cos(th))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    return path


def draw_spinner(p, rect, color, t, width=2.5):
    """M3 Expressive indeterminate circular progress: a wavy arc that grows, shrinks and spins."""
    p.save()
    rect = QRectF(rect).adjusted(width, width, -width, -width)
    radius = rect.width() / 2 - width * 0.4
    sweep = math.radians(20 + 250 * (0.5 - 0.5 * math.cos(t * 2.6)))
    start = math.radians(t * 300 + 125 * (1 - math.cos(t * 2.6)))
    waves = max(5, round(math.tau * radius / 7))
    p.setPen(QPen(color, width, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(wavy_arc(rect.center(), radius, start, start + sweep, width * 0.45, waves, t * 7))
    p.restore()


def draw_wavy_ring(p, rect, frac, color, track, width=2.5, phase=0.0):
    """Determinate circular progress: wavy arc up to `frac`, flat track for the rest, small gaps between."""
    p.save()
    rect = QRectF(rect).adjusted(width, width, -width, -width)
    radius = rect.width() / 2 - width * 0.4
    frac = max(0.0, min(1.0, frac))
    gap = math.radians(14) if 0 < frac < 1 else 0
    p.setBrush(Qt.BrushStyle.NoBrush)
    if frac < 1:
        p.setPen(QPen(track, width, cap=Qt.PenCapStyle.RoundCap))
        a0, a1 = math.tau * frac + gap, math.tau - gap
        if a1 > a0:
            p.drawPath(wavy_arc(rect.center(), radius, a0, a1, 0, 1, 0))
    if frac > 0:
        p.setPen(QPen(color, width, cap=Qt.PenCapStyle.RoundCap, join=Qt.PenJoinStyle.RoundJoin))
        p.drawPath(wavy_arc(rect.center(), radius, 0, max(0.05, math.tau * frac),
                            width * 0.45, max(5, round(math.tau * radius / 7)), phase))
    p.restore()


class Label(QLabel):
    def __init__(self, text="", role="onSurface", size=14, weight=400, wrap=False, parent=None):
        super().__init__(text, parent)
        self.role = role
        self.setFont(font(size, weight))
        self.setWordWrap(wrap)
        theme.changed.connect(self._recolor)
        self._recolor()

    def set_role(self, role):
        self.role = role
        self._recolor()

    def _recolor(self):
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.WindowText, theme[self.role])
        self.setPalette(pal)


class _Ripple:
    def __init__(self, pos, radius):
        self.pos, self.max, self.r, self.a, self.fading = pos, radius, 0.0, 0.12, False


class Button(QAbstractButton):
    """kind: standard | filled | tonal | outlined | text | chip | danger"""

    def __init__(self, icon=None, text="", kind="standard", size=40, tip=None, checkable=False,
                 parent=None, icon_px=None, width=None, radius=None, fill_on_check=True):
        super().__init__(parent)
        self.icon_name, self.kind, self.size_ = icon, kind, size
        self.icon_px = icon_px or (20 if size < 44 else 24)
        self.fixed_w, self.radius, self.fill_on_check = width, radius, fill_on_check
        self._hover = self._press = self._fill = 0.0
        self.ripples, self.badge, self.ring, self.spin = [], None, None, None
        self.setText(text)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if tip:
            self.setToolTip(tip)
        self.toggled.connect(lambda on: animate(self, "_fill", 1.0 if on else 0.0, 300, EMPHASIZED))
        theme.changed.connect(self.update)

    def setChecked(self, on):
        """Programmatic change: jump without animating."""
        super().setChecked(on)
        a = self.__dict__.get("_anim__fill")
        if a is not None:
            try:
                a.stop()
            except RuntimeError:
                pass
        self._fill = 1.0 if on else 0.0
        self.update()

    def set_icon(self, name):
        self.icon_name = name
        self.update()

    def sizeHint(self):
        if self.fixed_w:
            return QSize(self.fixed_w, self.size_)
        if not self.text():
            return QSize(self.size_, self.size_)
        pad = 12 if self.kind == "chip" else 20
        iw = self.icon_px + 8 if (self.icon_name or self.kind == "chip") else 0
        return QSize(QFontMetrics(font(14, 500)).horizontalAdvance(self.text()) + 2 * pad + iw + 2, self.size_)

    def _colors(self, on):
        c = theme
        k = self.kind
        clear = QColor(0, 0, 0, 0)
        if k == "filled":
            return c["primary"], c["onPrimary"]
        if k == "tonal":
            return (c["primary"], c["onPrimary"]) if on else (c["secondaryContainer"], c["onSecondaryContainer"])
        if k == "danger":
            return c["errorContainer"], c["onErrorContainer"]
        if k in ("outlined", "text"):
            return clear, c["primary"]
        if on:
            return c["secondaryContainer"], c["onSecondaryContainer"]
        return clear, (c["onSurfaceVariant"] if k != "chip" else c["onSurface"])

    def colors(self):
        bg, fg = self._colors(False)
        if self.isCheckable() and self._fill > 0:
            bg1, fg1 = self._colors(True)
            bg = alpha(bg1, self._fill) if bg.alpha() == 0 else mix(bg, bg1, self._fill)
            fg = mix(fg, fg1, self._fill)
        if not self.isEnabled():
            bg = alpha(theme["onSurface"], 0.1) if bg.alpha() else bg
            fg = alpha(theme["onSurface"], 0.38)
        return bg, fg

    def enterEvent(self, e):
        animate(self, "_hover", 1, 150, STANDARD)

    def leaveEvent(self, e):
        animate(self, "_hover", 0, 220, STANDARD)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position()
            far = max(math.hypot(pos.x() - x, pos.y() - y)
                      for x in (0, self.width()) for y in (0, self.height()))
            rp = _Ripple(pos, far)
            self.ripples.append(rp)
            animate(rp, "r", 1, 550, DECEL, start=0, step=self.update, owner=self)
            animate(self, "_press", 1, 180, STANDARD)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        animate(self, "_press", 0, 500, SPRING)
        for rp in self.ripples:
            if not rp.fading:
                rp.fading = True
                animate(rp, "a", 0, 450, STANDARD, step=self.update, owner=self,
                        done=lambda rp=rp: rp in self.ripples and self.ripples.remove(rp))
        super().mouseReleaseEvent(e)

    def shape_radius(self):
        h = self.height()
        base = h / 2 if self.radius is None else theme.r(self.radius)
        pressed = min(base, h * 0.26)
        return base + (pressed - base) * self._press

    def paintEvent(self, e):
        p = painter(self)
        r = QRectF(self.rect())
        path = rounded(r, self.shape_radius())
        bg, fg = self.colors()
        if bg.alpha():
            p.fillPath(path, bg)
        if self.kind == "outlined" or (self.kind == "chip" and self._fill < 1):
            col = theme["outlineVariant" if self.kind == "chip" else "outline"]
            p.setPen(QPen(alpha(col, 1 - self._fill if self.kind == "chip" else 1), 1))
            p.drawPath(rounded(r.adjusted(.5, .5, -.5, -.5), self.shape_radius()))
            p.setPen(NOPEN)
        layer = 0.08 * self._hover + 0.1 * self._press
        if layer and self.isEnabled():
            p.fillPath(path, alpha(fg, layer))
        if self.ripples:
            p.save()
            p.setClipPath(path)
            for rp in self.ripples:
                p.setBrush(alpha(fg, rp.a))
                p.drawEllipse(rp.pos, rp.r * rp.max, rp.r * rp.max)
            p.restore()
        fill = self._fill if self.fill_on_check else 0.0
        icon = self.icon_name
        if self.kind == "chip" and self.isChecked() and not icon:
            icon = "check"
        if self.spin is not None:
            draw_spinner(p, QRectF(0, 0, 20, 20).translated(r.center() - QPointF(10, 10)), fg, self.spin)
        elif self.text():
            p.setFont(font(14, 500))
            tw = p.fontMetrics().horizontalAdvance(self.text())
            iw = self.icon_px + 8 if icon else 0
            x = (r.width() - tw - iw) / 2
            if icon:
                draw_icon(p, QRectF(x, 0, self.icon_px, r.height()), icon, fg, self.icon_px, fill)
            p.setFont(font(14, 500))
            p.setPen(fg)
            p.drawText(QRectF(x + iw, 0, tw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter, self.text())
        elif icon:
            draw_icon(p, r, icon, fg, self.icon_px, fill)
        if self.ring is not None:
            draw_wavy_ring(p, r.adjusted(2, 2, -2, -2), self.ring, theme["primary"], theme["secondaryContainer"],
                           2.5, time.monotonic() * 5)
        if self.badge:
            p.setPen(NOPEN)
            txt = str(self.badge)
            p.setFont(font(10, 600))
            bw = max(16, p.fontMetrics().horizontalAdvance(txt) + 8)
            br = QRectF(r.right() - bw - 1, 2, bw, 16)
            p.setBrush(theme["error"])
            p.drawRoundedRect(br, 8, 8)
            p.setPen(theme["onError"])
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, txt)


class Switch(QAbstractButton):
    """M3 switch: handle grows when on, shows a check, springs into place."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self._pos = 1.0 if checked else 0.0
        self._hover = self._press = 0.0
        self.setFixedSize(56, 40)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggled.connect(lambda on: animate(self, "_pos", 1 if on else 0, 420, SPRING))
        theme.changed.connect(self.update)

    def set_silent(self, on):
        self.blockSignals(True)
        self.setChecked(on)
        self.blockSignals(False)
        self._pos = 1.0 if on else 0.0
        self.update()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def enterEvent(self, e):
        animate(self, "_hover", 1, 150, STANDARD)

    def leaveEvent(self, e):
        animate(self, "_hover", 0, 200, STANDARD)

    def mousePressEvent(self, e):
        animate(self, "_press", 1, 150, STANDARD)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        animate(self, "_press", 0, 300, STANDARD)
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        t = max(0.0, min(1.0, self._pos))
        p.setBrush(mix(c["surfaceContainerHighest"], c["primary"], t))
        p.drawRoundedRect(QRectF(2, 4, 52, 32), 16, 16)
        if t < 1:
            p.setPen(QPen(alpha(c["outline"], 1 - t), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(3, 5, 50, 30), 15, 15)
            p.setPen(NOPEN)
        cx = 18 + 20 * self._pos
        d = 16 + 8 * t
        d += (28 - d) * self._press
        if self._hover or self._press:
            p.setBrush(alpha(mix(c["onSurface"], c["primary"], t), 0.08 * self._hover + 0.1 * self._press))
            p.drawEllipse(QPointF(cx, 20), 20, 20)
        p.setBrush(mix(c["outline"], c["onPrimary"], t))
        p.drawEllipse(QPointF(cx, 20), d / 2, d / 2)
        if t > 0.4:
            draw_icon(p, QRectF(cx - 8, 12, 16, 16), "check", alpha(c["onPrimaryContainer"], (t - 0.4) / 0.6), 16, 0, 600)


class Segmented(QWidget):
    """Segmented button with a sliding, shape-clipped selection."""
    changed = pyqtSignal(object)

    def __init__(self, options, value, parent=None):
        super().__init__(parent)
        self.opts = list(options)
        self.value = value
        idx = [o[0] for o in self.opts].index(value) if value in [o[0] for o in self.opts] else 0
        self._sel = float(idx)
        self._hover = -1
        self.setFixedHeight(40)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        fm = QFontMetrics(font(14, 500))
        self.widths = [max(64, fm.horizontalAdvance(o[1]) + 56) for o in self.opts]
        self.setFixedWidth(sum(self.widths))
        theme.changed.connect(self.update)

    def set_value(self, value):
        keys = [o[0] for o in self.opts]
        if value in keys and value != self.value:
            self.value = value
            animate(self, "_sel", keys.index(value), 450, SPRING)

    def _seg(self, i):
        x = sum(self.widths[:int(i)])
        return QRectF(x, 0, self.widths[int(i)], self.height())

    def _at(self, x):
        acc = 0
        for i, w in enumerate(self.widths):
            acc += w
            if x < acc:
                return i
        return len(self.widths) - 1

    def mouseMoveEvent(self, e):
        self._hover = self._at(e.position().x())
        self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position().x())
        if self.opts[i][0] != self.value:
            self.set_value(self.opts[i][0])
            self.changed.emit(self.value)

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        r = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        pill = rounded(r, r.height() / 2)
        p.save()
        p.setClipPath(pill)
        lo = int(math.floor(max(0, min(self._sel, len(self.opts) - 1))))
        hi = min(lo + 1, len(self.opts) - 1)
        f = self._sel - lo
        a, b = self._seg(lo), self._seg(hi)
        sel = QRectF(a.x() + (b.x() - a.x()) * f, 0, a.width() + (b.width() - a.width()) * f, self.height())
        if self._hover >= 0:
            p.fillRect(self._seg(self._hover), alpha(c["onSurface"], 0.06))
        p.fillRect(sel, c["secondaryContainer"])
        p.restore()
        p.setPen(QPen(c["outline"], 1))
        p.drawPath(pill)
        for i in range(1, len(self.opts)):
            x = sum(self.widths[:i]) + .5
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        p.setFont(font(14, 500))
        for i, o in enumerate(self.opts):
            seg = self._seg(i)
            near = max(0.0, 1 - abs(self._sel - i))
            fg = mix(c["onSurface"], c["onSecondaryContainer"], near)
            tw = p.fontMetrics().horizontalAdvance(o[1])
            iw = 26 * near
            x = seg.x() + (seg.width() - tw - iw) / 2
            if near > 0.05:
                draw_icon(p, QRectF(x, 0, 18, seg.height()), o[2] if len(o) > 2 else "check", alpha(fg, near), 18)
            p.setPen(fg)
            p.setFont(font(14, 500))
            p.drawText(QRectF(x + iw, 0, tw + 2, seg.height()), Qt.AlignmentFlag.AlignVCenter, o[1])


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=8):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, rect, test):
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self.items:
            sh = it.sizeHint()
            if x + sh.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), sh))
            x += sh.width() + sp
            line = max(line, sh.height())
        return y + line - rect.y()


class Flyout(QWidget):
    """Popup surface with an M3 expand-from-anchor + fade entrance."""
    M = 18

    def __init__(self, anchor_window, width=360, radius=24):
        super().__init__(anchor_window, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.body = QWidget(self)
        self.body.setFixedWidth(width)
        self.lay = QVBoxLayout(self.body)
        self.lay.setContentsMargins(12, 12, 12, 12)
        self.lay.setSpacing(2)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(self.M, self.M, self.M, self.M)
        outer.addWidget(self.body)
        self.radius = radius
        self._t = 1.0
        self._eff = None
        theme.changed.connect(self.update)

    def popup(self, anchor, align="right"):
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, anchor.height() + 4))
        x = g.x() + anchor.width() - self.width() + self.M if align == "right" else g.x() - self.M
        win = anchor.window()
        wl = win.mapToGlobal(QPoint(0, 0)).x()
        x = max(wl - self.M + 4, min(x, wl + win.width() - self.width() + self.M - 4))
        self.move(x, g.y() - self.M)
        self._t = 0.0
        self._set_effect()
        self._step()
        self.show()
        animate(self, "_t", 1, 380, EMPHASIZED, start=0, step=self._step, done=self._opened)

    def _set_effect(self):
        self._eff = QGraphicsOpacityEffect(self.body)
        self.body.setGraphicsEffect(self._eff)

    def _step(self):
        if self._eff is not None:
            try:
                self._eff.setOpacity(min(1.0, self._t * 1.5))
            except RuntimeError:
                self._eff = None
        vis = int(self.body.height() * (0.6 + 0.4 * self._t))
        self.body.setMask(QRegion(0, 0, self.body.width(), max(1, vis)))
        self.update()

    def _opened(self):
        self.body.setGraphicsEffect(None)
        self._eff = None
        self.body.clearMask()

    def dismiss(self):
        if self.isVisible():
            self._set_effect()
            animate(self, "_t", 0, 160, ACCEL, step=self._step, done=self.hide)

    def hideEvent(self, e):
        self._opened()
        self._t = 1.0
        super().hideEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        b = QRectF(self.body.geometry())
        b.setHeight(b.height() * (0.6 + 0.4 * self._t))
        rad = theme.r(self.radius)
        for i in range(7):
            s = 1 + i * 2.2
            p.setBrush(alpha(theme["shadow"], 0.05 * (1 - i / 7) * min(1, self._t * 2)))
            p.drawRoundedRect(b.adjusted(-s, -s + 3, s, s + 5), rad + s, rad + s)
        p.setBrush(theme["surfaceContainer"])
        p.drawRoundedRect(b, rad, rad)


class Snackbar(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.hide()
        self._t = 0.0
        self.text, self.action, self.cb = "", None, None
        self.timer = QTimer(self, singleShot=True, timeout=self.dismiss)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        theme.changed.connect(self.update)

    def show_msg(self, text, action=None, cb=None, ms=4500):
        self.text, self.action, self.cb = text, action, cb
        fm = QFontMetrics(font(14))
        w = fm.horizontalAdvance(text) + 40 + (QFontMetrics(font(14, 600)).horizontalAdvance(action) + 32 if action else 0)
        self.resize(min(w, self.parentWidget().width() - 32), 48)
        self.show()
        self.raise_()
        animate(self, "_t", 1, 420, EMPHASIZED, step=self._place)
        self.timer.start(ms)

    def _place(self):
        par = self.parentWidget()
        self.move((par.width() - self.width()) // 2, par.height() - int((self.height() + 20) * self._t))
        self.update()

    def dismiss(self):
        animate(self, "_t", 0, 220, ACCEL, step=self._place, done=self.hide)

    def _action_rect(self):
        aw = QFontMetrics(font(14, 600)).horizontalAdvance(self.action or "") + 24
        return QRectF(self.width() - aw - 8, 6, aw, 36)

    def mousePressEvent(self, e):
        if self.action and self._action_rect().contains(e.position()) and self.cb:
            self.cb()
        self.dismiss()

    def paintEvent(self, e):
        p = painter(self)
        p.setOpacity(min(1.0, self._t * 1.6))
        p.setBrush(theme["inverseSurface"])
        p.drawRoundedRect(QRectF(self.rect()), theme.r(14), theme.r(14))
        p.setPen(theme["inverseOnSurface"])
        p.setFont(font(14))
        right = self._action_rect().left() if self.action else self.width() - 16
        p.drawText(QRectF(20, 0, right - 20, self.height()), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideRight, int(right - 20)))
        if self.action:
            p.setPen(theme["inversePrimary"])
            p.setFont(font(14, 600))
            p.drawText(self._action_rect(), Qt.AlignmentFlag.AlignCenter, self.action)


class Row(QAbstractButton):
    """List / menu row: leading icon, title, optional subtitle and trailing text."""

    def __init__(self, icon, title, subtitle=None, trailing=None, height=None, parent=None):
        super().__init__(parent)
        self.icon_name, self.title, self.sub, self.trailing = icon, title, subtitle, trailing
        self.qicon = None
        self._hover = 0.0
        self.setFixedHeight(height or (56 if subtitle else 44))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        theme.changed.connect(self.update)

    def enterEvent(self, e):
        animate(self, "_hover", 1, 120, STANDARD)

    def leaveEvent(self, e):
        animate(self, "_hover", 0, 200, STANDARD)

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        r = QRectF(self.rect())
        if self._hover or self.isDown():
            p.setBrush(alpha(c["onSurface"], 0.08 * self._hover + (0.06 if self.isDown() else 0)))
            p.drawRoundedRect(r, theme.r(12), theme.r(12))
        x = 12
        if self.qicon is not None:
            self.qicon.paint(p, QRect(14, int(r.height() / 2 - 10), 20, 20))
            x = 48
        elif self.icon_name:
            draw_icon(p, QRectF(10, 0, 24, r.height()), self.icon_name, c["onSurfaceVariant"], 22)
            x = 48
        tw = r.width() - x - 12
        if self.trailing:
            p.setFont(font(12))
            ttw = p.fontMetrics().horizontalAdvance(self.trailing)
            p.setPen(c["onSurfaceVariant"])
            p.drawText(QRectF(r.width() - ttw - 14, 0, ttw + 2, r.height()), Qt.AlignmentFlag.AlignVCenter, self.trailing)
            tw -= ttw + 12
        p.setFont(font(14))
        p.setPen(c["onSurface"])
        el = lambda s: p.fontMetrics().elidedText(s, Qt.TextElideMode.ElideRight, int(tw))
        if self.sub:
            p.drawText(QRectF(x, 8, tw, 22), Qt.AlignmentFlag.AlignVCenter, el(self.title))
            p.setFont(font(12))
            p.setPen(c["onSurfaceVariant"])
            p.drawText(QRectF(x, 28, tw, 20), Qt.AlignmentFlag.AlignVCenter, el(self.sub))
        else:
            p.drawText(QRectF(x, 0, tw, r.height()), Qt.AlignmentFlag.AlignVCenter, el(self.title))


class Divider(QWidget):
    def __init__(self, margin=8, parent=None):
        super().__init__(parent)
        self.setFixedHeight(9)
        self.m = margin
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(QRectF(self.m, 4, self.width() - 2 * self.m, 1), theme["outlineVariant"])


class TextField(QWidget):
    def __init__(self, text="", placeholder="", icon=None, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self._focus = 0.0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(44 if icon else 16, 0, 12, 0)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFont(font(14))
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        self.setFixedHeight(44)
        theme.changed.connect(self.update)

    def eventFilter(self, obj, e):
        if e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            animate(self, "_focus", 1 if e.type() == e.Type.FocusIn else 0, 200, STANDARD)
        return False

    def paintEvent(self, e):
        p = painter(self)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(theme["surfaceContainerHighest"])
        p.setPen(QPen(mix(theme["outlineVariant"], theme["primary"], self._focus), 1 + self._focus))
        p.drawRoundedRect(r, theme.r(14), theme.r(14))
        if self.icon_name:
            draw_icon(p, QRectF(12, 0, 24, self.height()), self.icon_name, theme["onSurfaceVariant"], 20)


def hbox(*widgets, spacing=8, margins=(0, 0, 0, 0)):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    return w


def is_wayland():
    return QGuiApplication.platformName().startswith("wayland")
