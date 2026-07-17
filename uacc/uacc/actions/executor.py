"""
Action Executor — translate typed Action objects into real mouse / keyboard events.

Uses pyautogui for cross-platform input simulation, with optional
human-like movement from the mimicry module.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import pyautogui

from uacc.actions.human_mimicry import move_mouse_human, type_human
from uacc.actions.schema import (
    Action,
    ClickAction,
    DoneAction,
    DragAction,
    HotkeyAction,
    HoverAction,
    ScrollAction,
    ScreenshotAction,
    TypeAction,
    WaitAction,
    is_potentially_destructive,
    LaunchAction,
    ClipboardAction,
    FocusWindowAction,
)
from uacc.config import config

logger = logging.getLogger(__name__)

# Safety: disable pyautogui's fail-safe (move mouse to corner to abort)
pyautogui.FAILSAFE = config.uacc.pyautogui_failsafe
pyautogui.PAUSE = 0.05  # Small pause between pyautogui calls


class ActionExecutor:
    """Dispatches Action objects to real input events."""

    def __init__(
        self,
        human_mimicry: Optional[bool] = None,
        action_delay_ms: Optional[int] = None,
        safe_mode: Optional[bool] = None,
    ):
        self.human_mimicry = (
            human_mimicry if human_mimicry is not None else config.uacc.human_mimicry
        )
        self.action_delay_ms = (
            action_delay_ms if action_delay_ms is not None else config.uacc.action_delay_ms
        )
        self.safe_mode = safe_mode if safe_mode is not None else config.uacc.safe_mode
        self._last_action_time = 0.0

    def execute(self, action: Action) -> dict:
        """Execute a single action and return a result dict.

        Returns:
            {"success": bool, "message": str, "action": str}
        """
        # Safety check
        if self.safe_mode and is_potentially_destructive(action):
            logger.warning("Destructive action detected: %s", action)
            return {
                "success": False,
                "message": "Action blocked by safe mode (potentially destructive)",
                "action": getattr(action, "action", "unknown"),
            }

        # Inter-action delay
        elapsed = (time.time() - self._last_action_time) * 1000
        if elapsed < self.action_delay_ms:
            time.sleep((self.action_delay_ms - elapsed) / 1000)

        try:
            result = self._dispatch(action)
            self._last_action_time = time.time()
            return result
        except Exception as exc:
            logger.error("Action execution failed: %s", exc)
            return {
                "success": False,
                "message": f"Execution error: {exc}",
                "action": getattr(action, "action", "unknown"),
            }

    def _dispatch(self, action: Action) -> dict:
        """Route to the appropriate handler."""
        if isinstance(action, ClickAction):
            return self._click(action)
        elif isinstance(action, DragAction):
            return self._drag(action)
        elif isinstance(action, TypeAction):
            return self._type(action)
        elif isinstance(action, HotkeyAction):
            return self._hotkey(action)
        elif isinstance(action, ScrollAction):
            return self._scroll(action)
        elif isinstance(action, HoverAction):
            return self._hover(action)
        elif isinstance(action, WaitAction):
            return self._wait(action)
        elif isinstance(action, ScreenshotAction):
            return self._screenshot(action)
        elif isinstance(action, DoneAction):
            return self._done(action)
        elif isinstance(action, LaunchAction):
            return self._launch(action)
        elif isinstance(action, ClipboardAction):
            return self._clipboard(action)
        elif isinstance(action, FocusWindowAction):
            return self._focus_window(action)
        else:
            return {"success": False, "message": f"Unknown action: {action}", "action": "unknown"}

    # ── Handlers ─────────────────────────────────────────────

    def _click(self, action: ClickAction) -> dict:
        """Execute a click action."""
        # Move to position
        if self.human_mimicry:
            current = pyautogui.position()
            move_mouse_human(current, (action.x, action.y))
        else:
            pyautogui.moveTo(action.x, action.y, duration=0.1)

        # Apply modifiers
        for mod in action.modifiers:
            pyautogui.keyDown(mod)

        # Click
        button = action.button.value
        if action.count == 2:
            pyautogui.doubleClick(action.x, action.y, button=button)
        else:
            for _ in range(action.count):
                pyautogui.click(action.x, action.y, button=button)

        # Release modifiers
        for mod in reversed(action.modifiers):
            pyautogui.keyUp(mod)

        logger.info(
            "Click: (%d, %d) button=%s count=%d", action.x, action.y, button, action.count
        )
        return {
            "success": True,
            "message": f"Clicked at ({action.x}, {action.y})",
            "action": "click",
        }

    def _drag(self, action: DragAction) -> dict:
        """Execute a drag action with smooth movement."""
        if self.human_mimicry:
            current = pyautogui.position()
            move_mouse_human(current, (action.start_x, action.start_y))

        duration = action.duration_ms / 1000
        pyautogui.moveTo(action.start_x, action.start_y, duration=0.1)
        pyautogui.mouseDown(button=action.button.value)

        if self.human_mimicry:
            move_mouse_human(
                (action.start_x, action.start_y),
                (action.end_x, action.end_y),
                duration_ms=action.duration_ms,
            )
        else:
            pyautogui.moveTo(action.end_x, action.end_y, duration=duration)

        pyautogui.mouseUp(button=action.button.value)

        logger.info(
            "Drag: (%d,%d) → (%d,%d)",
            action.start_x, action.start_y, action.end_x, action.end_y,
        )
        return {
            "success": True,
            "message": f"Dragged ({action.start_x},{action.start_y}) → ({action.end_x},{action.end_y})",
            "action": "drag",
        }

    def _type(self, action: TypeAction) -> dict:
        """Type text."""
        if self.human_mimicry and action.delay_ms == 0:
            type_human(action.text)
        elif action.delay_ms > 0:
            pyautogui.typewrite(action.text, interval=action.delay_ms / 1000)
        else:
            # Use write() for fast typing — handles special characters
            pyautogui.write(action.text)

        logger.info("Typed: %s", action.text[:50])
        return {
            "success": True,
            "message": f"Typed {len(action.text)} characters",
            "action": "type",
        }

    def _hotkey(self, action: HotkeyAction) -> dict:
        """Press a hotkey combination."""
        pyautogui.hotkey(*action.keys)
        combo = "+".join(action.keys)
        logger.info("Hotkey: %s", combo)
        return {"success": True, "message": f"Pressed {combo}", "action": "hotkey"}

    def _scroll(self, action: ScrollAction) -> dict:
        """Scroll at a position."""
        pyautogui.moveTo(action.x, action.y, duration=0.05)
        if action.direction.value in ("up", "down"):
            clicks = action.amount if action.direction.value == "up" else -action.amount
            pyautogui.scroll(clicks, action.x, action.y)
        else:
            clicks = action.amount if action.direction.value == "right" else -action.amount
            pyautogui.hscroll(clicks, action.x, action.y)

        logger.info("Scroll: %s ×%d at (%d,%d)", action.direction.value, action.amount, action.x, action.y)
        return {
            "success": True,
            "message": f"Scrolled {action.direction.value} ×{action.amount}",
            "action": "scroll",
        }

    def _hover(self, action: HoverAction) -> dict:
        """Hover at a position."""
        if self.human_mimicry:
            current = pyautogui.position()
            move_mouse_human(current, (action.x, action.y))
        else:
            pyautogui.moveTo(action.x, action.y, duration=0.15)

        time.sleep(action.duration_ms / 1000)
        logger.info("Hover: (%d,%d) for %dms", action.x, action.y, action.duration_ms)
        return {
            "success": True,
            "message": f"Hovered at ({action.x}, {action.y}) for {action.duration_ms}ms",
            "action": "hover",
        }

    def _wait(self, action: WaitAction) -> dict:
        """Wait for a fixed duration."""
        time.sleep(action.duration_ms / 1000)
        logger.info("Waited: %dms", action.duration_ms)
        return {
            "success": True,
            "message": f"Waited {action.duration_ms}ms",
            "action": "wait",
        }

    def _screenshot(self, action: ScreenshotAction) -> dict:
        """Signal that a screenshot should be taken (handled by controller)."""
        logger.info("Screenshot requested")
        return {
            "success": True,
            "message": "Screenshot requested — will be captured by controller",
            "action": "screenshot",
        }

    def _done(self, action: DoneAction) -> dict:
        """Signal task completion."""
        logger.info("Task done: success=%s result=%s", action.success, action.result)
        return {
            "success": action.success,
            "message": action.result or "Task completed",
            "action": "done",
        }

    def _launch(self, action: LaunchAction) -> dict:
        """Launch an application."""
        from uacc.core.window_manager import launch_application
        res = launch_application(action.name_or_path, action.arguments)
        return {**res, "action": "launch"}

    def _clipboard(self, action: ClipboardAction) -> dict:
        """Read from or write to the clipboard."""
        from uacc.core.clipboard import read_clipboard, write_clipboard
        if action.mode == "write":
            res = write_clipboard(action.text)
        else:
            res = read_clipboard()
        return {**res, "action": "clipboard"}

    def _focus_window(self, action: FocusWindowAction) -> dict:
        """Focus a window."""
        from uacc.core.window_manager import focus_window
        res = focus_window(action.title)
        return {**res, "action": "focus_window"}
