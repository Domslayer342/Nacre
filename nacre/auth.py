"""Re-authenticate with the user's own login password before revealing saved passwords.

Linux: PAM. No privileges are involved: pam_unix checks the caller's own password through the setuid
`unix_chkpwd` helper, exactly like a screen locker. Nothing runs as root and Nacre never gets elevated rights.
Windows: LogonUserW checks the account password (a Microsoft account's password works too; a PIN doesn't).
"""
import ctypes
import ctypes.util
import getpass
import os
import sys
import threading
import time

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal

from .config import cfg
from .widgets import Button, Flyout, Label, TextField, hbox

# service stacks that only check the password (screen-locker style) come first
SERVICES = ["vlock", "kde", "system-auth", "common-auth", "login"]
PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON = 1, 2


class _Msg(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class _Resp(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]


_CONV = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.POINTER(_Msg)),
                         ctypes.POINTER(ctypes.POINTER(_Resp)), ctypes.c_void_p)


class _Conv(ctypes.Structure):
    _fields_ = [("conv", _CONV), ("appdata_ptr", ctypes.c_void_p)]


def _lib():
    name = ctypes.util.find_library("pam")
    if not name:
        return None, None
    pam, libc = ctypes.CDLL(name), ctypes.CDLL(ctypes.util.find_library("c"))
    pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(_Conv), ctypes.POINTER(ctypes.c_void_p)]
    pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    libc.calloc.restype = ctypes.c_void_p
    libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
    libc.strdup.restype = ctypes.c_void_p
    libc.strdup.argtypes = [ctypes.c_char_p]
    return pam, libc


def service():
    return next((s for s in SERVICES if os.path.exists(f"/etc/pam.d/{s}")), None)


WINDOWS = sys.platform == "win32"


def available():
    if WINDOWS:
        return True
    return _lib()[0] is not None and service() is not None


def check_windows_password(password, user=None):
    from ctypes import wintypes
    advapi, kernel = ctypes.windll.advapi32, ctypes.windll.kernel32
    advapi.LogonUserW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                  wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    token = wintypes.HANDLE()
    domain = os.environ.get("USERDOMAIN") or "."
    # LOGON32_LOGON_INTERACTIVE = 2, LOGON32_PROVIDER_DEFAULT = 0
    ok = advapi.LogonUserW(user or getpass.getuser(), domain, password, 2, 0, ctypes.byref(token))
    if ok:
        kernel.CloseHandle(token)
    return bool(ok)


def check_password(password, user=None):
    """True if `password` is the current user's login password. Blocking (PAM delays failures ~2 s)."""
    if WINDOWS:
        return check_windows_password(password, user)
    pam, libc = _lib()
    svc = service()
    if pam is None or svc is None:
        return False
    secret = password.encode()

    @_CONV
    def conv(n, msgs, resp, _):
        # PAM frees the replies, so they must come from malloc
        arr = libc.calloc(n, ctypes.sizeof(_Resp))
        replies = ctypes.cast(arr, ctypes.POINTER(_Resp))
        for i in range(n):
            if msgs[i].contents.msg_style in (PAM_PROMPT_ECHO_OFF, PAM_PROMPT_ECHO_ON):
                replies[i].resp = ctypes.cast(libc.strdup(secret), ctypes.c_char_p)
                replies[i].resp_retcode = 0
        resp[0] = replies
        return 0

    handle = ctypes.c_void_p()
    c = _Conv(conv, None)
    if pam.pam_start(svc.encode(), (user or getpass.getuser()).encode(), ctypes.byref(c), ctypes.byref(handle)) != 0:
        return False
    try:
        return pam.pam_authenticate(handle, 0) == 0
    finally:
        pam.pam_end(handle, 0)


class Gate(QObject):
    """`require(win, reason, fn)`: run fn now if recently verified, else ask for the login password first."""
    verified = pyqtSignal()
    _result = pyqtSignal(bool)

    def __init__(self):
        super().__init__()
        self.until = 0.0

    def fresh(self):
        return time.monotonic() < self.until

    def require(self, win, reason, fn):
        if not cfg.get("vault.reauth") or self.fresh() or not available():
            return fn()
        AuthFlyout(win, self, reason, fn).open_center()

    def grant(self):
        self.until = time.monotonic() + max(0, cfg.get("vault.reauth_min")) * 60
        self.verified.emit()

    def forget(self):
        self.until = 0.0


gate = Gate()


class AuthFlyout(Flyout):
    _done = pyqtSignal(bool)

    def __init__(self, win, gate_, reason, fn):
        super().__init__(win, width=380)
        self.win, self.gate, self.fn = win, gate_, fn
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.lay.addWidget(Label("Confirm it's you", "onSurface", 18, 500))
        kind = "Windows password" if WINDOWS else "login password"
        self.lay.addWidget(Label(f"Enter the {kind} for “{getpass.getuser()}” to {reason}.",
                                 "onSurfaceVariant", 13, wrap=True))
        self.lay.addSpacing(8)
        self.field = TextField("", "Windows password" if WINDOWS else "Login password", "lock")
        self.field.edit.setEchoMode(self.field.edit.EchoMode.Password)
        self.field.edit.returnPressed.connect(self._go)
        self.lay.addWidget(self.field)
        self.err = Label("", "error", 12)
        self.lay.addWidget(self.err)
        cancel = Button(None, "Cancel", kind="text", size=36)
        cancel.clicked.connect(self.dismiss)
        self.ok = Button(None, "Unlock", kind="filled", size=36)
        self.ok.clicked.connect(self._go)
        self.lay.addWidget(hbox(None, cancel, self.ok, margins=(0, 8, 0, 0)))
        self._done.connect(self._finish)

    def open_center(self):
        win = self.parentWidget()
        self.popup(win.omni, align="left")
        self.move(win.mapToGlobal(win.rect().center()).x() - self.width() // 2, self.y())
        QTimer.singleShot(60, self.field.edit.setFocus)

    def _go(self):
        pw = self.field.edit.text()
        if not pw:
            return
        self.ok.setEnabled(False)
        self.ok.spin = 0.0
        self.err.setText("Checking…")
        threading.Thread(target=lambda: self._done.emit(check_password(pw)), daemon=True).start()

    def _finish(self, ok):
        self.ok.spin = None
        self.ok.setEnabled(True)
        self.field.edit.clear()
        if not ok:
            self.err.setText("That isn't your Windows password" if WINDOWS else "That isn't your login password")
            self.field.edit.setFocus()
            return
        self.gate.grant()
        self.dismiss()
        self.fn()
