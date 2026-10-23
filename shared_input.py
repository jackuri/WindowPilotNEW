"""Ways for the AI to act without taking your mouse and keyboard away.

BackgroundInput: the AI's own virtual mouse/keyboard. Clicks and keys are posted straight to the target window as
window messages, so your real cursor and keyboard focus are never touched. Works with many classic Windows apps;
browsers, games and many modern apps ignore it.

InputBorrower: for everything else. Waits until you pause, briefly focuses the target, performs the actions with the
real input, then puts your cursor and active window back exactly as they were.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import win_input
import winapi

WM_KEYDOWN, WM_KEYUP, WM_CHAR = 0x0100, 0x0101, 0x0102
WM_MOUSEMOVE, WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x0200, 0x020A, 0x020E
MOUSE_MSGS = {  # button: (down, up, double-click)
    "left": (0x0201, 0x0202, 0x0203), "right": (0x0204, 0x0205, 0x0206), "middle": (0x0207, 0x0208, 0x0209),
}
MK = {"left": 0x0001, "right": 0x0002, "shift": 0x0004, "ctrl": 0x0008, "middle": 0x0010}
MOD_VK_TO_MK = {win_input.VK_SHIFT: MK["shift"], win_input.VK_CONTROL: MK["ctrl"]}
PACE = 0.012  # give the app time to process each posted message

# Windows has one real mouse and keyboard. Every job that needs them (take-over, share, or background fallback)
# holds this turn only while acting, so several jobs can think in parallel and take turns acting.
REAL_INPUT = threading.RLock()


def acquire_real_input(stop: threading.Event, status: Callable[[str], None]) -> None:
    told = False
    while not REAL_INPUT.acquire(timeout=0.2):
        if stop.is_set():
            raise InterruptedError("Stopped by the user")
        if not told:
            status("Waiting for my turn on the mouse/keyboard (another task is acting)…")
            told = True


class TakeoverLease:
    """Take-over mode: hold the real mouse/keyboard for one burst of actions, then let other jobs have a turn."""

    def __init__(self, hwnd: int, stop: threading.Event, status: Callable[[str], None]) -> None:
        self.hwnd, self.stop, self.status = hwnd, stop, status
        self.active = False

    def acquire(self) -> None:
        if not self.active:
            acquire_real_input(self.stop, self.status)
            self.active = True

    def release(self) -> None:
        if self.active:
            self.active = False
            REAL_INPUT.release()


class NeedsRealInput(Exception):
    """This action can't be done with background input; borrow the real mouse/keyboard instead."""


def _lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


class BackgroundInput:
    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd
        self.cursor: tuple[int, int] | None = None  # the AI's own (invisible) cursor, in screen pixels
        self.held: str | None = None                 # button held by left_mouse_down
        self._last_target = 0

    # ------------------------------------------------------------ mouse

    def _post_mouse(self, msg: int, sx: int, sy: int, wparam: int) -> int:
        child, cx, cy = winapi.deepest_child_at(self.hwnd, sx, sy)
        winapi.post(child, msg, wparam, _lparam(cx, cy))
        self._last_target = child
        time.sleep(PACE)
        return child

    def move(self, sx: int, sy: int) -> None:
        self.cursor = (sx, sy)
        held = MK[self.held] if self.held else 0
        self._post_mouse(WM_MOUSEMOVE, sx, sy, held)

    def click(self, sx: int, sy: int, button: str = "left", count: int = 1, mods: list[int] | None = None) -> None:
        extra = self._mods_to_mk(mods)
        down, up, dbl = MOUSE_MSGS[button]
        self.move(sx, sy)
        for i in range(count):
            self._post_mouse(dbl if i % 2 == 1 else down, sx, sy, MK[button] | extra)
            self._post_mouse(up, sx, sy, extra)
            time.sleep(0.03)

    def button(self, down: bool, button: str = "left") -> None:
        if self.cursor is None:
            raise ValueError("Move the mouse first (mouse_move) so I know where to press.")
        sx, sy = self.cursor
        if down:
            self._post_mouse(MOUSE_MSGS[button][0], sx, sy, MK[button])
            self.held = button
        else:
            self._post_mouse(MOUSE_MSGS[button][1], sx, sy, 0)
            self.held = None

    def drag(self, start: tuple[int, int], end: tuple[int, int], mods: list[int] | None = None,
             steps: int = 20) -> None:
        extra = self._mods_to_mk(mods)
        down, up, _ = MOUSE_MSGS["left"]
        child, cx0, cy0 = winapi.deepest_child_at(self.hwnd, *start)
        winapi.post(child, WM_MOUSEMOVE, extra, _lparam(cx0, cy0))
        winapi.post(child, down, MK["left"] | extra, _lparam(cx0, cy0))
        # Once the button is down the app usually "captures" the mouse, so keep sending moves to the same control,
        # in its own client coordinates.
        (x0, y0), (x1, y1) = start, end
        for i in range(1, steps + 1):
            x = round(x0 + (x1 - x0) * i / steps) - (x0 - cx0)
            y = round(y0 + (y1 - y0) * i / steps) - (y0 - cy0)
            winapi.post(child, WM_MOUSEMOVE, MK["left"] | extra, _lparam(x, y))
            time.sleep(PACE)
        winapi.post(child, up, extra, _lparam(x1 - (x0 - cx0), y1 - (y0 - cy0)))
        self.cursor = end
        time.sleep(PACE)

    def scroll(self, sx: int | None, sy: int | None, direction: str, amount: int,
               mods: list[int] | None = None) -> None:
        if sx is None:
            if self.cursor is None:
                raise ValueError("Give a coordinate to scroll at.")
            sx, sy = self.cursor
        self.cursor = (sx, sy)
        child, _, _ = winapi.deepest_child_at(self.hwnd, sx, sy)
        msg = WM_MOUSEWHEEL if direction in ("up", "down") else WM_MOUSEHWHEEL
        delta = {"up": 120, "down": -120, "right": 120, "left": -120}[direction]
        for _ in range(max(1, min(int(amount), 50))):
            # Wheel messages carry *screen* coordinates.
            winapi.post(child, msg, ((delta & 0xFFFF) << 16) | self._mods_to_mk(mods), _lparam(sx, sy))
            time.sleep(0.03)

    @staticmethod
    def _mods_to_mk(mods: list[int] | None) -> int:
        mk = 0
        for vk in mods or []:
            if vk not in MOD_VK_TO_MK:
                raise NeedsRealInput("alt/win-clicks need the real keyboard")
            mk |= MOD_VK_TO_MK[vk]
        return mk

    # ------------------------------------------------------------ keyboard

    def _focus_target(self) -> int:
        return winapi.focus_hwnd_of(self.hwnd) or self._last_target or self.hwnd

    def type_text(self, text: str, stop: threading.Event | None = None, delay: float = 0.003) -> None:
        target = self._focus_target()
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        for ch in text:
            if stop is not None and stop.is_set():
                raise InterruptedError("Stopped by the user")
            if ch == "\n":
                self._key(target, win_input.VK_RETURN)
            elif ch == "\t":
                self._key(target, win_input.VK_TAB)
            else:
                raw = ch.encode("utf-16-le")
                for i in range(0, len(raw), 2):
                    winapi.post(target, WM_CHAR, int.from_bytes(raw[i:i + 2], "little"), 1)
            time.sleep(delay)

    def press(self, spec: str, repeat: int = 1) -> None:
        vks = win_input.resolve_combo(spec)
        if any(vk in win_input.MODIFIER_VKS for vk in vks):
            # Apps check the real keyboard state for Ctrl/Alt/Shift, which posted messages can't fake.
            raise NeedsRealInput("shortcut keys need the real keyboard")
        target = self._focus_target()
        for _ in range(max(1, min(int(repeat), 100))):
            for vk in vks:
                self._key(target, vk)

    @staticmethod
    def _key(target: int, vk: int) -> None:
        scan = win_input._u32.MapVirtualKeyW(vk, 0)
        ext = 1 if vk in win_input.EXTENDED_VKS else 0
        base = 1 | (scan << 16) | (ext << 24)
        winapi.post(target, WM_KEYDOWN, vk, base)
        time.sleep(PACE)
        winapi.post(target, WM_KEYUP, vk, base | (1 << 30) | (1 << 31))
        time.sleep(PACE)


class InputBorrower:
    """Borrow the real mouse and keyboard in short bursts while the user pauses, then hand them back."""

    def __init__(self, hwnd: int, idle_seconds: float, stop: threading.Event,
                 status: Callable[[str], None]) -> None:
        self.hwnd, self.idle_ms, self.stop, self.status = hwnd, int(idle_seconds * 1000), stop, status
        self.active = False
        self._saved_fg = 0
        self._saved_cursor = (0, 0)
        self._inject_start: int | None = None
        self._inject_end = 0
        self._user_last = winapi.last_input_tick()

    def _user_idle_ms(self) -> int:
        """How long since the *user* last touched the mouse/keyboard (ignoring our own injected bursts)."""
        last = winapi.last_input_tick()
        if self._inject_start is not None:
            since_last = winapi.ms_since(last)
            inside_burst = (since_last <= winapi.ms_since(self._inject_start)
                            and since_last >= winapi.ms_since(self._inject_end) - 20)  # 20ms ≈ timer resolution
            if inside_burst:
                return winapi.ms_since(self._user_last)
        self._user_last = last
        return winapi.ms_since(last)

    def acquire(self) -> None:
        if self.active:
            return
        acquire_real_input(self.stop, self.status)
        waited = False
        while self._user_idle_ms() < self.idle_ms:
            if self.stop.is_set():
                REAL_INPUT.release()
                raise InterruptedError("Stopped by the user")
            if not waited:
                self.status("Waiting for you to pause…")
                waited = True
            time.sleep(0.1)
        self._user_last = winapi.last_input_tick()
        self._saved_fg = winapi.foreground()
        self._saved_cursor = winapi.cursor_pos()
        self._inject_start = winapi.tick()
        self._inject_end = self._inject_start
        self.active = True
        if not winapi.bring_to_front(self.hwnd, settle=0.05):
            self.release()
            raise RuntimeError("Couldn't bring the window forward for a moment.")

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        time.sleep(0.05)
        winapi.user32.SetCursorPos(*self._saved_cursor)
        fg = self._saved_fg
        try:
            if fg and fg != self.hwnd and winapi.is_window(fg):
                winapi.bring_to_front(fg, settle=0.0)
        finally:
            self._inject_end = winapi.tick()
            REAL_INPUT.release()
