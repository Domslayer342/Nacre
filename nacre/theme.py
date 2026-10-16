"""Material You colour engine, motion tokens and a shared animation ticker."""
import configparser
import os
import shutil
import subprocess
import sys
import time
from functools import lru_cache

from PyQt6.QtCore import (QAbstractAnimation, QEasingCurve, QFileSystemWatcher, QObject, QPointF, QRectF, Qt, QTimer,
                          QVariantAnimation, pyqtSignal, pyqtSlot)
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPalette, QPixmap, QIcon
from PyQt6.QtWidgets import QApplication

from materialyoucolor.hct import Hct
from materialyoucolor.dynamiccolor.material_dynamic_colors import MaterialDynamicColors as MDC
from materialyoucolor.quantize import QuantizeCelebi
from materialyoucolor.score.score import Score
from materialyoucolor.scheme.scheme_content import SchemeContent
from materialyoucolor.scheme.scheme_expressive import SchemeExpressive
from materialyoucolor.scheme.scheme_fidelity import SchemeFidelity
from materialyoucolor.scheme.scheme_fruit_salad import SchemeFruitSalad
from materialyoucolor.scheme.scheme_monochrome import SchemeMonochrome
from materialyoucolor.scheme.scheme_neutral import SchemeNeutral
from materialyoucolor.scheme.scheme_rainbow import SchemeRainbow
from materialyoucolor.scheme.scheme_tonal_spot import SchemeTonalSpot
from materialyoucolor.scheme.scheme_vibrant import SchemeVibrant

from .config import cfg

VARIANTS = {
    "tonal_spot": ("Tonal spot", SchemeTonalSpot), "content": ("Content", SchemeContent),
    "vibrant": ("Vibrant", SchemeVibrant), "expressive": ("Expressive", SchemeExpressive),
    "fidelity": ("Fidelity", SchemeFidelity), "rainbow": ("Rainbow", SchemeRainbow),
    "fruit_salad": ("Fruit salad", SchemeFruitSalad), "neutral": ("Neutral", SchemeNeutral),
    "monochrome": ("Monochrome", SchemeMonochrome),
}
ROLES = (
    "primary onPrimary primaryContainer onPrimaryContainer secondary onSecondary secondaryContainer "
    "onSecondaryContainer tertiary onTertiary tertiaryContainer onTertiaryContainer error onError "
    "errorContainer onErrorContainer surface onSurface surfaceVariant onSurfaceVariant surfaceDim "
    "surfaceBright surfaceContainerLowest surfaceContainerLow surfaceContainer surfaceContainerHigh "
    "surfaceContainerHighest outline outlineVariant inverseSurface inverseOnSurface inversePrimary "
    "shadow scrim"
).split()
PRESETS = ["#6750A4", "#3F6EE8", "#00687A", "#006A6A", "#386A20", "#6B5E10",
           "#8B5000", "#B3261E", "#984061", "#7B4E7F", "#5E5E62"]


# ── motion ────────────────────────────────────────────────────────────────────
def _curve(*p):
    c = QEasingCurve(QEasingCurve.Type.BezierSpline)
    for i in range(0, len(p), 6):
        c.addCubicBezierSegment(QPointF(p[i], p[i + 1]), QPointF(p[i + 2], p[i + 3]), QPointF(p[i + 4], p[i + 5]))
    return c


EMPHASIZED = _curve(0.05, 0, 2 / 15, 0.06, 1 / 6, 0.4, 5 / 24, 0.82, 0.25, 1, 1, 1)
DECEL = _curve(0.05, 0.7, 0.1, 1, 1, 1)
ACCEL = _curve(0.3, 0, 0.8, 0.15, 1, 1)
STANDARD = _curve(0.2, 0, 0, 1, 1, 1)
SPRING_FAST = _curve(0.42, 1.67, 0.21, 0.9, 1, 1)      # M3 expressive fast spatial
SPRING = _curve(0.38, 1.21, 0.22, 1, 1, 1)             # M3 expressive default spatial


def animate(obj, attr, end, ms=300, curve=EMPHASIZED, start=None, step=None, done=None, owner=None):
    """Tween float attribute `attr` on obj; calls obj.update() (or `step`) each frame."""
    key = "_anim_" + attr
    old = obj.__dict__.get(key)
    if old is not None:
        try:
            old.stop()
        except RuntimeError:
            pass
    a = QVariantAnimation(obj if isinstance(obj, QObject) else owner)
    a.setStartValue(float(getattr(obj, attr) if start is None else start))
    a.setEndValue(float(end))
    a.setDuration(max(1, int(ms * cfg.get("ui.motion"))))
    a.setEasingCurve(curve)
    cb = step or getattr(obj, "update", None)

    def _set(v):
        setattr(obj, attr, v)
        if cb:
            cb()
    a.valueChanged.connect(_set)
    if done:
        a.finished.connect(done)
    obj.__dict__[key] = a
    a.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return a


class Ticker(QObject):
    """One 60 fps timer shared by everything that animates continuously."""

    def __init__(self):
        super().__init__()
        self.subs = set()
        self.t = time.monotonic()
        self.timer = QTimer(self, interval=16, timeout=self._tick)

    def add(self, w):
        self.subs.add(w)
        if not self.timer.isActive():
            self.t = time.monotonic()
            self.timer.start()

    def remove(self, w):
        self.subs.discard(w)
        if not self.subs:
            self.timer.stop()

    def _tick(self):
        now = time.monotonic()
        dt, self.t = min(0.05, now - self.t), now
        for w in list(self.subs):
            try:
                w.tick(dt)
            except RuntimeError:          # widget deleted
                self.subs.discard(w)


ticker = Ticker()


# ── colour helpers ────────────────────────────────────────────────────────────
def mix(a, b, t):
    return QColor.fromRgbF(*(a.getRgbF()[i] + (b.getRgbF()[i] - a.getRgbF()[i]) * t for i in range(4)))


def alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, c.alphaF() * a)))
    return c


@lru_cache(maxsize=64)
def build_scheme(seed, dark, variant, contrast):
    cls = VARIANTS.get(variant, VARIANTS["tonal_spot"])[1]
    s = cls(Hct.from_int(seed), dark, contrast)
    return {r: QColor.fromRgba(getattr(MDC, r).get_argb(s)) for r in ROLES}


def seed_from_image(img: QImage):
    if img is None or img.isNull():
        return None
    img = img.convertToFormat(QImage.Format.Format_ARGB32).scaled(
        40, 40, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    px = []
    for y in range(img.height()):
        for x in range(img.width()):
            v = img.pixel(x, y)
            if (v >> 24) > 200:
                px.append([(v >> 16) & 255, (v >> 8) & 255, v & 255, 255])
    if len(px) < 12:
        return None
    pop = QuantizeCelebi(px, 16)
    best = Score.score(pop)[0]
    return best if best in pop else None


def seed_from_color(c: QColor, min_chroma=16):
    if not c.isValid() or c.alpha() < 128:
        return None
    argb = c.rgba() | 0xFF000000
    return argb if Hct.from_int(argb).chroma >= min_chroma else None


def read_kde_accent():
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.optionxform = str
    try:
        cp.read(os.path.expanduser("~/.config/kdeglobals"))
    except configparser.Error:
        pass                                # partial parse is still usable
    for sec, key in (("General", "AccentColor"), ("Colors:Selection", "BackgroundNormal")):
        v = cp.get(sec, key, fallback="").strip()
        if not v:
            continue
        c = QColor(v) if v.startswith("#") else QColor(*map(int, v.split(",")[:3]))
        if c.isValid():
            return c.rgba() | 0xFF000000
    return None


def read_windows_accent():
    """Windows' accent colour (Settings → Personalisation → Colours)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            try:
                v = winreg.QueryValueEx(k, "AccentColor")[0]                  # 0xAABBGGRR
                return QColor(v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF).rgba()
            except OSError:
                v = winreg.QueryValueEx(k, "ColorizationColor")[0]            # 0xAARRGGBB
                return (v & 0xFFFFFF) | 0xFF000000
    except (ImportError, OSError):
        return None


def read_portal_accent():
    """XDG desktop portal accent (KDE Plasma 6, GNOME 47+, and other desktops with a portal backend)."""
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusMessage
    except ImportError:
        return None
    m = QDBusMessage.createMethodCall("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                                      "org.freedesktop.portal.Settings", "ReadOne")
    m.setArguments(["org.freedesktop.appearance", "accent-color"])
    r = QDBusConnection.sessionBus().call(m, timeout=800)
    try:
        rgb = r.arguments()[0]
        if hasattr(rgb, "variant"):
            rgb = rgb.variant()
        rgb = [float(x) for x in rgb][:3]
    except (IndexError, TypeError, ValueError):
        return None
    if len(rgb) != 3 or not all(0.0 <= x <= 1.0 for x in rgb):         # out of range = "no accent set"
        return None
    return QColor.fromRgbF(*rgb).rgba() | 0xFF000000


GNOME_ACCENTS = {"blue": "#3584e4", "teal": "#2190a4", "green": "#3a944a", "yellow": "#c88800",
                 "orange": "#ed5b00", "red": "#e62d42", "pink": "#d56199", "purple": "#9141ac", "slate": "#6f8396"}


def read_gnome_accent():
    if not shutil.which("gsettings"):
        return None
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "accent-color"],
                             capture_output=True, text=True, timeout=1).stdout.strip().strip("'")
    except (OSError, subprocess.SubprocessError):
        return None
    return QColor(GNOME_ACCENTS[out]).rgba() if out in GNOME_ACCENTS else None


def read_system_accent():
    """The desktop's accent colour: Windows, then the XDG portal, KDE's kdeglobals or GNOME's setting."""
    if sys.platform == "win32":
        return read_windows_accent()
    return read_portal_accent() or read_kde_accent() or read_gnome_accent()


# ── theme manager ─────────────────────────────────────────────────────────────
class Theme(QObject):
    changed = pyqtSignal()          # every animation frame
    target_changed = pyqtSignal()   # once per new scheme (for web pages)

    def __init__(self):
        super().__init__()
        self.c, self._from, self.target = {}, {}, {}
        self.site_seed = None
        self.dark = True
        self.t = 1.0

    def init(self):
        for k in ("theme.source", "theme.fallback"):          # "kde" became "system"
            if cfg.get(k) == "kde":
                cfg.set(k, "system")
        self.system_seed = read_system_accent()
        self._watch = QFileSystemWatcher([os.path.expanduser("~/.config/kdeglobals")], self)
        self._watch.fileChanged.connect(self._kde_changed)
        self._watch_portal()
        if sys.platform == "win32":                           # no change signal we can hook cheaply: poll the registry
            self._accent_poll = QTimer(self, interval=3000, timeout=self._accent_changed)
            self._accent_poll.start()
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.refresh())
        cfg.changed.connect(lambda k, v: k.startswith(("theme.", "ui.roundness")) and self.refresh())
        self.refresh(smooth=False)

    def _kde_changed(self, path):
        if path not in self._watch.files() and os.path.exists(path):
            self._watch.addPath(path)       # file got replaced, re-arm
        self._accent_changed()

    def _watch_portal(self):
        try:
            from PyQt6.QtDBus import QDBusConnection, QDBusMessage
        except ImportError:                                   # no D-Bus (Windows)
            return
        accent_changed = self._accent_changed

        class Receiver(QObject):
            @pyqtSlot(QDBusMessage)
            def changed(self, msg):
                args = msg.arguments()
                if args[:2] == ["org.freedesktop.appearance", "accent-color"]:
                    accent_changed()

        self._portal = Receiver(self)
        QDBusConnection.sessionBus().connect("", "/org/freedesktop/portal/desktop", "org.freedesktop.portal.Settings",
                                             "SettingChanged", self._portal.changed)

    def _accent_changed(self):
        seed = read_system_accent()
        if seed != self.system_seed:
            self.system_seed = seed
            self.refresh()

    # sources
    def base_seed(self):
        src = cfg.get("theme.source")
        if src == "custom":
            return QColor(cfg.get("theme.custom")).rgba()
        if src in ("system", "kde") or (src == "website" and cfg.get("theme.fallback") in ("system", "kde")):
            if self.system_seed:
                return self.system_seed
        return QColor(cfg.get("theme.preset")).rgba()

    def seed(self):
        if cfg.get("theme.source") == "website" and self.site_seed:
            return self.site_seed
        return self.base_seed()

    def set_site_seed(self, seed):
        if seed != self.site_seed:
            self.site_seed = seed
            if cfg.get("theme.source") == "website":
                self.refresh()

    def is_dark(self):
        mode = cfg.get("theme.mode")
        if mode == "system":
            return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
        return mode == "dark"

    def scheme_for(self, seed, dark=None):
        return build_scheme(seed & 0xFFFFFFFF, self.is_dark() if dark is None else dark,
                            cfg.get("theme.variant"), float(cfg.get("theme.contrast")))

    def refresh(self, smooth=True):
        self.dark = self.is_dark()
        target = self.scheme_for(self.seed(), self.dark)
        if target is self.target:
            return
        self._from, self.target = (dict(self.c) or target), target
        self.target_changed.emit()
        if not smooth or not self.c:
            self.c = dict(target)
            self._apply_app()
            self.changed.emit()
            return
        animate(self, "t", 1.0, 500, EMPHASIZED, start=0.0, step=self._frame, done=self._apply_app)

    def _frame(self):
        self.c = {k: mix(self._from[k], self.target[k], self.t) for k in self.target}
        self.changed.emit()

    def __getitem__(self, role):
        return self.c[role]

    def r(self, px):
        return px * cfg.get("ui.roundness")

    def css_vars(self):
        t = self.target
        names = {"bg": "surface", "fg": "onSurface", "var": "onSurfaceVariant", "p": "primary",
                 "op": "onPrimary", "pc": "primaryContainer", "opc": "onPrimaryContainer",
                 "sc": "secondaryContainer", "osc": "onSecondaryContainer", "high": "surfaceContainerHigh",
                 "highest": "surfaceContainerHighest", "outline": "outlineVariant", "tc": "tertiaryContainer"}
        return {k: t[v].name() for k, v in names.items()}

    def _apply_app(self):
        app = QApplication.instance()
        c = self.c
        pal = QPalette()
        R = QPalette.ColorRole
        for role, name in ((R.Window, "surface"), (R.WindowText, "onSurface"), (R.Base, "surfaceContainerHigh"),
                           (R.AlternateBase, "surfaceContainer"), (R.Text, "onSurface"),
                           (R.Button, "surfaceContainerHigh"), (R.ButtonText, "onSurface"),
                           (R.Highlight, "primary"), (R.HighlightedText, "onPrimary"), (R.Link, "primary"),
                           (R.PlaceholderText, "onSurfaceVariant"), (R.ToolTipBase, "inverseSurface"),
                           (R.ToolTipText, "inverseOnSurface"), (R.Mid, "outlineVariant"), (R.Accent, "primary")):
            pal.setColor(role, c[name])
        app.setPalette(pal)
        app.setStyleSheet(self.qss())

    def qss(self):
        c = {k: v.name() for k, v in self.c.items()}
        hov = alpha(self.c["onSurface"], 0.1).name(QColor.NameFormat.HexArgb)
        thumb = alpha(self.c["onSurfaceVariant"], 0.45).name(QColor.NameFormat.HexArgb)
        return f"""
QToolTip {{ background: {c['inverseSurface']}; color: {c['inverseOnSurface']}; border: none;
           border-radius: 6px; padding: 4px 8px; }}
QMenu {{ background: {c['surfaceContainer']}; color: {c['onSurface']}; border: 1px solid {c['outlineVariant']};
        border-radius: {self.r(14):.0f}px; padding: 6px; }}
QMenu::item {{ padding: 8px 32px 8px 12px; border-radius: {self.r(10):.0f}px; }}
QMenu::item:selected {{ background: {hov}; }}
QMenu::item:disabled {{ color: {c['outline']}; }}
QMenu::separator {{ height: 1px; background: {c['outlineVariant']}; margin: 6px 8px; }}
QMenu::icon {{ padding-left: 10px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 4px 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px 4px; }}
QScrollBar::handle {{ background: {thumb}; border-radius: 4px; min-height: 32px; min-width: 32px; margin: 0 2px; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ height: 0; width: 0; background: none; }}
QLineEdit {{ background: transparent; border: none; color: {c['onSurface']};
            selection-background-color: {c['primary']}; selection-color: {c['onPrimary']}; }}
QListView#completer {{ background: transparent; color: {c['onSurface']}; border: none; outline: 0; }}
QListView#completer::item {{ padding: 8px 12px; border-radius: {self.r(10):.0f}px; }}
QListView#completer::item:selected {{ background: {c['secondaryContainer']}; color: {c['onSecondaryContainer']}; }}
QColorDialog, QFileDialog, QDialog {{ background: {c['surfaceContainerHigh']}; }}
"""


theme = Theme()


# ── icons (Material Symbols Rounded via ligatures) ────────────────────────────
@lru_cache(maxsize=256)
def icon_font(px, fill=0.0, weight=400):
    f = QFont("Material Symbols Rounded")
    f.setPixelSize(int(px))
    f.setVariableAxis(QFont.Tag("FILL"), float(fill))
    f.setVariableAxis(QFont.Tag("wght"), float(weight))
    f.setVariableAxis(QFont.Tag("opsz"), float(max(20, min(48, px))))
    return f


def draw_icon(p: QPainter, rect, name, color, px=20, fill=0.0, weight=400):
    p.setFont(icon_font(px, round(fill, 1), weight))
    p.setPen(color)
    p.drawText(QRectF(rect), Qt.AlignmentFlag.AlignCenter, name)


def symbol_icon(name, color, px=20, fill=0.0):
    dpr = 2.0
    pm = QPixmap(int(px * dpr), int(px * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_icon(p, QRectF(0, 0, px, px), name, color, px, fill)
    p.end()
    return QIcon(pm)


def font(px, weight=400):
    f = QFont("Rubik")
    f.setPixelSize(int(px))
    f.setWeight(QFont.Weight(weight))
    return f
