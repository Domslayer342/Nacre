"""Toolbar flyouts: downloads, ad-block shield, main menu, history / bookmarks lists."""
import os
import time

from PyQt6.QtCore import QRectF, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QGuiApplication
from PyQt6.QtWidgets import QHBoxLayout, QMenu, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from .config import bookmarks, cfg, history, reading
from .media import LiveWave, WavyProgress
from .services import host_of
from .theme import draw_icon, theme, ticker
from .widgets import Button, Divider, Flyout, Label, Row, Switch, TextField, guard, hbox, painter

EXT_ICONS = {("zip", "7z", "rar", "tar", "gz", "xz", "zst"): "folder_zip", ("png", "jpg", "jpeg", "gif", "webp", "svg",
             "avif"): "image", ("mp4", "mkv", "webm", "mov", "avi"): "movie", ("mp3", "flac", "ogg", "opus", "wav",
             "m4a"): "music_note", ("pdf",): "picture_as_pdf", ("exe", "msi", "appimage", "deb", "rpm"): "deployed_code",
             ("iso", "img"): "album", ("txt", "md", "json", "csv"): "description"}


def ext_icon(name):
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return next((v for k, v in EXT_ICONS.items() if ext in k), "draft")


def scroller(max_h):
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    area.setWidget(inner)
    area.setMaximumHeight(max_h)
    return area, lay


def clear(lay):
    while lay.count():
        w = lay.takeAt(0).widget()
        if w:
            w.deleteLater()


def header(title, *buttons):
    return hbox(Label(title, "onSurface", 18, 500), None, *buttons, margins=(8, 0, 0, 6))


class Tile(QWidget):
    """Tonal rounded square with a symbol (leading visual for rows)."""

    def __init__(self, icon, size=44):
        super().__init__()
        self.icon = icon
        self.setFixedSize(size, size)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        p.setBrush(theme["secondaryContainer"])
        p.drawRoundedRect(QRectF(self.rect()), theme.r(14), theme.r(14))
        draw_icon(p, QRectF(self.rect()), self.icon, theme["onSecondaryContainer"], 22)


# ── downloads ────────────────────────────────────────────────────────────────
class DownloadRow(QWidget):
    def __init__(self, entry):
        super().__init__()
        self.e = entry
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 6, 4, 6)
        lay.setSpacing(12)
        lay.addWidget(Tile("open_in_new" if entry.external else ext_icon(entry.name)), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = Label("", "onSurface", 14, 500)
        self.wave = WavyProgress(interactive=False, height=16, stroke=3.5, thumb=False)
        self.status = Label("", "onSurfaceVariant", 12)
        for w in (self.name, self.wave, self.status):
            col.addWidget(w)
        lay.addLayout(col, 1)
        self.b_pause = Button("pause", size=36, tip="Pause")
        self.b_cancel = Button("close", size=36, tip="Cancel")
        self.b_folder = Button("folder_open", size=36, tip="Show in folder")
        self.b_retry = Button("refresh", size=36, tip="Retry")
        self.b_retry.clicked.connect(lambda: self.e.req.resume() if self.e.req.state() == self.e.S.DownloadInterrupted
                                     else None)
        for b in (self.b_pause, self.b_cancel, self.b_retry, self.b_folder):
            lay.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
        self.b_pause.clicked.connect(lambda: self.e.req.resume() if self.e.req.isPaused() else self.e.req.pause())
        self.b_cancel.clicked.connect(lambda: self.e.req.cancel())
        self.b_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(self.e.path))))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        entry.changed.connect(self.refresh)
        self.refresh()

    def mouseReleaseEvent(self, e):
        if self.e.state == "done":
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.e.path))

    def refresh(self):
        e, s = self.e, self.e.state
        self.name.setText(self.name.fontMetrics().elidedText(e.name, Qt.TextElideMode.ElideMiddle, 210))
        self.name.setToolTip(e.url)
        self.status.setText(e.status())
        live = s in ("progress", "paused")
        self.wave.setVisible(live)
        frac = e.fraction
        self.wave.frac = frac if frac is not None else 0.0
        if live and (self.wave.amp > 0.5) != (s == "progress"):
            self.wave.set_waving(s == "progress")
        self.b_pause.setVisible(live)
        self.b_pause.set_icon("play_arrow" if s == "paused" else "pause")
        self.b_cancel.setVisible(live)
        self.b_folder.setVisible(s == "done")
        self.b_retry.setVisible(s == "failed")
        self.wave.update()

    def tick(self, dt):
        if self.wave.isVisible():
            self.wave.tick(dt)


class DownloadsFlyout(Flyout):
    def __init__(self, win):
        super().__init__(win, width=440)
        self.win = win
        b_dir = Button("folder", size=36, tip="Open downloads folder")
        b_dir.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(cfg.get("downloads.dir"))))
        b_clear = Button("clear_all", size=36, tip="Clear finished")
        b_clear.clicked.connect(lambda: (win.downloads.clear_finished(), self.rebuild()))
        b_page = Button("open_in_new", size=36, tip="Open the downloads page")
        b_page.clicked.connect(lambda: (self.dismiss(), win.new_tab("nacre://downloads")))
        self.lay.addWidget(header("Downloads", b_dir, b_clear, b_page))
        self.area, self.list = scroller(420)
        self.lay.addWidget(self.area)
        self.abdm = Label("", "onSurfaceVariant", 12)
        self.abdm.setContentsMargins(8, 6, 8, 2)
        self.lay.addWidget(self.abdm)
        self.rows = []
        win.downloads.added.connect(guard(self, lambda e: self.isVisible() and self.rebuild()))

    def rebuild(self):
        clear(self.list)
        self.rows = [DownloadRow(e) for e in self.win.downloads.entries[:40]]
        for r in self.rows:
            self.list.addWidget(r)
        if not self.rows:
            empty = Label("No downloads yet", "onSurfaceVariant", 14)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setMinimumHeight(80)
            self.list.addWidget(empty)
        d = self.win.downloads
        self.abdm.setText("AB Download Manager: " + ("connected — new downloads go there" if d.abdm_alive else
                          "not running — using built-in downloads") if cfg.get("downloads.abdm") else
                          "Built-in downloads")
        self.area.setFixedHeight(min(420, max(80, len(self.rows) * 66)))
        self.adjustSize()

    def open_for(self, anchor):
        self.rebuild()
        ticker.add(self)
        self.popup(anchor)

    def tick(self, dt):
        for r in self.rows:
            r.tick(dt)

    def hideEvent(self, e):
        ticker.remove(self)
        super().hideEvent(e)


# ── ad-block shield ──────────────────────────────────────────────────────────
class BigShield(QWidget):
    def __init__(self):
        super().__init__()
        self.on = True
        self.setFixedSize(64, 64)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        p.setBrush(c["primaryContainer"] if self.on else c["surfaceContainerHighest"])
        p.drawRoundedRect(QRectF(self.rect()), theme.r(22), theme.r(22))
        draw_icon(p, QRectF(self.rect()), "verified_user" if self.on else "remove_moderator",
                  c["onPrimaryContainer"] if self.on else c["onSurfaceVariant"], 34, 1)


class ShieldFlyout(Flyout):
    """Per-site view only; everything global lives in Settings → Ad blocker."""

    def __init__(self, win):
        super().__init__(win, width=360)
        self.win = win
        ab = win.adblock
        self.shield = BigShield()
        col = QVBoxLayout()
        col.setSpacing(0)
        self.host = Label("", "onSurface", 18, 500)
        self.count = Label("", "onSurfaceVariant", 13)
        col.addWidget(self.host)
        col.addWidget(self.count)
        top = QHBoxLayout()
        top.setContentsMargins(8, 4, 8, 10)
        top.setSpacing(14)
        top.addWidget(self.shield)
        top.addLayout(col, 1)
        self.lay.addLayout(top)
        self.sw_site = Switch()
        self.lay.addWidget(hbox(Label("Block ads on this site", "onSurface", 14), None, self.sw_site,
                                margins=(8, 0, 4, 0)))
        self.sw_site.toggled.connect(self._site)
        self.info = Label("", "onSurfaceVariant", 12, wrap=True)
        self.info.setContentsMargins(8, 8, 8, 4)
        self.lay.addWidget(self.info)
        self.wave = LiveWave()
        self.wave.setContentsMargins(8, 0, 8, 0)
        self.lay.addWidget(self.wave)
        ab.progress.connect(self.wave.set_frac)
        b_upd = Button("sync", "Update lists", kind="tonal", size=36)
        b_upd.clicked.connect(lambda: (ab.update_lists(), self.info.setText("Downloading filter lists…")))
        b_set = Button("tune", "Ad blocker settings", kind="text", size=36)
        b_set.clicked.connect(lambda: (self.dismiss(), win.new_tab("nacre://settings#adblock")))
        self.lay.addWidget(hbox(b_upd, None, b_set, margins=(4, 4, 4, 0)))
        ab.lists_updated.connect(guard(self, lambda: self.isVisible() and self.refresh()))
        ab.blocked.connect(guard(self, lambda h, u: self.isVisible() and self.refresh()))

    def _site(self, on):
        h = self._h()
        if h and self.win.adblock.site_allowed(h) == on:
            self.win.adblock.toggle_site(h)
            self.win.snack.show_msg("Reload to apply", "Reload", self.win.reload_or_stop)
        self.refresh()

    def _h(self):
        t = self.win.tab()
        return host_of(t.page.url()) if t and not t.is_nacre() else ""

    def refresh(self):
        ab, h = self.win.adblock, self._h()
        enabled = cfg.get("adblock.enabled")
        allowed = bool(h) and ab.site_allowed(h)
        self.shield.on = enabled and not allowed
        self.shield.update()
        self.host.setText(h or "Nacre page")
        self.count.setText(("Paused on this site" if allowed else f"{ab.count(h)} blocked on this page")
                           if enabled else "Ad blocker is off in Settings")
        self.sw_site.set_silent(not allowed)
        self.sw_site.setEnabled(bool(h) and enabled)
        self.info.setText(f"{cfg.get('adblock.total'):,} blocked in total · {ab.rule_count():,} rules")

    def open_for(self, anchor):
        self.refresh()
        self.popup(anchor)


# ── main menu ────────────────────────────────────────────────────────────────
class ZoomRow(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(Label("Zoom", "onSurface", 14))
        lay.addStretch(1)
        minus = Button("remove", kind="tonal", size=36, width=44, radius=12, tip="Zoom out (Ctrl+-)")
        self.pct = Button(None, "100%", kind="text", size=36, width=64, tip="Reset (Ctrl+0)")
        plus = Button("add", kind="tonal", size=36, width=44, radius=12, tip="Zoom in (Ctrl+=)")
        full = Button("fullscreen", size=36, tip="Full screen (F11)")
        for w in (minus, self.pct, plus, full):
            lay.addWidget(w)
        minus.clicked.connect(lambda: self.set(win.zoom(-1)))
        plus.clicked.connect(lambda: self.set(win.zoom(1)))
        self.pct.clicked.connect(lambda: self.set(win.zoom(0)))
        full.clicked.connect(lambda: (self.window().dismiss(), win.toggle_fullscreen()))
        self.setFixedHeight(48)

    def set(self, z):
        self.pct.setText(f"{round(z * 100)}%")
        self.pct.update()


class MainMenu(Flyout):
    def __init__(self, win):
        super().__init__(win, width=330)
        self.win = win
        ctx = win.ctx
        rows = [
            ("tab", "New tab", "Ctrl+T", lambda: win.new_tab()),
            ("open_in_new", "New window", "Ctrl+N", lambda: ctx.new_window()),
            ("domino_mask", "New Incognito window", "Ctrl+Shift+N", lambda: ctx.new_window(incognito=True)),
            ("splitscreen_right", "Split view", "Ctrl+Shift+S", win.toggle_split),
            None, "zoom", None,
            ("history", "History", "Ctrl+H", lambda: win.toolbar_action("history")),
            ("download", "Downloads", "Ctrl+J", lambda: win.toolbar_action("downloads")),
            ("bookmarks", "Bookmarks", "Ctrl+Shift+B", lambda: win.toolbar_action("bookmarks")),
            ("chrome_reader_mode", "Reading list", None, lambda: win.toolbar_action("reading_list")),
            None,
            ("search", "Find in page", "Ctrl+F", win.findbar.open_bar),
            ("install_desktop", "Install page as app…", None, lambda: win.install_current()),
            ("add_to_home_screen", "Create shortcut…", None, lambda: win.install_current(shortcut=True)),
            ("apps", "Apps", None, lambda: win.new_tab("nacre://apps")),
            None,
            ("print", "Print", "Ctrl+P", win.print_page),
            ("auto_stories", "Reading mode", "Alt+R", lambda: win.toolbar_action("reading_mode")),
            ("monitoring", "Task manager", "Shift+Esc", lambda: win.toolbar_action("task_manager")),
            ("code", "Developer tools", "F12", lambda: win.tab() and win.devtools(win.tab())),
            None,
            ("settings", "Settings", "Ctrl+,", lambda: win.new_tab("nacre://settings")),
            ("info", "About Nacre", None, lambda: win.new_tab("nacre://about")),
            ("logout", "Exit", "Ctrl+Q", ctx.quit),
        ]
        self.zoom = None
        for r in rows:
            if r is None:
                self.lay.addWidget(Divider())
            elif r == "zoom":
                self.zoom = ZoomRow(win)
                self.lay.addWidget(self.zoom)
            else:
                row = Row(r[0], r[1], trailing=r[2])
                row.clicked.connect(lambda _, fn=r[3]: (self.dismiss(), fn()))
                self.lay.addWidget(row)

    def open_for(self, anchor):
        t = self.win.tab()
        self.zoom.set(t.page.zoomFactor() if t else 1.0)
        self.popup(anchor)


# ── history / bookmarks ──────────────────────────────────────────────────────
def ago(t):
    d = time.time() - t
    if d < 60:
        return "now"
    if d < 3600:
        return f"{int(d // 60)}m"
    if d < 86400:
        return f"{int(d // 3600)}h"
    return time.strftime("%b %d", time.localtime(t))


class ListFlyout(Flyout):
    def __init__(self, win, kind):
        super().__init__(win, width=420)
        self.win, self.kind = win, kind
        self.store = {"history": history, "bookmarks": bookmarks, "reading": reading}[kind]
        b_clear = Button("delete_sweep", size=36, tip="Clear history")
        b_clear.clicked.connect(lambda: win.snack.show_msg("Clear all history?", "Clear", self._clear_all, 6000))
        b_clear.setVisible(kind == "history")
        b_page = Button("open_in_new", size=36, tip=f"Open the {kind} page")
        b_page.clicked.connect(lambda: (self.dismiss(), win.new_tab("nacre://" + kind)))
        b_add = Button("add", "Add this tab", kind="tonal", size=36)
        b_add.clicked.connect(lambda: (win.add_to_reading(), self.rebuild()))
        b_add.setVisible(kind == "reading")
        title = {"history": "History", "bookmarks": "Bookmarks", "reading": "Reading list"}[kind]
        self.lay.addWidget(header(title, b_add, b_clear, b_page))
        self.search = TextField(placeholder="Search " + kind, icon="search")
        self.search.edit.textChanged.connect(self.rebuild)
        self.lay.addWidget(self.search)
        self.lay.addSpacing(6)
        self.area, self.list = scroller(460)
        self.lay.addWidget(self.area)

    def _clear_all(self):
        history.data.clear()
        history.touch()
        self.win.omni.reload_completions()
        self.rebuild()

    def rebuild(self):
        clear(self.list)
        q = self.search.edit.text().lower()
        items = [h for h in reversed(self.store.data) if not q or q in h["url"].lower() or q in (h.get("title") or "").lower()]
        if self.kind == "reading":
            items.sort(key=lambda h: bool(h.get("read")))          # unread first
        for h in items[:150]:
            row = Row(None, h.get("title") or h["url"], host_of(h["url"]) or h["url"],
                      ago(h["t"]) if self.kind == "history" and h.get("t") else None)
            icon = {"bookmarks": "bookmark", "history": "schedule"}.get(self.kind) or (
                "check_circle" if h.get("read") else "chrome_reader_mode")
            row.icon_name = icon
            row.setToolTip(h["url"])
            row.clicked.connect(lambda _, h=h: self._open(h["url"], item=h))
            row.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            row.customContextMenuRequested.connect(lambda pos, h=h, row=row: self._menu(h, row))
            self.list.addWidget(row)
        if not items:
            e = Label("Nothing here yet" if not q else "No matches", "onSurfaceVariant", 14)
            e.setAlignment(Qt.AlignmentFlag.AlignCenter)
            e.setMinimumHeight(80)
            self.list.addWidget(e)
        self.list.addStretch(1)
        self.area.setFixedHeight(min(460, max(80, len(items[:150]) * 58)))
        self.adjustSize()

    def _open(self, url, new=False, item=None):
        self.dismiss()
        if self.kind == "reading" and item is not None and not item.get("read"):
            item["read"] = True
            reading.touch()
        t = self.win.tab()
        if new or not t:
            self.win.new_tab(url)
        else:
            t.load(url)

    def _menu(self, h, row):
        m = QMenu(self)
        m.addAction("Open in new tab", lambda: self._open(h["url"], True))
        m.addAction("Copy link", lambda: QGuiApplication.clipboard().setText(h["url"]))
        if self.kind == "reading":
            m.addAction("Mark as unread" if h.get("read") else "Mark as read", lambda: self._toggle_read(h))
        m.addAction("Remove", lambda: (self.store.data.remove(h), self.store.touch(), self.rebuild(),
                                       self.win.sync_omnibox()))
        m.exec(row.cursor().pos())

    def _toggle_read(self, h):
        h["read"] = not h.get("read")
        reading.touch()
        self.rebuild()

    def open_for(self, anchor):
        self.search.edit.clear()
        self.rebuild()
        self.popup(anchor)
        self.search.edit.setFocus()



# ── site info (the lock / info icon in the address bar) ──────────────────────
PERM_LABELS = {"MediaAudioCapture": ("mic", "Microphone"), "MediaVideoCapture": ("videocam", "Camera"),
               "MediaAudioVideoCapture": ("video_camera_front", "Camera & microphone"),
               "Geolocation": ("location_on", "Location"), "Notifications": ("notifications", "Notifications"),
               "DesktopVideoCapture": ("screen_share", "Screen sharing"),
               "DesktopAudioVideoCapture": ("screen_share", "Screen & audio sharing"),
               "ClipboardReadWrite": ("content_paste", "Clipboard"), "LocalFontsAccess": ("font_download", "Fonts"),
               "MouseLock": ("mouse", "Pointer lock")}


class SiteInfoFlyout(Flyout):
    def __init__(self, win):
        super().__init__(win, width=380)
        self.win = win
        self.content = QWidget()
        self.bl = QVBoxLayout(self.content)
        self.bl.setContentsMargins(0, 0, 0, 0)
        self.bl.setSpacing(2)
        self.lay.addWidget(self.content)

    def open_for(self, anchor):
        self.rebuild()
        self.popup(anchor, align="left")

    def _row(self, icon, title, sub=None, button=None):
        r = Row(icon, title, sub)
        r.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, button is None)
        r.setMinimumWidth(0)
        r.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)   # shrink beside its button
        if button:
            box = hbox(r, button, spacing=4)
            box.layout().setStretch(0, 1)
            self.bl.addWidget(box)
        else:
            self.bl.addWidget(r)

    def rebuild(self):
        while self.bl.count():
            w = self.bl.takeAt(0).widget()
            w and w.deleteLater()
        t = self.win.tab()
        if t is None:
            return
        url = t.url()
        host = host_of(url) or url.toString()
        scheme = url.scheme()
        self.bl.addWidget(Label(host, "onSurface", 18, 500))
        if scheme == "https":
            self._row("lock", "Connection is secure", "Information you send to this site is encrypted")
        elif scheme == "http":
            self._row("lock_open", "Connection is not secure",
                      "Don't enter passwords or payment details on this site")
        else:
            self._row("verified", "Nacre page" if scheme == "nacre" else "Local page")
        if scheme not in ("http", "https"):
            return self._fit()
        self.bl.addWidget(Divider())
        # permissions
        origin = url.adjusted(QUrl.UrlFormattingOption.RemovePath | QUrl.UrlFormattingOption.RemoveQuery
                              | QUrl.UrlFormattingOption.RemoveFragment)
        perms = [x for x in self.win.profile.listPermissionsForOrigin(origin)
                 if x.state() in (x.State.Granted, x.State.Denied)]
        for perm in perms:
            icon, label = PERM_LABELS.get(perm.permissionType().name, ("tune", perm.permissionType().name))
            reset = Button("restart_alt", size=32, icon_px=18, tip="Reset (ask again)")
            reset.clicked.connect(lambda _, perm=perm: (perm.reset(), self.rebuild()))
            self._row(icon, label, "Allowed" if perm.state() == perm.State.Granted else "Blocked", reset)
        if not perms:
            self._row("tune", "Permissions", "This site hasn't asked for any")
        # cookies (mirrored into the downloads cookie jar, which tracks the profile's store)
        jar = self.win.downloads.jar
        mine = [c for c in jar.allCookies() if host.endswith(c.domain().lstrip(".").removeprefix("www."))
                or c.domain().lstrip(".").endswith(host)]
        clear = Button("delete", size=32, icon_px=18, tip="Delete this site's cookies")
        clear.clicked.connect(lambda: self._clear_cookies(mine))
        self._row("cookie", "Cookies", f"{len(mine)} in use" if mine else "None", clear if mine else None)
        # ad blocker, zoom, passwords
        ab = self.win.adblock
        allowed = ab.site_allowed(host)
        sw_ab = Switch(not allowed)
        sw_ab.setEnabled(cfg.get("adblock.enabled"))
        sw_ab.toggled.connect(lambda on: (ab.site_allowed(host) == on and ab.toggle_site(host),
                                          self.win.snack.show_msg("Reload to apply", "Reload", self.win.reload_or_stop)))
        self._row("shield", "Block ads", f"{ab.count(host)} blocked on this page", sw_ab)
        z = t.page.zoomFactor()
        if abs(z - 1) > 1e-3:
            rz = Button("restart_alt", size=32, icon_px=18, tip="Reset zoom")
            rz.clicked.connect(lambda: (self.win.zoom(0), self.rebuild()))
            self._row("zoom_in", "Zoom", f"{round(z * 100)}% on this site", rz)
        try:
            from .vault import get_vault
            v = get_vault()
            n = 0 if v.locked() else len(v.logins_for(url.toString()))
        except Exception:
            n = 0
        if n:
            mg = Button("open_in_new", size=32, icon_px=18, tip="Manage passwords")
            mg.clicked.connect(lambda: (self.dismiss(), self.win.new_tab("nacre://passwords")))
            self._row("key", "Saved passwords", f"{n} for this site", mg)
        self._fit()

    def _fit(self):
        self.bl.activate()
        self.content.layout().activate()
        self.content.adjustSize()
        self.adjustSize()

    def _clear_cookies(self, cookies):
        store = self.win.profile.cookieStore()
        for c in cookies:
            store.deleteCookie(c)
        self.win.snack.show_msg(f"Deleted {len(cookies)} cookies — reload to sign out of this site", "Reload",
                                self.win.reload_or_stop)
        QTimer.singleShot(300, self.rebuild)
