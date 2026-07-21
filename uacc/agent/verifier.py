"""
Action Verifier — pre/post verification to ensure actions hit the right
targets and produce the expected screen changes.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from PIL import Image

from uacc.actions.schema import Action, ClickAction, DragAction, HoverAction
from uacc.core.screen_capture import capture_full
from uacc.core.screen_diff import DiffResult, compute_diff

logger = logging.getLogger(__name__)


@dataclass
class VerificationResult:
    """Result of a pre or post-action verification check."""

    passed: bool
    message: str
    corrected_x: Optional[int] = None
    corrected_y: Optional[int] = None
    diff: Optional[DiffResult] = None


class ActionVerifier:
    """Verifies that actions are targeting the right elements and
    producing the expected screen changes.

    Combines pixel-level diffing with semantic checks (window title,
    text map content, element counts) for more reliable verification.
    """

    def __init__(
        self,
        verify_pre: bool = True,
        verify_post: bool = True,
        post_wait_ms: int = 300,
        max_retries: int = 2,
    ):
        self.verify_pre = verify_pre
        self.verify_post = verify_post
        self.post_wait_ms = post_wait_ms
        self.max_retries = max_retries
        self._last_screenshot: Optional[Image.Image] = None
        self._last_text_map: Optional[str] = None
        self._last_window_title: str = ""

    def capture_before(self, text_map: Optional[str] = None, window_title: str = "") -> Image.Image:
        """Capture a screenshot before an action (for post-action diffing).

        Args:
            text_map: Compact text map of the screen before the action.
            window_title: Active window title before the action.
        """
        self._last_screenshot = capture_full()
        self._last_text_map = text_map
        self._last_window_title = window_title
        return self._last_screenshot

    def pre_verify(
        self,
        action: Action,
        text_map_elements: list,
    ) -> VerificationResult:
        """Verify that the target coordinates match a known element.

        For click/hover/drag actions, checks that the target coordinates
        actually correspond to an interactive element from the text map.

        Args:
            action: The action about to be executed.
            text_map_elements: List of ScreenElement dicts from the text map.

        Returns:
            VerificationResult — passed=True if target is valid, or includes
            corrected coordinates if a nearby match was found.
        """
        if not self.verify_pre:
            return VerificationResult(passed=True, message="Pre-verification disabled")

        # Only verify coordinate-based actions
        target_xy = self._get_target_coords(action)
        if target_xy is None:
            return VerificationResult(passed=True, message="Non-coordinate action — skip")

        tx, ty = target_xy

        # Find the nearest interactive element to the target
        nearest = None
        nearest_dist = float("inf")

        for elem in text_map_elements:
            if not (elem.get("clickable") or elem.get("editable") or elem.get("expandable")):
                continue
            center = elem.get("center", [0, 0])
            cx, cy = center[0], center[1]
            dist = ((cx - tx) ** 2 + (cy - ty) ** 2) ** 0.5
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = elem

        if nearest is None:
            return VerificationResult(
                passed=True,
                message="No interactive elements found — proceeding anyway",
            )

        # If target is within 15px of an element, it's fine
        if nearest_dist <= 15:
            return VerificationResult(
                passed=True,
                message=f"Target matches element '{nearest.get('name', '?')}' (dist={nearest_dist:.0f}px)",
            )

        # If target is within 50px, suggest correction
        if nearest_dist <= 50:
            center = nearest.get("center", [tx, ty])
            return VerificationResult(
                passed=True,
                message=(
                    f"Target slightly off — nearest element '{nearest.get('name', '?')}' "
                    f"is {nearest_dist:.0f}px away. Correcting to ({center[0]}, {center[1]})"
                ),
                corrected_x=center[0],
                corrected_y=center[1],
            )

        # Target is far from any element — warn but proceed
        return VerificationResult(
            passed=True,
            message=(
                f"Warning: target ({tx}, {ty}) is {nearest_dist:.0f}px from nearest "
                f"element '{nearest.get('name', '?')}'. Proceeding anyway."
            ),
        )

    def post_verify(
        self,
        action: Action,
        expected_change: bool = True,
        text_map: Optional[str] = None,
        window_title: str = "",
    ) -> VerificationResult:
        """Verify that the action produced a screen change — pixel + semantic.

        Uses both pixel-level diffing and semantic checks (window title,
        text map content, element counts) for robust verification.

        Args:
            action: The action that was just executed.
            expected_change: Whether we expect the screen to have changed.
            text_map: Text map after the action (for semantic diff).
            window_title: Window title after the action.

        Returns:
            VerificationResult with diff information.
        """
        if not self.verify_post or self._last_screenshot is None:
            return VerificationResult(passed=True, message="Post-verification disabled/skipped")

        # Wait for UI to settle
        time.sleep(self.post_wait_ms / 1000)

        after = capture_full()
        diff = compute_diff(
            self._last_screenshot,
            after,
            before_text_map=self._last_text_map,
            after_text_map=text_map,
            before_window_title=self._last_window_title,
            after_window_title=window_title,
        )

        if expected_change:
            if diff.changed:
                msg = f"Screen changed (pixel: {diff.changed_percentage:.1f}%)"
                if diff.semantic and diff.semantic.changed:
                    msg += f" | semantic: {diff.semantic.summary}"
                return VerificationResult(passed=True, message=msg, diff=diff)
            else:
                return VerificationResult(
                    passed=False,
                    message="Expected screen change but nothing changed — action may have missed",
                    diff=diff,
                )
        else:
            return VerificationResult(
                passed=not diff.changed,
                message=(
                    "No change (as expected)"
                    if not diff.changed
                    else f"Unexpected change detected ({diff.changed_percentage:.1f}%)"
                ),
                diff=diff,
            )

    def _get_target_coords(self, action: Action) -> Optional[Tuple[int, int]]:
        """Extract target (x, y) from a coordinate-based action."""
        if isinstance(action, (ClickAction, HoverAction)):
            return (action.x, action.y)
        elif isinstance(action, DragAction):
            return (action.start_x, action.start_y)
        return None

    def apply_correction(self, action: Action, result: VerificationResult) -> Action:
        """Apply coordinate correction to an action if the verifier suggested one."""
        if result.corrected_x is None or result.corrected_y is None:
            return action

        if isinstance(action, ClickAction):
            action.x = result.corrected_x
            action.y = result.corrected_y
        elif isinstance(action, HoverAction):
            action.x = result.corrected_x
            action.y = result.corrected_y
        elif isinstance(action, DragAction):
            action.start_x = result.corrected_x
            action.start_y = result.corrected_y

        logger.info(
            "Applied coordinate correction: → (%d, %d)",
            result.corrected_x,
            result.corrected_y,
        )
        return action
