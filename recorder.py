"""Watches the user work in the target window: logs clicks, drags, scrolls and typing, and captures
screenshots at each meaningful moment so Claude can later understand and repeat the workflow."""
from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageStat
from pynput import keyboard, mouse

import winapi
from capture import Capturer, fit_scale

try:  # optional: names the button/field under each click ("Button 'Save'")
    import uiautomation as uia
except Exception:  # pragma: no cover
    uia = None

FRAME_LONG_EDGE = 1280
FRAME_PIXELS = 1_000_000
PERIODIC_SECONDS = 3.0
TYPING_IDLE_FLUSH = 1.5
MAX_FRAMES = 300

MOD_NAMES = {
    keyboard.Key.ctrl: "ctrl", keyboard.Key.ctrl_l: "ctrl", keyboard.Key.ctrl_r: "ctrl",
    keyboard.Key.alt: "alt", keyboard.Key.alt_l: "alt", keyboard.Key.alt_r: "alt",
    keyboard.Key.shift: "shift", keyboard.Key.shift_l: "shift", keyboard.Key.shift_r: "shift",
    keyboard.Key.cmd: "win", keyboard.Key.cmd_l: "win", keyboard.Key.cmd_r: "win",
}
if hasattr(keyboard.Key, "alt_gr"):
    MOD_NAMES[keyboard.Key.alt_gr] = "alt"
MOD_ORDER = ["ctrl", "alt", "shift", "win"]


@dataclass
class FrameRec:
    t: float
    image: Image.Image
    note: str
    marker: tuple[int, int] | None = None
    path: str = ""


@dataclass
class Recording:
    title: str
    exe: str
    duration: float
    events: list[dict] = field(default_factory=list)
    frames: list[FrameRec] = field(default_factory=list)
    folder: Path | None = None

    def event_log(self) -> str:
        lines = []
        for ev in self.events:
            ref = f"  [frame {ev['frame'] + 1}]" if ev.get("frame") is not None else ""
            lines.append(f"{ev['t']:7.1f}s  {ev['text']}{ref}")
        return "\n".join(lines) or "(no actions recorded)"


class Recorder:
    def __init__(self, capturer: Capturer, on_update=None) -> None:
        self.capturer = capturer
        self.on_update = on_update or (lambda events, frames: None)
        self.active = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ lifecycle

    def start(self, hwnd: int, title: str, exe: str, folder: Path) -> None:
        self.hwnd, self.title, self.exe, self.folder = hwnd, title, exe, folder
        self.events: list[dict] = []
        self.frames: list[FrameRec] = []
        self._typed, self._typed_t, self._last_key_t = "", 0.0, 0.0
        self._mods: set[str] = set()
        self._press = None
        self._last_click = None
        self._scroll = None
        self._last_frame_t = -999.0
        self._last_thumb = None
        self._q: queue.Queue = queue.Queue()
        self.t0 = time.monotonic()
        self.active = True
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()
        threading.Thread(target=self._tick, daemon=True).start()
        self._ml = mouse.Listener(on_click=self._on_click, on_scroll=self._on_scroll)
        self._kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._ml.start()
        self._kl.start()
        self._request_frame("Recording started (initial state)")

    def stop(self) -> Recording:
        self.active = False
        for listener in (self._ml, self._kl):
            try:
                listener.stop()
            except Exception:
                pass
        with self._lock:
            self._flush_typed(frame=False)
            self._flush_scroll()
        self._request_frame("Recording stopped (final state)")
        self._q.put(None)
        self._worker.join(timeout=10)
        rec = Recording(self.title, self.exe, round(self._now(), 1), list(self.events), list(self.frames), self.folder)
        self._save(rec)
        return rec

    # ------------------------------------------------------------ helpers

    def _now(self) -> float:
        return time.monotonic() - self.t0

    def _inside(self, x: int, y: int) -> bool:
        return winapi.window_at(x, y) == self.hwnd

    def _rel(self, x: int, y: int) -> str:
        """Express a screen point in the coordinate space of the recorded frames."""
        try:
            l, t, r, b = winapi.get_window_rect(self.hwnd)
            s = fit_scale(r - l, b - t, FRAME_LONG_EDGE, FRAME_PIXELS)
            return f"({round((x - l) * s)}, {round((y - t) * s)})"
        except OSError:
            return "(?, ?)"

    def _add_event(self, t: float, text: str) -> dict:
        ev = {"t": round(t, 2), "text": text, "frame": None}
        self.events.append(ev)
        self.on_update(len(self.events), len(self.frames))
        return ev

    def _request_frame(self, note: str | None, ev: dict | None = None, click: tuple[int, int] | None = None) -> None:
        self._q.put((self._now(), note, ev, click))

    def _flush_typed(self, frame: bool = True) -> None:
        if self._typed:
            ev = self._add_event(self._typed_t, f'typed "{self._typed}"')
            self._typed = ""
            if frame:
                self._request_frame("after typing", ev)

    def _flush_scroll(self) -> None:
        if self._scroll:
            t, pos, dy, dx = self._scroll
            parts = []
            if dy:
                parts.append(f"{'up' if dy > 0 else 'down'} {abs(dy)} notch(es)")
            if dx:
                parts.append(f"{'right' if dx > 0 else 'left'} {abs(dx)} notch(es)")
            ev = self._add_event(t, f"scrolled {' and '.join(parts)} at {pos}")
            self._request_frame("after scrolling", ev)
            self._scroll = None

    def _mods_prefix(self) -> str:
        mods = [m for m in MOD_ORDER if m in self._mods]
        return "+".join(mods) + "+" if mods else ""

    # ------------------------------------------------------------ background threads

    def _work(self) -> None:
        ctx = None
        if uia is not None:
            try:
                ctx = uia.UIAutomationInitializerInThread(debug=False)
                ctx.__enter__()
            except Exception:
                ctx = None
        try:
            while True:
                item = self._q.get()
                if item is None:
                    break
                self._capture(*item, use_uia=ctx is not None)
        finally:
            if ctx is not None:
                try:
                    ctx.__exit__(None, None, None)
                except Exception:
                    pass

    def _capture(self, t, note, ev, click, use_uia: bool) -> None:
        if len(self.frames) >= MAX_FRAMES:
            return
        try:
            shot = self.capturer.grab_window(self.hwnd, FRAME_LONG_EDGE, FRAME_PIXELS)
        except Exception:
            return
        img = shot.image
        thumb = img.convert("L").resize((96, 64))
        periodic = note is None
        if periodic and self._last_thumb is not None:
            diff = ImageStat.Stat(ImageChops.difference(thumb, self._last_thumb)).mean[0]
            if diff < 2.0:
                return
        marker = None
        if click:
            mx, my = shot.from_screen(*click)
            if 0 <= mx < img.width and 0 <= my < img.height:
                marker = (mx, my)
                img = img.copy()
                d = ImageDraw.Draw(img)
                for r, w in ((16, 4), (4, 4)):
                    d.ellipse((mx - r, my - r, mx + r, my + r), outline=(255, 0, 0), width=w)
        with self._lock:
            self.frames.append(FrameRec(round(t, 2), img, note or "screen changed", marker))
            if ev is not None:
                ev["frame"] = len(self.frames) - 1
            self._last_frame_t = t
            self._last_thumb = thumb
        if click and use_uia and ev is not None:
            desc = self._describe_element(*click)
            if desc:
                with self._lock:
                    ev["text"] += f" on {desc}"
        self.on_update(len(self.events), len(self.frames))

    @staticmethod
    def _describe_element(x: int, y: int) -> str:
        try:
            ctrl = uia.ControlFromPoint(x, y)
            if not ctrl:
                return ""
            name = (ctrl.Name or "").strip().replace("\n", " ")
            kind = ctrl.ControlTypeName.replace("Control", "")
            return f"{kind} '{name[:60]}'" if name else kind
        except Exception:
            return ""

    def _tick(self) -> None:
        while self.active:
            time.sleep(0.5)
            now = self._now()
            with self._lock:
                if self._typed and now - self._last_key_t > TYPING_IDLE_FLUSH:
                    self._flush_typed()
                if self._scroll and now - self._scroll[0] > 1.0:
                    self._flush_scroll()
                due = now - self._last_frame_t > PERIODIC_SECONDS
            if due and winapi.root_owner(winapi.foreground()) == self.hwnd:
                self._request_frame(None)

    # ------------------------------------------------------------ input hooks (keep these fast!)

    def _on_click(self, x, y, button, pressed) -> None:
        if not self.active:
            return
        t = self._now()
        bname = getattr(button, "name", "left")
        with self._lock:
            if pressed:
                if not self._inside(x, y):
                    self._press = None
                    return
                self._flush_typed()
                self._flush_scroll()
                lc = self._last_click
                if lc and lc[3] == bname and t - lc[2] < 0.45 and math.hypot(x - lc[0], y - lc[1]) < 6:
                    lc[4]["text"] = lc[4]["text"].replace(" click", " double-click", 1)
                    self._last_click = None
                    self._press = (x, y, t, bname, None)
                    return
                ev = self._add_event(t, f"{self._mods_prefix()}{bname} click at {self._rel(x, y)}")
                self._press = (x, y, t, bname, ev)
                self._request_frame(f"{bname} click", ev, (x, y))
                return
            if not self._press:
                return
            x0, y0, t0, b, ev = self._press
            self._press = None
            if ev is None:
                return
            if math.hypot(x - x0, y - y0) > 10:
                ev["text"] = f"{self._mods_prefix()}{b}-drag from {self._rel(x0, y0)} to {self._rel(x, y)}"
                self._request_frame("after drag", None)
                self._last_click = None
            else:
                self._last_click = (x0, y0, t0, b, ev)

    def _on_scroll(self, x, y, dx, dy) -> None:
        if not self.active or not self._inside(x, y):
            return
        with self._lock:
            t = self._now()
            if self._scroll:
                st, pos, ay, ax = self._scroll
                self._scroll = (t, pos, ay + dy, ax + dx)
            else:
                self._flush_typed()
                self._scroll = (t, self._rel(x, y), dy, dx)

    @staticmethod
    def _key_name(key) -> str:
        vk = getattr(key, "vk", None)
        if vk is not None:
            if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
                return chr(vk).lower()
            if 0x70 <= vk <= 0x87:
                return f"f{vk - 0x6F}"
        name = getattr(key, "name", None)
        if name:
            return name
        ch = getattr(key, "char", None)
        return ch if ch and ch.isprintable() else str(key)

    def _on_press(self, key) -> None:
        if not self.active:
            return
        mod = MOD_NAMES.get(key)
        if mod:
            self._mods.add(mod)
            return
        if winapi.root_owner(winapi.foreground()) != self.hwnd:
            return
        t = self._now()
        with self._lock:
            self._flush_scroll()
            ch = getattr(key, "char", None)
            if self._mods - {"shift"}:
                self._flush_typed(frame=False)
                ev = self._add_event(t, f"pressed {self._mods_prefix()}{self._key_name(key)}")
                self._request_frame("after shortcut", ev)
            elif ch and ch.isprintable():
                if not self._typed:
                    self._typed_t = t
                self._typed += ch
                self._last_key_t = t
            else:
                name = self._key_name(key)
                if name == "space":
                    if not self._typed:
                        self._typed_t = t
                    self._typed += " "
                    self._last_key_t = t
                elif name == "backspace" and self._typed:
                    self._typed = self._typed[:-1]
                    self._last_key_t = t
                else:
                    self._flush_typed(frame=False)
                    ev = self._add_event(t, f"pressed {self._mods_prefix()}{name}")
                    if name in ("enter", "tab", "esc", "delete"):
                        self._request_frame(f"after {name}", ev)

    def _on_release(self, key) -> None:
        mod = MOD_NAMES.get(key)
        if mod:
            self._mods.discard(mod)

    # ------------------------------------------------------------ persistence

    def _save(self, rec: Recording) -> None:
        if not rec.folder:
            return
        try:
            frames_dir = rec.folder / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            for i, fr in enumerate(rec.frames):
                fr.path = str(frames_dir / f"frame_{i + 1:03d}.png")
                fr.image.save(fr.path)
            (rec.folder / "events.json").write_text(json.dumps(rec.events, indent=2), encoding="utf-8")
            (rec.folder / "events.txt").write_text(rec.event_log(), encoding="utf-8")
        except OSError:
            pass
