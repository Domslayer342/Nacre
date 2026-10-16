"""nacre://settings — native page: navigation rail + M3 Expressive grouped row cards."""
import os
import sys

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QEasingCurve, QRectF, Qt, QUrl, QVariantAnimation, pyqtSignal
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWebEngineCore import qWebEngineChromiumVersion
from PyQt6.QtWidgets import QColorDialog, QFileDialog, QHBoxLayout, QMenu, QScrollArea, QVBoxLayout, QWidget

from .config import CACHE_DIR, CONFIG_DIR, DATA_DIR, FILTER_GROUPS, SEARCH_ENGINES, TOOLBAR_ITEMS, cfg
from .media import LiveWave
from .theme import PRESETS, SPRING, VARIANTS, alpha, animate, draw_icon, font, theme
from .widgets import Button, FlowLayout, Label, Segmented, Switch, TextField, guard, hbox, painter, rounded

SECTIONS = [("general", "tune", "General"), ("appearance", "palette", "Appearance"),
            ("toolbar", "toolbar", "Toolbar"), ("privacy", "lock", "Privacy"), ("adblock", "shield", "Ad blocker"),
            ("downloads", "download", "Downloads"), ("media", "music_note", "Media"), ("about", "info", "About")]


class Rail(QWidget):
    """M3 navigation rail; the active pill slides (and stretches) between destinations."""
    selected = pyqtSignal(int)
    H = 68

    def __init__(self):
        super().__init__()
        self.cur, self._y, self._hover = 0, 0.0, -1
        self.setFixedWidth(96)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        theme.changed.connect(self.update)

    def set_current(self, i, anim=True):
        if i != self.cur:
            self.cur = i
            animate(self, "_y", i, 450, SPRING) if anim else setattr(self, "_y", float(i))
            self.update()

    def _at(self, y):
        i = int((y - 16) // self.H)
        return i if 0 <= i < len(SECTIONS) else -1

    def mouseMoveEvent(self, e):
        self._hover = self._at(e.position().y())
        self.update()

    def leaveEvent(self, e):
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):
        i = self._at(e.position().y())
        if i >= 0:
            self.set_current(i)
            self.selected.emit(i)

    def paintEvent(self, e):
        p = painter(self)
        c = theme
        frac = self._y - int(self._y)
        stretch = 1 + 0.5 * (1 - abs(2 * frac - 1)) if frac else 1        # pill elongates mid-flight
        py = 16 + self._y * self.H + 6
        pw, ph = 56 * stretch, 32
        p.setBrush(c["secondaryContainer"])
        p.drawRoundedRect(QRectF(48 - pw / 2, py, pw, ph), 16, 16)
        for i, (_, icon, label) in enumerate(SECTIONS):
            y = 16 + i * self.H
            on = i == self.cur
            if i == self._hover and not on:
                p.setBrush(alpha(c["onSurface"], 0.08))
                p.drawRoundedRect(QRectF(20, y + 6, 56, 32), 16, 16)
            draw_icon(p, QRectF(20, y + 6, 56, 32), icon, c["onSecondaryContainer" if on else "onSurfaceVariant"],
                      24, 1 if on else 0)
            p.setFont(font(12, 600 if on else 500))
            p.setPen(c["onSurface" if on else "onSurfaceVariant"])
            p.drawText(QRectF(0, y + 40, 96, 18), Qt.AlignmentFlag.AlignCenter, label)


class SetRow(QWidget):
    """One row of a grouped card: corners are large at the group's ends, tight in between."""

    def __init__(self, icon, title, sub=None, control=None, below=None):
        super().__init__()
        self.icon, self.pos = icon, "single"
        self.title_text, self.search_text = title, f"{title} {sub or ''}".lower()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(56 if icon else 20, 12, 16, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(1)
        col.addWidget(Label(title, "onSurface", 15, 500))
        self.sub = None
        if sub:
            self.sub = Label(sub, "onSurfaceVariant", 13, wrap=True)
            col.addWidget(self.sub)
        top.addLayout(col, 1)
        if control is not None:
            top.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(top)
        if below is not None:
            lay.addWidget(below)
        self.setMinimumHeight(60)
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        big, small = theme.r(24), theme.r(6)
        tl = big if self.pos in ("first", "single") else small
        bl = big if self.pos in ("last", "single") else small
        p.fillPath(rounded(QRectF(self.rect()), tl, tl, bl, bl), theme["surfaceContainer"])
        if self.icon:
            draw_icon(p, QRectF(16, 0, 28, min(self.height(), 64)), self.icon, theme["primary"], 24)


def group(*rows):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    w.rows = [r for r in rows if r is not None]
    for r in w.rows:
        lay.addWidget(r)
    regroup(w)
    return w


def regroup(w):
    """Re-assign rounded ends among the rows that are currently shown."""
    vis = [r for r in w.rows if not r.isHidden()]
    for i, r in enumerate(vis):
        r.pos = "single" if len(vis) == 1 else "first" if i == 0 else "last" if i == len(vis) - 1 else "mid"
        r.update()


def sw(key, after=None):
    s = Switch(cfg.get(key))
    s.toggled.connect(lambda on: (cfg.set(key, on), after and after(on)))
    return s


def seg(key, opts):
    s = Segmented(opts, cfg.get(key))
    s.changed.connect(lambda v: cfg.set(key, v))
    return s


class Swatch(Button):
    """Preset seed preview: primary / secondary / tertiary wedges, ring when selected."""

    def __init__(self, hexcol):
        super().__init__(None, size=48, tip=hexcol)
        self.hex = hexcol

    def paintEvent(self, e):
        p = painter(self)
        sch = theme.scheme_for(QColor(self.hex).rgba())
        r = QRectF(self.rect()).adjusted(5, 5, -5, -5)
        sel = cfg.get("theme.source") in ("preset", "website") and cfg.get("theme.preset").lower() == self.hex.lower()
        if sel:
            p.setPen(QPen(theme["primary"], 2.5))
            p.drawEllipse(QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5))
            p.setPen(Qt.PenStyle.NoPen)
            r = r.adjusted(2, 2, -2, -2)
        r = r.adjusted(-2 * self._hover, -2 * self._hover, 2 * self._hover, 2 * self._hover)
        p.setBrush(sch["primary"])
        p.drawPie(r, 0, 180 * 16)
        p.setBrush(sch["secondaryContainer"])
        p.drawPie(r, 180 * 16, 90 * 16)
        p.setBrush(sch["tertiary"])
        p.drawPie(r, 270 * 16, 90 * 16)


class SettingsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setAutoFillBackground(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.rail = Rail()
        lay.addWidget(self.rail)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        lay.addWidget(self.area, 1)
        inner = QWidget()
        outer = QHBoxLayout(inner)
        outer.setContentsMargins(24, 8, 24, 48)
        self.col = QWidget()
        self.col.setMaximumWidth(780)
        self.v = QVBoxLayout(self.col)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.v.setSpacing(10)
        outer.addStretch(1)
        outer.addWidget(self.col, 100)
        outer.addStretch(1)
        self.area.setWidget(inner)
        self.anchors, self.blocks = [], []          # blocks: (kind, widget, label text) in page order
        self.search = TextField(placeholder="Search settings", icon="search")
        self.search.setFixedHeight(48)
        self.v.addWidget(self.search)
        self.search.edit.textChanged.connect(self.filter)
        self.empty = Label("No settings match your search", "onSurfaceVariant", 15)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(160)
        self.empty.hide()
        self.build()
        self.v.addWidget(self.empty)
        self.v.addStretch(1)
        self.rail.selected.connect(self.scroll_to)
        self.area.verticalScrollBar().valueChanged.connect(self._scrolled)
        self._scrolling = False
        theme.changed.connect(self.update)

    def paintEvent(self, e):
        p = painter(self)
        p.fillRect(self.rect(), theme["surface"])

    # navigation
    def jump(self, key):
        """Deep link: nacre://settings#adblock etc."""
        keys = [k for k, _, _ in SECTIONS]
        if key in keys:
            i = keys.index(key)
            self.rail.set_current(i)
            self.scroll_to(i)

    def scroll_to(self, i):
        bar = self.area.verticalScrollBar()
        target = min(bar.maximum(), self.anchors[i].y() - 8)
        self._scrolling = True
        a = QVariantAnimation(self, startValue=bar.value(), endValue=target, duration=int(450 * max(.01, cfg.get("ui.motion"))))
        a.setEasingCurve(QEasingCurve(QEasingCurve.Type.OutCubic))
        a.valueChanged.connect(bar.setValue)
        a.finished.connect(lambda: (setattr(self, "_scrolling", False), self.rail.set_current(i)))
        a.start(QVariantAnimation.DeletionPolicy.DeleteWhenStopped)

    def _scrolled(self, v):
        if self._scrolling:
            return
        bar = self.area.verticalScrollBar()
        i = len(self.anchors) - 1 if v >= bar.maximum() - 4 else max(
            (k for k, a in enumerate(self.anchors) if a.y() - 40 <= v), default=0)
        self.rail.set_current(i)

    # content
    def section(self, title):
        lbl = Label(title, "onSurface", 30, 400)
        lbl.setContentsMargins(4, 28, 0, 6)
        self.anchors.append(lbl)
        self.blocks.append(("section", lbl, title.lower()))
        self.v.addWidget(lbl)

    def sub(self, title):
        lbl = Label(title, "primary", 14, 500)
        lbl.setContentsMargins(20, 12, 0, 2)
        self.blocks.append(("sub", lbl, title.lower()))
        self.v.addWidget(lbl)

    def add(self, *rows):
        self.block(group(*rows))

    def block(self, w):
        self.blocks.append(("block", w, ""))
        self.v.addWidget(w)

    def filter(self, text):
        """Show matching rows (or whole sections whose heading matches); hide empty headings."""
        q = text.strip().lower()
        sec = sub = ""
        shown = {}
        for kind, w, label in self.blocks:
            if kind == "section":
                sec, sub = label, ""
            elif kind == "sub":
                sub = label
            else:
                any_vis = False
                for g in [w] + w.findChildren(QWidget):
                    rows = getattr(g, "rows", None)
                    if rows is None:
                        continue
                    for r in rows:
                        r.setHidden(not (q in r.search_text or q in sec or q in sub) if q
                                    else getattr(r, "collapsed", False))
                    regroup(g)
                    any_vis |= any(not r.isHidden() for r in rows)
                w.setHidden(not any_vis)
                shown[id(w)] = any_vis
        # headings stay only if something under them is visible
        for i, (kind, w, _) in enumerate(self.blocks):
            if kind == "block":
                continue
            vis = False
            for kind2, w2, _ in self.blocks[i + 1:]:
                if kind2 == "section" or (kind == "sub" and kind2 == "sub"):
                    break
                vis |= kind2 == "block" and shown.get(id(w2), False)
            w.setHidden(bool(q) and not vis)
        self.empty.setVisible(bool(q) and not any(shown.values()))
        if q:
            self.area.verticalScrollBar().setValue(0)

    def build(self):

        # General
        self.section("General")
        home = TextField(cfg.get("home.url"), "nacre://newtab", "home")
        home.setFixedWidth(280)
        home.edit.editingFinished.connect(lambda: cfg.set("home.url", home.edit.text().strip() or "nacre://newtab"))
        eng = Button("search", SEARCH_ENGINES[cfg.get("search.engine")][0], kind="outlined", size=40)
        eng.clicked.connect(lambda: self._engine_menu(eng))
        self.add(SetRow("rocket_launch", "On startup", None,
                        seg("startup.mode", [("newtab", "New tab"), ("restore", "Restore"), ("home", "Home page")])),
                 SetRow("home", "Home page", "Used by the Home button and the Home startup option", home),
                 SetRow("search", "Search engine", "Used in the address bar and on the new tab page", eng))
        self.sub("New tab shortcuts")
        self.shortcuts = QWidget()
        QVBoxLayout(self.shortcuts).setContentsMargins(0, 0, 0, 0)
        self.block(self.shortcuts)
        self._build_shortcuts()
        cfg.changed.connect(guard(self, lambda k, _: k == "ntp.shortcuts" and self._build_shortcuts()))
        self.sub("Performance & data")
        self.add(SetRow("data_saver_on", "Save data", "Asks sites for lighter pages (Save-Data header)",
                        sw("net.save_data")),
                 SetRow("restore_page", "Load restored tabs when opened", "Startup only fetches the tab you're on",
                        sw("tabs.lazy_restore")),
                 SetRow("ac_unit", "Freeze background tabs", "Silent tabs stop running scripts",
                        seg("tabs.freeze_min", [(0, "Never"), (2, "2 min"), (5, "5 min"), (15, "15 min")])),
                 SetRow("memory", "Unload background tabs", "Frees memory; the tab reloads when you return",
                        seg("tabs.discard_min", [(0, "Never"), (30, "30 min"), (60, "1 h"), (240, "4 h")])))
        self.add(SetRow("web_asset", "Custom title bar", "Frameless window with Nacre's own controls (restart to apply)",
                        sw("ui.frameless")),
                 SetRow("crop_free", "Floating content card", "Rounded page with a margin around it",
                        sw("ui.floating_card")))

        # Appearance
        self.section("Appearance")
        src = seg("theme.source", [("website", "Website"), ("system", "System accent"), ("preset", "Palette"),
                                   ("custom", "Custom")])
        self.fallback = SetRow("format_color_fill", "When a site has no colour", None,
                               seg("theme.fallback", [("system", "System accent"), ("preset", "Palette")]))
        sw_wrap = QWidget()
        fl = FlowLayout(sw_wrap, 4)
        for h in PRESETS:
            s = Swatch(h)
            s.clicked.connect(lambda _, h=h: (cfg.set("theme.preset", h),
                                              cfg.get("theme.source") not in ("website", "preset")
                                              and cfg.set("theme.source", "preset")))
            fl.addWidget(s)
        custom = Button("colorize", "Pick…", kind="tonal", size=40)
        custom.clicked.connect(self._pick_color)
        theme.target_changed.connect(sw_wrap.update)
        cfg.changed.connect(guard(self, lambda k, _: k.startswith("theme.") and [w.update() for w in sw_wrap.findChildren(Swatch)]))
        self.add(SetRow("palette", "Colour source", "Website follows each page's theme colour or favicon", src),
                 self.fallback,
                 SetRow("colors", "Palette", None, custom, below=sw_wrap))
        var_wrap = QWidget()
        vf = FlowLayout(var_wrap, 8)
        self.var_chips = {}
        for k, (label, _) in VARIANTS.items():
            b = Button(None, label, kind="chip", size=36, checkable=True, radius=10)
            b.setChecked(cfg.get("theme.variant") == k)
            b.clicked.connect(lambda _, k=k: self._variant(k))
            self.var_chips[k] = b
            vf.addWidget(b)
        self.add(SetRow("style", "Scheme style", "How colours are derived from the seed", below=var_wrap),
                 SetRow("contrast", "Contrast", None,
                        seg("theme.contrast", [(0.0, "Standard"), (0.5, "Medium"), (1.0, "High")])),
                 SetRow("dark_mode", "Mode", None,
                        seg("theme.mode", [("system", "System"), ("light", "Light"), ("dark", "Dark")])))
        self.add(SetRow("animation", "Motion", "Speed of every animation",
                        seg("ui.motion", [(0.0, "Off"), (0.6, "Fast"), (1.0, "Normal"), (1.6, "Slow")])),
                 SetRow("rounded_corner", "Roundness", None,
                        seg("ui.roundness", [(0.5, "Subtle"), (1.0, "Default"), (1.4, "Round")])),
                 SetRow("contrast_square", "Force dark web pages", "Chromium's automatic dark mode (restart to apply)",
                        sw("web.force_dark")),
                 SetRow("memory", "Hardware acceleration", "Turn off if pages show up black or blank (restart to apply)",
                        seg("web.gpu", [("auto", "Auto"), ("on", "On"), ("off", "Off")])))

        # Toolbar
        self.section("Toolbar")
        for grp, items in TOOLBAR_ITEMS.items():
            self.sub(grp)
            self.add(*[SetRow(sym, label, None if ok else "Coming soon", sw("toolbar." + k))
                       for k, (sym, label, ok) in items.items()])

        # Privacy
        self.section("Privacy")
        allow = Button("restart_alt", "Reset", kind="text", size=40)
        allow.clicked.connect(lambda: (cfg.set("adblock.allow", []), self.win.snack.show_msg("Allowlist cleared")))
        clear = Button("delete", "Delete…", kind="danger", size=40)
        clear.clicked.connect(lambda: self.win.toolbar_action("clear_data"))
        self.add(SetRow("do_not_disturb_on", "Send Do Not Track & Global Privacy Control", None, sw("privacy.dnt")),
                 SetRow("login", "Google sign-in fix", "Google blocks embedded browsers; this signs in as Firefox "
                        "on accounts.google.com only", sw("quirks.google_signin")),
                 SetRow("verified_user", "Sites where ad blocking is paused",
                        ", ".join(cfg.get("adblock.allow")) or "None", allow),
                 SetRow("delete_history", "Delete browsing data", "History, cookies and cache", clear))

        self.section("Ad blocker")
        ab = self.win.adblock
        self.ab_info = Label("", "onSurfaceVariant", 13)
        upd = Button("sync", "Update now", kind="tonal", size=40)
        upd.clicked.connect(lambda: (ab.update_lists(), self.ab_info.setText("Downloading…")))
        ab.lists_updated.connect(self._ab_info)
        self._ab_info()
        self.add(SetRow("shield", "Block ads and trackers", None, sw("adblock.enabled")),
                 SetRow("info", "Filter engine", None, upd, below=self._with_wave(self.ab_info, ab)),
                 SetRow("visibility_off", "Hide ad leftovers", "Cosmetic (##) rules from the lists",
                        sw("adblock.cosmetic")),
                 SetRow("auto_awesome", "Animate ad removal", "Ads you can see squish away in your theme colours",
                        sw("adblock.animate")),
                 SetRow("smart_display", "Block YouTube ads", "Strips ads from the player; skips any that slip by",
                        sw("adblock.youtube")),
                 SetRow("update", "Update lists automatically", "Only re-downloads lists that changed",
                        seg("adblock.update_days", [(0, "Manually"), (1, "Daily"), (7, "Weekly"), (30, "Monthly")])))
        self.sub("Filter lists")
        for grp, items in FILTER_GROUPS.items():
            rows, switches = [], []
            for k, (name, url, desc) in items.items():
                sw_ = Switch(k in cfg.get("adblock.lists"))
                sw_.toggled.connect(lambda on, k=k: cfg.set("adblock.lists", [x for x in cfg.get("adblock.lists")
                                                                             if x != k] + ([k] if on else [])))
                age = ab.list_age(k)
                when = f"updated {int(age // 3600)} h ago" if age is not None else "not downloaded"
                row = SetRow(None, name, f"{desc} · {when}", sw_)
                row.collapsed = True
                row.setHidden(True)
                rows.append(row)
                switches.append(sw_)
            chevron = Button("expand_more", size=36, tip="Show lists")
            head = SetRow("folder", grp, "…", chevron)          # subtitle label is filled by count()
            head.search_text += " " + " ".join(r.search_text for r in rows)   # searching a list name finds its group
            g = group(head, *rows)

            def count(head=head, items=items):
                on = sum(k in cfg.get("adblock.lists") for k in items)
                head.sub.setText(f"{on} of {len(items)} on")

            def toggle(_=False, rows=rows, chevron=chevron, g=g):
                opening = rows[0].collapsed
                for r in rows:
                    r.collapsed = not opening
                    r.setHidden(not opening)
                chevron.set_icon("expand_less" if opening else "expand_more")
                regroup(g)
            chevron.clicked.connect(toggle)
            head.mousePressEvent = lambda e, toggle=toggle: toggle()
            head.setCursor(Qt.CursorShape.PointingHandCursor)
            count()
            cfg.changed.connect(lambda k, _, count=count, head=head: k == "adblock.lists" and self._alive(head)
                                and count())
            self.block(g)
        self.sub("Your lists")
        self.custom = QWidget()
        QVBoxLayout(self.custom).setContentsMargins(0, 0, 0, 0)
        self.block(self.custom)
        self._build_custom()
        cfg.changed.connect(guard(self, lambda k, _: k == "adblock.custom" and self._build_custom()))

        # Downloads
        self.section("Downloads")
        d = Button("folder", os.path.basename(cfg.get("downloads.dir")) or cfg.get("downloads.dir"),
                   kind="outlined", size=40)
        d.clicked.connect(lambda: self._pick_dir(d))
        port = TextField(str(cfg.get("downloads.abdm_port")), "15151", "lan")
        port.setFixedWidth(130)
        port.edit.editingFinished.connect(lambda: port.edit.text().isdigit()
                                          and cfg.set("downloads.abdm_port", int(port.edit.text())))
        self.abdm_state = Label("", "onSurfaceVariant", 13)
        test = Button("network_ping", "Test", kind="tonal", size=40)
        test.clicked.connect(self._ping)
        self.add(SetRow("folder", "Save to", cfg.get("downloads.dir"), d),
                 SetRow("help", "Ask where to save each file", None, sw("downloads.ask")),
                 SetRow("open_in_full", "Show downloads when they start", None, sw("downloads.auto_open")))
        self.add(SetRow("rocket", "Send downloads to AB Download Manager",
                        "Falls back to the built-in downloader if it isn't running", sw("downloads.abdm")),
                 SetRow("lan", "AB Download Manager port", None, port),
                 SetRow("network_ping", "Connection", None, test, below=self.abdm_state))
        self._ping()

        # Media
        self.section("Media")
        self.add(SetRow("music_note", "Media button", "Toolbar button that opens the player",
                        seg("media.button", [("auto", "When playing"), ("always", "Always"), ("never", "Never")])),
                 SetRow("graphic_eq", "Visualizer", "Auto listens to the playing tab's audio, bass to treble",
                        seg("media.visualizer", [("auto", "Auto"), ("simulated", "Simulated"), ("off", "Off")])),
                 SetRow("cookie", "Spin artwork while playing", None, sw("media.spin")))
        self.sub("Discord Rich Presence")
        rpc = self.win.ctx.rpc
        cid = TextField(cfg.get("rpc.client_id"), "Paste your application ID", "key")
        cid.setFixedWidth(260)

        def set_id():
            v = "".join(ch for ch in cid.edit.text() if ch.isdigit())
            cfg.set("rpc.client_id", v)
            if v and not cfg.get("rpc.enabled"):
                cfg.set("rpc.enabled", True)           # pasting an ID is the "turn it on" moment
                self._sync_switch("rpc.enabled")
        cid.edit.editingFinished.connect(set_id)
        portal = Button("open_in_new", "Developer portal", kind="tonal", size=40)
        portal.clicked.connect(lambda: self.win.new_tab("https://discord.com/developers/applications"))
        self.rpc_state = Label("", "onSurfaceVariant", 13)
        rpc.status.connect(self._rpc_state)
        self._rpc_state(rpc.state)
        self.rpc_switch = sw("rpc.enabled")
        steps = Label("1. Open the developer portal and choose New Application — its name is what Discord shows "
                      "(e.g. “Nacre”, or “YouTube” for a per-site app).\n2. Copy the Application ID from General "
                      "Information and paste it here.", "onSurfaceVariant", 13, wrap=True)
        self.add(SetRow("sports_esports", "Show what you're doing on Discord",
                        "Media and (optionally) browsing, never incognito", self.rpc_switch, below=self.rpc_state),
                 SetRow("key", "Application ID", None, hbox(cid, portal), below=steps),
                 SetRow("hub", "Send to", "arRPC covers Equibop / Vesktop / web Discord; Discord is the desktop app",
                        seg("rpc.target", [("arrpc", "arRPC"), ("discord", "Discord")])),
                 SetRow("tab", "When nothing is playing", "Report the tab you're on — its site, or its page title",
                        seg("rpc.browsing", [("off", "Off"), ("site", "Site"), ("title", "Page title")])),
                 SetRow("visibility", "Show browsing as", "Not “Playing”, so it can sit next to a game or your "
                        "shell's activity — Discord shows only one activity of each kind",
                        seg("rpc.browse_type", [("watch", "Watching"), ("listen", "Listening")])),
                 SetRow("badge", "Member list shows", None,
                        seg("rpc.display", [("name", "App name"), ("state", "Creator"), ("details", "Title")])),
                 SetRow("link", "Buttons", "“Watch on YouTube” + channel / artist", sw("rpc.button")),
                 SetRow("pause", "Keep showing while paused", "Marked “Paused” without a progress bar",
                        sw("rpc.show_paused")))
        from .rpc import SITES
        rows = []
        for key, (name, kind, _) in SITES.items():
            on = Switch(key not in cfg.get("rpc.disabled_sites"))
            on.toggled.connect(lambda v, key=key: cfg.set("rpc.disabled_sites", [x for x in cfg.get(
                "rpc.disabled_sites") if x != key] + ([] if v else [key])))
            own = TextField(cfg.get("rpc.site_ids").get(key, ""), "Own app ID (optional)")
            own.setFixedWidth(200)
            own.edit.editingFinished.connect(lambda key=key, own=own: cfg.set("rpc.site_ids", {
                **{k: v for k, v in cfg.get("rpc.site_ids").items() if k != key},
                **({key: own.edit.text().strip()} if own.edit.text().strip() else {})}))
            row = SetRow(None, name, f"{key} · {'Listening' if kind == 'listen' else 'Watching'}", hbox(own, on))
            row.collapsed = True
            row.setHidden(True)
            rows.append(row)
        chevron = Button("expand_more", size=36, tip="Show sites")
        head = SetRow("public", "Supported sites", f"{len(SITES)} sites with full info", chevron)
        head.search_text += " " + " ".join(r.search_text for r in rows)
        g = group(head, *rows)

        def toggle(_=False):
            opening = rows[0].collapsed
            for r in rows:
                r.collapsed = not opening
                r.setHidden(not opening)
            chevron.set_icon("expand_less" if opening else "expand_more")
            regroup(g)
        chevron.clicked.connect(toggle)
        head.mousePressEvent = lambda e: toggle()
        head.setCursor(Qt.CursorShape.PointingHandCursor)
        self.block(g)
        extra = TextField(", ".join(cfg.get("rpc.custom")), "more sites, comma separated", "add_link")
        extra.edit.editingFinished.connect(lambda: cfg.set(
            "rpc.custom", [x.strip() for x in extra.edit.text().split(",") if x.strip()]))
        self.add(SetRow("add_link", "Other media sites", "Reported like music sites", below=extra))

        # Widevine
        self.sub("Protected content")
        self.drm_state = Label("", "onSurfaceVariant", 13, wrap=True)
        self.drm_btn = Button("download", "Install", kind="tonal", size=40)
        self.drm_btn.clicked.connect(self._install_widevine)
        self._drm_refresh()
        self.add(SetRow("hd", "Widevine (Netflix, Hulu, Spotify, Disney+…)", None, self.drm_btn,
                        below=self.drm_state))

        # About
        self.section("About")
        try:
            import materialyoucolor
            myc = getattr(materialyoucolor, "__version__", "installed")
        except ImportError:
            myc = "missing"
        self.add(SetRow("diamond", "Nacre", "A Material You browser built on Qt WebEngine"),
                 SetRow("memory", "Engine", f"Chromium {qWebEngineChromiumVersion()} · Qt {QT_VERSION_STR} · "
                                            f"PyQt {PYQT_VERSION_STR} · Python {sys.version.split()[0]}"),
                 SetRow("palette", "Colour", f"materialyoucolor {myc}"),
                 SetRow("folder_data", "Profile", f"{CONFIG_DIR}\n{DATA_DIR}\n{CACHE_DIR}"))

        self.fallback.setVisible(cfg.get("theme.source") == "website")
        cfg.changed.connect(guard(self, lambda k, val: k == "theme.source" and self.fallback.setVisible(val == "website")))

    def _build_shortcuts(self):
        lay = self.shortcuts.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            w and w.deleteLater()
        rows = []
        for i, (name, url) in enumerate(cfg.get("ntp.shortcuts")):
            up = Button("arrow_upward", size=36, tip="Move up")
            up.setEnabled(i > 0)
            rm = Button("delete", size=36, tip="Remove")
            up.clicked.connect(lambda _, i=i: self._shortcut_op("up", i))
            rm.clicked.connect(lambda _, i=i: self._shortcut_op("rm", i))
            row = SetRow("bookmark", name, url, hbox(up, rm, spacing=0))
            rows.append(row)
        n, u = TextField(placeholder="Name"), TextField(placeholder="https://…", icon="link")
        n.setFixedWidth(150)
        add = Button("add", "Add", kind="tonal", size=40)
        add.clicked.connect(lambda: self._shortcut_add(n.edit.text(), u.edit.text()))
        u.edit.returnPressed.connect(add.click)
        rows.append(SetRow(None, "Add a shortcut", None, below=hbox(n, u, add)))
        lay.addWidget(group(*rows))
        hasattr(self, "search") and self.filter(self.search.edit.text())

    def _shortcut_op(self, op, i):
        lst = [list(x) for x in cfg.get("ntp.shortcuts")]
        if op == "rm":
            gone = lst.pop(i)
            self.win.snack.show_msg(f"Removed “{gone[0]}”", "Undo", lambda: cfg.set(
                "ntp.shortcuts", [list(x) for x in cfg.get("ntp.shortcuts")][:i] + [gone]
                + [list(x) for x in cfg.get("ntp.shortcuts")][i:]))
        elif op == "up" and i > 0:
            lst[i - 1], lst[i] = lst[i], lst[i - 1]
        cfg.set("ntp.shortcuts", lst)

    def _shortcut_add(self, name, url):
        url = url.strip()
        if not url:
            return
        if "://" not in url:
            url = "https://" + url
        name = name.strip() or (QUrl(url).host().removeprefix("www.") or url)
        cfg.set("ntp.shortcuts", [list(x) for x in cfg.get("ntp.shortcuts")] + [[name[:40], url]])

    def _build_custom(self):
        lay = self.custom.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            w and w.deleteLater()
        rows = []
        for url in cfg.get("adblock.custom"):
            rm = Button("delete", size=36, tip="Remove list")
            rm.clicked.connect(lambda _, u=url: cfg.set("adblock.custom", [x for x in cfg.get("adblock.custom")
                                                                          if x != u]))
            rows.append(SetRow("link", QUrl(url).host() or url, url, rm))
        f = TextField(placeholder="https://… filter list or hosts file", icon="add_link")
        add = Button("add", "Add", kind="tonal", size=40)

        def go():
            u = f.edit.text().strip()
            if QUrl(u).scheme() in ("http", "https") and u not in cfg.get("adblock.custom"):
                cfg.set("adblock.custom", list(cfg.get("adblock.custom")) + [u])
        add.clicked.connect(go)
        f.edit.returnPressed.connect(go)
        rows.append(SetRow(None, "Add a list by link", "Adblock (ABP / uBO) syntax, hosts files or plain domains",
                           below=hbox(f, add)))
        lay.addWidget(group(*rows))
        hasattr(self, "search") and self.filter(self.search.edit.text())

    def _with_wave(self, label, ab):
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        wave = LiveWave()
        ab.progress.connect(lambda f: self._alive(wave) and wave.set_frac(f))
        lay.addWidget(label)
        lay.addWidget(wave)
        return box

    @staticmethod
    def _alive(w):
        try:
            w.isVisible()
            return True
        except RuntimeError:
            return False

    def _sync_switch(self, key):
        if key == "rpc.enabled":
            self.rpc_switch.set_silent(cfg.get(key))

    def _drm_refresh(self):
        from .widevine import find_cdm
        path = find_cdm()
        self.drm_state.setText(f"Installed: {path}" + ("" if self.win.ctx.widevine_loaded else
                                                           " · restart Nacre to use it") if path else
                               "Not installed — Install fetches Google's Widevine module (about 10 MB) into your "
                               "Nacre profile. It's Google's, so Nacre downloads it rather than shipping it.")
        self.drm_btn.setVisible(not path)

    def _install_widevine(self):
        from .widevine import install
        self.drm_state.setText("Downloading Widevine from Google…")
        self.drm_btn.setEnabled(False)
        install(self, lambda ok, msg: (self.drm_btn.setEnabled(True), self.drm_state.setText(msg), ok and
                                       self.win.snack.show_msg("Widevine installed — restart Nacre to use it")))

    def _rpc_state(self, s):
        try:
            self.rpc_state.setText(f"Status: {s}")
        except RuntimeError:
            pass

    # actions
    def _engine_menu(self, btn):
        m = QMenu(self)
        for k, (name, _) in SEARCH_ENGINES.items():
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(cfg.get("search.engine") == k)
            a.triggered.connect(lambda _, k=k, n=name: (cfg.set("search.engine", k), btn.setText(n),
                                                       btn.setFixedSize(btn.sizeHint())))
        m.exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _variant(self, k):
        cfg.set("theme.variant", k)
        for kk, b in self.var_chips.items():
            b.setChecked(kk == k)

    def _pick_color(self):
        c = QColorDialog.getColor(QColor(cfg.get("theme.custom")), self, "Custom seed colour")
        if c.isValid():
            cfg.set("theme.custom", c.name())
            cfg.set("theme.source", "custom")

    def _pick_dir(self, btn):
        d = QFileDialog.getExistingDirectory(self, "Download folder", cfg.get("downloads.dir"))
        if d:
            cfg.set("downloads.dir", d)
            btn.setText(os.path.basename(d) or d)
            btn.setFixedSize(btn.sizeHint())

    def _ab_info(self):
        ab = self.win.adblock
        self.ab_info.setText(f"{ab.engine_name()} · {ab.rule_count():,} rules · "
                             f"{cfg.get('adblock.total'):,} blocked in total")

    def _ping(self):
        self.abdm_state.setText("Checking…")
        self.win.downloads.ping(lambda ok: self.abdm_state.setText(
            f"Connected on port {cfg.get('downloads.abdm_port')}" if ok else
            "Not reachable — is AB Download Manager running?" if cfg.get("downloads.abdm") else "Disabled"))
