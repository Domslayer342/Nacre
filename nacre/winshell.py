"""Windows integration: Start menu shortcuts, .ico icons, default-browser registration and media keys.

Everything is per-user (HKCU / %APPDATA%), so nothing needs admin rights. Each function is a no-op elsewhere.
"""
import os
import subprocess
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter
from PyQt6.QtGui import QImage

ENABLED = sys.platform == "win32"
FROZEN = getattr(sys, "frozen", False)                 # the PyInstaller build (Nacre.exe)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN = os.path.join(ROOT, "run.py")
PROG_ID = "NacreHTML"
CREATE_NO_WINDOW = 0x08000000


def gui_python():
    """pythonw.exe next to the running interpreter, so launchers don't open a console window."""
    exe = sys.executable
    w = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return w if os.path.exists(w) else exe


def launcher():
    """(program, leading args) that start Nacre: Nacre.exe when bundled, else pythonw.exe run.py."""
    return (sys.executable, []) if FROZEN else (gui_python(), [f'"{RUN}"'])


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
    exe, base = launcher()
    env = dict(os.environ, N_LNK=lnk, N_EXE=exe, N_ARGS=" ".join(base + list(args)),
               N_DIR=os.path.dirname(exe) if FROZEN else ROOT, N_ICON=icon or "", N_DESC=description)
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
    exe, base = launcher()
    start = " ".join([f'"{exe}"'] + base)
    cmd = f'{start} "%1"'
    keys = {
        rf"Software\Classes\{PROG_ID}": {"": "Nacre HTML Document"},
        rf"Software\Classes\{PROG_ID}\DefaultIcon": {"": f"{icon},0"},
        rf"Software\Classes\{PROG_ID}\shell\open\command": {"": cmd},
        r"Software\Clients\StartMenuInternet\Nacre": {"": "Nacre"},
        r"Software\Clients\StartMenuInternet\Nacre\DefaultIcon": {"": f"{icon},0"},
        r"Software\Clients\StartMenuInternet\Nacre\shell\open\command": {"": start},
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


def install_fonts():
    """Rubik + Material Symbols for this user, so every web page (reader mode, autofill menus) can use them."""
    import shutil
    import winreg
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    if not os.path.isdir(src):
        return
    dest = os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Microsoft\Windows\Fonts")
    os.makedirs(dest, exist_ok=True)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows NT\CurrentVersion\Fonts") as k:
        for name in os.listdir(src):
            if not name.lower().endswith(".ttf"):
                continue
            target = os.path.join(dest, name)
            if not os.path.exists(target):
                shutil.copyfile(os.path.join(src, name), target)
            label = "Material Symbols Rounded (TrueType)" if name.startswith("Material") else "Rubik (TrueType)"
            winreg.SetValueEx(k, label, 0, winreg.REG_SZ, target)


def install():
    """`run.py --install-desktop` on Windows: Start menu shortcut, fonts and default-browser registration."""
    from PyQt6.QtGui import QGuiApplication
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])      # QImage's ICO writer needs one
    if FROZEN:
        icon = sys.executable                      # Nacre.exe carries the icon
    else:
        icon = to_ico(os.path.join(ROOT, "assets", "nacre.png"), os.path.join(ROOT, "assets", "nacre.ico"))
    lnk = os.path.join(start_menu(), "Nacre.lnk")
    if not os.path.exists(lnk):
        make_shortcut(lnk, [], icon, "Nacre web browser")
    register_browser(icon or gui_python())
    install_fonts()
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
