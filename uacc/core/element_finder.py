"""
Element Finder — intelligent UI element location with fuzzy matching,
wait-for-element polling, and click-by-name functionality.

This is the "smart targeting" layer that makes agents more reliable
by abstracting away raw pixel coordinates.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Tuple

from uacc.core.accessibility import (
    get_ui_tree,
)
from uacc.core.screen_capture import get_screen_size
from uacc.core.text_map import ScreenElement, build_text_map

logger = logging.getLogger(__name__)


@dataclass
class ElementMatch:
    """A matched UI element with confidence score."""

    element_id: str
    name: str
    element_type: str
    center: Tuple[int, int]
    bounds: Tuple[int, int, int, int]
    confidence: float  # 0.0 – 1.0
    clickable: bool = False
    editable: bool = False
    expandable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.element_id,
            "name": self.name,
            "type": self.element_type,
            "center": {"x": self.center[0], "y": self.center[1]},
            "bounds": {
                "left": self.bounds[0],
                "top": self.bounds[1],
                "right": self.bounds[2],
                "bottom": self.bounds[3],
            },
            "confidence": round(self.confidence, 3),
            "clickable": self.clickable,
            "editable": self.editable,
            "expandable": self.expandable,
        }


def _similarity(a: str, b: str) -> float:
    """Compute string similarity ratio (0.0 – 1.0)."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _scan_elements() -> List[ScreenElement]:
    """Perform a fresh screen scan and return all elements."""
    screen_w, screen_h = get_screen_size()
    ui_elements = get_ui_tree()

    active_window = ""
    if ui_elements and ui_elements[0].name:
        active_window = ui_elements[0].name

    text_map = build_text_map(
        screen_width=screen_w,
        screen_height=screen_h,
        ui_elements=ui_elements,
        active_window=active_window,
    )
    return text_map.all_elements


def find_elements_smart(
    name: Optional[str] = None,
    element_type: Optional[str] = None,
    min_confidence: float = 0.4,
    max_results: int = 10,
    elements: Optional[List[ScreenElement]] = None,
) -> List[ElementMatch]:
    """Find UI elements using fuzzy name matching.

    Unlike the basic find_element, this uses string similarity scoring
    to find approximate matches — crucial when the agent doesn't know
    the exact label text.

    Args:
        name: Text to search for (fuzzy match).
        element_type: Element type filter (exact match).
        min_confidence: Minimum similarity score to include.
        max_results: Maximum number of results to return.
        elements: Pre-scanned elements (if None, performs a fresh scan).

    Returns:
        List of ElementMatch objects sorted by confidence (highest first).
    """
    if elements is None:
        elements = _scan_elements()

    matches: List[ElementMatch] = []

    for el in elements:
        # Type filter
        if element_type and el.element_type != element_type:
            continue

        # Compute confidence
        confidence = 0.0
        if name:
            # Exact substring match = highest confidence
            if name.lower() in el.text.lower():
                # Scale by how much of the element text the query covers
                confidence = max(0.8, len(name) / max(len(el.text), 1))
            else:
                confidence = _similarity(name, el.text)
        else:
            # No name filter — include all (with type filter)
            confidence = 1.0 if (el.clickable or el.editable or el.expandable) else 0.5

        if confidence < min_confidence:
            continue

        matches.append(ElementMatch(
            element_id=el.id,
            name=el.text,
            element_type=el.element_type,
            center=el.center,
            bounds=el.bounds,
            confidence=confidence,
            clickable=el.clickable,
            editable=el.editable,
            expandable=el.expandable,
        ))

    # Sort by confidence descending
    matches.sort(key=lambda m: m.confidence, reverse=True)
    return matches[:max_results]


def wait_for_element(
    name: str,
    element_type: Optional[str] = None,
    timeout_ms: int = 10000,
    poll_interval_ms: int = 500,
    min_confidence: float = 0.6,
) -> Dict[str, Any]:
    """Poll the screen until an element matching the criteria appears.

    This is the single most important tool for agent reliability.
    After any action that triggers a UI change (opening an app, clicking
    a menu, navigating a page), agents should call this to wait for
    the expected element to appear before proceeding.

    Args:
        name: Text to search for in element labels (fuzzy match).
        element_type: Optional element type filter.
        timeout_ms: Maximum time to wait before giving up.
        poll_interval_ms: Time between screen scans.
        min_confidence: Minimum match confidence to consider "found".

    Returns:
        Dict with success status and the found element (if any).
    """
    start = time.time()
    deadline = start + (timeout_ms / 1000)
    attempts = 0

    logger.info("Waiting for element '%s' (timeout=%dms)...", name, timeout_ms)

    while time.time() < deadline:
        attempts += 1

        try:
            matches = find_elements_smart(
                name=name,
                element_type=element_type,
                min_confidence=min_confidence,
                max_results=1,
            )

            if matches:
                best = matches[0]
                elapsed = int((time.time() - start) * 1000)
                logger.info(
                    "Found element '%s' (confidence=%.2f) after %dms (%d attempts)",
                    best.name, best.confidence, elapsed, attempts,
                )
                return {
                    "success": True,
                    "found": True,
                    "element": best.to_dict(),
                    "elapsed_ms": elapsed,
                    "attempts": attempts,
                    "message": (
                        f"Found '{best.name}' ({best.element_type}) "
                        f"at ({best.center[0]}, {best.center[1]}) "
                        f"after {elapsed}ms"
                    ),
                }
        except Exception as exc:
            logger.debug("Scan attempt %d failed: %s", attempts, exc)

        # Wait before next poll
        remaining = deadline - time.time()
        sleep_time = min(poll_interval_ms / 1000, remaining)
        if sleep_time > 0:
            time.sleep(sleep_time)

    elapsed = int((time.time() - start) * 1000)
    logger.warning("Element '%s' not found after %dms (%d attempts)", name, elapsed, attempts)
    return {
        "success": True,  # Tool itself succeeded — element just wasn't found
        "found": False,
        "element": None,
        "elapsed_ms": elapsed,
        "attempts": attempts,
        "message": f"Element '{name}' not found after {elapsed}ms ({attempts} attempts)",
    }


def click_element_by_name(
    name: str,
    element_type: Optional[str] = None,
    button: str = "left",
    min_confidence: float = 0.6,
) -> Dict[str, Any]:
    """Find an element by name and click its center coordinates.

    This is the "easy mode" alternative to raw coordinate clicking.
    The agent says "click the Save button" and UACC figures out where it is.

    Args:
        name: Text to search for in element labels (fuzzy match).
        element_type: Optional element type filter.
        button: Mouse button — "left", "right", or "middle".
        min_confidence: Minimum match confidence.

    Returns:
        Dict with the target coordinates and match info.
        The MCP server layer handles the actual click execution.
    """
    matches = find_elements_smart(
        name=name,
        element_type=element_type,
        min_confidence=min_confidence,
        max_results=3,
    )

    if not matches:
        return {
            "success": False,
            "message": f"No element found matching '{name}'",
            "alternatives": [],
        }

    best = matches[0]

    # If confidence is low, warn but proceed
    result: Dict[str, Any] = {
        "success": True,
        "element": best.to_dict(),
        "click_x": best.center[0],
        "click_y": best.center[1],
        "button": button,
        "message": (
            f"Found '{best.name}' ({best.element_type}) "
            f"at ({best.center[0]}, {best.center[1]}) "
            f"confidence={best.confidence:.2f}"
        ),
    }

    # Include alternatives if the top match isn't highly confident
    if best.confidence < 0.9 and len(matches) > 1:
        result["alternatives"] = [m.to_dict() for m in matches[1:]]
        result["message"] += f" — {len(matches)-1} alternative(s) found"

    return result


def get_mouse_position() -> Dict[str, Any]:
    """Get the current mouse cursor position.

    Returns:
        Dict with x, y coordinates.
    """
    try:
        import pyautogui
        x, y = pyautogui.position()
        return {
            "success": True,
            "x": x,
            "y": y,
            "message": f"Mouse at ({x}, {y})",
        }
    except Exception as exc:
        return {
            "success": False,
            "x": 0,
            "y": 0,
            "message": f"Failed to get mouse position: {exc}",
        }
