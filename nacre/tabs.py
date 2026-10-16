"""Animated tab strip: grow-in/shrink-out tabs, drag to reorder, loading spinners, audio badges."""
import math

from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QPen
from PyQt6.QtWidgets import QToolTip, QWidget

from .config import cfg
from .theme import alpha, draw_icon, font, theme, ticker
from .widgets import draw_spinner, painter

TOP, TAB_H, PAD, GAP, MAXW, MINW = 6, 36, 8, 4, 236, 36


class TabItem:
    def __init__(self, title="New Tab"):
        self.title, self.icon, self.symbol = title, None, None
        self.loading = self.audible = self.muted = self.dying = self.split = False
        self.x = None
        self.w = self.tx = self.tw = self.op = self.hover = self.hover_t = 0.0


class TabStrip(QWidget):
    currentChanged = pyqtSignal(int)
    closeRequested = pyqtSignal(int)
    newTabRequested = pyqtSignal()
    moved = pyqtSignal(int, int)
    muteRequested = pyqtSignal(int)
    emptyPressed = pyqtSignal(object)
    emptyDoubleClicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs, self.ghosts = [], []
        self.cur = None
        self.drag = None
        self.t = 0.0
        self.new_x = self.new_tx = float(PAD)
        self.new_hover = 0.0
        self.setMouseTracking(True)
        self.setFixedHeight(TOP + TAB_H + 4)
        theme.changed.connect(self.update)

    # ── model API ──
    def insert(self, i, item):
        if self.tabs:
            ref = self.tabs[min(i, len(self.tabs) - 1)]
            item.x = ref.x + (ref.w if i >= len(self.tabs) else 0)
        else:
            item.x = float(PAD)
        self.tabs.insert(i, item)
        self._relayout()

    def remove(self, i):
        it = self.tabs.pop(i)
        it.dying = True
        it.tw = 0.0
        self.ghosts.append(it)
        self._relayout()

    def set_current(self, i):
        self.cur = self.tabs[i] if 0 <= i < len(self.tabs) else None
        self.update()

    def refresh(self, item):
        if item.loading:
            ticker.add(self)
        self.update()

    def _relayout(self):
        n = len(self.tabs)
        avail = self.width() - PAD - 48
        w = max(MINW, min(MAXW, (avail - GAP * (n - 1)) / max(1, n)))
        x = float(PAD)
        for it in self.tabs:
            it.tx, it.tw = x, w
            x += w + GAP
        self.new_tx = x
        ticker.add(self)

    def resizeEvent(self, e):
        self._relayout()

    def tick(self, dt):
        motion = cfg.get("ui.motion")
        k = 1.0 if motion <= 0 else 1 - math.exp(-dt * 15 / motion)
        busy = False
        dragged = self.drag["item"] if self.drag and self.drag["active"] else None
        for it in self.tabs + self.ghosts:
            if it is not dragged:
                it.x += (it.tx - it.x) * k
            it.w += (it.tw - it.w) * k
            it.op += ((0.0 if it.dying else 1.0) - it.op) * k
            it.hover += (it.hover_t - it.hover) * min(1, k * 1.5)
            busy |= abs(it.tx - it.x) > .3 or abs(it.tw - it.w) > .3 or abs(it.hover_t - it.hover) > .01 or it.loading
        self.ghosts = [g for g in self.ghosts if g.w > 1]
        self.new_x += (self.new_tx - self.new_x) * k
        busy |= bool(self.ghosts) or abs(self.new_tx - self.new_x) > .3
        self.t += dt
        self.update()
        if not busy:
            ticker.remove(self)

    # ── geometry ──
    def _rect(self, it):
        return QRectF(it.x, TOP, max(0.0, it.w), TAB_H)

    def _parts(self, it):
        r = self._rect(it)
        parts = {}
        if r.width() < 72:
            return parts
        right = r.right() - 6
        if it is self.cur or it.hover > .01:
            parts["close"] = QRectF(right - 26, r.center().y() - 13, 26, 26)
            right -= 28
        if it.audible or it.muted:
            parts["audio"] = QRectF(right - 24, r.center().y() - 12, 24, 24)
        return parts

    def _new_rect(self):
        return QRectF(self.new_x + 2, TOP + 2, 32, 32)

    def _hit(self, pos):
        for i, it in enumerate(self.tabs):
            if self._rect(it).contains(pos):
                for name, pr in self._parts(it).items():
                    if pr.contains(pos):
                        return i, name
                return i, "tab"
        if self._new_rect().contains(pos):
            return -1, "new"
        return -1, None

    # ── painting ──
    def paintEvent(self, e):
        p = painter(self)
        dragged = self.drag["item"] if self.drag and self.drag["active"] else None
        order = self.ghosts + [t for t in self.tabs if t is not self.cur and t is not dragged]
        order += [t for t in (self.cur, dragged) if t is not None and t in self.tabs]
        for it in order:
            self._paint_tab(p, it)
        nb = self._new_rect()
        if self.new_hover:
            p.setBrush(alpha(theme["onSurface"], 0.08 * self.new_hover))
            p.drawRoundedRect(nb, theme.r(12), theme.r(12))
        draw_icon(p, nb, "add", theme["onSurfaceVariant"], 22)

    def _paint_tab(self, p, it):
        c = theme
        r = self._rect(it)
        if r.width() < 2:
            return
        active = it is self.cur
        p.setOpacity(max(0.0, min(1.0, it.op)))
        rad = theme.r(12)
        if active:
            p.setBrush(c["secondaryContainer"])
            p.drawRoundedRect(r, rad, rad)
        elif it.hover > .01:
            p.setBrush(alpha(c["onSurface"], 0.08 * it.hover))
            p.drawRoundedRect(r, rad, rad)
        if it.split and not active:              # partner in split view: outlined
            p.setPen(QPen(c["secondaryContainer"], 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), rad - 1, rad - 1)
            p.setPen(Qt.PenStyle.NoPen)
        fg = c["onSecondaryContainer"] if active else c["onSurfaceVariant"]
        p.save()
        p.setClipRect(r)
        compact = r.width() < 72
        ix = r.x() + (r.width() - 16) / 2 if compact else r.x() + 12
        ir = QRectF(ix, r.center().y() - 8, 16, 16)
        if it.loading:
            draw_spinner(p, ir.adjusted(-1, -1, 1, 1), c["primary"], self.t, 2.2)
        elif it.icon is not None and not it.icon.isNull():
            it.icon.paint(p, ir.toRect())
        else:
            draw_icon(p, ir.adjusted(-2, -2, 2, 2), it.symbol or "public", fg, 18)
        if not compact:
            parts = self._parts(it)
            right = min(pr.left() for pr in parts.values()) - 2 if parts else r.right() - 10
            if "close" in parts:
                draw_icon(p, parts["close"], "close", alpha(fg, 1 if active else it.hover), 18)
            if "audio" in parts:
                draw_icon(p, parts["audio"], "volume_off" if it.muted else "volume_up", c["primary"], 17, 1)
            x = ix + 26
            p.setFont(font(13, 500 if active else 400))
            p.setPen(fg)
            p.drawText(QRectF(x, r.y(), right - x, r.height()), Qt.AlignmentFlag.AlignVCenter,
                       p.fontMetrics().elidedText(it.title or "Untitled", Qt.TextElideMode.ElideRight, int(right - x)))
        p.restore()
        p.setOpacity(1)

    # ── input ──
    def mousePressEvent(self, e):
        pos = e.position()
        i, part = self._hit(pos)
        if e.button() == Qt.MouseButton.MiddleButton and i >= 0:
            self.closeRequested.emit(i)
            return
        if e.button() != Qt.MouseButton.LeftButton:
            return
        if part == "new":
            self.newTabRequested.emit()
        elif i < 0:
            self.emptyPressed.emit(e)
        elif part == "close":
            self.closeRequested.emit(i)
        elif part == "audio":
            self.muteRequested.emit(i)
        else:
            if self.tabs[i] is not self.cur:
                self.currentChanged.emit(i)
            it = self.tabs[i]
            self.drag = {"item": it, "ox": pos.x() - it.x, "start": pos.x(), "active": False}

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.drag:
            d = self.drag
            it = d["item"]
            if not d["active"] and abs(pos.x() - d["start"]) > 6:
                d["active"] = True
            if d["active"] and it in self.tabs:
                it.x = max(PAD, min(pos.x() - d["ox"], self.new_tx - it.tw - GAP))
                i = self.tabs.index(it)
                mid = it.x + it.w / 2
                j = i
                if i + 1 < len(self.tabs) and mid > self.tabs[i + 1].tx + self.tabs[i + 1].tw / 2:
                    j = i + 1
                elif i > 0 and mid < self.tabs[i - 1].tx + self.tabs[i - 1].tw / 2:
                    j = i - 1
                if j != i:
                    self.tabs.insert(j, self.tabs.pop(i))
                    self.moved.emit(i, j)
                    self._relayout()
                ticker.add(self)
                self.update()
            return
        i, part = self._hit(pos)
        for k, it in enumerate(self.tabs):
            it.hover_t = 1.0 if k == i else 0.0
        self.new_hover = 1.0 if part == "new" else 0.0
        ticker.add(self)

    def mouseReleaseEvent(self, e):
        if self.drag:
            self.drag = None
            ticker.add(self)

    def mouseDoubleClickEvent(self, e):
        if self._hit(e.position()) == (-1, None):
            self.emptyDoubleClicked.emit()

    def leaveEvent(self, e):
        for it in self.tabs:
            it.hover_t = 0.0
        self.new_hover = 0.0
        ticker.add(self)

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            i, part = self._hit(QPointF(e.pos()))
            if i >= 0 and part == "tab":
                QToolTip.showText(e.globalPos(), self.tabs[i].title, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(e)
