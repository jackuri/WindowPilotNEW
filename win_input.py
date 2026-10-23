"""Mouse and keyboard simulation via the Win32 SendInput API.

Key names follow the style Claude uses (xdotool-like): "Return", "ctrl+s", "alt+Tab", "Page_Down", "super".
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

import winapi

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x1, 0x2, 0x4
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
WHEEL_DELTA = 120
MAGIC = 0x57504C54  # tags our synthetic input ("WPLT")

BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN = 0x10, 0x11, 0x12, 0x5B
VK_RETURN, VK_TAB, VK_F12 = 0x0D, 0x09, 0x7B
MODIFIER_VKS = {VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN}

VK_NAMES: dict[str, int] = {
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "control_l": VK_CONTROL, "control_r": VK_CONTROL, "ctl": VK_CONTROL,
    "shift": VK_SHIFT, "shift_l": VK_SHIFT, "shift_r": VK_SHIFT,
    "alt": VK_MENU, "alt_l": VK_MENU, "alt_r": VK_MENU, "option": VK_MENU,
    "super": VK_LWIN, "super_l": VK_LWIN, "super_r": VK_LWIN, "win": VK_LWIN, "windows": VK_LWIN,
    "meta": VK_LWIN, "cmd": VK_LWIN, "command": VK_LWIN,
    "return": VK_RETURN, "enter": VK_RETURN, "kp_enter": VK_RETURN,
    "tab": VK_TAB, "iso_left_tab": VK_TAB, "space": 0x20,
    "escape": 0x1B, "esc": 0x1B, "backspace": 0x08, "back_space": 0x08,
    "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23,
    "page_up": 0x21, "pageup": 0x21, "prior": 0x21, "pgup": 0x21,
    "page_down": 0x22, "pagedown": 0x22, "next": 0x22, "pgdn": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "menu": 0x5D, "apps": 0x5D, "print": 0x2C, "printscreen": 0x2C, "pause": 0x13,
    "caps_lock": 0x14, "capslock": 0x14, "num_lock": 0x90, "scroll_lock": 0x91,
    "plus": 0xBB, "equal": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "semicolon": 0xBA, "grave": 0xC0, "bracketleft": 0xDB, "backslash": 0xDC, "bracketright": 0xDD,
    "apostrophe": 0xDE,
}
VK_NAMES.update({f"f{i}": 0x70 + i - 1 for i in range(1, 25)})
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0x2C}

# The stop hotkey (Ctrl+Shift+F12) must never be sent by the agent.
RESERVED_COMBO = {VK_CONTROL, VK_SHIFT, VK_F12}

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


if winapi.IS_WINDOWS:
    _u32 = winapi.user32
    _u32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _u32.SendInput.restype = wintypes.UINT
    _u32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _u32.MapVirtualKeyW.restype = wintypes.UINT
    _u32.VkKeyScanW.argtypes = [wintypes.WCHAR]
    _u32.VkKeyScanW.restype = ctypes.c_short
    _u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    _u32.SetCursorPos.restype = wintypes.BOOL


class InputBlocked(RuntimeError):
    pass


def _send(*inputs: INPUT) -> None:
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    if _u32.SendInput(n, arr, ctypes.sizeof(INPUT)) != n:
        raise InputBlocked("Windows blocked the simulated input. If the target app runs as administrator, "
                           "run WindowPilot as administrator too.")


def _mouse(flags: int, data: int = 0) -> INPUT:
    return INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(0, 0, data & 0xFFFFFFFF, flags, 0, MAGIC))


def _key(vk: int, up: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, _u32.MapVirtualKeyW(vk, 0), flags, 0, MAGIC))


def _unicode(unit: int, up: bool) -> INPUT:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, unit, flags, 0, MAGIC))


# ---------------------------------------------------------------- key parsing

def split_combo(spec: str) -> list[str]:
    spec = spec.strip()
    if spec == "+":
        return ["+"]
    if spec.endswith("++"):
        return [t for t in spec[:-2].split("+") if t] + ["+"]
    return [t for t in spec.split("+") if t.strip()]


def resolve_key(token: str) -> tuple[int, bool]:
    """Return (virtual-key code, needs_shift) for a key name or single character."""
    t = token.strip()
    low = t.lower()
    if low in VK_NAMES:
        return VK_NAMES[low], False
    if len(t) == 1:
        ch = low if t.isalpha() else t
        r = _u32.VkKeyScanW(ch)
        if r == -1:
            raise ValueError(f"Key {t!r} isn't on the current keyboard layout")
        return r & 0xFF, bool(r & 0x100)
    raise ValueError(f"Unknown key name: {token!r}")


def resolve_combo(spec: str) -> list[int]:
    tokens = split_combo(spec)
    if not tokens:
        raise ValueError("Empty key combination")
    vks: list[int] = []
    for i, tok in enumerate(tokens):
        vk, shift = resolve_key(tok)
        if shift and i == len(tokens) - 1 and VK_SHIFT not in vks:
            vks.append(VK_SHIFT)
        vks.append(vk)
    if RESERVED_COMBO <= set(vks):
        raise ValueError("Ctrl+Shift+F12 is reserved as WindowPilot's stop hotkey.")
    return vks


def parse_modifiers(text: str | None) -> list[int]:
    if not text:
        return []
    mods = []
    for tok in split_combo(text):
        vk = VK_NAMES.get(tok.strip().lower())
        if vk not in MODIFIER_VKS:
            raise ValueError(f"{tok!r} is not a modifier key")
        mods.append(vk)
    return mods


# ---------------------------------------------------------------- mouse

def move_to(x: int, y: int) -> None:
    _u32.SetCursorPos(int(x), int(y))
    time.sleep(0.03)


def _hold_mods(mods: list[int], up: bool) -> None:
    for vk in (reversed(mods) if up else mods):
        _send(_key(vk, up))


def click(button: str = "left", count: int = 1, mods: list[int] | None = None) -> None:
    down, up = BUTTONS[button]
    mods = mods or []
    _hold_mods(mods, False)
    try:
        for _ in range(count):
            _send(_mouse(down), _mouse(up))
            time.sleep(0.04)
    finally:
        _hold_mods(mods, True)


def mouse_button(down: bool, button: str = "left") -> None:
    _send(_mouse(BUTTONS[button][0 if down else 1]))


def drag(start: tuple[int, int], end: tuple[int, int], mods: list[int] | None = None, steps: int = 25) -> None:
    mods = mods or []
    move_to(*start)
    _hold_mods(mods, False)
    try:
        mouse_button(True)
        time.sleep(0.05)
        (x0, y0), (x1, y1) = start, end
        for i in range(1, steps + 1):
            _u32.SetCursorPos(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps))
            time.sleep(0.012)
        time.sleep(0.05)
        mouse_button(False)
    finally:
        _hold_mods(mods, True)


def scroll(direction: str, amount: int = 3, mods: list[int] | None = None) -> None:
    amount = max(1, min(int(amount), 50))
    sign = {"up": 1, "down": -1, "left": -1, "right": 1}[direction]
    flag = MOUSEEVENTF_WHEEL if direction in ("up", "down") else MOUSEEVENTF_HWHEEL
    mods = mods or []
    _hold_mods(mods, False)
    try:
        for _ in range(amount):
            _send(_mouse(flag, sign * WHEEL_DELTA))
            time.sleep(0.03)
    finally:
        _hold_mods(mods, True)


# ---------------------------------------------------------------- keyboard

def press_combo(spec: str, repeat: int = 1) -> None:
    vks = resolve_combo(spec)
    for _ in range(max(1, min(int(repeat), 100))):
        _send(*[_key(vk) for vk in vks], *[_key(vk, True) for vk in reversed(vks)])
        time.sleep(0.03)


def type_text(text: str, stop: threading.Event | None = None, delay: float = 0.006) -> None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in text:
        if stop is not None and stop.is_set():
            raise InterruptedError("Stopped by the user")
        if ch == "\n":
            _send(_key(VK_RETURN), _key(VK_RETURN, True))
        elif ch == "\t":
            _send(_key(VK_TAB), _key(VK_TAB, True))
        else:
            raw = ch.encode("utf-16-le")
            for i in range(0, len(raw), 2):
                unit = int.from_bytes(raw[i:i + 2], "little")
                _send(_unicode(unit, False), _unicode(unit, True))
        time.sleep(delay)


def hold_key(spec: str, duration: float, stop: threading.Event | None = None) -> None:
    vks = resolve_combo(spec)
    _send(*[_key(vk) for vk in vks])
    try:
        end = time.monotonic() + max(0.0, min(float(duration), 300.0))
        while time.monotonic() < end:
            if stop is not None and stop.is_set():
                raise InterruptedError("Stopped by the user")
            time.sleep(0.05)
    finally:
        _send(*[_key(vk, True) for vk in reversed(vks)])
