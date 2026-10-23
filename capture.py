"""Window screenshots, sized to fit Claude's image limits, with the geometry needed to map coordinates back."""
from __future__ import annotations

import base64
import io
import math
import threading
from dataclasses import dataclass

import mss
from PIL import Image

import winapi

# ~1.15 MP / 1568 px keeps each screenshot around 1.5k tokens; `zoom` provides full detail when needed.
MAX_LONG_EDGE = 1568
MAX_PIXELS = 1_150_000


def fit_scale(width: int, height: int, max_long: int = MAX_LONG_EDGE, max_pixels: int = MAX_PIXELS) -> float:
    if width <= 0 or height <= 0:
        return 1.0
    return min(1.0, max_long / max(width, height), math.sqrt(max_pixels / (width * height)))


def fit_image(img: Image.Image, max_long: int = MAX_LONG_EDGE, max_pixels: int = MAX_PIXELS) -> Image.Image:
    s = fit_scale(img.width, img.height, max_long, max_pixels)
    if s >= 1.0:
        return img
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS)


def image_to_b64(img: Image.Image, fmt: str = "JPEG") -> str:
    buf = io.BytesIO()
    if fmt.upper() == "JPEG":
        img.convert("RGB").save(buf, format="JPEG", quality=80)
    else:
        img.save(buf, format="PNG", compress_level=1)  # fast; optimize=True is very slow on big images
    return base64.b64encode(buf.getvalue()).decode("ascii")


def image_block(img: Image.Image, fmt: str = "JPEG") -> dict:
    media = "image/jpeg" if fmt.upper() == "JPEG" else "image/png"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": image_to_b64(img, fmt)}}


@dataclass
class WindowShot:
    image: Image.Image   # what Claude sees (possibly downscaled)
    left: int            # window rect on screen, physical pixels
    top: int
    width: int
    height: int

    @property
    def iw(self) -> int:
        return self.image.width

    @property
    def ih(self) -> int:
        return self.image.height

    def from_screen(self, x: int, y: int) -> tuple[int, int]:
        return round((x - self.left) * self.iw / self.width), round((y - self.top) * self.ih / self.height)


class Capturer:
    def __init__(self) -> None:
        self._tls = threading.local()  # mss handles aren't shareable across threads

    def _mss(self):
        sct = getattr(self._tls, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._tls.sct = sct
        return sct

    def grab(self, left: int, top: int, width: int, height: int) -> Image.Image:
        if width <= 0 or height <= 0:
            raise ValueError("Nothing to capture (window has no size — is it minimized?)")
        shot = self._mss().grab({"left": int(left), "top": int(top), "width": int(width), "height": int(height)})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def window_image(self, hwnd: int, method: str = "screen") -> tuple[Image.Image, int, int, int, int]:
        """Full-resolution image of the window's visible frame plus its (left, top, width, height).

        method "screen": copy the window's area from the screen (it must be in front).
        method "print":  ask Windows to render the window, so it works even when covered by other windows.
        """
        left, top, right, bottom = winapi.get_window_rect(hwnd)
        w, h = right - left, bottom - top
        if method == "screen":
            return self.grab(left, top, w, h), left, top, w, h
        raw, rw, rh = winapi.print_window_bgrx(hwnd)
        full = Image.frombytes("RGB", (rw, rh), raw, "raw", "BGRX")
        if winapi.is_dpi_unaware(hwnd):
            full = _fix_dpi_virtualized(full)
        rl, rt, _, _ = winapi.raw_window_rect(hwnd)
        img = full.crop((left - rl, top - rt, left - rl + w, top - rt + h))
        if is_blank(img):
            raise RuntimeError("This app can't be captured in the background (it renders black)")
        return img, left, top, w, h

    def grab_window(self, hwnd: int, max_long: int = MAX_LONG_EDGE, max_pixels: int = MAX_PIXELS,
                    method: str = "screen") -> WindowShot:
        full, left, top, w, h = self.window_image(hwnd, method)
        return WindowShot(fit_image(full, max_long, max_pixels), left, top, w, h)


def is_blank(img: Image.Image) -> bool:
    lo, hi = img.convert("L").getextrema()
    return hi - lo < 3


def _fix_dpi_virtualized(img: Image.Image) -> Image.Image:
    """Old DPI-unaware apps on scaled displays can render at their smaller logical size in the top-left of the
    bitmap. If everything right of / below a common logical size is empty, stretch the content to fill."""
    w, h = img.size
    for scale in (3.0, 2.5, 2.25, 2.0, 1.75, 1.5, 1.25):  # tightest fit first
        lw, lh = round(w / scale), round(h / scale)
        if lw < 50 or lh < 50:
            continue
        right, bottom = img.crop((lw + 2, 0, w, h)), img.crop((0, lh + 2, w, h))
        inner = img.crop((0, 0, lw, lh))
        if right.convert("L").getextrema()[1] < 8 and bottom.convert("L").getextrema()[1] < 8 \
                and inner.convert("L").getextrema()[1] >= 8:
            return inner.resize((w, h), Image.LANCZOS)
    return img
