"""Installable web apps (PWAs) and site shortcuts: manifest detection, install dialog, launcher entries."""
import json
import os
import sys
import time
import uuid

from PyQt6.QtCore import QRectF, Qt, QUrl
from PyQt6.QtGui import QColor, QIcon, QImage, QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PyQt6.QtWidgets import QLabel

from .config import DATA_DIR, JsonStore, launch_command
from .widgets import Button, Flyout, Label, Switch, TextField, guard, hbox, painter

apps = JsonStore(os.path.join(DATA_DIR, "apps.json"), [])
ICON_DIR = os.path.join(DATA_DIR, "apps")
os.makedirs(ICON_DIR, exist_ok=True)
SHARE = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# page metadata + manifest link (sync — Python fetches the manifest itself)
PAGE_JS = r"""(() => { const l = document.querySelector('link[rel="manifest"]');
  const meta = n => (document.querySelector(`meta[name="${n}"]`) || {}).content || '';
  const icons = [...document.querySelectorAll('link[rel~="icon"], link[rel="apple-touch-icon"]')]
    .map(x => ({src: x.href, sizes: (x.sizes && x.sizes.value) || (x.rel.includes('apple') ? '180x180' : '')}));
  return {manifest: l ? l.href : '', title: document.title, url: location.href, icons,
          name: meta('application-name') || meta('apple-mobile-web-app-title'), theme: meta('theme-color')}; })()"""

_nam = None


def nam():
    global _nam
    _nam = _nam or QNetworkAccessManager()
    return _nam


def get(url, done):
    req = QNetworkRequest(QUrl(url))
    req.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                     QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    req.setTransferTimeout(15000)
    r = nam().get(req)

    def fin():
        ok = r.error() == QNetworkReply.NetworkError.NoError
        data = bytes(r.readAll()) if ok else b""
        r.deleteLater()
        done(data)
    r.finished.connect(fin)


def best_icon(icons, base):
    """Largest square-ish icon from manifest/page lists (prefer 'any' purpose, PNG)."""
    def size(i):
        try:
            return max(int(s.lower().split("x")[0]) for s in (i.get("sizes") or "0x0").split() if "x" in s.lower())
        except ValueError:
            return 0
    cand = [i for i in icons if i.get("src") and "monochrome" not in (i.get("purpose") or "")]
    cand.sort(key=lambda i: (size(i), (i.get("type") or "").endswith("png")), reverse=True)
    return QUrl(base).resolved(QUrl(cand[0]["src"])).toString() if cand else None


def find(app_id):
    return next((a for a in apps.data if a["id"] == app_id), None)


def installed_for(url):
    u = QUrl(url).toString()
    return next((a for a in apps.data if a["window"] and in_scope(a, u)), None)


def in_scope(app, url):
    return url.startswith(app.get("scope") or app["url"])


def icon_path(app):
    return os.path.join(ICON_DIR, app["id"] + ".png")


def app_icon(app):
    p = icon_path(app)
    return QIcon(p) if os.path.exists(p) else QIcon()


def desktop_path(app):
    return os.path.join(SHARE, "applications", f"nacre-app-{app['id']}.desktop")


def _write_lnk(app):
    """Windows: a Start menu shortcut in "Nacre Apps" (named after the app; the path is kept to remove it later)."""
    from . import winshell
    _remove_lnk(app)
    name = "".join(c for c in app["name"] if c not in '<>:"/\\|?*').strip() or app["id"]
    lnk = os.path.join(winshell.start_menu(), "Nacre Apps", name + ".lnk")
    ico = winshell.to_ico(icon_path(app), os.path.join(ICON_DIR, app["id"] + ".ico"))
    args = ["--app", app["id"]] if app["window"] else [f'"{app["url"]}"']
    if winshell.make_shortcut(lnk, args, ico, f"{app['name']} · installed with Nacre"):
        app["lnk"] = lnk
        app["launcher"] = True


def _remove_lnk(app):
    try:
        os.remove(app.pop("lnk"))
    except (KeyError, OSError):
        pass


def write_desktop(app):
    if sys.platform == "win32":
        return _write_lnk(app)
    os.makedirs(os.path.dirname(desktop_path(app)), exist_ok=True)
    exe = f"{launch_command()} --app {app['id']}" if app["window"] else f"{launch_command()} {app['url']}"
    with open(desktop_path(app), "w") as f:
        f.write(f"""[Desktop Entry]
Type=Application
Name={app['name']}
Comment={'Web app' if app['window'] else 'Shortcut'} · {QUrl(app['url']).host()} · installed with Nacre
Exec={exe}
Icon={icon_path(app)}
Terminal=false
Categories=Network;
StartupWMClass=nacre
""")
    app["launcher"] = True


def remove_desktop(app):
    if sys.platform == "win32":
        _remove_lnk(app)
        app["launcher"] = False
        return
    try:
        os.remove(desktop_path(app))
    except OSError:
        pass
    app["launcher"] = False


def uninstall(app):
    remove_desktop(app)
    try:
        os.remove(icon_path(app))
    except OSError:
        pass
    if app in apps.data:
        apps.data.remove(app)
        apps.touch()


def save_icon(app, img):
    """Store a 256px icon; small favicons get centred on a rounded tile so they don't look blurry."""
    out = QImage(256, 256, QImage.Format.Format_ARGB32)
    out.fill(Qt.GlobalColor.transparent)
    p = painter(out)
    if img.width() >= 128:
        p.drawImage(QRectF(0, 0, 256, 256), img.scaled(256, 256, Qt.AspectRatioMode.KeepAspectRatio,
                                                       Qt.TransformationMode.SmoothTransformation))
    else:
        p.setBrush(QColor(app.get("theme") or "#EADDFF"))
        p.drawRoundedRect(QRectF(16, 16, 224, 224), 60, 60)
        p.drawImage(QRectF(64, 64, 128, 128), img.scaled(128, 128, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation))
    p.end()
    out.save(icon_path(app))


class InstallFlyout(Flyout):
    """Install as app (manifest) or create a shortcut for any page."""

    def __init__(self, win, tab, shortcut=False):
        super().__init__(win, width=400)
        self.win, self.tab, self.img = win, tab, None
        self.app = {"id": uuid.uuid4().hex[:12], "name": tab.title(), "url": tab.url().toString(),
                    "scope": "", "theme": "", "window": not shortcut, "launcher": False, "created": time.time()}
        self.heading = Label("Create shortcut" if shortcut else "Install app", "onSurface", 18, 500)
        self.lay.addWidget(self.heading)
        self.preview = QLabel()
        self.preview.setFixedSize(64, 64)
        self.name = TextField(self.app["name"], "Name", "edit")
        self.lay.addWidget(hbox(self.preview, self.name, spacing=12, margins=(0, 8, 0, 4)))
        self.host = Label(QUrl(self.app["url"]).host(), "onSurfaceVariant", 13)
        self.lay.addWidget(self.host)
        self.sw_window = Switch(self.app["window"])
        self.sw_launch = Switch(True)
        self.lay.addWidget(hbox(Label("Open as window", "onSurface", 14), None, self.sw_window, margins=(0, 6, 0, 0)))
        self.lay.addWidget(hbox(Label("Add to app launcher", "onSurface", 14), None, self.sw_launch))
        cancel = Button(None, "Cancel", kind="text", size=36)
        cancel.clicked.connect(self.dismiss)
        self.ok = Button(None, "Install" if not shortcut else "Create", kind="filled", size=36)
        self.ok.clicked.connect(self._install)
        self.lay.addWidget(hbox(None, cancel, self.ok, margins=(0, 10, 0, 0)))
        if tab.item.icon:
            self._set_img(tab.item.icon.pixmap(64).toImage())
        tab.page.runJavaScript(PAGE_JS, 0, guard(self, self._page))

    def _set_img(self, img):
        if img is None or img.isNull():
            return
        self.img = img
        self.preview.setPixmap(QPixmap.fromImage(img).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation))

    def _page(self, info):
        if not info:
            return
        self.app["theme"] = info.get("theme") or ""
        if info.get("name"):
            self.name.edit.setText(info["name"])
        icon = best_icon(info.get("icons") or [], info["url"])
        if icon:
            get(icon, guard(self, self._icon_data))
        if info.get("manifest"):
            get(info["manifest"], guard(self, lambda data, base=info["manifest"]: self._manifest(data, base)))

    def _manifest(self, data, base):
        try:
            m = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        b = QUrl(base)
        if m.get("name") or m.get("short_name"):
            self.name.edit.setText(m.get("short_name") if len(m.get("name") or "") > 24 else m.get("name"))
        if m.get("start_url"):
            self.app["url"] = b.resolved(QUrl(m["start_url"])).toString()
        if m.get("scope"):
            self.app["scope"] = b.resolved(QUrl(m["scope"])).toString()
        self.app["theme"] = m.get("theme_color") or self.app["theme"]
        self.app["manifest"] = True
        self.heading.setText("Install app")
        icon = best_icon(m.get("icons") or [], base)
        if icon:
            get(icon, guard(self, self._icon_data))

    def _icon_data(self, data):
        img = QImage()
        if img.loadFromData(data) and (self.img is None or img.width() >= self.img.width()):
            self._set_img(img)

    def _install(self):
        a = self.app
        a["name"] = self.name.edit.text().strip() or QUrl(a["url"]).host()
        a["window"] = self.sw_window.isChecked()
        if not a["scope"]:
            u = QUrl(a["url"])
            a["scope"] = u.toString(QUrl.UrlFormattingOption.RemoveQuery | QUrl.UrlFormattingOption.RemoveFragment
                                    | QUrl.UrlFormattingOption.RemoveFilename)
        save_icon(a, self.img if self.img is not None else QImage())
        if self.sw_launch.isChecked():
            write_desktop(a)
        apps.data.append(a)
        apps.touch()
        self.dismiss()
        self.win.snack.show_msg(f"Installed {a['name']}", "Manage", lambda: self.win.new_tab("nacre://apps"))
        if a["window"]:
            self.win.ctx.open_app(a["id"])
        self.win.check_installable(self.tab)

    def open_for(self, anchor):
        self.popup(anchor)
