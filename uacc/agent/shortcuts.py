"""
Shortcut Detector — discovers keyboard shortcuts from UI labels and
application menus, enabling the agent to prefer fast keyboard-driven
interactions over slower GUI clicks.

Learned shortcuts are persisted across sessions via SessionMemory.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from uacc.agent.memory import SessionMemory

logger = logging.getLogger(__name__)

# Regex patterns to detect shortcut annotations in UI element text
_SHORTCUT_PATTERNS = [
    re.compile(r"\(([\w\s\+]+)\)$"),                          # "Save (Ctrl+S)"
    re.compile(r"\b(Ctrl|Alt|Shift|Win|Cmd|Super)\+", re.IGNORECASE),  # "Ctrl+S"
    re.compile(r"\b([A-Z])\b"),                                # Isolated capital (weak)
]

# Common application shortcuts (cross-app)
_COMMON_SHORTCUTS: Dict[str, List[str]] = {
    "save": ["ctrl", "s"],
    "open": ["ctrl", "o"],
    "new": ["ctrl", "n"],
    "print": ["ctrl", "p"],
    "find": ["ctrl", "f"],
    "undo": ["ctrl", "z"],
    "redo": ["ctrl", "y"],
    "copy": ["ctrl", "c"],
    "cut": ["ctrl", "x"],
    "paste": ["ctrl", "v"],
    "select all": ["ctrl", "a"],
    "close tab": ["ctrl", "w"],
    "close window": ["alt", "f4"],
    "new tab": ["ctrl", "t"],
    "switch tab": ["ctrl", "tab"],
    "save as": ["ctrl", "shift", "s"],
    "run": ["win", "r"],
    "file explorer": ["win", "e"],
    "search": ["win", "s"],
    "settings": ["win", "i"],
    "task manager": ["ctrl", "shift", "esc"],
    "lock screen": ["win", "l"],
}

# Map of readable key names to pyautogui-compatible names
_KEY_NORMALIZE = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "cmd": "cmd",
    "command": "cmd",
    "super": "win",
    "esc": "esc",
    "escape": "esc",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "del": "delete",
    "delete": "delete",
    "backspace": "backspace",
    "space": "space",
    "home": "home",
    "end": "end",
    "pgup": "pageup",
    "pgdn": "pagedown",
    "pageup": "pageup",
    "pagedown": "pagedown",
    "ins": "insert",
    "insert": "insert",
}


def normalize_key(key: str) -> str:
    return _KEY_NORMALIZE.get(key.lower().strip(), key.lower().strip())


def parse_shortcut_text(text: str) -> Optional[List[str]]:
    """Try to parse a keyboard shortcut from UI element text.

    Examples:
        "Save (Ctrl+S)" → ["ctrl", "s"]
        "Ctrl+Shift+Z" → ["ctrl", "shift", "z"]
        "Ctrl+S" → ["ctrl", "s"]
    """
    # Try parenthetical format: "Action (Ctrl+S)"
    paren_match = re.search(r"\(([^)]+)\)$", text.strip())
    if paren_match:
        inner = paren_match.group(1)
        parts = re.split(r"[\s\+]+", inner.strip())
        if len(parts) >= 2 and any(p.lower() in _KEY_NORMALIZE for p in parts):
            return [normalize_key(p) for p in parts]

    # Try explicit format: "Ctrl+S" or "Ctrl + S"
    explicit_match = re.match(
        r"^([\w\+]+(?:\s*\+\s*[\w\+]+)+)$", text.strip()
    )
    if explicit_match:
        parts = re.split(r"\s*\+\s*", explicit_match.group(1))
        if len(parts) >= 2:
            return [normalize_key(p) for p in parts]

    return None


class ShortcutDetector:
    """Discovers and manages keyboard shortcuts from screen state.

    Integrates with SessionMemory to persist learned shortcuts
    across sessions.
    """

    def __init__(self, memory: Optional[SessionMemory] = None):
        self.memory = memory

    def set_memory(self, memory: SessionMemory) -> None:
        self.memory = memory

    def discover_from_text_map(self, text_map: str) -> List[Dict[str, str]]:
        """Scan a text map for UI elements containing shortcut annotations.

        Returns list of discovered shortcuts with context.
        """
        discovered: List[Dict[str, str]] = []
        if not text_map:
            return discovered

        for line in text_map.split("\n"):
            # Look for parenthetical shortcuts in element descriptions
            text_match = re.search(r'"([^"]+)"', line)
            if not text_match:
                continue
            element_text = text_match.group(1)

            shortcut_keys = parse_shortcut_text(element_text)
            if not shortcut_keys:
                continue

            # Extract the action name (text before the shortcut)
            action = re.sub(r"\s*\([^)]*\)\s*$", "", element_text).strip()
            if not action:
                action = element_text

            shortcut_str = "+".join(shortcut_keys).lower()
            discovered.append({
                "action": action,
                "shortcut": shortcut_str,
                "keys": shortcut_keys,
                "context": line.strip()[:80],
            })

            # Store in memory
            if self.memory:
                self.memory.learn_shortcut(
                    pattern=action.lower(),
                    method="hotkey",
                    keys=shortcut_keys,
                    confidence=0.8,
                )
                logger.info("Discovered shortcut: %s → %s", action, shortcut_str)

        return discovered

    def get_relevant_shortcuts(self, task_goal: str = "") -> List[str]:
        """Get formatted shortcut hints for the LLM context.

        Returns strings like "Ctrl+S → Save file (confidence: 0.8)"
        """
        if self.memory is None:
            return []

        shortcuts = []
        goal_lower = task_goal.lower() if task_goal else ""

        for pattern, sc in self.memory.learned_shortcuts.items():
            # Score relevance
            score = 0
            if goal_lower:
                pattern_words = set(pattern.split())
                goal_words = set(goal_lower.split())
                score = len(pattern_words & goal_words)

            shortcut_str = "+".join(sc.keys).title()
            shortcuts.append(
                f"{shortcut_str} → {sc.pattern} (confidence: {sc.confidence})"
            )

        # Sort by relevance to current task
        if goal_lower:
            shortcuts.sort(
                key=lambda s: sum(
                    1 for w in goal_lower.split() if w in s.lower()
                ),
                reverse=True,
            )

        return shortcuts[:5]  # Keep context small

    def get_common_shortcuts(self) -> List[str]:
        """Return a list of well-known cross-app shortcuts."""
        return [
            f"{'+'.join(k).title()} → {a}"
            for a, k in _COMMON_SHORTCUTS.items()
        ]

    def suggest_shortcut_for_action(
        self, action_text: str
    ) -> Optional[List[str]]:
        """Given action text, suggest a keyboard shortcut if known."""
        action_lower = action_text.lower().strip()
        if action_lower in _COMMON_SHORTCUTS:
            return _COMMON_SHORTCUTS[action_lower]

        if self.memory:
            sc = self.memory.get_shortcut(action_lower)
            if sc:
                return sc.keys

        return None
