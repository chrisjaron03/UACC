"""
Grid Encoder — overlay coordinate grids and Set-of-Mark badges on screenshots
for vision models to achieve pixel-precise UI targeting.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from uacc.config import config
from uacc.core.accessibility import UIElement


# ── Grid Overlay ─────────────────────────────────────────────

def overlay_grid(
    image: Image.Image,
    mode: Optional[str] = None,
    color: Tuple[int, int, int, int] = (255, 255, 255, 60),
    label_color: Tuple[int, int, int, int] = (255, 255, 255, 140),
) -> Image.Image:
    """Draw a labelled coordinate grid over a screenshot.

    Args:
        image: Source screenshot (PIL Image).
        mode: Grid density — "coarse", "medium", "fine", "micro".
              Defaults to config.uacc.grid_mode.
        color: RGBA colour for grid lines.
        label_color: RGBA colour for cell labels.

    Returns:
        New PIL Image with grid overlay (RGBA).
    """
    mode = mode or config.uacc.grid_mode
    cols, rows = config.uacc.GRID_SIZES.get(mode, (48, 27))

    img = image.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    w, h = img.size
    cell_w = w / cols
    cell_h = h / rows

    # Draw vertical lines
    for c in range(cols + 1):
        x = int(c * cell_w)
        draw.line([(x, 0), (x, h)], fill=color, width=1)

    # Draw horizontal lines
    for r in range(rows + 1):
        y = int(r * cell_h)
        draw.line([(0, y), (w, y)], fill=color, width=1)

    # Label cells (only for coarse/medium — fine/micro would be too cluttered)
    if mode in ("coarse", "medium"):
        font = _get_font(max(8, int(min(cell_w, cell_h) * 0.35)))
        for r in range(rows):
            for c in range(cols):
                label = _cell_label(c, r)
                cx = int(c * cell_w + cell_w * 0.5)
                cy = int(r * cell_h + cell_h * 0.5)
                bbox = draw.textbbox((cx, cy), label, font=font, anchor="mm")
                # Semi-transparent background for readability
                pad = 2
                draw.rectangle(
                    [bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad],
                    fill=(0, 0, 0, 80),
                )
                draw.text((cx, cy), label, fill=label_color, font=font, anchor="mm")

    result = Image.alpha_composite(img, overlay)
    return result


def _cell_label(col: int, row: int) -> str:
    """Generate a chess-style cell label: A1, B2, AA3, etc."""
    letters = ""
    c = col
    while True:
        letters = chr(65 + c % 26) + letters
        c = c // 26 - 1
        if c < 0:
            break
    return f"{letters}{row + 1}"


def grid_cell_to_pixel(
    col: int,
    row: int,
    screen_w: int,
    screen_h: int,
    mode: Optional[str] = None,
) -> Tuple[int, int]:
    """Convert a grid cell (col, row) to the pixel centre of that cell."""
    mode = mode or config.uacc.grid_mode
    cols, rows = config.uacc.GRID_SIZES.get(mode, (48, 27))
    cell_w = screen_w / cols
    cell_h = screen_h / rows
    return (int((col + 0.5) * cell_w), int((row + 0.5) * cell_h))


# ── Set-of-Mark Badges ──────────────────────────────────────

def overlay_markers(
    image: Image.Image,
    elements: List[UIElement],
    max_markers: int = 80,
    badge_radius: int = 12,
    badge_color: Tuple[int, int, int] = (230, 60, 60),
    text_color: Tuple[int, int, int] = (255, 255, 255),
) -> Image.Image:
    """Place numbered circular badges on interactive UI elements.

    Args:
        image: Source screenshot.
        elements: UI elements (only clickable/editable ones get badges).
        max_markers: Maximum number of badges to draw.
        badge_radius: Radius of each badge circle.
        badge_color: RGB fill colour for badges.
        text_color: RGB colour for badge numbers.

    Returns:
        New PIL Image with badges overlaid.
    """
    img = image.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Filter to interactive elements only
    interactive = [
        el for el in elements
        if el.clickable or el.editable or el.expandable
    ][:max_markers]

    font = _get_font(max(9, badge_radius))

    for idx, el in enumerate(interactive, start=1):
        cx, cy = el.center
        r = badge_radius

        # Draw badge circle with slight shadow
        draw.ellipse(
            [cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1],
            fill=(0, 0, 0, 120),
        )
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*badge_color, 230),
            outline=(255, 255, 255, 200),
            width=1,
        )

        # Draw number
        label = str(idx)
        draw.text(
            (cx, cy), label,
            fill=(*text_color, 255),
            font=font,
            anchor="mm",
        )

    result = Image.alpha_composite(img, overlay)
    return result


def build_marker_legend(elements: List[UIElement], max_markers: int = 80) -> str:
    """Build a text legend mapping badge numbers to element info.

    Sent alongside the marked screenshot so the model knows what each
    number represents.

    Returns:
        Multi-line string like:
            [1] button "File"  at (22, 15)
            [2] menu_item "Edit"  at (67, 15)
    """
    interactive = [
        el for el in elements
        if el.clickable or el.editable or el.expandable
    ][:max_markers]

    lines = []
    for idx, el in enumerate(interactive, start=1):
        name = el.name[:50] if el.name else "(unnamed)"
        lines.append(
            f'[{idx}] {el.control_type:<14} "{name}"  at ({el.center[0]}, {el.center[1]})'
        )
    return "\n".join(lines)


# ── Progressive Zoom ─────────────────────────────────────────

def zoom_region(
    image: Image.Image,
    cx: int,
    cy: int,
    zoom_level: int = 2,
    output_size: Tuple[int, int] = (800, 600),
) -> Image.Image:
    """Crop and upscale a region around (cx, cy) for fine-grained inspection.

    Args:
        image: Full screenshot.
        cx, cy: Centre point to zoom into.
        zoom_level: 2 = 2× zoom (crop half the area), 4 = 4× zoom, etc.
        output_size: Size of the returned image.

    Returns:
        Zoomed-in PIL Image.
    """
    w, h = image.size
    crop_w = output_size[0] // zoom_level
    crop_h = output_size[1] // zoom_level

    x1 = max(0, cx - crop_w // 2)
    y1 = max(0, cy - crop_h // 2)
    x2 = min(w, x1 + crop_w)
    y2 = min(h, y1 + crop_h)

    cropped = image.crop((x1, y1, x2, y2))
    zoomed = cropped.resize(output_size, Image.LANCZOS)
    return zoomed


# ── Font helper ──────────────────────────────────────────────

def _get_font(size: int = 12) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try to load a clean font; fall back to PIL default."""
    font_candidates = [
        "consola.ttf",      # Windows Consolas
        "arial.ttf",        # Windows Arial
        "DejaVuSans.ttf",   # Linux
        "Helvetica.ttf",    # macOS
    ]
    for name in font_candidates:
        try:
            return ImageFont.truetype(name, size)
        except (OSError, IOError):
            continue
    return ImageFont.load_default()
