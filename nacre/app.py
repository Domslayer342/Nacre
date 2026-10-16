"""Application bootstrap: scheme registration, profiles, services, windows and session."""
import hashlib
import json
import os
import re
import sys
import traceback

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEngineSettings, QWebEngineUrlScheme
from PyQt6.QtWidgets import QApplication, QFileDialog, QMenu
from PyQt6 import QtWebEngineWidgets  # noqa: F401  (must load before QApplication)

from .config import CACHE_DIR, DATA_DIR, cfg, session


def register_scheme():
    s = QWebEngineUrlScheme(b"nacre")
    s.setSyntax(QWebEngineUrlScheme.Syntax.Host)
    F = QWebEngineUrlScheme.Flag
    s.setFlags(F.SecureScheme | F.LocalScheme | F.LocalAccessAllowed | F.ContentSecurityPolicyIgnored | F.FetchApiAllowed)
    QWebEngineUrlScheme.registerScheme(s)


class PopupFilter(QObject):
    """Make every QMenu (and the omnibox completer) frameless + translucent so QSS radii show."""

    def eventFilter(self, obj, e):
        if e.type() == QEvent.Type.Polish and obj.isWidgetType() and obj.isWindow() and (
                isinstance(obj, QMenu) or obj.objectName() == "completer"):
            if not obj.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground):
                obj.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
                obj.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)
                obj.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        return False


class Nacre(QObject):
    def __init__(self, app):
        super().__init__()
        from . import autofill
        from .media import INJECT_JS
        from .services import AdBlock, NacreScheme
        from .theme import theme
        self.app, self.windows, self._downloads = app, [], {}
        self.profile = QWebEngineProfile("nacre", self)
        self.profile.setPersistentStoragePath(os.path.join(DATA_DIR, "profile"))
        self.profile.setCachePath(os.path.join(CACHE_DIR, "web"))
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        self.profile.setHttpCacheMaximumSize(1024 ** 3)                # 1 GB: fewer re-downloads on a slow line
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies)
        self.otr_profile = QWebEngineProfile(self)          # no storage name → off the record
        self.adblock = AdBlock(self)
        self.scheme = NacreScheme(theme.css_vars, self)
        script = QWebEngineScript()
        script.setName("nacre-media")
        script.setSourceCode(INJECT_JS)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        for p in (self.profile, self.otr_profile):
            p.setHttpUserAgent(re.sub(r" QtWebEngine/\S+", "", p.httpUserAgent()))
            p.setUrlRequestInterceptor(self.adblock)
            self.adblock.attach(p)
            p.installUrlSchemeHandler(b"nacre", self.scheme)
            p.scripts().insert(script)
            autofill.install(p)
            p.setDownloadPath(cfg.get("downloads.dir"))
            s, A = p.settings(), QWebEngineSettings.WebAttribute
            for attr, on in ((A.FullScreenSupportEnabled, True), (A.ScrollAnimatorEnabled, True),
                             (A.PdfViewerEnabled, True), (A.PluginsEnabled, True),
                             (A.ScreenCaptureEnabled, True), (A.DnsPrefetchEnabled, True),
                             (A.ForceDarkMode, bool(cfg.get("web.force_dark")))):
                s.setAttribute(attr, on)
        from .hub import MediaHub
        from .rpc import DiscordRPC
        self.media = MediaHub(self)
        self.mpris = None
        if sys.platform.startswith("linux"):
            from .mpris import Mpris
            self.mpris = Mpris(self, self.media)      # media keys / Plasma media controls
        elif sys.platform == "win32" and not os.environ.get("NACRE_NO_MEDIAKEYS"):
            from .winshell import MediaKeys
            self.mpris = MediaKeys(app, self.media)   # hardware media keys while a tab plays
        self.rpc = DiscordRPC(self)
        self.save_timer = QTimer(self, interval=30000, timeout=lambda: self.save_session())
        self.save_timer.start()
        app.aboutToQuit.connect(lambda: self.save_session())

    def downloads_for(self, profile):
        from .services import Downloads
        d = self._downloads.get(id(profile))
        if d is None:
            d = self._downloads[id(profile)] = Downloads(profile, self)
            d.ask_path = lambda suggested: QFileDialog.getSaveFileName(None, "Save file", suggested)[0]
        return d

    def new_window(self, incognito=False, urls=None, app=None):
        from .window import BrowserWindow
        w = BrowserWindow(self, incognito, urls, app)
        self.windows.append(w)
        w.show()
        return w

    def open_app(self, app_id):
        from . import pwa
        app = pwa.find(app_id)
        if app is None:
            return self.browser_window()
        for w in self.windows:
            if w.app and w.app["id"] == app_id:
                w.raise_()
                w.activateWindow()
                return w
        if not app["window"]:
            return self.browser_window().new_tab(app["url"])
        w = self.new_window(app=app)
        w.resize(1100, 760)
        return w

    def browser_window(self):
        """The window ordinary links should land in (never an app or incognito window)."""
        last = getattr(self, "last_active", None)
        if last in self.windows and not last.app and not last.incognito:
            return last
        regular = [w for w in self.windows if not w.app and not w.incognito]
        return regular[-1] if regular else self.new_window()

    def active_window(self):
        return QApplication.activeWindow()

    def window_closed(self, w):
        if w in self.windows:
            self.windows.remove(w)

    def save_session(self, closing=None):
        regular = [w for w in self.windows if not w.incognito and not w.app]
        others = [w for w in regular if w is not closing]
        keep = others if others else regular          # last window keeps its tabs in the session
        data = [w.session_urls() for w in keep if w.session_urls()]
        if data or closing is not None:
            session.data = data
            session.save()

    def shutdown(self):
        self.rpc.shutdown()
        self.save_session()
        for w in list(self.windows):
            w.teardown()
        for p in (self.otr_profile, self.profile):
            sip.delete(p)

    def quit(self):
        self.save_session()
        for w in list(self.windows):
            w.close()
        self.app.quit()


def _excepthook(kind, value, tb):
    """Log instead of letting PyQt abort the whole browser on an exception in a slot."""
    traceback.print_exception(kind, value, tb)


def instance_path():
    # one per profile (short enough for the 108-char socket limit; test profiles never reach the real one)
    tag = hashlib.sha1(DATA_DIR.encode()).hexdigest()[:10]
    if os.name == "nt":
        return f"nacre-{tag}"                          # a named pipe on Windows
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", f"nacre-{tag}.sock")


def hand_off(urls, app=None):
    """If Nacre is already running on this profile, pass it our URLs and return True."""
    s = QLocalSocket()
    s.connectToServer(instance_path())
    if not s.waitForConnected(400):
        return False
    s.write(json.dumps({"urls": urls, "app": app}).encode() + b"\n")
    s.waitForBytesWritten(1000)
    s.disconnectFromServer()
    return True


def serve(ctx):
    path = instance_path()
    QLocalServer.removeServer(path)                    # stale socket from a crash
    srv = QLocalServer(ctx)
    srv.listen(path)

    def accept():
        c = srv.nextPendingConnection()

        def read():
            if not c.canReadLine():
                return
            try:
                msg = json.loads(bytes(c.readLine()).decode())
            except ValueError:
                msg = {}
            urls, app = msg.get("urls") or [], msg.get("app")
            c.disconnectFromServer()
            if app:
                return ctx.open_app(app)
            wins = [w for w in ctx.windows if not w.incognito and not w.app]
            if not wins:
                ctx.new_window(urls=urls or None)
            elif urls:
                w = getattr(ctx, "last_active", None) if getattr(ctx, "last_active", None) in wins else wins[-1]
                for u in urls:
                    w.new_tab(u)
                w.raise_()
                w.activateWindow()
            else:
                ctx.new_window()
        c.readyRead.connect(read)
    srv.newConnection.connect(accept)
    ctx.instance_server = srv


def install_desktop():
    """`run.py --install-desktop`: app-menu entry + icon (also lets the desktop portal recognise Nacre)."""
    if sys.platform == "win32":
        from .winshell import install
        return install()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    share = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    icon_dir = os.path.join(share, "icons/hicolor/scalable/apps")
    os.makedirs(icon_dir, exist_ok=True)
    os.makedirs(os.path.join(share, "applications"), exist_ok=True)
    with open(os.path.join(root, "assets/nacre.svg")) as src, open(os.path.join(icon_dir, "nacre.svg"), "w") as dst:
        dst.write(src.read())
    entry = os.path.join(share, "applications/nacre.desktop")
    with open(entry, "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name=Nacre
GenericName=Web Browser
Comment=A Material You web browser
Exec={sys.executable} {os.path.join(root, "run.py")} %U
Icon=nacre
Terminal=false
Categories=Network;WebBrowser;
MimeType=text/html;x-scheme-handler/http;x-scheme-handler/https;
StartupWMClass=nacre
""")
    print(f"Installed {entry}")


def windows_vm():
    """True on a Windows virtual machine (VirtualBox, VMware, Hyper-V, QEMU/KVM, Parallels, Xen)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\BIOS") as k:
            ids = " ".join(str(winreg.QueryValueEx(k, v)[0]) for v in ("SystemManufacturer", "SystemProductName"))
    except (ImportError, OSError):
        return False
    return bool(re.search(r"virtual|vmware|vbox|qemu|kvm|parallels|xen|bochs", ids, re.I))


def load_fonts():
    """Rubik + Material Symbols from nacre/fonts (fetched by the install scripts), if they're there."""
    from PyQt6.QtGui import QFontDatabase
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.lower().endswith((".ttf", ".otf")):
            QFontDatabase.addApplicationFont(os.path.join(d, name))


def main(argv=None):
    argv = sys.argv if argv is None else argv
    sys.excepthook = _excepthook
    if "--install-desktop" in argv:
        return install_desktop()
    register_scheme()
    from .widevine import chromium_flag
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "--enable-smooth-scrolling --enable-quic")
    drm = chromium_flag()                              # Widevine (Netflix, Hulu, Spotify…) if a CDM is installed
    gpu = cfg.get("web.gpu")
    if gpu == "off" or (gpu == "auto" and os.name == "nt" and windows_vm()):
        flags += " --disable-gpu --disable-gpu-compositing"     # VMs' virtual GPUs give Chromium black pages
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = f"{flags} {drm}".strip()
    QApplication.setApplicationName("Nacre")
    QApplication.setDesktopFileName("nacre")
    app = QApplication(argv)
    app_id = argv[argv.index("--app") + 1] if "--app" in argv[:-1] else None
    urls = [a for a in argv[1:] if not a.startswith("-") and a != app_id]
    if hand_off(urls, app_id):                                 # already running: that window gets the links
        return 0
    app.setStyle("Fusion")
    load_fonts()
    from .theme import font, theme
    app.setFont(font(14))
    pf = PopupFilter(app)
    app.installEventFilter(pf)
    theme.init()
    from .icon import app_icon
    app.setWindowIcon(app_icon(theme.target))
    ctx = Nacre(app)
    serve(ctx)
    ctx.widevine_loaded = bool(drm)
    app._nacre = ctx
    mode = cfg.get("startup.mode")
    if app_id:
        ctx.open_app(app_id)
    elif mode == "restore" and session.data and not urls:
        for tabs in session.data:
            ctx.new_window(urls=tabs)
    else:
        start = urls or ([cfg.get("home.url")] if mode == "home" else None)
        ctx.new_window(urls=start)
    if os.environ.get("NACRE_SCRIPT"):                 # dev hook: run a script with ctx/app in scope
        QTimer.singleShot(0, lambda: exec(open(os.environ["NACRE_SCRIPT"]).read(), {"ctx": ctx, "app": app}))
    code = app.exec()
    ctx.shutdown()
    return code
