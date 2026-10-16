"""Native nacre:// pages: history, bookmarks, downloads, about — searchable M3 grouped lists."""
import os
import re
import signal
import sys
import time

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWebEngineCore import QWebEnginePage, qWebEngineChromiumVersion
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QScrollArea, QVBoxLayout, QWidget

from .config import CACHE_DIR, CONFIG_DIR, DATA_DIR, bookmarks, cfg, history, reading
from .panels import DownloadRow
from .services import host_of
from .settings_page import SetRow, group
from .theme import theme, ticker
from .widgets import Button, Label, TextField, guard, hbox, painter

# host -> (title, tab symbol)
NATIVE = {"settings": ("Settings", "settings"), "history": ("History", "history"),
          "bookmarks": ("Bookmarks", "bookmarks"), "downloads": ("Downloads", "download"),
          "reading": ("Reading list", "chrome_reader_mode"), "tasks": ("Task manager", "monitoring"),
          "passwords": ("Passwords", "key"), "addresses": ("Addresses", "location_on"),
          "payments": ("Payment methods", "credit_card"), "apps": ("Apps", "apps"),
          "about": ("About Nacre", "info")}


def make_page(host, win):
    if host == "settings":
        from .settings_page import SettingsPage
        return SettingsPage(win)
    if host in ("passwords", "addresses", "payments"):
        from .passwords_ui import PasswordsPage
        return PasswordsPage(win, host)
    if host == "apps":
        return AppsPage(win)
    return {"history": HistoryPage, "bookmarks": BookmarksPage, "downloads": DownloadsPage,
            "reading": ReadingPage, "tasks": TasksPage, "about": AboutPage}[host](win)


class LinkRow(SetRow):
    """A grouped row that opens a URL: click → this tab, middle/Ctrl-click → new tab, right-click → menu."""

    def __init__(self, win, icon, title, sub, url, control=None, extra_menu=None):
        super().__init__(icon, title, sub, control)
        self.win, self.url, self.extra_menu = win, url, extra_menu
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(url)

    def mouseReleaseEvent(self, e):
        new = e.button() == Qt.MouseButton.MiddleButton or e.modifiers() & Qt.KeyboardModifier.ControlModifier
        if e.button() == Qt.MouseButton.RightButton:
            m = QMenu(self)
            m.addAction("Open in new tab", lambda: self.win.new_tab(self.url, background=True))
            m.addAction("Copy link", lambda: QGuiApplication.clipboard().setText(self.url))
            for label, fn in self.extra_menu or ():
                m.addAction(label, fn)
            m.exec(e.globalPosition().toPoint())
        elif new:
            self.win.new_tab(self.url, background=True)
        elif e.button() == Qt.MouseButton.LeftButton:
            self.win.tab().load(self.url)


class NativePage(QWidget):
    TITLE, PLACEHOLDER = "", "Search"

    def __init__(self, win):
        super().__init__()
        self.win = win
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(area)
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(24, 24, 24, 48)
        col = QWidget()
        col.setMaximumWidth(780)
        row.addStretch(1)
        row.addWidget(col, 100)
        row.addStretch(1)
        area.setWidget(inner)
        self.area = area
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(Label(self.TITLE, "onSurface", 30))
        head.addStretch(1)
        self.actions = QHBoxLayout()
        head.addLayout(self.actions)
        self.search = TextField(placeholder=self.PLACEHOLDER, icon="search")
        self.search.setFixedWidth(300)
        head.addWidget(self.search)
        lay.addLayout(head)
        self.col_lay = lay                       # subclasses may insert widgets under the header
        self.body = QWidget()
        self.v = QVBoxLayout(self.body)
        self.v.setContentsMargins(0, 8, 0, 0)
        self.v.setSpacing(10)
        lay.addWidget(self.body)
        lay.addStretch(1)
        self._debounce = QTimer(self, singleShot=True, interval=150, timeout=self.rebuild)
        self.search.edit.textChanged.connect(self._debounce.start)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        painter(self).fillRect(self.rect(), theme["surface"])

    def showEvent(self, e):
        self.rebuild()
        QTimer.singleShot(0, self.search.edit.setFocus)

    def q(self):
        return self.search.edit.text().strip().lower()

    def clear(self):
        while self.v.count():
            w = self.v.takeAt(0).widget()
            w and w.deleteLater()

    def sub(self, text):
        lbl = Label(text, "primary", 14, 500)
        lbl.setContentsMargins(20, 12, 0, 2)
        self.v.addWidget(lbl)

    def empty(self, text):
        lbl = Label(text, "onSurfaceVariant", 15)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setMinimumHeight(160)
        self.v.addWidget(lbl)

    def rebuild(self):
        pass


def matches(q, *fields):
    return not q or any(q in (f or "").lower() for f in fields)


def day_label(t):
    d = time.localtime(t)
    today = time.localtime()
    days = (time.mktime(today[:3] + (0, 0, 0, 0, 0, -1)) - time.mktime(d[:3] + (0, 0, 0, 0, 0, -1))) // 86400
    return "Today" if days == 0 else "Yesterday" if days == 1 else time.strftime("%A, %B %-d, %Y", d)


class HistoryPage(NativePage):
    TITLE, PLACEHOLDER = "History", "Search history"

    def __init__(self, win):
        super().__init__(win)
        self.limit = 300
        clear_btn = Button("delete_sweep", "Clear history", kind="text", size=40)
        clear_btn.clicked.connect(lambda: win.snack.show_msg("Delete all history?", "Delete", self._clear_all, 7000))
        self.actions.addWidget(clear_btn)

    def _clear_all(self):
        history.data.clear()
        history.touch()
        self.win.omni.reload_completions()
        self.rebuild()

    def _remove(self, h):
        if h in history.data:
            history.data.remove(h)
            history.touch()
        self.rebuild()

    def rebuild(self):
        self.clear()
        q = self.q()
        items = [h for h in reversed(history.data) if matches(q, h["url"], h.get("title"))]
        shown, day, rows = 0, None, []
        for h in items[:self.limit]:
            d = day_label(h.get("t", 0))
            if d != day:
                if rows:
                    self.v.addWidget(group(*rows))
                self.sub(d)
                day, rows = d, []
            rm = Button("close", size=32, tip="Remove from history", icon_px=18)
            rm.clicked.connect(lambda _, h=h: self._remove(h))
            host = host_of(h["url"]) or h["url"]
            rows.append(LinkRow(self.win, None, h.get("title") or h["url"],
                                f"{time.strftime('%H:%M', time.localtime(h.get('t', 0)))} · {host}", h["url"], rm,
                                [("Remove from history", lambda h=h: self._remove(h)),
                                 ("More from this site", lambda host=host: self.search.edit.setText(host))]))
            shown += 1
        if rows:
            self.v.addWidget(group(*rows))
        if not items:
            self.empty("No history matches your search" if q else "Pages you visit will show up here")
        elif len(items) > self.limit:
            more = Button("expand_more", f"Show more ({len(items) - self.limit} left)", kind="tonal", size=40)
            more.clicked.connect(lambda: (setattr(self, "limit", self.limit + 300), self.rebuild()))
            self.v.addWidget(more, 0, Qt.AlignmentFlag.AlignHCenter)


class BookmarksPage(NativePage):
    TITLE, PLACEHOLDER = "Bookmarks", "Search bookmarks"

    def _remove(self, b):
        if b in bookmarks.data:
            bookmarks.data.remove(b)
            bookmarks.touch()
            self.win.sync_omnibox()
            self.win.snack.show_msg("Bookmark removed", "Undo", lambda: (bookmarks.data.append(b), bookmarks.touch(),
                                                                         self.rebuild()))
        self.rebuild()

    def rebuild(self):
        self.clear()
        q = self.q()
        rows = []
        for b in reversed(bookmarks.data):
            if not matches(q, b["url"], b.get("title")):
                continue
            rm = Button("delete", size=32, tip="Remove bookmark", icon_px=18)
            rm.clicked.connect(lambda _, b=b: self._remove(b))
            rows.append(LinkRow(self.win, "bookmark", b.get("title") or b["url"], host_of(b["url"]) or b["url"],
                                b["url"], rm, [("Remove bookmark", lambda b=b: self._remove(b))]))
        if rows:
            self.v.addWidget(group(*rows))
        else:
            self.empty("No bookmarks match your search" if q else "Press Ctrl+D or the ☆ in the address bar to bookmark")


class ReadingPage(NativePage):
    TITLE, PLACEHOLDER = "Reading list", "Search reading list"

    def __init__(self, win):
        super().__init__(win)
        add = Button("add", "Add current tab", kind="tonal", size=40)
        add.clicked.connect(lambda: self._add_from_window())
        self.actions.addWidget(add)

    def _add_from_window(self):
        # this page *is* the current tab, so offer the most recent web tab in the window instead
        web = [t for t in self.win.tabs if not t.is_nacre()]
        if web:
            self.win.add_to_reading(web[-1])
            self.rebuild()

    def _set(self, item, **kw):
        item.update(kw)
        reading.touch()
        self.rebuild()

    def _remove(self, item):
        if item in reading.data:
            reading.data.remove(item)
            reading.touch()
        self.rebuild()

    def rebuild(self):
        self.clear()
        q = self.q()
        items = [r for r in reversed(reading.data) if matches(q, r["url"], r.get("title"))]
        for label, read in (("Unread", False), ("Read", True)):
            rows = []
            for r in (x for x in items if bool(x.get("read")) == read):
                tog = Button("check_circle" if not read else "undo", size=32, icon_px=18,
                             tip="Mark as read" if not read else "Mark as unread")
                tog.clicked.connect(lambda _, r=r, read=read: self._set(r, read=not read))
                rm = Button("delete", size=32, icon_px=18, tip="Remove")
                rm.clicked.connect(lambda _, r=r: self._remove(r))
                row = LinkRow(self.win, "chrome_reader_mode" if not read else "check_circle",
                              r.get("title") or r["url"], f"{host_of(r['url'])} · added {day_label(r.get('t', 0))}",
                              r["url"], hbox(tog, rm, spacing=0))
                row.mouseReleaseEvent = lambda e, row=row, r=r: (
                    e.button() == Qt.MouseButton.LeftButton and not r.get("read") and self._set(r, read=True),
                    LinkRow.mouseReleaseEvent(row, e))
                rows.append(row)
            if rows:
                self.sub(f"{label} · {len(rows)}")
                self.v.addWidget(group(*rows))
        if not items:
            self.empty("No matches" if q else "Save pages for later with the reading list button or the menu")


def _proc(pid):
    """(name/type, rss bytes, cpu ticks) for a pid, or None if it's gone."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            st = f.read().rsplit(")", 1)[1].split()
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            cmd = re.split(r"[\0 ]", f.read().decode(errors="ignore"))   # Chromium rewrites argv with spaces
        with open(f"/proc/{pid}/statm") as f:
            rss = int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        return None
    kind = next((a.split("=", 1)[1] for a in cmd if a.startswith("--type=")), "")
    sub = next((a.split("=", 1)[1] for a in cmd if a.startswith("--utility-sub-type=")), "")
    return {"ppid": int(st[1]), "ticks": int(st[11]) + int(st[12]), "rss": rss, "type": kind, "sub": sub}


def nacre_processes():
    me = os.getpid()
    procs = {}
    if not os.path.isdir("/proc"):                # Linux only for now
        return {}
    for d in os.listdir("/proc"):
        if d.isdigit():
            p = _proc(int(d))
            if p:
                procs[int(d)] = p
    keep, frontier = {me: procs.get(me)}, [me]
    while frontier:                               # every descendant of this python process
        parent = frontier.pop()
        for pid, p in procs.items():
            if p["ppid"] == parent and pid not in keep:
                keep[pid] = p
                frontier.append(pid)
    return {k: v for k, v in keep.items() if v}


PROC_NAMES = {"": "Browser (Nacre)", "gpu-process": "GPU", "zygote": "Zygote", "renderer": "Renderer"}
SUB_NAMES = {"network.mojom.NetworkService": "Network service", "storage.mojom.StorageService": "Storage service",
             "audio.mojom.AudioService": "Audio service", "video_capture.mojom.VideoCaptureService": "Video capture",
             "data_decoder.mojom.DataDecoderService": "Data decoder"}


class TasksPage(NativePage):
    TITLE, PLACEHOLDER = "Task manager", "Search tabs and processes"

    def __init__(self, win):
        super().__init__(win)
        self.total = Label("", "onSurfaceVariant", 14, 500)
        self.actions.addWidget(self.total)
        self.rows, self.keys, self.last = {}, None, {}
        self.timer = QTimer(self, interval=2000, timeout=self.refresh)
        self.clk = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def showEvent(self, e):
        super().showEvent(e)
        self.timer.start()

    def hideEvent(self, e):
        self.timer.stop()

    def _tabs(self):
        return [(w, t) for w in self.win.ctx.windows for t in w.tabs]

    def _snapshot(self):
        procs = nacre_processes()
        now = time.monotonic()
        cpu = {}
        for pid, p in procs.items():
            t0, k0 = self.last.get(pid, (None, None))
            cpu[pid] = (p["ticks"] - k0) / self.clk / (now - t0) * 100 if t0 else 0.0
        self.last = {pid: (now, p["ticks"]) for pid, p in procs.items()}
        return procs, cpu

    def rebuild(self):
        self.keys = None
        self.refresh()

    def refresh(self):
        procs, cpu = self._snapshot()
        q = self.q()
        tabs = [(w, t) for w, t in self._tabs() if matches(q, t.title(), t.url().toString())]
        by_pid = {}
        for _, t in tabs:
            by_pid.setdefault(t.page.renderProcessPid(), []).append(t)
        system = [pid for pid, p in procs.items() if p["type"] != "renderer"
                  and matches(q, PROC_NAMES.get(p["type"], p["type"]), SUB_NAMES.get(p["sub"], p["sub"]))]
        keys = (tuple(id(t) for _, t in tabs), tuple(system))
        self.total.setText(f"{sum(p['rss'] for p in procs.values()) / 2 ** 20:,.0f} MB · "
                           f"{sum(cpu.values()):.0f}% CPU · {len(procs)} processes")
        if keys != self.keys:
            self.keys = keys
            self._build(tabs, system, procs)
        for _, t in tabs:
            pid = t.page.renderProcessPid()
            p = procs.get(pid)
            state = t.page.lifecycleState().name
            shared = len(by_pid.get(pid, [])) > 1
            info = (f"{p['rss'] / 2 ** 20:,.0f} MB · {cpu.get(pid, 0):.0f}% CPU · PID {pid}"
                    + (" (shared)" if shared else "")) if p else ("Not loaded" if t.pending else "No process")
            if state and state != "Active":
                info += f" · {state}"
            row = self.rows.get(id(t))
            if row and row.sub:
                row.sub.setText(info)
        for pid in system:
            p, row = procs[pid], self.rows.get(("p", pid))
            if row and row.sub:
                row.sub.setText(f"{p['rss'] / 2 ** 20:,.0f} MB · {cpu.get(pid, 0):.0f}% CPU · PID {pid}")

    def _build(self, tabs, system, procs):
        self.clear()
        self.rows = {}
        rows = []
        for w, t in tabs:
            freeze = Button("ac_unit", size=32, icon_px=18, tip="Freeze tab (stops its scripts)")
            freeze.clicked.connect(lambda _, t=t: self._freeze(t))
            end = Button("dangerous", size=32, icon_px=18, tip="End process")
            end.clicked.connect(lambda _, t=t: self._kill(t.page.renderProcessPid()))
            close = Button("close", size=32, icon_px=18, tip="Close tab")
            close.clicked.connect(lambda _, w=w, t=t: t in w.tabs and w.close_tab(w.tabs.index(t)))
            row = SetRow("tab" + ("_unselected" if w.incognito else ""), t.title() or t.url().toString(), "…",
                         hbox(freeze, end, close, spacing=0))
            self.rows[id(t)] = row
            rows.append(row)
        if rows:
            self.sub("Tabs")
            self.v.addWidget(group(*rows))
        rows = []
        for pid in system:
            p = procs[pid]
            name = SUB_NAMES.get(p["sub"]) or PROC_NAMES.get(p["type"]) or p["sub"] or p["type"] or "Helper"
            row = SetRow({"": "diamond", "gpu-process": "memory"}.get(p["type"], "settings"), name, "…")
            self.rows[("p", pid)] = row
            rows.append(row)
        if rows:
            self.sub("Nacre processes")
            self.v.addWidget(group(*rows))
        if not tabs and not system:
            self.empty("Nothing matches your search")

    def _freeze(self, t):
        L = QWebEnginePage.LifecycleState
        if t is t.win.tab():
            return self.win.snack.show_msg("Can't freeze the tab you're looking at")
        if t.page.recommendedState().value < L.Frozen.value:
            return self.win.snack.show_msg("This tab is busy (playing audio or similar) and can't be frozen")
        t.page.setLifecycleState(L.Frozen)
        self.refresh()

    def _kill(self, pid):
        if pid and pid != os.getpid():
            try:
                os.kill(pid, signal.SIGTERM)             # the tab shows its "crashed — Reload" snackbar
            except OSError:
                pass


class AppRow(SetRow):
    """Grouped row with the app's real icon instead of a symbol."""

    def __init__(self, app, *a, **k):
        super().__init__("apps", *a, **k)
        from .pwa import app_icon
        self.pm = app_icon(app).pixmap(28, 28)

    def paintEvent(self, e):
        icon, self.icon = self.icon, (None if not self.pm.isNull() else self.icon)
        SetRow.paintEvent(self, e)
        self.icon = icon
        if not self.pm.isNull():
            p = painter(self)
            p.drawPixmap(16, (min(self.height(), 64) - 28) // 2, self.pm)


class AppsPage(NativePage):
    TITLE, PLACEHOLDER = "Apps", "Search apps"

    def __init__(self, win):
        super().__init__(win)
        add = Button("install_desktop", "Install current page", kind="tonal", size=40)
        add.clicked.connect(self._install_from_window)
        self.actions.addWidget(add)

    def _install_from_window(self):
        web = [t for t in self.win.tabs if not t.is_nacre()]
        if not web:
            return self.win.snack.show_msg("Open the site you want to install in a tab first")
        self.win.set_current(self.win.index(web[-1]))
        self.win.install_current()

    def _save(self, app):
        from . import pwa
        if app.get("launcher"):
            pwa.write_desktop(app)                # keep the launcher entry in sync
        pwa.apps.touch()
        self.rebuild()

    def _edit(self, app):
        from .passwords_ui import FormFlyout

        def save(d):
            if QUrl(d["url"]).scheme() not in ("http", "https"):
                self.win.snack.show_msg("The start address must be a web address")
                return False
            app.update(name=d["name"] or app["name"], url=d["url"])
            if not d["url"].startswith(app.get("scope") or ""):
                app["scope"] = ""                  # scope no longer contains the start page: widen it
            self._save(app)
        FormFlyout(self.win, "Edit app", [("name", "Name", "edit", False), ("url", "Start address", "link", False)],
                   {"name": app["name"], "url": app["url"]}, save).open_center()

    def _uninstall(self, app):
        from . import pwa
        for w in list(self.win.ctx.windows):
            if w.app and w.app["id"] == app["id"]:
                w.close()
        pwa.uninstall(app)
        self.win.snack.show_msg(f"Uninstalled {app['name']}")
        self.rebuild()

    def rebuild(self):
        from . import pwa
        self.clear()
        q = self.q()
        rows = []
        for a in sorted(pwa.apps.data, key=lambda a: a["name"].lower()):
            if not matches(q, a["name"], a["url"]):
                continue
            opn = Button("open_in_new", size=32, icon_px=18, tip="Open")
            opn.clicked.connect(lambda _, a=a: self.win.ctx.open_app(a["id"]))
            mode = Button("web_asset" if a["window"] else "tab", size=32, icon_px=18,
                          tip="Opens as a window — switch to tab" if a["window"] else "Opens as a tab — switch to window")
            mode.clicked.connect(lambda _, a=a: (a.update(window=not a["window"]), self._save(a)))
            launch = Button("rocket_launch", size=32, icon_px=18, checkable=True,
                            tip="Shown in the app launcher" if a.get("launcher") else "Add to the app launcher")
            launch.setChecked(bool(a.get("launcher")))
            launch.clicked.connect(lambda _, a=a: (pwa.remove_desktop(a) if a.get("launcher") else pwa.write_desktop(a),
                                                   self._save(a)))
            ed = Button("edit", size=32, icon_px=18, tip="Edit")
            ed.clicked.connect(lambda _, a=a: self._edit(a))
            rm = Button("delete", size=32, icon_px=18, tip="Uninstall")
            rm.clicked.connect(lambda _, a=a: self.win.snack.show_msg(f"Uninstall {a['name']}?", "Uninstall",
                                                                       lambda a=a: self._uninstall(a), 7000))
            kind = ("Web app" if a.get("manifest") else "Site") + (" · window" if a["window"] else " · tab")
            rows.append(AppRow(a, a["name"], f"{QUrl(a['url']).host()} · {kind}",
                               hbox(opn, mode, launch, ed, rm, spacing=0)))
        if rows:
            self.v.addWidget(group(*rows))
        else:
            self.empty("No matches" if q else "Install a site from the ⤓ button in the address bar, or the menu's\n"
                                              "“Install page as app…” / “Create shortcut…”.")


class CardBox(QWidget):
    """Wraps any widget in grouped-row card styling."""
    paintEvent = SetRow.paintEvent

    def __init__(self, inner):
        super().__init__()
        self.icon, self.pos = None, "single"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.addWidget(inner)
        theme.changed.connect(self.update)


class DownloadsPage(NativePage):
    TITLE, PLACEHOLDER = "Downloads", "Search downloads"

    def __init__(self, win):
        super().__init__(win)
        self.rows = []
        folder = Button("folder", "Open folder", kind="text", size=40)
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(cfg.get("downloads.dir"))))
        clr = Button("clear_all", "Clear finished", kind="text", size=40)
        clr.clicked.connect(lambda: (win.downloads.clear_finished(), self.rebuild()))
        self.actions.addWidget(folder)
        self.actions.addWidget(clr)
        win.downloads.added.connect(guard(self, lambda e: self.isVisible() and self.rebuild()))

    def rebuild(self):
        self.clear()
        q = self.q()
        self.rows = [DownloadRow(e) for e in self.win.downloads.entries if matches(q, e.name, e.url)]
        if self.rows:
            self.v.addWidget(group(*[CardBox(r) for r in self.rows]))
        else:
            self.empty("No downloads match your search" if q else "Files you download will show up here")

    def showEvent(self, e):
        super().showEvent(e)
        ticker.add(self)

    def hideEvent(self, e):
        ticker.remove(self)

    def tick(self, dt):
        for r in self.rows:
            r.tick(dt)


class AboutPage(NativePage):
    TITLE, PLACEHOLDER = "About Nacre", "Search pages"

    def rebuild(self):
        self.clear()
        q = self.q()
        try:
            import materialyoucolor
            myc = getattr(materialyoucolor, "__version__", "installed")
        except ImportError:
            myc = "missing"
        info = [SetRow("diamond", "Nacre", "A Material You browser built on Qt WebEngine"),
                SetRow("memory", "Engine", f"Chromium {qWebEngineChromiumVersion()} · Qt {QT_VERSION_STR} · "
                                           f"PyQt {PYQT_VERSION_STR} · Python {sys.version.split()[0]}"),
                SetRow("palette", "Colour", f"materialyoucolor {myc}"),
                SetRow("shield", "Ad blocking", f"{self.win.adblock.engine_name()} · "
                                                f"{self.win.adblock.rule_count():,} rules"),
                SetRow("folder_data", "Profile", f"{CONFIG_DIR}\n{DATA_DIR}\n{CACHE_DIR}")]
        info = [r for r in info if matches(q, r.title_text)]
        if info:
            self.v.addWidget(group(*info))
        pages = [LinkRow(self.win, sym, title, f"nacre://{host}", f"nacre://{host}")
                 for host, (title, sym) in list(NATIVE.items()) + [("newtab", ("New Tab", "add_circle"))]
                 if matches(q, host, title)]
        if pages:
            self.sub("Nacre pages")
            self.v.addWidget(group(*pages))
