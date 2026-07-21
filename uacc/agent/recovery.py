"""
4-Tier Error Recovery — Retry → Shift → Backtrack → Escalate.

When an action fails (post-verification detects no change), the recovery
system escalates through increasingly aggressive strategies:

  Tier 1: Local refinement  — retry with corrected parameters
  Tier 2: Modality shift    — try a different approach (GUI→keyboard→CLI)
  Tier 3: Backtrack         — restore to known-good state, try alternative
  Tier 4: Escalate          — ask the user for guidance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional

from uacc.actions.schema import Action, ClickAction, HotkeyAction, TypeAction

logger = logging.getLogger(__name__)


class RecoveryTier(Enum):
    NONE = auto()
    LOCAL_REFINEMENT = auto()
    MODALITY_SHIFT = auto()
    BACKTRACK = auto()
    ESCALATE = auto()


@dataclass
class RecoveryAction:
    """A suggested recovery action from the recovery system."""

    tier: RecoveryTier
    action: Optional[Action] = None
    description: str = ""
    requires_approval: bool = False


@dataclass
class RecoverySession:
    """Tracks the recovery state for a single task session."""

    consecutive_failures: int = 0
    current_tier: RecoveryTier = RecoveryTier.NONE
    failed_action_name: str = ""
    failed_coords: tuple = (0, 0)
    tried_methods: List[str] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)

    def record_failure(self, action: Action, result: Dict[str, Any]) -> None:
        self.consecutive_failures += 1
        self.failed_action_name = getattr(action, "action", "?")
        if isinstance(action, (ClickAction,)):
            self.failed_coords = (action.x, action.y)
        self.history.append({
            "action": self.failed_action_name,
            "result": result.get("message", ""),
            "tier": self.current_tier.name,
        })

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.current_tier = RecoveryTier.NONE
        self.tried_methods.clear()

    def next_tier(self) -> RecoveryAction:
        """Determine the next recovery tier based on failure count."""
        if self.consecutive_failures <= 1:
            self.current_tier = RecoveryTier.LOCAL_REFINEMENT
            return RecoveryAction(
                tier=RecoveryTier.LOCAL_REFINEMENT,
                description="Retrying with adjusted parameters",
            )
        elif self.consecutive_failures <= 3:
            self.current_tier = RecoveryTier.MODALITY_SHIFT
            return RecoveryAction(
                tier=RecoveryTier.MODALITY_SHIFT,
                description="Trying a different approach",
            )
        elif self.consecutive_failures <= 5:
            self.current_tier = RecoveryTier.BACKTRACK
            return RecoveryAction(
                tier=RecoveryTier.BACKTRACK,
                description="Backtracking to previous known state",
                requires_approval=True,
            )
        else:
            self.current_tier = RecoveryTier.ESCALATE
            return RecoveryAction(
                tier=RecoveryTier.ESCALATE,
                description="Unable to proceed — requesting user guidance",
                requires_approval=True,
            )


class RecoveryEngine:
    """Orchestrates multi-tier recovery from action failures.

    Usage:
        engine = RecoveryEngine()
        recovery = engine.handle_failure(action, result, screen_state)
        if recovery.tier == RecoveryTier.MODALITY_SHIFT:
            alt_action = engine.suggest_modality_shift(action)
    """

    def __init__(self):
        self.session = RecoverySession()

    def handle_failure(
        self,
        action: Action,
        result: Dict[str, Any],
        screen_state: Dict[str, Any],
    ) -> RecoveryAction:
        """Process an action failure and determine the next recovery step."""
        self.session.record_failure(action, result)
        recovery = self.session.next_tier()

        action_name = getattr(action, "action", "?")
        logger.info(
            "Recovery tier %s for '%s' (failure #%d): %s",
            recovery.tier.name,
            action_name,
            self.session.consecutive_failures,
            recovery.description,
        )

        if recovery.tier == RecoveryTier.MODALITY_SHIFT:
            alt = self._suggest_alternative(action)
            if alt:
                recovery.action = alt
                recovery.description = f"Modality shift: trying {getattr(alt, 'action', '?')} instead"

        return recovery

    def handle_success(self) -> None:
        """Reset recovery state after a successful action."""
        self.session.record_success()

    def _suggest_alternative(self, action: Action) -> Optional[Action]:
        """Suggest an alternative approach for a failed action.

        Implements modality shift: if clicking failed, try keyboard shortcut.
        If typing failed, try clipboard paste. Etc.
        """
        action_name = getattr(action, "action", "?")

        if action_name == "click" and isinstance(action, ClickAction):
            method = f"click_{action.x}_{action.y}"
            if method not in self.session.tried_methods:
                self.session.tried_methods.append(method)
                return action  # Try same click again (might be timing)

        if action_name == "type" and isinstance(action, TypeAction):
            method = f"clipboard_{action.text[:20]}"
            if method not in self.session.tried_methods:
                self.session.tried_methods.append(method)
                return HotkeyAction(
                    keys=["ctrl", "v"],
                    reasoning=f"Type via clipboard paste instead (failed: {action.reasoning})",
                )

        return None

    def get_status(self) -> Dict[str, Any]:
        """Return current recovery state for logging/debugging."""
        return {
            "consecutive_failures": self.session.consecutive_failures,
            "current_tier": self.session.current_tier.name,
            "failed_action": self.session.failed_action_name,
            "tried_methods": self.session.tried_methods,
            "history": self.session.history[-5:],
        }
