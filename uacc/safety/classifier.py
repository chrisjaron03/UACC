"""
Risk Classifier — maps UI actions to risk levels based on
action type, target element text, coordinates, and context.

Risk levels:
  LOW      — read-only / non-destructive
  MEDIUM   — standard interaction (click buttons, type text)
  HIGH     — destructive operations (delete, close, format)
  CRITICAL — system-level operations (shutdown, registry, disk)

The classifier is deterministic — no ML, no heuristics that vary
between runs. Every action maps to a consistent risk level.
"""

from __future__ import annotations

import logging
from enum import IntEnum
from typing import Any, Dict, List, Optional

from uacc.actions.schema import Action, ClickAction, HotkeyAction, TypeAction

logger = logging.getLogger(__name__)


class RiskLevel(IntEnum):
    """Risk level for an action. Higher = more dangerous."""

    LOW = 0
    MEDIUM = 1
    HIGH = 2
    CRITICAL = 3


# ── Keywords that indicate destructive intent ──────────────────

_DESTRUCTIVE_LABELS = {
    "delete", "remove", "uninstall", "format", "erase", "wipe",
    "truncate", "drop", "purge", "clear all", "clear history",
    "reset", "factory reset", "restore defaults",
}

_CLOSE_LABELS = {
    "close", "exit", "quit", "terminate", "end task", "kill",
}

_CRITICAL_LABELS = {
    "shut down", "shutdown", "restart", "reboot", "power off",
    "format disk", "format drive", "partition", "diskpart",
    "regedit", "reg delete", "gpo update", "gpupdate",
    "system restore", "clean install",
}

_SAFE_LABELS = {
    "minimize", "maximize", "scroll", "read", "view",
    "properties", "about", "help", "settings",
}

# ── Hotkey patterns by risk ────────────────────────────────────

_HIGH_RISK_HOTKEYS: List[List[str]] = [
    ["alt", "f4"],           # Close window
    ["ctrl", "w"],           # Close tab
    ["ctrl", "shift", "w"],  # Close all tabs
    ["ctrl", "shift", "esc"],# Task manager
]

_CRITICAL_HOTKEYS: List[List[str]] = [
    ["ctrl", "alt", "del"],  # Security screen
    ["win", "d"],            # Show desktop (desktop apps)
    ["alt", "tab"],          # Switch app
]


class RiskClassifier:
    """Deterministic risk classification for UI actions."""

    def classify_action(self, action: Action, context: Optional[Dict[str, Any]] = None) -> RiskLevel:
        action_type = getattr(action, "action", "")

        if action_type == "screenshot" or action_type == "wait":
            return RiskLevel.LOW

        if action_type == "type":
            return self._classify_type(action)

        if action_type == "click":
            return self._classify_click(action)

        if action_type == "hotkey":
            return self._classify_hotkey(action)

        if action_type == "scroll":
            return RiskLevel.LOW

        if action_type in ("launch_app", "focus_window"):
            return RiskLevel.MEDIUM

        if self._text_contains_risk(getattr(action, "reasoning", "")):
            return RiskLevel.HIGH

        return RiskLevel.MEDIUM

    def _classify_type(self, action: Action) -> RiskLevel:
        text = getattr(action, "text", "")
        # Typing destructive commands?
        commands = text.lower().strip().split()
        if any(cmd in _CRITICAL_LABELS for cmd in commands):
            return RiskLevel.CRITICAL
        if any(cmd in _DESTRUCTIVE_LABELS for cmd in commands):
            return RiskLevel.HIGH
        return RiskLevel.MEDIUM

    def _classify_click(self, action: Action) -> RiskLevel:
        target = getattr(action, "target_text", None)
        if not target:
            return RiskLevel.MEDIUM

        target_lower = target.lower().strip()

        if target_lower in _CRITICAL_LABELS:
            return RiskLevel.CRITICAL
        if target_lower in _DESTRUCTIVE_LABELS:
            return RiskLevel.HIGH
        if target_lower in _CLOSE_LABELS:
            return RiskLevel.HIGH

        # Check for substrings (e.g. "Delete file" contains "delete")
        if any(label in target_lower for label in _DESTRUCTIVE_LABELS):
            return RiskLevel.HIGH
        if any(label in target_lower for label in _CRITICAL_LABELS):
            return RiskLevel.CRITICAL

        return RiskLevel.MEDIUM

    def _classify_hotkey(self, action: HotkeyAction) -> RiskLevel:
        keys = getattr(action, "keys", [])
        if not keys:
            return RiskLevel.MEDIUM

        keys_normalized = [k.lower().strip() for k in keys]

        for pattern in _CRITICAL_HOTKEYS:
            if keys_normalized == pattern:
                return RiskLevel.CRITICAL

        for pattern in _HIGH_RISK_HOTKEYS:
            if keys_normalized == pattern:
                return RiskLevel.HIGH

        # Alt+key combos can be menu accelerators (usually safe)
        if "alt" in keys_normalized and len(keys_normalized) == 2:
            return RiskLevel.LOW

        # Ctrl+key combos are usually safe
        if "ctrl" in keys_normalized and len(keys_normalized) <= 2:
            return RiskLevel.LOW

        return RiskLevel.MEDIUM

    def _text_contains_risk(self, text: str) -> bool:
        if not text:
            return False
        t = text.lower()
        for label in _DESTRUCTIVE_LABELS:
            if label in t:
                return True
        return False
