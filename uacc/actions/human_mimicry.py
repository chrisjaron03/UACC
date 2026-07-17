"""
Human Mimicry — make mouse movement and typing look natural.

Bézier curve paths with slight randomness, variable keystroke delays,
and configurable speed profiles.
"""

from __future__ import annotations

import math
import random
import time
from typing import Tuple

import pyautogui


# ── Mouse Movement ───────────────────────────────────────────

def move_mouse_human(
    start: Tuple[int, int],
    end: Tuple[int, int],
    duration_ms: int = 300,
    steps: int = 0,
    jitter: int = 3,
) -> None:
    """Move the mouse along a Bézier curve with slight randomness.

    Args:
        start: Starting (x, y) position.
        end: Target (x, y) position.
        duration_ms: Total movement time in milliseconds.
        steps: Number of intermediate points (0 = auto based on distance).
        jitter: Max random pixel offset at each step for natural feel.
    """
    sx, sy = start
    ex, ey = end

    # Calculate distance for adaptive step count
    dist = math.hypot(ex - sx, ey - sy)
    if dist < 5:
        pyautogui.moveTo(ex, ey)
        return

    if steps == 0:
        # ~1 step per 8 pixels, minimum 10, max 80
        steps = max(10, min(80, int(dist / 8)))

    # Generate 1–2 random control points for a quadratic/cubic Bézier
    cp1 = _random_control_point(sx, sy, ex, ey, spread=0.3)
    cp2 = _random_control_point(sx, sy, ex, ey, spread=0.2)

    step_delay = (duration_ms / 1000) / steps

    for i in range(steps + 1):
        t = i / steps

        # Ease-in-out timing
        t = _ease_in_out(t)

        # Cubic Bézier
        x = _cubic_bezier(sx, cp1[0], cp2[0], ex, t)
        y = _cubic_bezier(sy, cp1[1], cp2[1], ey, t)

        # Add jitter (decreasing as we approach the target)
        if i < steps:
            jitter_scale = 1.0 - (i / steps) ** 2  # Less jitter near the end
            x += random.randint(-jitter, jitter) * jitter_scale
            y += random.randint(-jitter, jitter) * jitter_scale

        pyautogui.moveTo(int(x), int(y), _pause=False)

        # Variable delay (slightly random)
        actual_delay = step_delay * random.uniform(0.8, 1.2)
        if actual_delay > 0.001:
            time.sleep(actual_delay)

    # Ensure we land exactly on target
    pyautogui.moveTo(ex, ey, _pause=False)


def _cubic_bezier(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    """Evaluate a cubic Bézier at parameter t ∈ [0, 1]."""
    return (
        (1 - t) ** 3 * p0
        + 3 * (1 - t) ** 2 * t * p1
        + 3 * (1 - t) * t ** 2 * p2
        + t ** 3 * p3
    )


def _random_control_point(
    sx: float, sy: float, ex: float, ey: float, spread: float = 0.3
) -> Tuple[float, float]:
    """Generate a random control point offset from the midpoint."""
    mx = (sx + ex) / 2
    my = (sy + ey) / 2
    dist = math.hypot(ex - sx, ey - sy)
    offset = dist * spread

    # Offset perpendicular to the line
    angle = math.atan2(ey - sy, ex - sx)
    perp_angle = angle + math.pi / 2 + random.uniform(-0.5, 0.5)

    cpx = mx + offset * math.cos(perp_angle) * random.uniform(-1, 1)
    cpy = my + offset * math.sin(perp_angle) * random.uniform(-1, 1)
    return (cpx, cpy)


def _ease_in_out(t: float) -> float:
    """Smoothstep ease-in-out curve."""
    return t * t * (3 - 2 * t)


# ── Typing ───────────────────────────────────────────────────

# Approximate typing speed profiles (WPM → ms per character)
SPEED_PROFILES = {
    "slow": (150, 300),      # 30-40 WPM
    "normal": (60, 150),     # 60-80 WPM
    "fast": (30, 80),        # 100-120 WPM
}


def type_human(
    text: str,
    speed: str = "normal",
    mistake_rate: float = 0.0,
) -> None:
    """Type text with variable per-character delays to mimic human typing.

    Args:
        text: The text to type.
        speed: Speed profile — "slow", "normal", "fast".
        mistake_rate: Probability of making a typo (0.0–1.0).
                      Set to 0 for reliability (default).
    """
    min_delay, max_delay = SPEED_PROFILES.get(speed, SPEED_PROFILES["normal"])

    for i, char in enumerate(text):
        # Simulate occasional mistakes
        if mistake_rate > 0 and random.random() < mistake_rate:
            wrong_char = chr(ord(char) + random.choice([-1, 1]))
            pyautogui.press(wrong_char) if wrong_char.isprintable() else None
            time.sleep(random.uniform(0.1, 0.3))
            pyautogui.press("backspace")
            time.sleep(random.uniform(0.05, 0.15))

        # Type the character
        if char == "\n":
            pyautogui.press("enter")
        elif char == "\t":
            pyautogui.press("tab")
        else:
            pyautogui.write(char)

        # Variable delay
        delay_ms = random.uniform(min_delay, max_delay)

        # Longer pause after punctuation and spaces
        if char in ".!?,;:\n":
            delay_ms *= random.uniform(1.5, 3.0)
        elif char == " ":
            delay_ms *= random.uniform(1.0, 1.5)

        # Occasional longer "thinking" pause
        if random.random() < 0.02:
            delay_ms *= random.uniform(3.0, 6.0)

        time.sleep(delay_ms / 1000)


# ── Drag Helper ──────────────────────────────────────────────

def drag_human(
    start: Tuple[int, int],
    end: Tuple[int, int],
    duration_ms: int = 500,
    button: str = "left",
) -> None:
    """Perform a human-like drag operation.

    Moves to start, presses mouse, moves along Bézier to end, releases.
    """
    # Move to start
    current = pyautogui.position()
    move_mouse_human(current, start, duration_ms=200)
    time.sleep(0.05)

    # Press
    pyautogui.mouseDown(button=button)
    time.sleep(0.05)

    # Drag along curve
    move_mouse_human(start, end, duration_ms=duration_ms, jitter=2)
    time.sleep(0.05)

    # Release
    pyautogui.mouseUp(button=button)
