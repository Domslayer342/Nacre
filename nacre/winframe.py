"""Windows: keep the frameless window snappable.

A FramelessWindowHint window is a WS_POPUP, which Windows won't Aero Snap (drag to the top/sides, Win+arrows) or
animate. We add the normal frame styles back and answer WM_NCCALCSIZE so the client area still covers the whole
window: no visible frame, but Windows treats it as a real top-level window again (snap, animations, shadow).
"""
import ctypes
import os
import sys
from ctypes import wintypes

ENABLED = sys.platform == "win32" and not os.environ.get("NACRE_NO_WINFRAME")

GWL_STYLE = -16
WS_THICKFRAME, WS_CAPTION, WS_MAXIMIZEBOX, WS_MINIMIZEBOX = 0x00040000, 0x00C00000, 0x00010000, 0x00020000
SWP = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020       # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE | FRAMECHANGED
WM_NCCALCSIZE = 0x0083
SM_CXSIZEFRAME, SM_CYSIZEFRAME, SM_CXPADDEDBORDER = 32, 33, 92


def _user32():
    u = ctypes.windll.user32
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               ctypes.c_uint]
    u.IsZoomed.argtypes = [wintypes.HWND]
    return u


def install(win):
    """Call once the native window exists (first show)."""
    if not ENABLED:
        return
    u = _user32()
    hwnd = int(win.winId())
    style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
    u.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_CAPTION | WS_MAXIMIZEBOX | WS_MINIMIZEBOX)
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP)


def _frame(u, hwnd):
    """Resize-border thickness (x, y) at the window's DPI."""
    try:
        dpi = u.GetDpiForWindow(wintypes.HWND(hwnd))
        m = lambda i: u.GetSystemMetricsForDpi(i, dpi)
    except AttributeError:                              # Windows < 10 1607
        m = u.GetSystemMetrics
    pad = m(SM_CXPADDEDBORDER)
    return m(SM_CXSIZEFRAME) + pad, m(SM_CYSIZEFRAME) + pad


def native_event(win, event_type, message):
    """Returns (True, 0) when handled, else None."""
    if not ENABLED or event_type != b"windows_generic_MSG":
        return None
    msg = wintypes.MSG.from_address(int(message))
    if msg.message != WM_NCCALCSIZE or not msg.wParam:
        return None
    u = _user32()
    if u.IsZoomed(msg.hWnd):          # maximized windows overhang the screen by the frame: pull the client back in
        r = wintypes.RECT.from_address(msg.lParam)     # NCCALCSIZE_PARAMS.rgrc[0]
        fx, fy = _frame(u, msg.hWnd)
        r.left += fx
        r.top += fy
        r.right -= fx
        r.bottom -= fy
    return True, 0
