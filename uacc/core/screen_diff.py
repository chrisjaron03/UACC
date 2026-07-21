"""
Screen Diff Engine — pixel-level + semantic comparison between consecutive
screenshots to detect what changed after an action.

The semantic checks (OCR text diff, element-count diff, window-title diff)
catch cases that pure pixel comparison misses — e.g. a dialog appearing
with the same background color, or cursor movement masking a real change.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# ── Pixel-level diff ──────────────────────────────────────────


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
class SemanticDiff:
    """Semantic differences detected between two screen states."""

    window_title_changed: bool = False
    window_title_before: str = ""
    window_title_after: str = ""
    text_added: List[str] = field(default_factory=list)
    text_removed: List[str] = field(default_factory=list)
    element_count_changed: bool = False
    element_count_before: int = 0
    element_count_after: int = 0
    changed_elements: List[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any([
            self.window_title_changed,
            self.text_added,
            self.text_removed,
            self.element_count_changed,
            self.changed_elements,
        ])

    @property
    def summary(self) -> str:
        parts = []
        if self.window_title_changed:
            parts.append(f"window: \"{self.window_title_before}\" → \"{self.window_title_after}\"")
        if self.element_count_changed:
            parts.append(f"elements: {self.element_count_before} → {self.element_count_after}")
        if len(self.text_added) <= 3:
            for t in self.text_added:
                parts.append(f"+ \"{t[:40]}\"")
        elif self.text_added:
            parts.append(f"+{len(self.text_added)} text regions")
        if len(self.text_removed) <= 3:
            for t in self.text_removed:
                parts.append(f"- \"{t[:40]}\"")
        elif self.text_removed:
            parts.append(f"-{len(self.text_removed)} text regions")
        for e in self.changed_elements[:3]:
            parts.append(f"~ {e[:60]}")
        return "; ".join(parts) if parts else "No semantic change"


@dataclass
class DiffResult:
    """Result of comparing two screenshots — pixel + semantic."""

    changed: bool
    changed_percentage: float  # 0.0 – 100.0
    regions: List[ChangedRegion]
    total_pixels_changed: int
    semantic: Optional[SemanticDiff] = None

    @property
    def summary(self) -> str:
        lines = []
        if self.changed:
            lines.append(
                f"Changed: {self.changed_percentage:.1f}% of screen ({self.total_pixels_changed} pixels)"
            )
            for r in self.regions[:5]:
                lines.append(
                    f"  region ({r.bounds[0]},{r.bounds[1]})–({r.bounds[2]},{r.bounds[3]})  "
                    f"{r.width}×{r.height}px  intensity={r.change_intensity:.1f}"
                )
        else:
            lines.append("No pixel change detected")
        if self.semantic and self.semantic.changed:
            lines.append(f"  semantic: {self.semantic.summary}")
        return "\n".join(lines)


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


def compute_semantic_diff(
    before_text_map: Optional[str],
    after_text_map: Optional[str],
    before_window_title: str = "",
    after_window_title: str = "",
) -> SemanticDiff:
    """Detect semantic differences between two screen states.

    Compares text map content and window titles to identify changes
    that pixel-level diff might miss.

    Args:
        before_text_map: Compact text from before the action (or None).
        after_text_map: Compact text after the action (or None).
        before_window_title: Active window title before.
        after_window_title: Active window title after.

    Returns:
        SemanticDiff with detected changes.
    """
    sem = SemanticDiff(
        window_title_before=before_window_title,
        window_title_after=after_window_title,
    )

    # Window title change
    if before_window_title and after_window_title and before_window_title != after_window_title:
        sem.window_title_changed = True

    # Text map content diff
    if before_text_map and after_text_map:
        before_lines = set(before_text_map.split("\n"))
        after_lines = set(after_text_map.split("\n"))

        added = after_lines - before_lines
        removed = before_lines - after_lines

        # Filter out noisy lines (screenshot timestamps, coordinates with small changes)
        for line in added:
            clean = line.strip()
            if clean and not clean.startswith("Screen:") and not clean.startswith("───"):
                sem.text_added.append(clean)

        for line in removed:
            clean = line.strip()
            if clean and not clean.startswith("Screen:") and not clean.startswith("───"):
                sem.text_removed.append(clean)

        # Detect element count changes
        import re
        before_count = len(re.findall(r'^\[', before_text_map, re.MULTILINE))
        after_count = len(re.findall(r'^\[', after_text_map, re.MULTILINE))
        if before_count != after_count:
            sem.element_count_changed = True
            sem.element_count_before = before_count
            sem.element_count_after = after_count

        # Detect specific element type changes
        interactive_pattern = r'(button|menu_item|text_input|dialog|window|tab) '
        before_types = set(re.findall(interactive_pattern, before_text_map, re.IGNORECASE))
        after_types = set(re.findall(interactive_pattern, after_text_map, re.IGNORECASE))
        new_types = after_types - before_types
        for t in new_types:
            sem.changed_elements.append(f"New {t} appeared")

    return sem


def compute_diff(
    before: Image.Image,
    after: Image.Image,
    pixel_threshold: int = 20,
    min_region_area: int = 100,
    merge_distance: int = 30,
    before_text_map: Optional[str] = None,
    after_text_map: Optional[str] = None,
    before_window_title: str = "",
    after_window_title: str = "",
) -> DiffResult:
    """Compute a detailed diff between two screenshots — pixel + semantic.

    Args:
        before, after: Consecutive screenshots (same size).
        pixel_threshold: Minimum per-channel difference to count as changed.
        min_region_area: Ignore regions smaller than this (noise filtering).
        merge_distance: Merge nearby changed regions within this distance.
        before_text_map: Text map before action (for semantic diff).
        after_text_map: Text map after action (for semantic diff).
        before_window_title: Window title before (for semantic diff).
        after_window_title: Window title after (for semantic diff).

    Returns:
        DiffResult with pixel + semantic change information.
    """
    # Compute semantic diff first (always, even if pixel diff fails)
    sem = compute_semantic_diff(
        before_text_map=before_text_map,
        after_text_map=after_text_map,
        before_window_title=before_window_title,
        after_window_title=after_window_title,
    )

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
            semantic=sem,
        )

    # Per-pixel difference (average across RGB)
    diff = np.abs(a - b).mean(axis=2)
    mask = diff > pixel_threshold

    total_pixels = mask.size
    changed_pixels = int(mask.sum())
    pct = (changed_pixels / total_pixels) * 100

    # If no pixel change detected, defer to semantic diff
    if changed_pixels == 0:
        return DiffResult(
            changed=sem.changed,
            changed_percentage=0.0,
            regions=[],
            total_pixels_changed=0,
            semantic=sem,
        )

    # Find contiguous changed regions
    regions = _find_regions(mask, diff, min_region_area, merge_distance)

    pixel_changed_pct = round(pct, 2)
    # Combine: consider "changed" if either pixel or semantic says so
    overall_changed = pixel_changed_pct > 0.5 or sem.changed

    return DiffResult(
        changed=overall_changed,
        changed_percentage=pixel_changed_pct,
        regions=regions,
        total_pixels_changed=changed_pixels,
        semantic=sem,
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
