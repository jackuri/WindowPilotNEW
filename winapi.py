"""Thin ctypes wrappers around the Win32 calls WindowPilot needs (no pywin32 required)."""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
GA_ROOTOWNER = 3
SW_RESTORE = 9
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
WDA_EXCLUDEFROMCAPTURE = 0x11
VK_MENU = 0x12

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    H = wintypes.HWND

    def _sig(fn, args, res=wintypes.BOOL):
        fn.argtypes = args
        fn.restype = res

    _sig(user32.EnumWindows, [WNDENUMPROC, wintypes.LPARAM])
    _sig(user32.IsWindowVisible, [H])
    _sig(user32.IsWindow, [H])
    _sig(user32.IsIconic, [H])
    _sig(user32.GetWindowTextLengthW, [H], ctypes.c_int)
    _sig(user32.GetWindowTextW, [H, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int)
    _sig(user32.GetWindow, [H, wintypes.UINT], H)
    _sig(user32.GetWindowLongW, [H, ctypes.c_int], wintypes.LONG)
    _sig(user32.SetWindowLongW, [H, ctypes.c_int, wintypes.LONG], wintypes.LONG)
    _sig(user32.GetWindowThreadProcessId, [H, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD)
    _sig(user32.GetWindowRect, [H, ctypes.POINTER(wintypes.RECT)])
    _sig(user32.GetForegroundWindow, [], H)
    _sig(user32.SetForegroundWindow, [H])
    _sig(user32.BringWindowToTop, [H])
    _sig(user32.ShowWindow, [H, ctypes.c_int])
    _sig(user32.GetAncestor, [H, wintypes.UINT], H)
    _sig(user32.WindowFromPoint, [wintypes.POINT], H)
    _sig(user32.GetCursorPos, [ctypes.POINTER(wintypes.POINT)])
    _sig(user32.AttachThreadInput, [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL])
    _sig(user32.FlashWindow, [H, wintypes.BOOL])
    _sig(user32.keybd_event, [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t], None)
    _sig(kernel32.GetCurrentThreadId, [], wintypes.DWORD)
    _sig(dwmapi.DwmGetWindowAttribute, [H, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD], ctypes.c_long)


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    pid: int

    @property
    def label(self) -> str:
        title = self.title if len(self.title) <= 80 else self.title[:77] + "..."
        return f"{title}   —   {self.exe}" if self.exe else title


def set_dpi_awareness() -> None:
    """Use physical pixels everywhere so screenshots, clicks and hooks agree on high-DPI screens."""
    if not IS_WINDOWS:
        return
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        if fn(ctypes.c_void_p(-4)):  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def system_dpi() -> int:
    try:
        return int(user32.GetDpiForSystem())
    except (AttributeError, OSError, NameError):
        return 96


def _text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_title(hwnd: int) -> str:
    return _text(hwnd)


def _pid(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _exe_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _is_cloaked(hwnd) -> bool:
    val = ctypes.c_int(0)
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
    return hr == 0 and val.value != 0


def list_windows(exclude: set[int] | None = None) -> list[WindowInfo]:
    """Visible, titled, top-level app windows in z-order (most recently used first)."""
    exclude = exclude or set()
    found: list[WindowInfo] = []

    def cb(hwnd, _lparam):
        try:
            if not hwnd or hwnd in exclude or not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                return True
            if _is_cloaked(hwnd):
                return True
            title = _text(hwnd).strip()
            if not title or title == "Program Manager":
                return True
            pid = _pid(hwnd)
            found.append(WindowInfo(int(hwnd), title, _exe_name(pid), pid))
        except Exception:
            pass
        return True

    proc = WNDENUMPROC(cb)
    user32.EnumWindows(proc, 0)
    return found


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) of the visible window frame in physical pixels."""
    rect = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise OSError("Could not read the window position (was it closed?)")
    return rect.left, rect.top, rect.right, rect.bottom


def root_owner(hwnd) -> int:
    """The top-level owner of a window, so an app's dialogs and menus count as the app."""
    if not hwnd:
        return 0
    return int(user32.GetAncestor(hwnd, GA_ROOTOWNER) or 0)


def root_of(hwnd) -> int:
    if not hwnd:
        return 0
    return int(user32.GetAncestor(hwnd, GA_ROOT) or 0)


def foreground() -> int:
    return int(user32.GetForegroundWindow() or 0)


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def window_at(x: int, y: int) -> int:
    return root_owner(user32.WindowFromPoint(wintypes.POINT(int(x), int(y))))


def bring_to_front(hwnd: int, settle: float = 0.08) -> bool:
    """Restore and focus a window. Windows restricts focus stealing, so try a few known workarounds."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.3)
    if root_owner(foreground()) == hwnd:
        return True

    user32.SetForegroundWindow(hwnd)
    if root_owner(foreground()) != hwnd:
        fg = foreground()
        fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        me = kernel32.GetCurrentThreadId()
        attached = bool(fg_thread) and fg_thread != me and user32.AttachThreadInput(me, fg_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(me, fg_thread, False)
    if root_owner(foreground()) != hwnd:
        # Last resort: a synthetic Alt tap lets this process take the foreground.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 2, 0)
        user32.SetForegroundWindow(hwnd)
    time.sleep(settle)
    return root_owner(foreground()) == hwnd


def flash(hwnd: int) -> None:
    try:
        user32.FlashWindow(hwnd, True)
    except Exception:
        pass


def make_click_through(hwnd: int) -> None:
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                          style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)


def exclude_from_capture(hwnd: int) -> None:
    """Hide a window from screenshots (Windows 10 2004+). Harmless if unsupported."""
    try:
        fn = user32.SetWindowDisplayAffinity
        fn.argtypes = [wintypes.HWND, wintypes.DWORD]
        fn.restype = wintypes.BOOL
        fn(hwnd, WDA_EXCLUDEFROMCAPTURE)
    except (AttributeError, OSError):
        pass


# ---------------------------------------------------------------- background capture & input helpers

SW_SHOWNOACTIVATE = 4
PW_RENDERFULLCONTENT = 2
CWP_SKIPINVISIBLE, CWP_SKIPDISABLED, CWP_SKIPTRANSPARENT = 0x1, 0x2, 0x4


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


if IS_WINDOWS:
    gdi32 = ctypes.WinDLL("gdi32")
    HDC = ctypes.c_void_p
    _sig(user32.PrintWindow, [H, HDC, wintypes.UINT])
    _sig(user32.GetDC, [H], HDC)
    _sig(user32.ReleaseDC, [H, HDC], ctypes.c_int)
    _sig(user32.ScreenToClient, [H, ctypes.POINTER(wintypes.POINT)])
    _sig(user32.GetClientRect, [H, ctypes.POINTER(wintypes.RECT)])
    _sig(user32.ChildWindowFromPointEx, [H, wintypes.POINT, wintypes.UINT], H)
    _sig(user32.MapWindowPoints, [H, H, ctypes.POINTER(wintypes.POINT), wintypes.UINT], ctypes.c_int)
    _sig(user32.GetGUIThreadInfo, [wintypes.DWORD, ctypes.POINTER(GUITHREADINFO)])
    _sig(user32.PostMessageW, [H, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
    _sig(user32.GetLastInputInfo, [ctypes.POINTER(LASTINPUTINFO)])
    _sig(user32.SetCursorPos, [ctypes.c_int, ctypes.c_int])
    _sig(kernel32.GetTickCount, [], wintypes.DWORD)
    _sig(gdi32.CreateCompatibleDC, [HDC], HDC)
    _sig(gdi32.CreateCompatibleBitmap, [HDC, ctypes.c_int, ctypes.c_int], ctypes.c_void_p)
    _sig(gdi32.SelectObject, [HDC, ctypes.c_void_p], ctypes.c_void_p)
    _sig(gdi32.DeleteObject, [ctypes.c_void_p])
    _sig(gdi32.DeleteDC, [HDC])
    _sig(gdi32.GetDIBits, [HDC, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                           ctypes.c_void_p, wintypes.UINT], ctypes.c_int)


def raw_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    """GetWindowRect, which (unlike the visible frame) includes the invisible resize borders."""
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("Could not read the window position (was it closed?)")
    return rect.left, rect.top, rect.right, rect.bottom


def restore_without_focus(hwnd: int) -> None:
    """Un-minimize a window without bringing it in front of what the user is doing."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(0.3)


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def print_window_bgrx(hwnd: int) -> tuple[bytes, int, int]:
    """Render a window into a bitmap even if other windows cover it. Returns (BGRX bytes, width, height)."""
    l, t, r, b = raw_window_rect(hwnd)
    w, h = r - l, b - t
    if w <= 0 or h <= 0:
        raise ValueError("Window has no size")
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    try:
        old = gdi32.SelectObject(mem_dc, bmp)
        ok = user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT)
        gdi32.SelectObject(mem_dc, old)  # GetDIBits needs the bitmap deselected
        if not ok:
            raise OSError("PrintWindow failed")
        bmi = BITMAPINFOHEADER(biSize=ctypes.sizeof(BITMAPINFOHEADER), biWidth=w, biHeight=-h,
                               biPlanes=1, biBitCount=32, biCompression=0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0) != h:
            raise OSError("GetDIBits failed")
        return buf.raw, w, h
    finally:
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def focus_hwnd_of(hwnd: int) -> int:
    """The control that has keyboard focus inside a window's thread (works while it's in the background)."""
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    if tid and user32.GetGUIThreadInfo(tid, ctypes.byref(info)) and info.hwndFocus:
        return int(info.hwndFocus)
    return 0


def deepest_child_at(hwnd: int, sx: int, sy: int) -> tuple[int, int, int]:
    """(child hwnd, x, y in that child's client coordinates) for a screen point inside hwnd's client area."""
    pt = wintypes.POINT(int(sx), int(sy))
    user32.ScreenToClient(hwnd, ctypes.byref(pt))
    rc = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rc))
    if not (0 <= pt.x < rc.right and 0 <= pt.y < rc.bottom):
        raise ValueError("That point is on the window's title bar or border, which can't be clicked in "
                         "background mode")
    parent = hwnd
    for _ in range(32):
        child = user32.ChildWindowFromPointEx(parent, pt, CWP_SKIPINVISIBLE | CWP_SKIPTRANSPARENT)
        if not child or int(child) == int(parent):
            break
        user32.MapWindowPoints(parent, child, ctypes.byref(pt), 1)
        parent = int(child)
    return int(parent), pt.x, pt.y


def post(hwnd: int, msg: int, wparam: int = 0, lparam: int = 0) -> None:
    if not user32.PostMessageW(hwnd, msg, wparam, lparam):
        raise OSError(f"Couldn't deliver input to the window (error {ctypes.get_last_error()})")


def tick() -> int:
    return int(kernel32.GetTickCount())


def last_input_tick() -> int:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    user32.GetLastInputInfo(ctypes.byref(info))
    return int(info.dwTime)


def ms_since(t: int) -> int:
    return (tick() - t) & 0xFFFFFFFF


def is_dpi_unaware(hwnd: int) -> bool:
    """True for old apps that Windows bitmap-scales on high-DPI displays."""
    try:
        get_ctx = user32.GetWindowDpiAwarenessContext
        get_ctx.argtypes, get_ctx.restype = [wintypes.HWND], ctypes.c_void_p
        get_aw = user32.GetAwarenessFromDpiAwarenessContext
        get_aw.argtypes, get_aw.restype = [ctypes.c_void_p], ctypes.c_int
        return get_aw(get_ctx(hwnd)) == 0
    except (AttributeError, OSError):
        return False
