"""Browser window: frameless shell, tab strip, toolbar + omnibox, floating web card, WebTab."""
import json
import math
import os
import time

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QStringListModel, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QGuiApplication, QImage, QKeySequence, QPainterPath, QPen, QPixmap,
                         QShortcut)
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import (QCompleter, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu, QSplitter, QStackedWidget,
                             QVBoxLayout, QWidget)

from .config import SEARCH_ENGINES, TOOLBAR_ITEMS, bookmarks, cfg, history, reading
from . import autofill, pwa, winframe
from .icon import app_icon
from .media import INJECT_JS, MediaFlyout
from .pages import NATIVE, make_page
from .reader import reader_js
from .services import host_of, zap_badge
from .tabs import TabItem, TabStrip
from .theme import (ACCEL, EMPHASIZED, STANDARD, alpha, animate, font, seed_from_color, seed_from_image, ticker,
                    theme)
from .widgets import Button, Flyout, Label, Snackbar, TextField, guard, hbox, painter, rounded

DEBUG = bool(os.environ.get("NACRE_DEBUG"))
NEWTAB = "nacre://newtab"
SETTINGS = "nacre://settings"
PERM_NAMES = {"MediaAudioCapture": "your microphone", "MediaVideoCapture": "your camera",
              "MediaAudioVideoCapture": "your camera and microphone", "Geolocation": "your location",
              "Notifications": "notifications", "DesktopVideoCapture": "your screen",
              "DesktopAudioVideoCapture": "your screen and audio", "ClipboardReadWrite": "the clipboard",
              "LocalFontsAccess": "your fonts", "MouseLock": "your pointer"}


def fixup(text):
    s = text.strip()
    if not s:
        return None
    if "://" in s or s.startswith(("about:", "nacre:", "file:", "view-source:", "data:")):
        return QUrl(s)
    if " " not in s and ("." in s or s.startswith("localhost")) and not s.endswith("."):
        u = QUrl.fromUserInput(s)
        if u.isValid():
            return u
    tpl = SEARCH_ENGINES.get(cfg.get("search.engine"), SEARCH_ENGINES["duckduckgo"])[1]
    return QUrl(tpl.format(bytes(QUrl.toPercentEncoding(s)).decode()))


# ── web tab ───────────────────────────────────────────────────────────────────
class WebPage(QWebEnginePage):
    def __init__(self, profile, tab):
        super().__init__(profile, tab)
        self.tab = tab

    def acceptNavigationRequest(self, url, nav_type, main_frame):
        app = self.tab.win.app
        if (app and main_frame and nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
                and url.scheme() in ("http", "https") and not pwa.in_scope(app, url.toString())):
            QTimer.singleShot(0, lambda: self.tab.win.ctx.browser_window().new_tab(url))
            return False
        if main_frame and url.scheme() == "nacre" and url.host() in NATIVE:
            QTimer.singleShot(0, lambda: self.tab.load(url))
            return False
        return super().acceptNavigationRequest(url, nav_type, main_frame)

    def javaScriptConsoleMessage(self, level, msg, line, source):
        if DEBUG:                                  # page console spam stays out of the terminal unless asked
            print(f"js[{self.url().host()}]: {msg}")

    def createWindow(self, kind):
        T = QWebEnginePage.WebWindowType
        tab = self.tab.win.new_tab(None, background=kind == T.WebBrowserBackgroundTab, after=self.tab)
        return tab.page


class WebTab(QWidget):
    def __init__(self, win, url=None):
        super().__init__()
        self.win = win
        self.item = TabItem()
        self.seed = None
        self.had_audio = False
        self.native, self.native_host = {}, None     # nacre:// pages drawn as widgets
        self.devtools = None
        self.pending, self.hidden_at, self.css_done, self.installable = None, None, False, False
        self.progress = 0.0
        self.stack = QStackedWidget(self)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.stack)
        self.view = QWebEngineView()
        self.page = WebPage(win.profile, self)
        self.page.setBackgroundColor(theme["surface"])
        self.view.setPage(self.page)
        self.stack.addWidget(self.view)
        p = self.page
        p.titleChanged.connect(self._title)
        p.iconChanged.connect(self._icon)
        p.urlChanged.connect(lambda u: (self._site_zoom(), win.tab_url_changed(self)))
        p.loadStarted.connect(self._started)
        p.loadProgress.connect(self._progress)
        p.loadFinished.connect(self._finished)
        p.recentlyAudibleChanged.connect(self._audible)
        p.audioMutedChanged.connect(self._audible)
        p.linkHovered.connect(win.status.show_url)
        p.fullScreenRequested.connect(win.fullscreen_request)
        p.permissionRequested.connect(win.permission_request)
        p.renderProcessTerminated.connect(self._crashed)
        autofill.attach(self)
        if url:
            self.load(url)

    # info
    def url(self):
        if self.pending is not None:
            return self.pending
        return QUrl("nacre://" + self.native_host) if self.native_host else self.page.url()

    def native_widget(self):
        return self.native.get(self.native_host)

    def title(self):
        return self.item.title

    def host(self):
        return host_of(self.page.url()) or self.page.url().toString()

    def is_nacre(self):
        return self.url().scheme() == "nacre"

    # actions
    def load(self, url):
        url = QUrl(url) if isinstance(url, str) else url
        if url.scheme() == "nacre" and url.host() in NATIVE:
            self.show_native(url.host())
            if url.fragment() and hasattr(self.native_widget(), "jump"):
                QTimer.singleShot(50, lambda: self.native_widget().jump(url.fragment()))
            return
        if self.native_host:
            self.native_host = None
            self.stack.setCurrentWidget(self.view)
        self.page.load(url)

    def show_native(self, host):
        if host not in self.native:
            self.native[host] = make_page(host, self.win)
            self.stack.addWidget(self.native[host])
        self.native_host = host
        self.stack.setCurrentWidget(self.native[host])
        title, sym = NATIVE[host]
        self.item.title, self.item.icon, self.item.symbol, self.item.loading = title, None, sym, False
        self.seed = None
        self.win.tab_url_changed(self)
        self.win.tab_changed(self)

    def _site_zoom(self):
        z = cfg.get("zoom.sites").get(host_of(self.page.url()), 1.0)
        if abs(self.page.zoomFactor() - z) > 1e-3:
            self.page.setZoomFactor(z)

    def _title(self, t):
        if self.native_host:
            return
        self.item.title = t or self.page.url().toString()
        if self.is_nacre():
            self.item.title, self.item.symbol = "New Tab", "add_circle"
        self.win.tab_changed(self)
        if not self.win.incognito and self.page.url().scheme() in ("http", "https"):
            u = self.page.url().toString()
            for h in reversed(history.data[-20:]):
                if h["url"] == u:
                    h["title"] = t
                    history.touch()
                    break

    def _icon(self, icon):
        self.item.icon = None if icon.isNull() else icon
        if self.seed is None and not icon.isNull():
            self.seed = seed_from_image(icon.pixmap(64).toImage())
            self.win.tab_seed(self)
        self.win.tab_changed(self)

    def _started(self):
        self.css_done = False
        self.item.loading = True
        self.seed = None
        self.win.adblock.reset_page(host_of(self.page.url()))
        self._progress(5)
        self.win.tab_changed(self)

    def _progress(self, v):
        self.progress = v / 100
        if v >= 20 and not self.css_done:
            self.css_done = True
            self.win.zap_ads(self)
        if self is self.win.tab():
            self.win.omni.set_progress(self.progress)

    def _finished(self, ok):
        self.item.loading = False
        self._progress(100)
        self.page.runJavaScript(INJECT_JS, 0)
        if self.is_nacre():
            self.item.title, self.item.symbol = "New Tab", "add_circle"
            self.win.push_ntp_theme(self)
        else:
            self.page.runJavaScript("(document.querySelector('meta[name=theme-color]')||{}).content||''", 0,
                                    self._theme_color)
        self.win.check_installable(self)
        if ok and not self.win.incognito and self.page.url().scheme() in ("http", "https"):
            u = self.page.url().toString()
            if not history.data or history.data[-1]["url"] != u:
                history.data.append({"url": u, "title": self.page.title(), "t": time.time()})
                del history.data[:-5000]
                history.touch()
                self.win.omni.add_completion(u)
        self.win.tab_changed(self)

    def _theme_color(self, css):
        seed = seed_from_color(QColor(css)) if css else None
        if seed is None and self.item.icon:
            seed = seed_from_image(self.item.icon.pixmap(64).toImage())
        self.seed = seed
        self.win.tab_seed(self)

    def _audible(self, *_):
        self.item.audible = self.page.recentlyAudible()
        self.item.muted = self.page.isAudioMuted()
        self.had_audio |= self.item.audible
        self.win.tab_changed(self)
        self.win.update_media_button()

    def _crashed(self, status, code):
        if status != QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            self.win.snack.show_msg(f"{self.host()} crashed", "Reload",
                                    lambda: self.page.triggerAction(QWebEnginePage.WebAction.Reload))


# ── omnibox ───────────────────────────────────────────────────────────────────
class OmniPopup(QListView):
    """Suggestion list. It's a translucent popup (for rounded corners), so it paints its own surface —
    Qt skips stylesheet backgrounds on translucent item views."""

    def __init__(self):
        super().__init__()
        self.setObjectName("completer")
        self.setFrameShape(QListView.Shape.NoFrame)
        self.viewport().setAutoFillBackground(False)
        self.setViewportMargins(6, 6, 6, 6)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        theme.changed.connect(guard(self, self.update))

    def event(self, e):
        if e.type() == QEvent.Type.Paint:          # the frame (not the viewport): draw the M3 surface
            p = painter(self)
            r = QRectF(self.rect())
            for i in range(4):
                p.setBrush(alpha(theme["shadow"], 0.06))
                p.drawRoundedRect(r.adjusted(i, i, -i, -i + 1), theme.r(18), theme.r(18))
            p.setBrush(theme["surfaceContainerHigh"])
            p.drawRoundedRect(r.adjusted(2, 1, -2, -3), theme.r(18), theme.r(18))
            return True
        return super().event(e)


class Omnibox(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.progress = self.wash = self.phase = 0.0
        self._focus = 0.0
        self.setFixedHeight(40)
        self.setMinimumWidth(200)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(4)
        self.site = Button("search", size=32, tip="Site information")
        self.site.clicked.connect(lambda: win.open_flyout("site", self.site))
        self.edit = QLineEdit()
        self.edit.setFont(font(15))
        self.edit.setPlaceholderText("Search or type a URL")
        self.edit.returnPressed.connect(lambda: self.submitted.emit(self.edit.text()))
        self.edit.installEventFilter(self)
        self.star = Button("star", size=32, tip="Bookmark this tab (Ctrl+D)", checkable=True)
        self.star.clicked.connect(win.toggle_bookmark)
        self.install = Button("install_desktop", size=32, tip="Install this app")
        self.install.clicked.connect(lambda: win.install_current())
        self.install.hide()
        for w in (self.site, self.edit, self.install, self.star):
            lay.addWidget(w)
        lay.setStretch(1, 1)
        self.model = QStringListModel()
        self.completer = QCompleter(self.model, self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(8)
        pop = OmniPopup()
        self.completer.setPopup(pop)
        pop.setFont(font(14))
        self.completer.activated.connect(lambda s: self.submitted.emit(s))
        self.edit.setCompleter(self.completer)
        self.reload_completions()
        theme.changed.connect(self.update)

    def reload_completions(self):
        seen, out = set(), []
        for h in reversed(bookmarks.data + history.data):
            u = h["url"]
            if u not in seen:
                seen.add(u)
                out.append(u)
        self.model.setStringList(out[:3000])

    def add_completion(self, url):
        lst = self.model.stringList()
        if url not in lst:
            self.model.setStringList([url] + lst[:2999])

    def eventFilter(self, obj, e):
        t = e.type()
        popup = t in (QEvent.Type.FocusIn, QEvent.Type.FocusOut) and e.reason() in (
            Qt.FocusReason.PopupFocusReason, Qt.FocusReason.ActiveWindowFocusReason)
        if popup:
            return False          # completer popup / window switch: keep the text and the caret as they are
        if t == QEvent.Type.FocusIn:
            animate(self, "_focus", 1, 200, STANDARD)
            QTimer.singleShot(0, self.edit.selectAll)
        elif t == QEvent.Type.FocusOut:
            animate(self, "_focus", 0, 250, STANDARD)
            self.win.sync_omnibox()
        elif t == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.win.sync_omnibox(force=True)
            self.win.focus_page()
            return True
        return False

    def set_url(self, url, force=False):
        if self.edit.hasFocus() and not force:
            return
        s = url.toString()
        self.edit.setText("" if s.startswith(NEWTAB) else s)
        self.edit.setCursorPosition(0)
        sec = url.scheme()
        self.site.set_icon({"https": "lock", "http": "info", "nacre": "search"}.get(sec, "info"))

    def tick(self, dt):
        self.phase = (self.phase + dt * 7) % math.tau
        self.update()
        if self.wash < 0.01:
            ticker.remove(self)

    def set_progress(self, frac):
        ticker.add(self)
        if frac >= 1:
            animate(self, "progress", 1, 250, STANDARD)
            animate(self, "wash", 0, 600, ACCEL)
        else:
            if self.wash < 1:
                animate(self, "wash", 1, 150, STANDARD)
            animate(self, "progress", frac, 400, EMPHASIZED)

    def paintEvent(self, e):
        p = painter(self)
        r = QRectF(self.rect())
        path = rounded(r, r.height() / 2)
        p.fillPath(path, theme["surfaceContainerHighest"])
        if self.wash > 0.01 and self.progress > 0:
            p.save()
            p.setClipPath(path)
            end = r.width() * self.progress
            p.fillRect(QRectF(0, 0, end, r.height()), alpha(theme["primary"], 0.08 * self.wash))
            wave = QPainterPath()                      # M3 wavy progress riding the bottom of the pill
            y0, x0 = r.height() - 4.5, r.height() / 2
            x = x0
            while x <= end - 2:
                taper = min(1.0, (x - x0) / 12, (end - x) / 12 + 0.2)
                pt = QPointF(x, y0 + 1.6 * taper * math.sin(x / 7 - self.phase))
                wave.moveTo(pt) if x == x0 else wave.lineTo(pt)
                x += 1.5
            p.setPen(QPen(alpha(theme["primary"], self.wash), 2.5, cap=Qt.PenCapStyle.RoundCap))
            p.drawPath(wave)
            p.restore()
        if self._focus > 0.01:
            p.setPen(QPen(alpha(theme["primary"], self._focus), 2))
            p.drawPath(rounded(r.adjusted(1, 1, -1, -1), r.height() / 2 - 1))


# ── overlays ──────────────────────────────────────────────────────────────────
class Corner(QWidget):
    """Paints the window colour outside a quarter circle, rounding the web card's corner."""

    def __init__(self, parent, which):
        super().__init__(parent)
        self.which = which
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        s = self.width()
        path = QPainterPath()
        path.addRect(QRectF(0, 0, s, s))
        circ = QPainterPath()
        cx = s if "l" in self.which else 0
        cy = s if "t" in self.which else 0
        circ.addEllipse(QPointF(cx, cy), s, s)
        p.fillPath(path.subtracted(circ), self.parent().window().chrome_color())


class StatusBubble(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.text, self._t = "", 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self.hide_timer = QTimer(self, singleShot=True, interval=120,
                                 timeout=lambda: animate(self, "_t", 0, 180, ACCEL, step=self.update,
                                                         done=lambda: self._t < .01 and self.hide()))
        theme.changed.connect(self.update)

    def show_url(self, url):
        s = url.toString() if isinstance(url, QUrl) else str(url)
        if not s:
            self.hide_timer.start()
            return
        self.hide_timer.stop()
        self.text = s
        self.setFont(font(12))
        w = min(self.parentWidget().width() // 2, self.fontMetrics().horizontalAdvance(s) + 24)
        self.setGeometry(8, self.parentWidget().height() - 36, w, 28)
        self.show()
        self.raise_()
        if self._t < 1:
            animate(self, "_t", 1, 160, STANDARD)
        self.update()

    def paintEvent(self, e):
        p = painter(self)
        p.setOpacity(self._t)
        p.setBrush(theme["surfaceContainerHighest"])
        p.drawRoundedRect(QRectF(self.rect()), theme.r(10), theme.r(10))
        p.setPen(theme["onSurfaceVariant"])
        p.setFont(font(12))
        p.drawText(QRectF(self.rect()).adjusted(12, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter,
                   p.fontMetrics().elidedText(self.text, Qt.TextElideMode.ElideMiddle, self.width() - 24))


class FindBar(QWidget):
    def __init__(self, win, parent):
        super().__init__(parent)
        self.win = win
        self._t = 0.0
        self.hide()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 8, 8)
        lay.setSpacing(4)
        self.field = TextField(placeholder="Find in page", icon="search")
        self.field.setFixedWidth(240)
        self.count = Label("", "onSurfaceVariant", 12, 500)
        self.count.setMinimumWidth(52)
        self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        up = Button("keyboard_arrow_up", size=36, tip="Previous (Shift+Enter)")
        down = Button("keyboard_arrow_down", size=36, tip="Next (Enter)")
        close = Button("close", size=36, tip="Close (Esc)")
        for w in (self.field, self.count, up, down, close):
            lay.addWidget(w)
        self.setFixedSize(self.sizeHint())
        e = self.field.edit
        e.textChanged.connect(lambda: self.find())
        e.returnPressed.connect(lambda: self.find(back=bool(QGuiApplication.keyboardModifiers()
                                                            & Qt.KeyboardModifier.ShiftModifier)))
        e.installEventFilter(self)
        up.clicked.connect(lambda: self.find(back=True))
        down.clicked.connect(lambda: self.find())
        close.clicked.connect(self.close_bar)
        theme.changed.connect(self.update)

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.KeyPress and e.key() == Qt.Key.Key_Escape:
            self.close_bar()
            return True
        return False

    def open_bar(self):
        self.show()
        self.raise_()
        self.place()
        animate(self, "_t", 1, 380, EMPHASIZED, step=self.place)
        self.field.edit.setFocus()
        self.field.edit.selectAll()
        self.win.esc_sync()
        if self.field.edit.text():
            self.find()

    def close_bar(self):
        tab = self.win.tab()
        if tab:
            tab.page.findText("")
        animate(self, "_t", 0, 180, ACCEL, step=self.place, done=lambda: (self.hide(), self.win.esc_sync()))
        self.win.focus_page()

    def place(self):
        par = self.parentWidget()
        self.move(par.width() - self.width() - 16, int(-self.height() + (self.height() + 12) * self._t))
        self.update()

    def find(self, back=False):
        tab = self.win.tab()
        if not tab:
            return
        F = QWebEnginePage.FindFlag
        flags = F.FindBackward if back else F(0)
        tab.page.findText(self.field.edit.text(), flags, self._result)

    def _result(self, res):
        n = res.numberOfMatches()
        self.count.setText(f"{res.activeMatch()}/{n}" if n else ("0/0" if self.field.edit.text() else ""))

    def paintEvent(self, e):
        p = painter(self)
        for i in range(4):
            p.setBrush(alpha(theme["shadow"], 0.05))
            p.drawRoundedRect(QRectF(self.rect()).adjusted(2 - i, 4 - i, i - 2, i), 20, 20)
        p.setBrush(theme["surfaceContainerHigh"])
        p.drawRoundedRect(QRectF(self.rect()).adjusted(2, 0, -2, -4), theme.r(20), theme.r(20))


class Card(QWidget):
    """Holds the tab stack; corner overlays round it into a floating card."""

    def __init__(self, win):
        super().__init__()
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setHandleWidth(8)
        self.splitter.setChildrenCollapsible(False)
        self.stack = QStackedWidget()
        self.side = QWidget()                        # split view: partner tab lives here
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.side_title = Label("", "onSurfaceVariant", 13, 500)
        swap = Button("swap_horiz", size=32, icon_px=18, tip="Swap sides")
        swap.clicked.connect(lambda: win.swap_split())
        close = Button("close", size=32, icon_px=18, tip="Close split view")
        close.clicked.connect(lambda: win.unsplit())
        bar = hbox(self.side_title, None, swap, close, spacing=2, margins=(14, 4, 6, 4))
        bar.setAutoFillBackground(False)
        sl.addWidget(bar)
        self.side_l = sl
        self.side.hide()
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.side)
        self.splitter.splitterMoved.connect(lambda *_: self.place_corners())
        self.corners = [Corner(self, w) for w in ("tl", "tr", "bl", "br")]
        self.inner = [Corner(self, w) for w in ("tr", "br", "tl", "bl")]   # rounded edges either side of the gap

    def radius(self):
        return 0 if not cfg.get("ui.floating_card") or self.window().isFullScreen() else int(theme.r(16))

    def resizeEvent(self, e):
        self.splitter.setGeometry(self.rect())
        self.place_corners()
        for c in self.children():
            if hasattr(c, "place"):
                c.place()

    def place_corners(self):
        r = self.radius()
        w, h = self.width(), self.height()
        for c, (x, y) in zip(self.corners, ((0, 0), (w - r, 0), (0, h - r), (w - r, h - r))):
            c.setGeometry(x, y, r, r)
            c.setVisible(r > 0)
            c.raise_()
        split = self.side.isVisible() and r > 0
        lx, rx = self.stack.width(), self.stack.width() + self.splitter.handleWidth()
        for c, (x, y) in zip(self.inner, ((lx - r, 0), (lx - r, h - r), (rx, 0), (rx, h - r))):
            c.setGeometry(x, y, r, r)
            c.setVisible(split)
            c.raise_()


# ── window ────────────────────────────────────────────────────────────────────
class BrowserWindow(QWidget):
    EDGE = 6

    def __init__(self, ctx, incognito=False, restore=None, app=None):
        super().__init__()
        self.ctx, self.incognito, self.app = ctx, incognito, app
        self.profile = ctx.otr_profile if incognito else ctx.profile
        self.adblock = ctx.adblock
        self.downloads = ctx.downloads_for(self.profile)
        self.tabs, self.closed = [], []
        self.split_tab = self.last_tab = None
        self.flyouts = {}
        self.setWindowTitle("Nacre" + (" — Incognito" if incognito else ""))
        self.setWindowIcon(app_icon(theme.target))
        theme.target_changed.connect(guard(self, lambda: self.setWindowIcon(app_icon(theme.target))))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.frameless = cfg.get("ui.frameless")
        # Windows can't composite Chromium's output into a layered (translucent) window: the page comes out
        # see-through, so the window stays opaque there and skips the rounded outer corners
        self.translucent = self.frameless and os.name != "nt" and not os.environ.get("NACRE_OPAQUE")
        if self.frameless:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        if self.translucent:
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        if self.frameless:
            QGuiApplication.instance().installEventFilter(self)
        self.resize(1360, 860)
        self.setMinimumSize(560, 360)

        root = QVBoxLayout(self)
        root.setSpacing(0)
        # tab row
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 6, 0)
        top.setSpacing(2)
        if incognito:
            badge = Button("domino_mask", size=36, tip="Incognito")
            badge.setEnabled(False)
            top.addWidget(badge)
        self.strip = TabStrip()
        top.addWidget(self.strip, 1)
        self.wc = []
        if self.frameless:
            # the glyphs fill their em box differently: close is small, crop_square is big, so size them per icon
            for icon, px, tip, fn in (("remove", 22, "Minimize", self.showMinimized),
                                      ("crop_square", 18, "Maximize", self.toggle_max),
                                      ("close", 24, "Close", self.close)):
                b = Button(icon, size=40, tip=tip, icon_px=px)
                b.clicked.connect(fn)
                top.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
                self.wc.append(b)
        self.top = QWidget()
        self.top.setLayout(top)
        root.addWidget(self.top)

        # toolbar
        tb = QHBoxLayout()
        tb.setContentsMargins(8, 4, 8, 8)
        tb.setSpacing(2)
        self.b_back = self._tb("arrow_back", "Back (Alt+Left)", lambda: self.tab() and self.tab().view.back())
        self.b_back.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.b_back.customContextMenuRequested.connect(lambda: self._history_menu(self.b_back, back=True))
        self.items = {}
        self.items["forward"] = self._tb("arrow_forward", "Forward (Alt+Right)",
                                         lambda: self.tab() and self.tab().view.forward())
        self.b_reload = self._tb("refresh", "Reload (Ctrl+R)", self.reload_or_stop)
        self.items["home"] = self._tb("home", "Home", self.go_home)
        tb.addWidget(self.b_back)
        tb.addWidget(self.items["forward"])
        tb.addWidget(self.b_reload)
        tb.addWidget(self.items["home"])
        for k in ("split", "incognito"):
            sym, label, _ = TOOLBAR_ITEMS["Navigation"][k]
            self.items[k] = self._tb(sym, label, lambda k=k: self.toolbar_action(k))
            tb.addWidget(self.items[k])
        self.items["split"].setCheckable(True)
        tb.addSpacing(6)
        self.omni = Omnibox(self)
        self.omni.submitted.connect(self.navigate)
        tb.addWidget(self.omni, 1)
        tb.addSpacing(6)
        for group in ("Your Nacre", "Tools and actions"):
            for k, (sym, label, _) in TOOLBAR_ITEMS[group].items():
                self.items[k] = self._tb(sym, label, lambda k=k: self.toolbar_action(k))
                tb.addWidget(self.items[k])
        self.b_media = self._tb("music_note", "Media controls", lambda: self.open_flyout("media", self.b_media))
        tb.addWidget(self.b_media)
        self.b_menu = self._tb("more_vert", "Menu", lambda: self.open_flyout("menu", self.b_menu))
        tb.addWidget(self.b_menu)
        self.toolbar = QWidget()
        self.toolbar.setLayout(tb)
        root.addWidget(self.toolbar)

        # card
        self.card = Card(self)
        self.card_wrap = QWidget()
        cw = QVBoxLayout(self.card_wrap)
        cw.addWidget(self.card)
        root.addWidget(self.card_wrap, 1)
        self.status = StatusBubble(self.card)
        self.findbar = FindBar(self, self.card)
        self.snack = Snackbar(self.card)

        # wiring
        s = self.strip
        s.currentChanged.connect(lambda i: self.set_current(i))
        s.closeRequested.connect(lambda i: self.close_tab(i))
        s.newTabRequested.connect(lambda: self.new_tab())
        s.moved.connect(self._moved)
        s.muteRequested.connect(lambda i: self.tabs[i].page.setAudioMuted(not self.tabs[i].page.isAudioMuted()))
        s.emptyPressed.connect(lambda e: self.windowHandle().startSystemMove())
        s.emptyDoubleClicked.connect(self.toggle_max)
        cfg.changed.connect(self._cfg_changed)
        theme.changed.connect(self.update)
        theme.target_changed.connect(self._theme_target)
        self.downloads.added.connect(self._download_added)
        self.downloads.handed_off.connect(guard(self, lambda n: self.snack.show_msg(f"Sent “{n}” to AB Download Manager")))
        self.downloads.failed_handoff.connect(guard(self, 
            lambda n: self.snack.show_msg("AB Download Manager didn't answer — downloading here instead")))
        self.adblock.blocked.connect(self._blocked)
        self.dl_timer = QTimer(self, interval=50, timeout=self._dl_tick)      # also drives the ring's wave
        self.life_timer = QTimer(self, interval=30000, timeout=self._lifecycle)
        self.life_timer.start()
        self._shortcuts()
        self._apply_layout()
        self.update_toolbar()

        if app:
            self._app_mode(app)
            restore = [app["url"]]
        for u in (restore or []):
            self.new_tab(u, background=True, lazy=bool(cfg.get("tabs.lazy_restore")))
        if not self.tabs:
            self.new_tab()
        self.set_current(0 if restore else len(self.tabs) - 1)

    # ── helpers ──
    def _tb(self, icon, tip, fn):
        b = Button(icon, size=40, tip=tip)
        b.clicked.connect(lambda _=False: fn())      # swallow clicked(bool) so `lambda k=k` defaults survive
        return b

    def chrome_color(self):
        return theme["surfaceContainer"]

    def tab(self):
        w = self.card.stack.currentWidget()
        return w if any(w is t for t in self.tabs) else None

    def index(self, tab):
        return next((i for i, t in enumerate(self.tabs) if t is tab), -1)

    def media_tabs(self):
        return [t for t in self.tabs if t.had_audio or t.item.audible]

    def focus_tab(self, tab):
        if tab in self.tabs:
            self.set_current(self.tabs.index(tab))
        self.raise_()
        self.activateWindow()

    def focus_page(self):
        t = self.tab()
        if t:
            self.omni.edit.clearFocus()
            (t.native_widget() or t.view).setFocus()

    # ── tabs ──
    def new_tab(self, url=NEWTAB, background=False, after=None, lazy=False):
        if self.app and self.tabs:                   # app windows hold one page; everything else goes to the browser
            return self.ctx.browser_window().new_tab(url, background)
        tab = WebTab(self, None)
        i = self.tabs.index(after) + 1 if after in self.tabs else len(self.tabs)
        self.tabs.insert(i, tab)
        self.card.stack.addWidget(tab)
        self.strip.insert(i, tab.item)
        self.card.place_corners()
        if url and lazy and background:
            tab.pending = QUrl(url)              # slow-line friendly: don't fetch until the tab is opened
            tab.item.title = host_of(tab.pending) or url
        elif url:
            tab.load(url)
        if not background:
            self.set_current(i)
            self.focus_omnibox() if url == NEWTAB else self.focus_page()
        return tab

    def set_current(self, i):
        if not 0 <= i < len(self.tabs):
            return
        tab = self.tabs[i]
        if tab is self.split_tab:                 # picking the right-hand tab swaps the sides
            return self.swap_split()
        prev = self.tab()
        if prev is not None and prev is not tab:
            prev.hidden_at = time.monotonic()
            self.last_tab = prev
        tab.hidden_at = None
        if tab.pending is not None:
            url, tab.pending = tab.pending, None
            tab.load(url)
        if tab.page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            tab.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        if getattr(self, "page_fs", False) and tab is not self.tab():
            self.toggle_fullscreen()
        if self.omni.edit.hasFocus() and not tab.is_nacre():
            self.focus_page()
        self.card.stack.setCurrentWidget(tab)
        self.strip.set_current(i)
        self.card.place_corners()
        self.findbar.raise_()
        self.sync_omnibox(force=True)
        self.omni.progress = tab.progress if tab.item.loading else 0.0
        self.omni.wash = 1.0 if tab.item.loading else 0.0
        self.update_nav()
        self.tab_seed(tab)
        self.update_shield()
        self.omni.install.setVisible(tab.installable)
        self.setWindowTitle(f"{tab.title()} — Nacre" + (" Incognito" if self.incognito else ""))
        if self.findbar.isVisible():
            self.findbar.find()

    def close_tab(self, i=None):
        cur = self.tab()
        i = self.index(cur) if i is None else i
        if not 0 <= i < len(self.tabs):
            return
        if self.tabs[i] is self.split_tab or (self.tabs[i] is cur and self.split_tab):
            self.unsplit()
        tab = self.tabs.pop(i)
        if not tab.is_nacre():
            self.closed.append(tab.url())
        self.strip.remove(i)
        self.card.stack.removeWidget(tab)
        tab.page.setAudioMuted(True)
        tab.view.setPage(None)
        tab.page.deleteLater()
        tab.deleteLater()
        if tab.devtools:
            tab.devtools.close()
        if not self.tabs:
            self.close()
            return
        self.set_current(min(i, len(self.tabs) - 1) if tab is cur or cur is None else self.index(cur))
        self.update_media_button()

    def reopen_tab(self):
        if self.closed:
            self.new_tab(self.closed.pop())

    def _moved(self, a, b):
        self.tabs.insert(b, self.tabs.pop(a))     # the stack is order-free; only the list order matters

    def cycle(self, d):
        if self.tabs:
            self.set_current((self.index(self.tab()) + d) % len(self.tabs))

    # ── split view ──
    def toggle_split(self):
        if self.split_tab:
            return self.unsplit()
        main = self.tab()
        others = [t for t in self.tabs if t is not main]
        partner = self.last_tab if any(self.last_tab is t for t in others) else (others[-1] if others else None)
        if partner is None:
            partner = self.new_tab(NEWTAB, background=True)
        self.split_with(partner)

    def split_with(self, partner):
        c = self.card
        c.stack.removeWidget(partner)
        c.side_l.addWidget(partner, 1)
        partner.show()
        if partner.pending is not None:
            url, partner.pending = partner.pending, None
            partner.load(url)
        partner.hidden_at = None
        if partner.page.lifecycleState() != QWebEnginePage.LifecycleState.Active:
            partner.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)
        self.split_tab = partner
        partner.item.split = True
        c.side_title.setText(partner.title())
        c.side.show()
        half = c.width() // 2
        c.splitter.setSizes([half, c.width() - half])
        self.strip.refresh(partner.item)
        self.items["split"].setChecked(True)
        QTimer.singleShot(0, c.place_corners)

    def unsplit(self):
        p = self.split_tab
        if p is None:
            return
        self.split_tab = None
        c = self.card
        c.side_l.removeWidget(p)
        c.stack.addWidget(p)
        p.item.split = False
        p.hidden_at = time.monotonic()
        c.side.hide()
        self.strip.refresh(p.item)
        self.items["split"].setChecked(False)
        QTimer.singleShot(0, c.place_corners)

    def swap_split(self):
        main, p = self.tab(), self.split_tab
        if not p:
            return
        self.unsplit()
        self.set_current(self.index(p))
        if main is not None:
            self.split_with(main)

    # ── tab callbacks ──
    def tab_changed(self, tab):
        self.strip.refresh(tab.item)
        if tab is self.split_tab:
            self.card.side_title.setText(tab.title())
        self.esc_sync()
        if tab is self.tab():
            self.update_nav()
            self.setWindowTitle(self.app["name"] if self.app else
                                f"{tab.title()} — Nacre" + (" Incognito" if self.incognito else ""))

    def tab_url_changed(self, tab):
        if tab is self.tab():
            self.sync_omnibox()
            self.update_nav()
            self.update_shield()

    def tab_seed(self, tab):
        if tab is self.tab() and self.isActiveWindow() or tab is self.tab() and not self.ctx.active_window():
            theme.set_site_seed(tab.seed)

    def push_ntp_theme(self, tab):
        tab.page.runJavaScript(f"window.nacreTheme && nacreTheme({json.dumps(theme.css_vars())})", 0)

    def _theme_target(self):
        for t in self.tabs:
            if t.is_nacre():
                self.push_ntp_theme(t)
            t.page.setBackgroundColor(theme.target["surface"])

    def sync_omnibox(self, force=False):
        t = self.tab()
        if t:
            self.omni.set_url(t.url(), force)
            u = t.url().toString()
            self.omni.star.setChecked(any(b["url"] == u for b in bookmarks.data))

    def update_nav(self):
        t = self.tab()
        if not t:
            return
        h = t.page.history()
        self.b_back.setEnabled(h.canGoBack())
        self.items["forward"].setEnabled(h.canGoForward())
        self.b_reload.set_icon("close" if t.item.loading else "refresh")
        self.b_reload.setToolTip("Stop (Esc)" if t.item.loading else "Reload (Ctrl+R)")

    def focus_omnibox(self):
        self.omni.edit.setFocus()
        self.omni.edit.selectAll()

    # ── navigation ──
    def navigate(self, text):
        url = fixup(text)
        if url is None:
            return
        t = self.tab() or self.new_tab(None)
        t.load(url)
        self.focus_page()

    def go_home(self):
        self.tab() and self.tab().load(cfg.get("home.url"))

    def reload_or_stop(self):
        t = self.tab()
        if t:
            t.view.stop() if t.item.loading else t.view.reload()

    def _history_menu(self, anchor, back=True):
        t = self.tab()
        if not t:
            return
        h = t.page.history()
        items = h.backItems(12) if back else h.forwardItems(12)
        m = QMenu(self)
        for it in reversed(items) if back else items:
            a = m.addAction(it.title() or it.url().toString())
            a.triggered.connect(lambda _, it=it: h.goToItem(it))
        m.exec(anchor.mapToGlobal(QPoint(0, anchor.height())))

    def zoom(self, d):
        t = self.tab()
        if t:
            steps = [.25, .33, .5, .67, .75, .8, .9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5]
            z = t.page.zoomFactor()
            if d == 0:
                z = 1.0
            else:
                i = min(range(len(steps)), key=lambda k: abs(steps[k] - z))
                z = steps[max(0, min(len(steps) - 1, i + d))]
            t.page.setZoomFactor(z)
            host = host_of(t.page.url())
            if host:                                   # remembered per site, like Chrome
                sites = dict(cfg.get("zoom.sites"))
                if abs(z - 1) < 1e-3:
                    sites.pop(host, None)
                else:
                    sites[host] = z
                cfg.set("zoom.sites", sites)
            self.snack.show_msg(f"Zoom {round(z * 100)}% for {host or 'this page'}", ms=1500)
        return self.tab().page.zoomFactor() if self.tab() else 1.0

    def reader_mode(self, t=None):
        t = t or self.tab()
        if not t or t.is_nacre():
            return
        t.page.runJavaScript(reader_js(theme.css_vars()), QWebEngineScript.ScriptWorldId.ApplicationWorld,
                             lambda r: r == "none" and self.snack.show_msg("Couldn't find an article on this page"))

    def vault_locked_hint(self):
        if not getattr(self, "_lock_hinted", False):          # once per window, not on every page
            self._lock_hinted = True
            self.snack.show_msg("Saved passwords are locked", "Unlock", lambda: self.new_tab("nacre://passwords"), 7000)

    def zap_ads(self, tab):
        """Hand this page's cosmetic rules to the zapper (animated removal in the theme's colours)."""
        url = tab.page.url()
        if url.scheme() not in ("http", "https"):
            return
        sels, extra = self.adblock.site_rules(url.toString())
        c = theme.target
        bg = alpha(c["primaryContainer"], 0.92).name(QColor.NameFormat.HexArgb)
        bg = f"#{bg[3:]}{bg[1:3]}"                                   # Qt's #AARRGGBB → CSS #RRGGBBAA
        opts = {"animate": bool(cfg.get("adblock.animate")) and cfg.get("ui.motion") > 0,
                "ms": int(900 * max(0.3, cfg.get("ui.motion") or 1)), "bg": bg, "extra": extra,
                "badge": zap_badge(c["primary"].name(), c["onPrimary"].name()),
                "off": not cfg.get("adblock.enabled") or self.adblock.site_allowed(host_of(url))}
        tab.page.runJavaScript(f"window.__nacreZap && __nacreZap({json.dumps(sels)}, {json.dumps(opts)})",
                               QWebEngineScript.ScriptWorldId.ApplicationWorld)

    def add_to_reading(self, t=None):
        t = t or self.tab()
        if not t or t.is_nacre():
            return
        u = t.url().toString()
        if not any(r["url"] == u for r in reading.data):
            reading.data.append({"url": u, "title": t.title(), "t": time.time(), "read": False})
            reading.touch()
        self.snack.show_msg("Added to reading list", "Open list", lambda: self.toolbar_action("reading_list"))

    def toggle_bookmark(self):
        t = self.tab()
        if not t or t.is_nacre():
            self.omni.star.setChecked(False)
            return
        u = t.url().toString()
        hit = [b for b in bookmarks.data if b["url"] == u]
        if hit:
            bookmarks.data.remove(hit[0])
            self.snack.show_msg("Bookmark removed", "Undo", lambda: (bookmarks.data.append(hit[0]), bookmarks.touch(),
                                                                     self.sync_omnibox()))
        else:
            bookmarks.data.append({"url": u, "title": t.title(), "t": time.time()})
            self.snack.show_msg("Bookmarked")
        bookmarks.touch()
        self.sync_omnibox()

    # ── toolbar ──
    def update_toolbar(self):
        if self.app:
            return
        for k, b in self.items.items():
            on = bool(cfg.get("toolbar." + k))
            if k == "downloads":
                on |= bool(self.downloads.active())
            b.setVisible(on)
        self.update_media_button()
        self.update_shield()

    def update_media_button(self):
        mode = cfg.get("media.button")
        audible = any(t.item.audible for t in self.tabs)
        self.b_media.setVisible(mode == "always" or (mode == "auto" and bool(self.media_tabs())))
        self.b_media.setChecked(False)
        self.b_media.kind = "tonal" if audible else "standard"
        self.b_media.set_icon("graphic_eq" if audible else "music_note")

    def update_shield(self):
        b = self.items.get("adblock")
        t = self.tab()
        if not b or not t:
            return
        host = host_of(t.page.url())
        off = not cfg.get("adblock.enabled") or self.adblock.site_allowed(host)
        b.set_icon("remove_moderator" if off else "shield")
        n = self.adblock.count(host)
        b.badge = (str(n) if n < 100 else "99+") if n and not off else None
        b.update()

    def _blocked(self, host, url):
        t = self.tab()
        if t and host_of(t.page.url()) == host:
            QTimer.singleShot(0, self.update_shield)

    def toolbar_action(self, k):
        t = self.tab()
        url = t.url() if t else QUrl()
        if k == "incognito":
            self.ctx.new_window(incognito=True)
        elif k == "split":
            self.toggle_split()
        elif k in ("history", "bookmarks", "downloads", "adblock"):
            self.open_flyout(k, self.items[k])
        elif k == "passwords":
            self.open_flyout("passwords", self.items[k])
        elif k in ("payments", "addresses"):
            self.new_tab("nacre://" + k)
        elif k == "reading_list":
            self.open_flyout("reading", self.items[k])
        elif k == "reading_mode" and t:
            self.reader_mode(t)
        elif k == "task_manager":
            self.new_tab("nacre://tasks")
        elif k == "settings":
            self.new_tab("nacre://settings")
        elif k == "clear_data":
            self.snack.show_msg("Delete history, cookies and cache?", "Delete", self.clear_data, 7000)
        elif k == "print" and t:
            self.print_page()
        elif k == "translate" and t:
            lang = QGuiApplication.inputMethod().locale().name().split("_")[0] or "en"
            t.load(QUrl(f"https://translate.google.com/translate?sl=auto&tl={lang}&u="
                        + bytes(QUrl.toPercentEncoding(url.toString())).decode()))
        elif k == "qr":
            self.show_qr(url.toString())
        elif k == "copy_link":
            QGuiApplication.clipboard().setText(url.toString())
            self.snack.show_msg("Link copied")
        elif k == "devtools" and t:
            self.devtools(t)
        else:
            label = next(v[1] for g in TOOLBAR_ITEMS.values() for kk, v in g.items() if kk == k)
            self.snack.show_msg(f"{label} is coming soon")

    def open_flyout(self, kind, anchor):
        from . import panels
        from .passwords_ui import PasswordsFlyout
        f = self.flyouts.get(kind)
        if f is None:
            f = {"media": lambda: MediaFlyout(self), "downloads": lambda: panels.DownloadsFlyout(self),
                 "adblock": lambda: panels.ShieldFlyout(self), "menu": lambda: panels.MainMenu(self),
                 "history": lambda: panels.ListFlyout(self, "history"),
                 "bookmarks": lambda: panels.ListFlyout(self, "bookmarks"),
                 "reading": lambda: panels.ListFlyout(self, "reading"),
                 "passwords": lambda: PasswordsFlyout(self), "site": lambda: panels.SiteInfoFlyout(self)}[kind]()
            self.flyouts[kind] = f
        if not anchor.isVisible():
            anchor = self.b_menu
        f.open_for(anchor)

    def clear_data(self):
        self.profile.clearHttpCache()
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearAllVisitedLinks()
        if not self.incognito:
            history.data.clear()
            history.touch()
            self.omni.reload_completions()
        self.snack.show_msg("Browsing data deleted")

    def print_page(self):
        from PyQt6.QtPrintSupport import QPrintDialog, QPrinter
        t = self.tab()
        self._printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(self._printer, self)
        if dlg.exec():
            t.view.printFinished.connect(lambda ok: self.snack.show_msg("Printed" if ok else "Printing failed"))
            t.view.print(self._printer)

    def show_qr(self, text):
        import qrcode                                       # bundled in nacre/_vendor
        q = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
        q.add_data(text)
        q.make(fit=True)
        m = q.get_matrix()
        n, cell = len(m), 10
        img = QImage(n * cell, n * cell, QImage.Format.Format_ARGB32)
        img.fill(QColor("white"))
        p = painter(img)
        p.setBrush(QColor("#1d1b20"))
        for y, row in enumerate(m):
            for x, on in enumerate(row):
                if on:                                      # slightly rounded modules, still scanner-friendly
                    p.drawRoundedRect(QRectF(x * cell + .4, y * cell + .4, cell - .8, cell - .8), 2.5, 2.5)
        p.end()
        f = Flyout(self, width=320)
        f.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        f.lay.addWidget(Label("Scan to open on another device", "onSurface", 16, 500))
        f.lay.addWidget(Label(text, "onSurfaceVariant", 12))
        lbl = QLabel()
        pm = QPixmap.fromImage(img).scaled(280, 280, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.FastTransformation)
        lbl.setPixmap(pm)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f.lay.addWidget(lbl)
        copy = Button("content_copy", "Copy image", kind="tonal", size=36)
        copy.clicked.connect(lambda: (QGuiApplication.clipboard().setImage(img), self.snack.show_msg("QR code copied")))
        save = Button("download", "Save", kind="text", size=36)
        save.clicked.connect(lambda: self._save_qr(img))
        f.lay.addWidget(hbox(None, save, copy, margins=(0, 8, 0, 0)))
        f.popup(self.items["qr"] if self.items["qr"].isVisible() else self.b_menu)

    def _save_qr(self, img):
        path, _ = QFileDialog.getSaveFileName(self, "Save QR code", os.path.join(cfg.get("downloads.dir"), "qr.png"),
                                              "PNG (*.png)")
        if path:
            img.save(path)
            self.snack.show_msg("Saved")

    def devtools(self, tab):
        if tab.devtools is None:
            v = QWebEngineView()
            v.setWindowTitle(f"DevTools — {tab.host()}")
            v.resize(1000, 700)
            v.setPage(QWebEnginePage(self.profile, v))
            tab.page.setDevToolsPage(v.page())
            tab.devtools = v
        tab.devtools.show()
        tab.devtools.raise_()

    def _download_added(self, e):
        self.update_toolbar()
        self.dl_timer.start()
        if cfg.get("downloads.auto_open") and self.isActiveWindow():
            self.open_flyout("downloads", self.items["downloads"])

    def _dl_tick(self):
        b = self.items["downloads"]
        frac = self.downloads.overall()
        act = self.downloads.active()
        b.ring = frac if act else None
        b.update()
        if not act:
            self.dl_timer.stop()
            self.update_toolbar()

    def _lifecycle(self):
        """Freeze, then discard, background tabs that are silent — saves CPU, RAM and background traffic."""
        L = QWebEnginePage.LifecycleState
        now = time.monotonic()
        fz, dc = cfg.get("tabs.freeze_min") * 60, cfg.get("tabs.discard_min") * 60
        for t in self.tabs:
            if t is self.tab() or t is self.split_tab or t.hidden_at is None or t.pending is not None or t.item.audible:
                continue
            idle = now - t.hidden_at
            want = L.Discarded if dc and idle > dc else L.Frozen if fz and idle > fz else L.Active
            rec = t.page.recommendedState()
            want = want if want.value <= rec.value else rec           # never beyond what Qt allows
            if want.value > t.page.lifecycleState().value:
                t.page.setLifecycleState(want)

    # ── permissions / fullscreen ──
    def permission_request(self, perm):
        kind = perm.permissionType().name
        what = PERM_NAMES.get(kind, kind)
        host = host_of(perm.origin()) or perm.origin().toString()
        self.snack.show_msg(f"{host} wants to use {what}", "Allow", perm.grant, 12000)

    def fullscreen_request(self, req):
        req.accept()
        self.page_fs = req.toggleOn()
        self.set_fullscreen(self.page_fs, chrome=not self.page_fs)

    def set_fullscreen(self, on, chrome=True):
        self.top.setVisible(chrome or not on)
        self.toolbar.setVisible(chrome or not on)
        if on and not self.isFullScreen():
            self._was_max = self.isMaximized()
            self.showFullScreen()
        elif not on and self.isFullScreen():
            self.showMaximized() if getattr(self, "_was_max", False) else self.showNormal()
        self._apply_layout()
        self.esc_sync()

    def toggle_fullscreen(self):
        t = self.tab()
        if getattr(self, "page_fs", False) and t:
            t.page.triggerAction(QWebEnginePage.WebAction.ExitFullScreen)   # comes back via fullscreen_request
        else:
            self.set_fullscreen(not self.isFullScreen())

    def toggle_max(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    # ── layout / frame ──
    def _apply_layout(self):
        full = self.isFullScreen()
        edge = self.EDGE if self.translucent and not (self.isMaximized() or full) else 0
        self.layout().setContentsMargins(edge, edge, edge, edge)
        floating = cfg.get("ui.floating_card") and not full
        self.card_wrap.layout().setContentsMargins(*((8, 0, 8, 8) if floating else (0, 0, 0, 0)))
        if self.wc:
            self.wc[1].set_icon("filter_none" if self.isMaximized() else "crop_square")
        self.card.place_corners()
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        if self.frameless and winframe.ENABLED and not getattr(self, "_snappable", False):
            self._snappable = True
            winframe.install(self)                 # Aero Snap + window animations for the frameless window

    if winframe.ENABLED:                           # only Windows gets a Python hook on every native message
        def nativeEvent(self, event_type, message):
            if self.frameless:
                handled = winframe.native_event(self, event_type, message)
                if handled:
                    return handled
            return super().nativeEvent(event_type, message)

    def changeEvent(self, e):
        if e.type() == QEvent.Type.WindowStateChange:
            self._apply_layout()
        elif e.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.ctx.last_active = self
            if self.tab():
                theme.set_site_seed(self.tab().seed)
        super().changeEvent(e)

    def paintEvent(self, e):
        p = painter(self)
        r = QRectF(self.rect())
        if self.translucent and not (self.isMaximized() or self.isFullScreen()):
            m = self.EDGE - 2
            p.setBrush(theme["surfaceContainer"])
            p.drawRoundedRect(r.adjusted(m, m, -m, -m), theme.r(14), theme.r(14))
        else:
            p.fillRect(r, theme["surfaceContainer"])

    def _edges(self, pos):
        if not self.frameless or self.isMaximized() or self.isFullScreen():
            return Qt.Edge(0)
        E, m = Qt.Edge, self.EDGE + 2
        e = Qt.Edge(0)
        if pos.x() < m: e |= E.LeftEdge
        if pos.x() > self.width() - m: e |= E.RightEdge
        if pos.y() < m: e |= E.TopEdge
        if pos.y() > self.height() - m: e |= E.BottomEdge
        return e

    def eventFilter(self, obj, e):
        # the edge resize cursor is set on the window and inherited by children — drop it as soon as the
        # pointer enters any inner widget, otherwise it "sticks" over the toolbar and tabs
        if e.type() == QEvent.Type.Enter and obj is not self and self.testAttribute(Qt.WidgetAttribute.WA_SetCursor):
            try:
                if obj.isWidgetType() and obj.window() is self:
                    self.unsetCursor()
            except RuntimeError:
                pass
        return False

    def leaveEvent(self, e):
        self.unsetCursor()

    def mouseMoveEvent(self, e):
        E, ed = Qt.Edge, self._edges(e.position())
        C = Qt.CursorShape
        if ed in (E.LeftEdge | E.TopEdge, E.RightEdge | E.BottomEdge):
            self.setCursor(C.SizeFDiagCursor)
        elif ed in (E.RightEdge | E.TopEdge, E.LeftEdge | E.BottomEdge):
            self.setCursor(C.SizeBDiagCursor)
        elif ed & (E.LeftEdge | E.RightEdge):
            self.setCursor(C.SizeHorCursor)
        elif ed:
            self.setCursor(C.SizeVerCursor)
        else:
            self.unsetCursor()

    def mousePressEvent(self, e):
        ed = self._edges(e.position())
        if ed and e.button() == Qt.MouseButton.LeftButton:
            self.windowHandle().startSystemResize(ed)

    def _cfg_changed(self, k, v):
        if k == "ntp.shortcuts":
            for t in self.tabs:
                if t.is_nacre():
                    t.page.runJavaScript(f"window.nacreSetShortcuts && nacreSetShortcuts({json.dumps(v)})", 0)
        elif k.startswith(("toolbar.", "media.button", "adblock.")):
            self.update_toolbar()
        elif k.startswith("ui."):
            self._apply_layout()

    # ── web apps ──
    def _app_mode(self, app):
        """Chrome-style app window: no tabs or address bar, the app's own name and icon."""
        self.strip.hide()
        ic = pwa.app_icon(app)
        if not ic.isNull():
            self.setWindowIcon(ic)
        badge = QLabel()
        badge.setPixmap(ic.pixmap(22, 22))
        title = Label(app["name"], "onSurface", 14, 500)
        top = self.top.layout()
        top.insertWidget(0, badge)
        top.insertWidget(1, title)
        top.insertStretch(2, 1)
        top.setContentsMargins(14, 4, 6, 0)
        self.app_title = title
        self.omni.hide()
        for b in self.items.values():
            b.hide()
        self.b_media.hide()
        tb = self.toolbar.layout()
        tb.insertStretch(tb.indexOf(self.omni), 1)
        out = self._tb("open_in_browser", "Open in Nacre", self._open_in_browser)
        tb.insertWidget(tb.indexOf(self.b_menu), out)
        self.setWindowTitle(app["name"])

    def _open_in_browser(self):
        if self.tab():
            self.ctx.browser_window().new_tab(self.tab().url())

    def check_installable(self, tab):
        if self.app or tab.is_nacre() or tab.page.url().scheme() != "https":
            tab.installable = False
        else:
            tab.page.runJavaScript("!!document.querySelector('link[rel=\"manifest\"]')", 0,
                                   guard(self, lambda has, tab=tab: self._installable(tab, has)))
            return
        self._installable(tab, False)

    def _installable(self, tab, has):
        tab.installable = bool(has) and pwa.installed_for(tab.url().toString()) is None
        if tab is self.tab():
            self.omni.install.setVisible(tab.installable)

    def install_current(self, shortcut=False):
        t = self.tab()
        if t and not t.is_nacre():
            old = getattr(self, "_install_fly", None)
            if old is not None:
                old.deleteLater()
            self._install_fly = pwa.InstallFlyout(self, t, shortcut)   # kept until the next one: replies arrive async
            self._install_fly.open_for(self.omni.install if self.omni.install.isVisible() else self.b_menu)
            return self._install_fly

    # ── shortcuts ──
    def _shortcuts(self):
        K = QKeySequence
        binds = {
            "Ctrl+T": lambda: self.new_tab(), "Ctrl+W": lambda: self.close_tab(), "Ctrl+F4": lambda: self.close_tab(),
            "Ctrl+Shift+T": self.reopen_tab, "Ctrl+Tab": lambda: self.cycle(1), "Ctrl+Shift+Tab": lambda: self.cycle(-1),
            "Ctrl+PgDown": lambda: self.cycle(1), "Ctrl+PgUp": lambda: self.cycle(-1),
            "Ctrl+L": self.focus_omnibox, "Alt+D": self.focus_omnibox, "F6": self.focus_omnibox,
            "Ctrl+R": self.reload_or_stop, "F5": self.reload_or_stop,
            "Ctrl+Shift+R": lambda: self.tab() and self.tab().page.triggerAction(
                QWebEnginePage.WebAction.ReloadAndBypassCache),
            "Ctrl+F": self.findbar.open_bar, "F3": lambda: self.findbar.find(),
            "Ctrl+D": self.toggle_bookmark, "Ctrl+H": lambda: self.toolbar_action("history"),
            "Ctrl+Shift+H": lambda: self.new_tab("nacre://history"),
            "Ctrl+J": lambda: self.toolbar_action("downloads"), "Ctrl+Shift+B": lambda: self.toolbar_action("bookmarks"),
            "Ctrl+N": lambda: self.ctx.new_window(), "Ctrl+Shift+N": lambda: self.ctx.new_window(incognito=True),
            "Ctrl+=": lambda: self.zoom(1), "Ctrl++": lambda: self.zoom(1), "Ctrl+-": lambda: self.zoom(-1),
            "Ctrl+0": lambda: self.zoom(0), "Alt+Left": lambda: self.tab() and self.tab().view.back(),
            "Alt+Right": lambda: self.tab() and self.tab().view.forward(), "Alt+Home": self.go_home,
            "F11": self.toggle_fullscreen, "F12": lambda: self.tab() and self.devtools(self.tab()),
            "Ctrl+Shift+I": lambda: self.tab() and self.devtools(self.tab()), "Ctrl+P": self.print_page,
            "Ctrl+,": lambda: self.new_tab(SETTINGS), "Ctrl+U": lambda: self.tab() and self.new_tab(
                "view-source:" + self.tab().url().toString()),
            "Ctrl+M": lambda: self.tab() and self.tab().page.setAudioMuted(not self.tab().page.isAudioMuted()),
            "Ctrl+Q": self.ctx.quit, "Ctrl+Shift+S": self.toggle_split, "Alt+R": lambda: self.reader_mode(),
            "Shift+Esc": lambda: self.new_tab("nacre://tasks"),
        }
        for i in range(1, 10):
            binds[f"Ctrl+{i}"] = (lambda i=i: self.set_current(len(self.tabs) - 1 if i == 9 else i - 1))
        for key, fn in binds.items():
            QShortcut(K(key), self, activated=fn, context=Qt.ShortcutContext.WindowShortcut)
        # Esc is only grabbed while there's something of ours to escape; otherwise pages get it
        self.esc = QShortcut(K("Escape"), self, activated=self._escape, context=Qt.ShortcutContext.WindowShortcut)
        self.esc.setEnabled(False)

    def esc_sync(self):
        t = self.tab()
        self.esc.setEnabled(self.isFullScreen() or self.findbar.isVisible() or bool(t and t.item.loading))

    def _escape(self):
        t = self.tab()
        if self.isFullScreen():
            self.toggle_fullscreen()
        elif self.findbar.isVisible():
            self.findbar.close_bar()
        elif t and t.item.loading:
            t.view.stop()

    # ── session ──
    def session_urls(self):
        if self.app:
            return []
        return [t.url().toString() for t in self.tabs if not t.url().isEmpty()]

    def closeEvent(self, e):
        if not self.incognito:
            self.ctx.save_session(self)
        for f in self.flyouts.values():
            f.hide()
        self.teardown()
        self.ctx.window_closed(self)
        super().closeEvent(e)

    def teardown(self):
        QGuiApplication.instance().removeEventFilter(self)
        """Pages must die before their profile, or QtWebEngine segfaults on exit."""
        for t in self.tabs:
            t.view.setPage(None)
            sip.delete(t.page)
            if t.devtools:
                sip.delete(t.devtools)
        self.tabs.clear()
