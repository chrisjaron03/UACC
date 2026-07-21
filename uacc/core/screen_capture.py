"""
Screen Capture — fast, cross-platform screenshot and region cropping.

Uses `mss` for low-latency capture (~15 ms for a full 1920×1080 frame).
"""

from __future__ import annotations

import io
from typing import Optional, Tuple

import mss
import mss.tools
from PIL import Image

# Reuse a single mss instance for the lifetime of the process.
_sct: Optional[mss.mss] = None


def _get_sct() -> mss.mss:
    global _sct
    if _sct is None:
        _sct = mss.mss()
    return _sct


def capture_full(monitor_index: int = 1) -> Image.Image:
    """Capture the entire screen of a given monitor.

    Args:
        monitor_index: 1-based monitor index (1 = primary).

    Returns:
        PIL Image in RGB mode.
    """
    sct = _get_sct()
    mon = sct.monitors[monitor_index]
    raw = sct.grab(mon)
    # mss returns BGRA; convert via PIL.
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img


def capture_region(x: int, y: int, width: int, height: int) -> Image.Image:
    """Capture a rectangular region of the screen.

    Args:
        x, y: Top-left corner (absolute screen coordinates).
        width, height: Size in pixels.

    Returns:
        PIL Image in RGB mode.
    """
    sct = _get_sct()
    region = {"left": x, "top": y, "width": width, "height": height}
    raw = sct.grab(region)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    return img


def capture_around(
    cx: int, cy: int, radius: int = 100, monitor_index: int = 1
) -> Image.Image:
    """Capture a square region centred on (cx, cy).

    Useful for the verification layer — zoom into a click target.

    Args:
        cx, cy: Centre point.
        radius: Half-width of the square crop.
        monitor_index: Monitor to clamp bounds against.

    Returns:
        PIL Image in RGB mode.
    """
    sct = _get_sct()
    mon = sct.monitors[monitor_index]
    x = max(0, cx - radius)
    y = max(0, cy - radius)
    w = min(radius * 2, mon["width"] - x)
    h = min(radius * 2, mon["height"] - y)
    return capture_region(x, y, w, h)


def get_screen_size(monitor_index: int = 1) -> Tuple[int, int]:
    """Return (width, height) of the given monitor."""
    sct = _get_sct()
    mon = sct.monitors[monitor_index]
    return mon["width"], mon["height"]


def list_monitors() -> list[dict]:
    """Return info about all connected monitors.

    Index 0 is the virtual "all-in-one" union; indices 1+ are individual monitors.
    """
    sct = _get_sct()
    monitors = []
    for i, mon in enumerate(sct.monitors):
        if i == 0:
            continue  # Skip the virtual "combined" monitor
        monitors.append({
            "index": i,
            "left": mon["left"],
            "top": mon["top"],
            "width": mon["width"],
            "height": mon["height"],
        })
    return monitors


def image_to_bytes(img: Image.Image, fmt: str = "PNG", quality: int = 85) -> bytes:
    """Serialise a PIL Image to bytes (useful for API calls)."""
    buf = io.BytesIO()
    save_kwargs: dict = {"format": fmt}
    if fmt.upper() == "JPEG":
        save_kwargs["quality"] = quality
    img.save(buf, **save_kwargs)
    return buf.getvalue()


def image_to_base64(img: Image.Image, fmt: str = "PNG", quality: int = 85) -> str:
    """Serialise a PIL Image to a base64-encoded string."""
    import base64

    raw = image_to_bytes(img, fmt=fmt, quality=quality)
    return base64.b64encode(raw).decode("ascii")
