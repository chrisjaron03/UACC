"""
Vision-Based Element Detector — OmniParser-style fallback for apps
without accessibility trees (e.g. games, custom UI frameworks, remote desktop).

Pipeline:
  1. Screenshot
  2. OCR (existing EasyOCR engine) — detect all visible text
  3. Edge detection + contour analysis — find rectangular regions
  4. Heuristic matching — pair text labels with bounding boxes
  5. Return ScreenElement list

This is a best-effort fallback. When the accessibility tree is available,
it is always preferred for accuracy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from uacc.core.ocr_engine import OCRResult, extract_text
from uacc.core.text_map import ScreenElement

logger = logging.getLogger(__name__)


@dataclass
class DetectedRegion:
    """A rectangular region detected via image analysis."""

    bounds: Tuple[int, int, int, int]
    region_type: str  # "button", "input", "label", "unknown"
    confidence: float
    label: str = ""


def detect_regions(
    image: Image.Image,
    min_area: int = 500,
    max_area: int = 200000,
) -> List[DetectedRegion]:
    """Detect rectangular UI regions using edge detection and contours.

    Args:
        image: PIL Image (RGB).
        min_area: Minimum contour area to consider.
        max_area: Maximum contour area to consider.

    Returns:
        List of detected regions with types and confidence.
    """
    try:
        import cv2
    except ImportError:
        logger.warning("OpenCV not available — skipping region detection")
        return []

    img = np.array(image.convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # Edge detection
    edges = cv2.Canny(gray, 50, 150)
    kernel = np.ones((3, 3), np.uint8)
    dilated = cv2.dilate(edges, kernel, iterations=2)

    # Find contours
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    regions: List[DetectedRegion] = []
    img_h, img_w = gray.shape

    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area or area > max_area:
            continue

        x, y, w, h = cv2.boundingRect(contour)
        aspect_ratio = w / h if h > 0 else 1

        # Skip extremely wide or thin regions (likely separators/scrollbars)
        if aspect_ratio > 15 or aspect_ratio < 0.1:
            continue

        # Skip regions that touch the screen edge (likely window chrome)
        if (x <= 2 or y <= 2 or x + w >= img_w - 2 or y + h >= img_h - 2):
            continue

        bounds = (x, y, x + w, y + h)

        # Try to determine region type
        region_type = classify_region(gray[y:y+h, x:x+w], aspect_ratio, area)
        confidence = min(1.0, area / 10000) * 0.7  # Base confidence

        regions.append(DetectedRegion(
            bounds=bounds,
            region_type=region_type,
            confidence=confidence,
        ))

    # Merge overlapping regions
    regions = _merge_overlapping(regions)
    return regions


def classify_region(
    roi: np.ndarray,
    aspect_ratio: float,
    area: int,
) -> str:
    """Heuristic region type classification based on shape and content."""
    h, w = roi.shape

    # Ratio of white/light pixels (indicates text area)
    light_ratio = np.mean(roi > 200)

    # Ratio of edge pixels
    edges = cv2.Canny(roi, 50, 150) if roi.size > 0 else np.array([[]])
    edge_ratio = np.mean(edges > 0) if edges.size > 0 else 0

    # Button-like: moderate aspect ratio, few edges (solid fill)
    if 1.5 <= aspect_ratio <= 6 and area >= 2000:
        if edge_ratio < 0.1:
            return "button"

    # Input-like: wider aspect ratio, light background
    if 3 <= aspect_ratio <= 12 and light_ratio > 0.6:
        return "input"

    # Default: label
    return "unknown"


def _merge_overlapping(regions: List[DetectedRegion], overlap_threshold: float = 0.5) -> List[DetectedRegion]:
    """Merge regions that overlap significantly."""
    if len(regions) <= 1:
        return regions

    merged = list(regions)
    changed = True

    while changed:
        changed = False
        new_regions: List[DetectedRegion] = []

        for i, r1 in enumerate(merged):
            if r1 is None:
                continue
            for j, r2 in enumerate(merged[i + 1:]):
                if r2 is None:
                    continue
                if _iou(r1.bounds, r2.bounds) > overlap_threshold:
                    # Merge: take the union of bounds, higher confidence region type
                    l = min(r1.bounds[0], r2.bounds[0])
                    t = min(r1.bounds[1], r2.bounds[1])
                    r = max(r1.bounds[2], r2.bounds[2])
                    b = max(r1.bounds[3], r2.bounds[3])
                    merged[i] = DetectedRegion(
                        bounds=(l, t, r, b),
                        region_type=r1.region_type if r1.confidence >= r2.confidence else r2.region_type,
                        confidence=max(r1.confidence, r2.confidence),
                    )
                    merged[j + 1 + i] = None  # mark merged
                    changed = True

        new_regions = [r for r in merged if r is not None]
        merged = new_regions

    return merged


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    """Intersection over Union for two bounding boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b

    xi1 = max(ax1, bx1)
    yi1 = max(ay1, by1)
    xi2 = min(ax2, bx2)
    yi2 = min(ay2, by2)

    inter = max(0, xi2 - xi1) * max(0, yi2 - yi1)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0


def ocr_only_detect(image: Image.Image) -> List[ScreenElement]:
    """Build ScreenElements from OCR text detections only.

    Every OCR text detection becomes a 'label' element. Adjacent
    or near-adjacent labels may be upgraded to 'button' or 'input'
    based on heuristics.
    """
    try:
        ocr_results = extract_text(image, confidence_threshold=0.3)
    except Exception as exc:
        logger.warning("OCR failed in vision fallback: %s", exc)
        return []

    elements: List[ScreenElement] = []
    for i, ocr in enumerate(ocr_results):
        if not ocr.text.strip():
            continue

        el_id = f"ocr_{i}"
        center = ((ocr.bounds[0] + ocr.bounds[2]) // 2,
                   (ocr.bounds[1] + ocr.bounds[3]) // 2)

        # Heuristic: if text is short and centered in a larger area,
        # it might be a button
        is_short = len(ocr.text.strip()) <= 20
        is_centered = ocr.width > 100 and ocr.height > 25

        if is_short and is_centered:
            el_type = "button"
            clickable = True
        elif is_short and ocr.width > 150 and ocr.height > 20:
            el_type = "text_input"
            editable = True
        else:
            el_type = "label"
            clickable = False
            editable = False

        elements.append(ScreenElement(
            id=el_id,
            element_type=el_type,
            text=ocr.text.strip(),
            bounds=ocr.bounds,
            center=center,
            clickable=clickable,
            editable=editable,
            source="ocr",
        ))

    return elements


def full_vision_detect(image: Image.Image) -> List[ScreenElement]:
    """Full OmniParser-style pipeline: OCR + region detection → merged elements.

    Priority order:
      1. Regions detected by contour analysis (typed as button/input/label)
      2. OCR text detections
      3. Merge: OCR text is associated with nearby regions

    Returns a list of ScreenElements with source="vision".
    """
    elements: List[ScreenElement] = []

    # Step 1: OCR
    try:
        ocr_results = extract_text(image, confidence_threshold=0.3)
    except Exception as exc:
        logger.warning("OCR failed: %s", exc)
        ocr_results = []

    # Step 2: Region detection
    regions = detect_regions(image)

    # Step 3: Match OCR text to regions
    matched_regions: set[int] = set()
    for region_idx, region in enumerate(regions):
        el_id = f"vision_region_{region_idx}"
        l, t, r, b = region.bounds
        center = ((l + r) // 2, (t + b) // 2)

        # Find OCR text inside this region
        label = region.label
        for ocr in ocr_results:
            if _is_inside(ocr.bounds, region.bounds):
                if not label:
                    label = ocr.text.strip()
                elif ocr.text.strip() not in label:
                    label += " " + ocr.text.strip()[:20]

        clickable = region.region_type == "button"
        editable = region.region_type == "input"

        elements.append(ScreenElement(
            id=el_id,
            element_type=region.region_type,
            text=label or "(region)",
            bounds=region.bounds,
            center=center,
            clickable=clickable,
            editable=editable,
            source="vision",
        ))
        matched_regions.add(region_idx)

    # Step 4: Add unmatched OCR text as labels
    unmatched = 0
    for ocr_idx, ocr in enumerate(ocr_results):
        # Check if this OCR detection is inside any region
        is_matched = any(_is_inside(ocr.bounds, r.bounds) for r in regions)
        if not is_matched and ocr.text.strip():
            el_id = f"vision_ocr_{ocr_idx}"
            center = ((ocr.bounds[0] + ocr.bounds[2]) // 2,
                       (ocr.bounds[1] + ocr.bounds[3]) // 2)
            elements.append(ScreenElement(
                id=el_id,
                element_type="label",
                text=ocr.text.strip(),
                bounds=ocr.bounds,
                center=center,
                clickable=False,
                editable=False,
                source="vision",
            ))
            unmatched += 1

    logger.info(
        "Vision detected: %d regions, %d OCR texts (%d unmatched)",
        len(regions), len(ocr_results), unmatched,
    )
    return elements


def _is_inside(inner: Tuple[int, int, int, int], outer: Tuple[int, int, int, int]) -> bool:
    """Check if inner bounding box is inside outer bounding box (with margin)."""
    margin = 5
    return (
        inner[0] >= outer[0] - margin
        and inner[1] >= outer[1] - margin
        and inner[2] <= outer[2] + margin
        and inner[3] <= outer[3] + margin
    )
