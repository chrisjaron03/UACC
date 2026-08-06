"""
Mouse Sentinel — real-time mouse position monitoring for user override detection.

Runs a background thread that polls cursor position at 20Hz.
If the user moves the mouse more than the kill distance from the
last expected position (set after UACC mouse actions), a kill flag
is raised that stops all pending UACC operations.

Kill distance is adaptive: calculated as 1 inch at the current DPI,
with a minimum of 150px.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SAFETY_LOG_DIR = os.path.expanduser("~/.uacc/safety_logs")


def is_escape_pressed() -> bool:
    """Check if Escape key is pressed (Win32 GetAsyncKeyState or fallback)."""
    try:
        import ctypes
        if hasattr(ctypes, "windll") and hasattr(ctypes.windll, "user32"):
            # VK_ESCAPE = 0x1B
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x1B) & 0x8000)
    except Exception:
        pass
    return False


class MouseSentinel:
    """Background thread monitoring mouse position for user override detection."""

    def __init__(self, kill_distance_px: int = 40):
        self._kill_distance = kill_distance_px
        self._expected_pos: tuple[int, int] | None = None
        self._killed = False
        self._active = False
        self._running = False
        self._is_moving = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_uacc_call: float = 0.0
        self._pyautogui = None
        self._get_cursor_pos = None
        self._init_cursor_lib()

    def _init_cursor_lib(self):
        try:
            import pyautogui
            self._pyautogui = pyautogui
        except ImportError:
            try:
                import ctypes
                user32 = ctypes.windll.user32
                self._get_cursor_pos = user32.GetCursorPos
            except Exception:
                logger.warning("No cursor library available; sentinel disabled")
                self._active = False

    def _get_cursor(self):
        if self._pyautogui:
            pos = self._pyautogui.position()
            return pos.x, pos.y
        if self._get_cursor_pos:
            import ctypes
            class POINT(ctypes.Structure):
                _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
            pt = POINT()
            self._get_cursor_pos(ctypes.byref(pt))
            return pt.x, pt.y
        return None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._active = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True, name="mouse-sentinel")
        self._thread.start()
        logger.info("MouseSentinel started (kill_distance=%dpx, 20Hz polling)", self._kill_distance)

    def stop(self) -> None:
        self._running = False
        self._active = False
        if self._thread:
            self._thread.join(timeout=1.0)
        logger.info("MouseSentinel stopped")

    def set_expected_position(self, x: int, y: int) -> None:
        with self._lock:
            self._expected_pos = (x, y)
            self._last_uacc_call = time.time()
        logger.debug("Expected position set to (%d, %d)", x, y)

    def set_moving(self, moving: bool) -> None:
        """Mark whether UACC is currently in the middle of executing a mouse movement."""
        with self._lock:
            self._is_moving = moving
            if moving:
                self._last_uacc_call = time.time()

    def check_killed(self) -> bool:
        with self._lock:
            if self._killed:
                elapsed = time.time() - self._last_uacc_call
                if elapsed > 5.0:
                    self._killed = False
                    logger.info("Auto-acknowledge: %0.1fs since last UACC call", elapsed)
            return self._killed

    def acknowledge_override(self) -> None:
        with self._lock:
            self._killed = False
        logger.info("Override acknowledged, kill flag reset")

    def get_kill_distance(self) -> int:
        with self._lock:
            return self._kill_distance

    def set_kill_distance(self, px: int) -> None:
        if px <= 0:
            px = self._calculate_kill_distance()
        with self._lock:
            self._kill_distance = px
        logger.info("Kill distance set to %dpx", px)

    def is_active(self) -> bool:
        return self._active

    @staticmethod
    def _calculate_kill_distance() -> int:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            dpi = user32.GetDpiForSystem()
            return max(30, int(dpi * 0.25))
        except Exception:
            return 40

    def _log_override(self, distance: int, threshold: int, event_type: str = "mouse_override") -> None:
        try:
            os.makedirs(_SAFETY_LOG_DIR, exist_ok=True)
            try:
                from uacc.core.window_manager import get_active_window
                win = get_active_window()
                app_name = win.title if win else "unknown"
            except Exception:
                app_name = "unknown"
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "distance_px": distance,
                "threshold_px": threshold,
                "active_app": app_name,
            }
            log_file = os.path.join(_SAFETY_LOG_DIR, "mouse_overrides.jsonl")
            with open(log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            logger.debug("Failed to log override: %s", exc)

    def _monitor_loop(self) -> None:
        while self._running:
            if self._active:
                if is_escape_pressed():
                    with self._lock:
                        self._killed = True
                        self._last_uacc_call = time.time()
                    logger.warning("User override detected: Escape key pressed")
                    self._log_override(0, 0, event_type="escape_key_override")
                else:
                    with self._lock:
                        killed = self._killed
                        expected = self._expected_pos
                        is_moving = self._is_moving
                    if not killed and expected is not None and not is_moving:
                        cursor = self._get_cursor()
                        if cursor is not None:
                            cx, cy = cursor
                            dx = cx - expected[0]
                            dy = cy - expected[1]
                            distance = math.sqrt(dx * dx + dy * dy)
                            if distance > self._kill_distance:
                                with self._lock:
                                    self._killed = True
                                    self._last_uacc_call = time.time()
                                logger.warning(
                                    "User override detected: mouse moved %dpx (threshold: %dpx)",
                                    round(distance), self._kill_distance,
                                )
                                self._log_override(round(distance), self._kill_distance, event_type="mouse_override")
            time.sleep(0.05)
