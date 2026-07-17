"""
Session Memory — track visited screens, learned shortcuts, element positions,
and action history for intelligent decision-making across turns.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CachedElement:
    """A UI element whose position has been cached for quick re-use."""

    element_id: str
    name: str
    element_type: str
    center: Tuple[int, int]
    bounds: Tuple[int, int, int, int]
    last_verified: float  # time.time() timestamp
    hit_count: int = 0
    stable: bool = True  # True if position hasn't changed across screenshots

    @property
    def age_seconds(self) -> float:
        return time.time() - self.last_verified

    @property
    def is_stale(self) -> bool:
        """Consider stale after 30 seconds without verification."""
        return self.age_seconds > 30.0


@dataclass
class ScreenSnapshot:
    """A record of a visited screen state."""

    window_title: str
    timestamp: float
    key_elements: List[str]  # Top element names for quick reference
    element_count: int


@dataclass
class LearnedShortcut:
    """A keyboard shortcut the agent has discovered works for a task."""

    pattern: str  # e.g. "Save file", "Open terminal"
    method: str  # "hotkey", "menu_click"
    keys: List[str]  # e.g. ["ctrl", "s"]
    confidence: float  # 0.0 – 1.0
    times_used: int = 0


class SessionMemory:
    """Persistent memory across an agent session (one task run).

    Tracks:
    - Action history (what was done and what happened)
    - Visited screens (where we've been)
    - Element cache (last known positions)
    - Learned shortcuts (discovered faster methods)
    """

    def __init__(self, max_history: int = 100, max_cache: int = 500):
        self.max_history = max_history
        self.max_cache = max_cache

        self.action_history: List[Dict[str, Any]] = []
        self.visited_screens: List[ScreenSnapshot] = []
        self.element_cache: Dict[str, CachedElement] = {}
        self.learned_shortcuts: Dict[str, LearnedShortcut] = {}
        self._start_time = time.time()

    # ── Action History ───────────────────────────────────────

    def record_action(self, action_dict: Dict[str, Any], result: Dict[str, Any]) -> None:
        """Record an action and its result."""
        entry = {
            **action_dict,
            **result,
            "timestamp": time.time(),
            "turn": len(self.action_history) + 1,
        }
        self.action_history.append(entry)

        # Trim to max
        if len(self.action_history) > self.max_history:
            self.action_history = self.action_history[-self.max_history:]

        logger.debug("Recorded action #%d: %s", entry["turn"], entry.get("action", "?"))

    def get_recent_history(self, n: int = 10) -> List[Dict[str, Any]]:
        """Get the last N action records."""
        return self.action_history[-n:]

    def get_last_result(self) -> Optional[Dict[str, Any]]:
        """Get the most recent action result."""
        return self.action_history[-1] if self.action_history else None

    # ── Screen Tracking ──────────────────────────────────────

    def record_screen(
        self,
        window_title: str,
        key_elements: List[str],
        element_count: int,
    ) -> None:
        """Record a visited screen."""
        snapshot = ScreenSnapshot(
            window_title=window_title,
            timestamp=time.time(),
            key_elements=key_elements[:10],
            element_count=element_count,
        )
        self.visited_screens.append(snapshot)
        logger.debug("Recorded screen: %s (%d elements)", window_title, element_count)

    def has_visited_screen(self, window_title: str) -> bool:
        """Check if we've seen a window with this title before."""
        return any(
            s.window_title.lower() == window_title.lower() for s in self.visited_screens
        )

    # ── Element Cache ────────────────────────────────────────

    def cache_element(
        self,
        element_id: str,
        name: str,
        element_type: str,
        center: Tuple[int, int],
        bounds: Tuple[int, int, int, int],
    ) -> None:
        """Cache or update an element's position."""
        existing = self.element_cache.get(element_id)
        if existing:
            # Check if position changed
            if existing.center != center:
                existing.stable = False
            existing.center = center
            existing.bounds = bounds
            existing.last_verified = time.time()
            existing.hit_count += 1
        else:
            self.element_cache[element_id] = CachedElement(
                element_id=element_id,
                name=name,
                element_type=element_type,
                center=center,
                bounds=bounds,
                last_verified=time.time(),
            )

        # Evict stale entries if over capacity
        if len(self.element_cache) > self.max_cache:
            self._evict_stale_cache()

    def get_cached_position(self, element_id: str) -> Optional[Tuple[int, int]]:
        """Get cached center position for an element (None if stale/missing)."""
        cached = self.element_cache.get(element_id)
        if cached and not cached.is_stale:
            return cached.center
        return None

    def find_element_by_name(self, name: str) -> Optional[CachedElement]:
        """Search cache by element name (fuzzy substring match)."""
        name_lower = name.lower()
        for cached in self.element_cache.values():
            if name_lower in cached.name.lower() and not cached.is_stale:
                return cached
        return None

    def _evict_stale_cache(self) -> None:
        """Remove stale entries from the cache."""
        stale_ids = [eid for eid, el in self.element_cache.items() if el.is_stale]
        for eid in stale_ids:
            del self.element_cache[eid]
        logger.debug("Evicted %d stale cache entries", len(stale_ids))

    # ── Learned Shortcuts ────────────────────────────────────

    def learn_shortcut(
        self,
        pattern: str,
        method: str,
        keys: List[str],
        confidence: float = 0.8,
    ) -> None:
        """Record a discovered shortcut."""
        self.learned_shortcuts[pattern.lower()] = LearnedShortcut(
            pattern=pattern,
            method=method,
            keys=keys,
            confidence=confidence,
        )
        logger.info("Learned shortcut: %s → %s %s", pattern, method, keys)

    def get_shortcut(self, pattern: str) -> Optional[LearnedShortcut]:
        """Look up a known shortcut by pattern."""
        shortcut = self.learned_shortcuts.get(pattern.lower())
        if shortcut:
            shortcut.times_used += 1
            return shortcut
        return None

    # ── Summary ──────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the current session state."""
        elapsed = time.time() - self._start_time
        return {
            "elapsed_seconds": round(elapsed, 1),
            "total_actions": len(self.action_history),
            "screens_visited": len(self.visited_screens),
            "cached_elements": len(self.element_cache),
            "learned_shortcuts": len(self.learned_shortcuts),
            "success_rate": self._compute_success_rate(),
        }

    def _compute_success_rate(self) -> float:
        if not self.action_history:
            return 1.0
        successes = sum(1 for a in self.action_history if a.get("success", False))
        return round(successes / len(self.action_history), 3)

    def reset(self) -> None:
        """Clear all memory for a fresh session."""
        self.action_history.clear()
        self.visited_screens.clear()
        self.element_cache.clear()
        self.learned_shortcuts.clear()
        self._start_time = time.time()
