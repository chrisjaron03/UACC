"""
Screen Diff Engine — fast pixel-level comparison between consecutive
screenshots to detect what changed after an action.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class ChangedRegion:
    """A contiguous region of the screen that changed between two frames."""

    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    pixel_count: int
    change_intensity: float  # Average pixel difference in this region (0–255)

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass
class DiffResult:
    """Result of comparing two screenshots."""

    changed: bool
    changed_percentage: float  # 0.0 – 100.0
    regions: List[ChangedRegion]
    total_pixels_changed: int

    @property
    def summary(self) -> str:
        if not self.changed:
            return "No change detected"
        region_strs = [
            f"  region at ({r.bounds[0]},{r.bounds[1]})–({r.bounds[2]},{r.bounds[3]})  "
            f"{r.width}×{r.height}px  intensity={r.change_intensity:.1f}"
            for r in self.regions
        ]
        return (
            f"Changed: {self.changed_percentage:.1f}% of screen  "
            f"({self.total_pixels_changed} pixels)\n"
            + "\n".join(region_strs)
        )


def has_changed(
    before: Image.Image,
    after: Image.Image,
    threshold: float = 0.5,
) -> bool:
    """Quick check: did the screen change meaningfully?

    Args:
        before, after: Consecutive screenshots.
        threshold: Minimum % of pixels that must differ.

    Returns:
        True if the screen changed above the threshold.
    """
    a = np.array(before.convert("RGB"), dtype=np.int16)
    b = np.array(after.convert("RGB"), dtype=np.int16)
    if a.shape != b.shape:
        return True
    diff = np.abs(a - b).mean(axis=2)  # Average across RGB channels
    changed_pixels = (diff > 15).sum()
    total_pixels = diff.size
    pct = (changed_pixels / total_pixels) * 100
    return pct > threshold


def compute_diff(
    before: Image.Image,
    after: Image.Image,
    pixel_threshold: int = 20,
    min_region_area: int = 100,
    merge_distance: int = 30,
) -> DiffResult:
    """Compute a detailed diff between two screenshots.

    Args:
        before, after: Consecutive screenshots (same size).
        pixel_threshold: Minimum per-channel difference to count as changed.
        min_region_area: Ignore regions smaller than this (noise filtering).
        merge_distance: Merge nearby changed regions within this distance.

    Returns:
        DiffResult with list of changed regions.
    """
    a = np.array(before.convert("RGB"), dtype=np.int16)
    b = np.array(after.convert("RGB"), dtype=np.int16)

    if a.shape != b.shape:
        logger.warning("Screenshots differ in size — treating as full change")
        h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
        return DiffResult(
            changed=True,
            changed_percentage=100.0,
            regions=[ChangedRegion((0, 0, w, h), w * h, 255.0)],
            total_pixels_changed=w * h,
        )

    # Per-pixel difference (average across RGB)
    diff = np.abs(a - b).mean(axis=2)
    mask = diff > pixel_threshold

    total_pixels = mask.size
    changed_pixels = int(mask.sum())
    pct = (changed_pixels / total_pixels) * 100

    if changed_pixels == 0:
        return DiffResult(
            changed=False,
            changed_percentage=0.0,
            regions=[],
            total_pixels_changed=0,
        )

    # Find contiguous changed regions using connected components
    regions = _find_regions(mask, diff, min_region_area, merge_distance)

    return DiffResult(
        changed=True,
        changed_percentage=round(pct, 2),
        regions=regions,
        total_pixels_changed=changed_pixels,
    )


def _find_regions(
    mask: np.ndarray,
    diff: np.ndarray,
    min_area: int,
    merge_dist: int,
) -> List[ChangedRegion]:
    """Find bounding boxes of changed regions using connected components."""
    try:
        from skimage.measure import label, regionprops
    except ImportError:
        # Fallback: return a single bounding box of all changes
        ys, xs = np.where(mask)
        if len(ys) == 0:
            return []
        bounds = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        intensity = float(diff[mask].mean())
        return [ChangedRegion(bounds, int(mask.sum()), intensity)]

    labeled = label(mask.astype(np.uint8))
    props = regionprops(labeled)

    raw_regions: List[ChangedRegion] = []
    for prop in props:
        if prop.area < min_area:
            continue
        # regionprops gives (min_row, min_col, max_row, max_col)
        y1, x1, y2, x2 = prop.bbox
        region_diff = diff[y1:y2, x1:x2]
        region_mask = mask[y1:y2, x1:x2]
        intensity = float(region_diff[region_mask].mean()) if region_mask.any() else 0.0
        raw_regions.append(
            ChangedRegion(
                bounds=(x1, y1, x2, y2),
                pixel_count=int(prop.area),
                change_intensity=round(intensity, 1),
            )
        )

    # Merge nearby regions
    merged = _merge_regions(raw_regions, merge_dist)
    return merged


def _merge_regions(regions: List[ChangedRegion], distance: int) -> List[ChangedRegion]:
    """Merge regions whose bounding boxes are within `distance` pixels."""
    if not regions:
        return []

    merged: List[ChangedRegion] = []
    used = [False] * len(regions)

    for i, r1 in enumerate(regions):
        if used[i]:
            continue
        x1, y1, x2, y2 = r1.bounds
        total_px = r1.pixel_count
        total_intensity = r1.change_intensity * r1.pixel_count

        for j in range(i + 1, len(regions)):
            if used[j]:
                continue
            r2 = regions[j]
            # Check if within merge distance
            if (
                r2.bounds[0] <= x2 + distance
                and r2.bounds[2] >= x1 - distance
                and r2.bounds[1] <= y2 + distance
                and r2.bounds[3] >= y1 - distance
            ):
                x1 = min(x1, r2.bounds[0])
                y1 = min(y1, r2.bounds[1])
                x2 = max(x2, r2.bounds[2])
                y2 = max(y2, r2.bounds[3])
                total_px += r2.pixel_count
                total_intensity += r2.change_intensity * r2.pixel_count
                used[j] = True

        avg_intensity = total_intensity / max(1, total_px)
        merged.append(
            ChangedRegion(
                bounds=(x1, y1, x2, y2),
                pixel_count=total_px,
                change_intensity=round(avg_intensity, 1),
            )
        )

    return merged


def create_diff_visualization(
    before: Image.Image,
    after: Image.Image,
    diff_result: DiffResult,
    highlight_color: Tuple[int, int, int, int] = (255, 60, 60, 80),
) -> Image.Image:
    """Create a visualization of the diff with changed regions highlighted.

    Returns the 'after' image with semi-transparent red overlays on changed regions.
    """
    from PIL import ImageDraw

    img = after.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for region in diff_result.regions:
        x1, y1, x2, y2 = region.bounds
        draw.rectangle([x1, y1, x2, y2], fill=highlight_color, outline=(255, 0, 0, 180), width=2)

    return Image.alpha_composite(img, overlay)
