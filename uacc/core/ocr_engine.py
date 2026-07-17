"""
OCR Engine — extract visible text and positions from a screenshot.

Uses EasyOCR for offline, GPU-accelerated text detection.  The engine
is loaded once and reused across calls for speed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Lazy-loaded EasyOCR reader
_reader: Optional[object] = None


@dataclass
class OCRResult:
    """A single text detection from OCR."""

    text: str
    bounds: Tuple[int, int, int, int]  # (left, top, right, bottom)
    center: Tuple[int, int]
    confidence: float

    @property
    def width(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> int:
        return self.bounds[3] - self.bounds[1]


def _get_reader() -> object:
    """Lazily initialise the EasyOCR reader (downloads model on first run)."""
    global _reader
    if _reader is None:
        try:
            import easyocr

            _reader = easyocr.Reader(
                ["en"],
                gpu=True,
                verbose=False,
            )
            logger.info("EasyOCR reader initialised (GPU if available)")
        except ImportError:
            raise ImportError(
                "easyocr is required for OCR. Install with: pip install easyocr"
            )
    return _reader  # type: ignore[return-value]


def extract_text(
    image: Image.Image,
    confidence_threshold: float = 0.3,
    merge_close: bool = True,
    merge_distance: int = 10,
) -> List[OCRResult]:
    """Run OCR on a PIL Image and return detected text with positions.

    Args:
        image: Input screenshot (PIL Image, RGB).
        confidence_threshold: Minimum confidence to keep a detection.
        merge_close: If True, merge detections that are spatially close.
        merge_distance: Pixel distance threshold for merging.

    Returns:
        List of OCRResult sorted top-to-bottom, left-to-right.
    """
    reader = _get_reader()
    img_array = np.array(image)

    # EasyOCR returns: list of (bbox, text, confidence)
    # bbox is [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]  (four corners)
    raw_results = reader.readtext(img_array)  # type: ignore[union-attr]

    results: List[OCRResult] = []
    for bbox, text, conf in raw_results:
        if conf < confidence_threshold:
            continue

        text = text.strip()
        if not text:
            continue

        # Convert 4-corner bbox to (left, top, right, bottom)
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        left, top = int(min(xs)), int(min(ys))
        right, bottom = int(max(xs)), int(max(ys))
        cx = (left + right) // 2
        cy = (top + bottom) // 2

        results.append(
            OCRResult(
                text=text,
                bounds=(left, top, right, bottom),
                center=(cx, cy),
                confidence=round(conf, 3),
            )
        )

    # Sort: top-to-bottom, then left-to-right
    results.sort(key=lambda r: (r.bounds[1], r.bounds[0]))

    if merge_close:
        results = _merge_nearby(results, merge_distance)

    logger.info("OCR found %d text regions", len(results))
    return results


def _merge_nearby(results: List[OCRResult], distance: int) -> List[OCRResult]:
    """Merge OCR results that are on the same line and close together."""
    if not results:
        return results

    merged: List[OCRResult] = []
    used = set()

    for i, r1 in enumerate(results):
        if i in used:
            continue
        group_text = r1.text
        group_bounds = list(r1.bounds)
        group_conf = [r1.confidence]

        for j in range(i + 1, len(results)):
            if j in used:
                continue
            r2 = results[j]
            # Same line (vertical overlap) and horizontally close
            v_overlap = not (r2.bounds[1] > r1.bounds[3] or r2.bounds[3] < r1.bounds[1])
            h_close = abs(r2.bounds[0] - group_bounds[2]) < distance
            if v_overlap and h_close:
                group_text += " " + r2.text
                group_bounds[0] = min(group_bounds[0], r2.bounds[0])
                group_bounds[1] = min(group_bounds[1], r2.bounds[1])
                group_bounds[2] = max(group_bounds[2], r2.bounds[2])
                group_bounds[3] = max(group_bounds[3], r2.bounds[3])
                group_conf.append(r2.confidence)
                used.add(j)

        cx = (group_bounds[0] + group_bounds[2]) // 2
        cy = (group_bounds[1] + group_bounds[3]) // 2
        merged.append(
            OCRResult(
                text=group_text,
                bounds=tuple(group_bounds),  # type: ignore[arg-type]
                center=(cx, cy),
                confidence=round(sum(group_conf) / len(group_conf), 3),
            )
        )

    return merged


def extract_text_simple(image: Image.Image) -> str:
    """Quick helper — return all detected text as a single string."""
    results = extract_text(image)
    return "\n".join(r.text for r in results)
