"""
Reflection Engine — detects failure patterns, generates structured reflections,
and retrieves relevant past learnings for the agent to act on.

This implements the self-improvement loop from UI-Genie and GUI-Reflection:
  1. Detect failure (action produced no change)
  2. Classify the failure type
  3. Generate structured reflection
  4. Store in episodic memory
  5. Retrieve relevant past reflections for future similar tasks
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from uacc.actions.schema import Action, ClickAction, DragAction, HoverAction, TypeAction
from uacc.agent.memory import FailureReflection, SessionMemory

logger = logging.getLogger(__name__)


class FailureClass(enum.Enum):
    """Taxonomy of common failure patterns in UI control."""

    COORDINATE_MISMATCH = "coordinate_mismatch"  # Clicked wrong spot
    TIMING_ERROR = "timing_error"  # Action too fast/slow
    WRONG_APP_FOCUS = "wrong_app_focus"  # Wrong window was active
    ELEMENT_NOT_FOUND = "element_not_found"  # Expected element didn't exist
    WRONG_PARAMETER = "wrong_parameter"  # Correct action, wrong value
    MODALITY_ERROR = "modality_error"  # GUI when keyboard would work
    WINDOW_BLOCKED = "window_blocked"  # Dialog blocking interaction
    UNKNOWN = "unknown"


@dataclass
class ClassifiedFailure:
    """A classified failure with structured analysis."""

    classification: FailureClass
    confidence: float  # 0.0 – 1.0
    description: str
    root_cause: str = ""
    suggested_alternative: str = ""


@dataclass
class Reflection:
    """A complete reflection entry with context for future retrieval."""

    task_context: str
    action_name: str
    action_params: Dict[str, Any] = field(default_factory=dict)
    screen_context: str = ""
    classification: str = "unknown"
    description: str = ""
    root_cause: str = ""
    suggested_fix: str = ""
    resolved: bool = False
    created_at: float = 0.0


class ReflectionEngine:
    """Analyzes action failures, classifies them, and generates structured
    reflections for cross-session learning.

    Integrates with:
    - RecoveryEngine: receives failure context
    - SessionMemory: stores/retrieves reflections
    - Agent loop: provides past reflections as context to the LLM
    """

    def __init__(self, memory: Optional[SessionMemory] = None):
        self.memory = memory
        self._session_reflections: List[Reflection] = []

    def set_memory(self, memory: SessionMemory) -> None:
        self.memory = memory

    def analyze_failure(
        self,
        action: Action,
        result: Dict[str, Any],
        screen_state: Dict[str, Any],
        task_goal: str = "",
    ) -> ClassifiedFailure:
        """Classify the failure and determine its root cause.

        Args:
            action: The action that failed.
            result: The execution result.
            screen_state: Current screen state dict.
            task_goal: The overall task goal for context.

        Returns:
            ClassifiedFailure with analysis.
        """
        action_name = getattr(action, "action", "?")
        message = result.get("message", "").lower()

        # Pattern 1: Coordinate mismatch (click/hover/drag that missed)
        if isinstance(action, (ClickAction, HoverAction, DragAction)):
            if "coordinate" in message or "out of bounds" in message or "miss" in message:
                return ClassifiedFailure(
                    classification=FailureClass.COORDINATE_MISMATCH,
                    confidence=0.9,
                    description="Target coordinates did not match any interactive element",
                    root_cause="LLM estimated element position incorrectly",
                    suggested_alternative="Use find_element + click_element instead of raw coordinates",
                )
            if "safe mode" in message or "blocked" in message:
                return ClassifiedFailure(
                    classification=FailureClass.WRONG_PARAMETER,
                    confidence=0.85,
                    description="Action was blocked by safe mode",
                    root_cause="Action targeted a potentially destructive element",
                    suggested_alternative="Verify the target element before clicking",
                )

        # Pattern 2: Wrong window focus
        if isinstance(action, (TypeAction,)):
            text_map = screen_state.get("text_map", "")
            if "text_input" not in text_map and "edit" not in text_map:
                return ClassifiedFailure(
                    classification=FailureClass.WRONG_APP_FOCUS,
                    confidence=0.75,
                    description="Typed into a non-input area — wrong window or field focused",
                    root_cause="Target window/field was not focused before typing",
                    suggested_alternative="Click the input field first to focus it, then type",
                )

        # Pattern 3: Timing error (action executed too fast)
        if "disappeared" in message or "not found" in message:
            return ClassifiedFailure(
                classification=FailureClass.TIMING_ERROR,
                confidence=0.6,
                description="Element changed state before action completed",
                root_cause="Action executed before UI finished updating",
                suggested_alternative="Add a wait action before this step",
            )

        # Pattern 4: Window blocked by dialog
        text_map = screen_state.get("text_map", "")
        if any(w in text_map.lower() for w in ["dialog", "alert", "confirm", "error message", "notification"]):
            return ClassifiedFailure(
                classification=FailureClass.WINDOW_BLOCKED,
                confidence=0.6,
                description="A dialog is blocking interaction with the target window",
                root_cause="Unexpected dialog appeared",
                suggested_alternative="Close the dialog first before proceeding",
            )

        # Pattern 5: Element not found
        if "not found" in message or "no element" in message:
            return ClassifiedFailure(
                classification=FailureClass.ELEMENT_NOT_FOUND,
                confidence=0.8,
                description="Expected UI element was not present on screen",
                root_cause="Element may not exist, may be in a different state, or may need scrolling",
                suggested_alternative="Take a screenshot to verify the current UI state",
            )

        # Default: unknown
        return ClassifiedFailure(
            classification=FailureClass.UNKNOWN,
            confidence=0.3,
            description=f"Action '{action_name}' failed: {result.get('message', '')}",
            root_cause="Could not determine root cause",
            suggested_alternative="Re-observe the screen and try an alternative approach",
        )

    def generate_reflection(
        self,
        classification: ClassifiedFailure,
        action: Action,
        screen_state: Dict[str, Any],
        task_goal: str = "",
    ) -> Reflection:
        """Generate a structured reflection from a classified failure."""
        action_params = {}
        for field_name in ("x", "y", "text", "keys", "start_x", "start_y", "end_x", "end_y", "reasoning"):
            val = getattr(action, field_name, None)
            if val is not None:
                action_params[field_name] = val

        screen_context = screen_state.get("text_map", "")[:500]

        reflection = Reflection(
            task_context=task_goal,
            action_name=getattr(action, "action", "?"),
            action_params=action_params,
            screen_context=screen_context,
            classification=classification.classification.value,
            description=classification.description,
            root_cause=classification.root_cause,
            suggested_fix=classification.suggested_alternative,
            created_at=time.time(),
        )

        self._session_reflections.append(reflection)

        # Persist to episodic memory if available
        if self.memory is not None:
            self.memory.record_reflection(FailureReflection(
                task_goal=task_goal,
                failed_action=getattr(action, "action", "?"),
                expected_outcome="Screen should have changed after action",
                actual_outcome=classification.description,
                root_cause=classification.root_cause,
                suggested_fix=classification.suggested_alternative,
                timestamp=time.time(),
            ))

        logger.info(
            "Generated %s reflection: %s",
            classification.classification.value,
            classification.root_cause[:80],
        )

        return reflection

    def get_relevant_reflections(self, task_goal: str = "", max_results: int = 3) -> List[str]:
        """Get formatted summaries of relevant past reflections for LLM context."""
        if self.memory is None:
            return []

        reflections = self.memory.get_reflections(task_goal=task_goal, max_results=max_results)
        summaries = []
        for ref in reflections:
            summaries.append(
                f"[Past failure] Action: {ref.failed_action} | "
                f"Cause: {ref.root_cause} | Fix: {ref.suggested_fix}"
            )
        return summaries
