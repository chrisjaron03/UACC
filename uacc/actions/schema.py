"""
Action Schema — typed dataclasses for every UI action the agent can perform.

All actions are serialisable to/from JSON so they can be emitted by any LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union


class MouseButton(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"


class ScrollDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


# ── Action Types ─────────────────────────────────────────────


@dataclass
class ClickAction:
    """Click at exact screen coordinates."""

    action: Literal["click"] = "click"
    x: int = 0
    y: int = 0
    button: MouseButton = MouseButton.LEFT
    count: int = 1  # 1 = single, 2 = double
    modifiers: List[str] = field(default_factory=list)  # ["ctrl"], ["shift"], etc.
    reasoning: str = ""


@dataclass
class DragAction:
    """Click-hold at start, move to end, release."""

    action: Literal["drag"] = "drag"
    start_x: int = 0
    start_y: int = 0
    end_x: int = 0
    end_y: int = 0
    button: MouseButton = MouseButton.LEFT
    duration_ms: int = 500
    reasoning: str = ""


@dataclass
class TypeAction:
    """Type text via keyboard."""

    action: Literal["type"] = "type"
    text: str = ""
    delay_ms: int = 0  # 0 = instant, >0 = per-character delay
    reasoning: str = ""


@dataclass
class HotkeyAction:
    """Press a key combination (e.g. Ctrl+S)."""

    action: Literal["hotkey"] = "hotkey"
    keys: List[str] = field(default_factory=list)  # ["ctrl", "s"]
    reasoning: str = ""


@dataclass
class ScrollAction:
    """Scroll at a position."""

    action: Literal["scroll"] = "scroll"
    x: int = 0
    y: int = 0
    direction: ScrollDirection = ScrollDirection.DOWN
    amount: int = 3  # Number of scroll "clicks"
    reasoning: str = ""


@dataclass
class HoverAction:
    """Move mouse to a position and wait (trigger tooltips, menus)."""

    action: Literal["hover"] = "hover"
    x: int = 0
    y: int = 0
    duration_ms: int = 500
    reasoning: str = ""


@dataclass
class WaitAction:
    """Wait for a condition or fixed duration."""

    action: Literal["wait"] = "wait"
    duration_ms: int = 1000
    condition: str = ""  # Description of what to wait for
    reasoning: str = ""


@dataclass
class ScreenshotAction:
    """Request a fresh screenshot (for verification)."""

    action: Literal["screenshot"] = "screenshot"
    region: Optional[List[int]] = None  # [x, y, w, h] or None for full screen
    reasoning: str = ""


@dataclass
class DoneAction:
    """Signal that the task is complete."""

    action: Literal["done"] = "done"
    result: str = ""
    success: bool = True
    reasoning: str = ""


@dataclass
class LaunchAction:
    """Launch an application by name or path."""

    action: Literal["launch"] = "launch"
    name_or_path: str = ""
    arguments: str = ""
    reasoning: str = ""


@dataclass
class ClipboardAction:
    """Read from or write to the system clipboard."""

    action: Literal["clipboard"] = "clipboard"
    mode: Literal["read", "write"] = "read"
    text: str = ""  # Used when writing
    reasoning: str = ""


@dataclass
class FocusWindowAction:
    """Focus a window by title substring."""

    action: Literal["focus_window"] = "focus_window"
    title: str = ""
    reasoning: str = ""


# ── Union type ───────────────────────────────────────────────

Action = Union[
    ClickAction,
    DragAction,
    TypeAction,
    HotkeyAction,
    ScrollAction,
    HoverAction,
    WaitAction,
    ScreenshotAction,
    DoneAction,
    LaunchAction,
    ClipboardAction,
    FocusWindowAction,
]

# ── Parsing ──────────────────────────────────────────────────

_ACTION_MAP = {
    "click": ClickAction,
    "drag": DragAction,
    "type": TypeAction,
    "hotkey": HotkeyAction,
    "scroll": ScrollAction,
    "hover": HoverAction,
    "wait": WaitAction,
    "screenshot": ScreenshotAction,
    "done": DoneAction,
    "launch": LaunchAction,
    "clipboard": ClipboardAction,
    "focus_window": FocusWindowAction,
}


def parse_action(data: Dict[str, Any]) -> Action:
    """Parse a JSON dict into a typed Action dataclass.

    Args:
        data: Dictionary with at minimum an "action" key.

    Returns:
        Typed Action instance.

    Raises:
        ValueError: If the action type is unknown.
    """
    action_type = data.get("action", "").lower()
    cls = _ACTION_MAP.get(action_type)
    if cls is None:
        raise ValueError(f"Unknown action type: {action_type!r}")

    # Build kwargs, filtering to valid fields
    valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
    kwargs = {}
    for key, value in data.items():
        if key in valid_fields:
            # Handle enum conversions
            field_obj = cls.__dataclass_fields__[key]
            if field_obj.type == "MouseButton" or "MouseButton" in str(field_obj.type):
                value = MouseButton(value)
            elif field_obj.type == "ScrollDirection" or "ScrollDirection" in str(field_obj.type):
                value = ScrollDirection(value)
            kwargs[key] = value

    return cls(**kwargs)


def parse_actions(data_list: List[Dict[str, Any]]) -> List[Action]:
    """Parse a list of action dicts."""
    return [parse_action(d) for d in data_list]


def action_to_dict(action: Action) -> Dict[str, Any]:
    """Serialize an Action back to a JSON-compatible dict."""
    from dataclasses import asdict

    d = asdict(action)
    # Convert enums to strings
    for key, value in d.items():
        if isinstance(value, Enum):
            d[key] = value.value
    return d


# ── Safety Check ─────────────────────────────────────────────

_DESTRUCTIVE_PATTERNS = [
    # Command-level destructive patterns (high confidence)
    "rm -rf",
    "rm -fr",
    "rmdir /s",
    "del /s /q",
    "remove-item -recurse",
    "format c:",
    "format disk",
    "drop table",
    "truncate table",
    "delete database",
    "delete all",
    "erase all",
    "destroy all",
    "shutdown /s",
    "shutdown -s",
    "restart /r",
]

# Compound phrases that indicate destructive intent (verb + target)
# Used for reasoning analysis — catches "click the delete button" but NOT
# "I'll select text to delete it later"
_DESTRUCTIVE_COMPOUNDS = [
    "delete button",
    "delete file",
    "delete folder",
    "delete all",
    "remove file",
    "remove folder",
    "format disk",
    "format drive",
    "drop table",
    "erase disk",
    "erase drive",
    "destroy data",
    "shutdown computer",
    "restart computer",
]


def is_potentially_destructive(action: Action) -> bool:
    """Check if an action might be destructive (for safe mode confirmation).

    Uses command-pattern matching for typed text and compound-phrase
    matching for reasoning — avoids false positives on natural language
    while still catching real destructive intent.
    """
    text = getattr(action, "text", "").lower()
    reasoning = getattr(action, "reasoning", "").lower()

    # Check text content (typed text / commands) — strict pattern matching
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern in text:
            return True

    # Check reasoning for command-level patterns
    for pattern in _DESTRUCTIVE_PATTERNS:
        if pattern in reasoning:
            return True

    # Check reasoning for compound destructive phrases
    for compound in _DESTRUCTIVE_COMPOUNDS:
        if compound in reasoning:
            return True

    return False
