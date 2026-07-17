"""
MCP Server Utilities — helpers for image encoding, session state, and error formatting.
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)


# ── Image Encoding ───────────────────────────────────────────


def image_to_base64(img: Image.Image, fmt: str = "PNG", quality: int = 80) -> str:
    """Encode a PIL Image to a base64 string for MCP image content."""
    buf = io.BytesIO()
    save_kwargs: dict = {"format": fmt}
    if fmt.upper() == "JPEG":
        save_kwargs["quality"] = quality
    img.save(buf, **save_kwargs)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def get_image_media_type(fmt: str = "PNG") -> str:
    """Return the MIME type for a given image format."""
    return {
        "PNG": "image/png",
        "JPEG": "image/jpeg",
        "WEBP": "image/webp",
    }.get(fmt.upper(), "image/png")


# ── Session State ────────────────────────────────────────────


@dataclass
class CachedElement:
    """A UI element cached between tool calls for fast lookup."""

    element_id: str
    name: str
    element_type: str
    center: Tuple[int, int]
    bounds: Tuple[int, int, int, int]
    clickable: bool = False
    editable: bool = False
    expandable: bool = False
    timestamp: float = 0.0


class SessionState:
    """Persistent state across MCP tool calls within a session.

    Maintains:
    - Element cache (last known positions from screen scans)
    - Action log (history of executed actions for debugging)
    - Screen dimensions (cached to avoid re-querying)
    """

    def __init__(self, max_cache: int = 500, max_log: int = 200):
        self.max_cache = max_cache
        self.max_log = max_log
        self.element_cache: Dict[str, CachedElement] = {}
        self.action_log: List[Dict[str, Any]] = []
        self.screen_size: Optional[Tuple[int, int]] = None
        self._start_time = time.time()

    def cache_elements(self, elements: List[Dict[str, Any]]) -> None:
        """Cache a batch of screen elements from a text map scan."""
        now = time.time()
        for el in elements:
            eid = el.get("id", "")
            if not eid:
                continue
            center = el.get("center", [0, 0])
            bounds = el.get("bounds", [0, 0, 0, 0])
            self.element_cache[eid] = CachedElement(
                element_id=eid,
                name=el.get("text", el.get("name", "")),
                element_type=el.get("type", el.get("element_type", "")),
                center=(center[0], center[1]),
                bounds=(bounds[0], bounds[1], bounds[2], bounds[3]),
                clickable=el.get("clickable", False),
                editable=el.get("editable", False),
                expandable=el.get("expandable", False),
                timestamp=now,
            )
        # Evict oldest if over capacity
        if len(self.element_cache) > self.max_cache:
            sorted_items = sorted(
                self.element_cache.items(), key=lambda x: x[1].timestamp
            )
            for eid, _ in sorted_items[: len(sorted_items) - self.max_cache]:
                del self.element_cache[eid]

    def find_elements(
        self,
        name: Optional[str] = None,
        element_type: Optional[str] = None,
        max_age_seconds: float = 30.0,
    ) -> List[CachedElement]:
        """Search cached elements by name and/or type.

        Args:
            name: Substring to search in element names (case-insensitive).
            element_type: Exact element type to filter by.
            max_age_seconds: Ignore elements older than this.

        Returns:
            List of matching CachedElement objects.
        """
        now = time.time()
        results = []
        for el in self.element_cache.values():
            if (now - el.timestamp) > max_age_seconds:
                continue
            if name and name.lower() not in el.name.lower():
                continue
            if element_type and el.element_type != element_type:
                continue
            results.append(el)
        return results

    def log_action(self, tool_name: str, params: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Log a tool call for debugging and history."""
        self.action_log.append({
            "tool": tool_name,
            "params": params,
            "result": result,
            "timestamp": time.time(),
        })
        if len(self.action_log) > self.max_log:
            self.action_log = self.action_log[-self.max_log:]

    def get_recent_actions(self, n: int = 10) -> List[Dict[str, Any]]:
        """Return the last N logged actions."""
        return self.action_log[-n:]


# ── Singleton Session ────────────────────────────────────────

_session: Optional[SessionState] = None


def get_session() -> SessionState:
    """Get or create the global session state."""
    global _session
    if _session is None:
        _session = SessionState()
    return _session


# ── Error Formatting ─────────────────────────────────────────


def format_error(error: Exception, context: str = "") -> str:
    """Format an exception into a clean error message for MCP responses."""
    msg = f"Error: {type(error).__name__}: {error}"
    if context:
        msg = f"{context} — {msg}"
    return msg


def format_action_result(result: Dict[str, Any]) -> str:
    """Format an ActionExecutor result dict into a readable string."""
    success = result.get("success", False)
    message = result.get("message", "No message")
    action = result.get("action", "unknown")
    icon = "✓" if success else "✗"
    return f"{icon} [{action}] {message}"
