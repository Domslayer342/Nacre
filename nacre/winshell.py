"""Windows integration: Start menu shortcuts, .ico icons, default-browser registration and media keys.

Everything is per-user (HKCU / %APPDATA%), so nothing needs admin rights. Each function is a no-op elsewhere.
"""
import os
import subprocess
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtGui import QImage

ENABLED = sys.platform == "win32"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "run.py")
PROG_ID = "NacreHTML"
CREATE_NO_WINDOW = 0x08000000


def gui_python():
    """pythonw.exe next to the running interpreter, so launchers don't open a console window."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def start_menu():
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), r"Microsoft\Windows\Start Menu\Programs")


def to_ico(src, dest):
    """PNG/SVG → .ico (Windows shortcuts can't use PNGs). Returns dest, or None."""
    img = QImage(src)
    if img.isNull():
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    return dest if img.scaled(256, 256).save(dest, "ICO") else None


def make_shortcut(lnk, args, icon=None, description=""):
    """Create/replace a .lnk that runs Nacre's run.py with `args` (WScript.Shell through PowerShell)."""
    os.makedirs(os.path.dirname(lnk), exist_ok=True)
    env = dict(os.environ, N_LNK=lnk, N_EXE=gui_python(), N_ARGS=" ".join([f'"{RUN}"'] + list(args)),
               N_DIR=ROOT, N_ICON=icon or "", N_DESC=description)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:N_LNK);$s.TargetPath=$env:N_EXE;"
          "$s.Arguments=$env:N_ARGS;$s.WorkingDirectory=$env:N_DIR;$s.Description=$env:N_DESC;"
          "if($env:N_ICON){$s.IconLocation=$env:N_ICON};$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env,
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
    return r.returncode == 0


def register_browser(icon):
    """List Nacre under Settings → Apps → Default apps (Windows only lets the user pick the default)."""
    import ctypes
    import winreg
    cmd = f'"{gui_python()}" "{RUN}" "%1"'
    keys = {
        rf"Software\Classes\{PROG_ID}": {"": "Nacre HTML Document"},
        rf"Software\Classes\{PROG_ID}\DefaultIcon": {"": f"{icon},0"},
        rf"Software\Classes\{PROG_ID}\shell\open\command": {"": cmd},
        r"Software\Clients\StartMenuInternet\Nacre": {"": "Nacre"},
        r"Software\Clients\StartMenuInternet\Nacre\DefaultIcon": {"": f"{icon},0"},
        r"Software\Clients\StartMenuInternet\Nacre\shell\open\command": {"": f'"{gui_python()}" "{RUN}"'},
        r"Software\Clients\StartMenuInternet\Nacre\Capabilities": {
            "ApplicationName": "Nacre", "ApplicationDescription": "A Material You web browser",
            "ApplicationIcon": f"{icon},0"},
        r"Software\Clients\StartMenuInternet\Nacre\Capabilities\StartMenu": {"StartMenuInternet": "Nacre"},
        r"Software\Clients\StartMenuInternet\Nacre\Capabilities\URLAssociations": {
            "http": PROG_ID, "https": PROG_ID},
        r"Software\Clients\StartMenuInternet\Nacre\Capabilities\FileAssociations": {
            ".htm": PROG_ID, ".html": PROG_ID, ".xhtml": PROG_ID, ".svg": PROG_ID},
        r"Software\RegisteredApplications": {"Nacre": r"Software\Clients\StartMenuInternet\Nacre\Capabilities"},
    }
    for path, values in keys.items():
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
            for name, value in values.items():
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)          # SHCNE_ASSOCCHANGED


def install():
    """`run.py --install-desktop` on Windows: Start menu shortcut + default-browser registration."""
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])      # QImage's ICO writer needs one
    icon = to_ico(os.path.join(ROOT, "assets", "nacre.png"), os.path.join(ROOT, "assets", "nacre.ico"))
    lnk = os.path.join(start_menu(), "Nacre.lnk")
    if not os.path.exists(lnk):
        make_shortcut(lnk, [], icon, "Nacre web browser")
    register_browser(icon or gui_python())
    print("Nacre is in the Start menu and listed under Settings → Apps → Default apps.")
    del app


# ── media keys ────────────────────────────────────────────────────────────────
WM_HOTKEY, MOD_NOREPEAT = 0x0312, 0x4000
MEDIA_KEYS = {0xB3: "toggle", 0xB0: "next", 0xB1: "prev", 0xB2: "stop"}    # play/pause, next, previous, stop


class MediaKeys(QAbstractNativeEventFilter):
    """Hardware media keys → the playing tab. The keys are only claimed while a tab has media, so other players
    (Spotify etc.) keep them the rest of the time."""

    def __init__(self, app, hub):
        super().__init__()
        import ctypes
        self.user32 = ctypes.windll.user32
        self.hub, self.on = hub, False
        app.installNativeEventFilter(self)
        hub.changed.connect(self._sync)

    def _sync(self):
        want = self.hub.tab is not None
        if want == self.on:
            return
        for i, vk in enumerate(MEDIA_KEYS, 0xA000):
            if want:
                self.user32.RegisterHotKey(None, i, MOD_NOREPEAT, vk)
            else:
                self.user32.UnregisterHotKey(None, i)
        self.on = want

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and 0xA000 <= msg.wParam < 0xA000 + len(MEDIA_KEYS):
                action = list(MEDIA_KEYS.values())[msg.wParam - 0xA000]
                if action != "stop":
                    self.hub.act(action)
                elif not (self.hub.st or {}).get("paused", True):      # stop = pause, like MPRIS
                    self.hub.act("toggle")
                return True, 0
        return False, 0
