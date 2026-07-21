"""
Safety Gate — deterministic enforcement of safety policies.

Decides whether to allow, block, or queue an action based on its
risk level and the current safety policy.

Policies (configurable):
  permissive  — allow all actions, log HIGH+ (default)
  balanced    — require confirmation for CRITICAL only
  strict      — require confirmation for HIGH+
  lockdown    — block all HIGH+, require confirmation for MEDIUM+

Integration point: the agent calls `gate.decide(action, risk_level)`
before executing any action. If the decision is BLOCKED or REQUIRES_CONFIRMATION,
the agent must handle it (e.g. skip, ask user, or proceed after confirmation).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from uacc.actions.schema import Action
from uacc.safety.classifier import RiskLevel

logger = logging.getLogger(__name__)

_SAFETY_LOG_DIR = os.path.expanduser("~/.uacc/safety_logs")


class SafetyPolicy(str, Enum):
    PERMISSIVE = "permissive"
    BALANCED = "balanced"
    STRICT = "strict"
    LOCKDOWN = "lockdown"


class Decision(str, Enum):
    ALLOWED = "allowed"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    BLOCKED = "blocked"


@dataclass
class SafetyDecision:
    decision: Decision
    risk_level: RiskLevel
    policy: SafetyPolicy
    reason: str = ""
    action_id: str = ""

    def is_allowed(self) -> bool:
        return self.decision == Decision.ALLOWED

    def needs_confirmation(self) -> bool:
        return self.decision == Decision.REQUIRES_CONFIRMATION

    def is_blocked(self) -> bool:
        return self.decision == Decision.BLOCKED


class SafetyGate:
    """Enforces safety policies on UI actions.

    Usage:
        gate = SafetyGate(policy=SafetyPolicy.BALANCED)
        decision = gate.decide(action, RiskLevel.HIGH)
        if decision.needs_confirmation():
            # ask user; gate.confirm() to proceed
            ...
        elif decision.is_blocked():
            # skip action
            ...
    """

    def __init__(self, policy: SafetyPolicy = SafetyPolicy.BALANCED):
        self.policy = policy
        self._decisions: List[SafetyDecision] = []
        self._pending_confirmation: List[SafetyDecision] = []

    def set_policy(self, policy: SafetyPolicy) -> None:
        self.policy = policy
        logger.info("Safety policy set to: %s", policy.value)

    def decide(self, action: Action, risk_level: Optional[RiskLevel] = None) -> SafetyDecision:
        """Evaluate whether an action is allowed under current policy."""
        if risk_level is None:
            from uacc.safety.classifier import RiskClassifier
            risk_level = RiskClassifier().classify_action(action)

        action_id = f"{getattr(action, 'action', '?')}_{id(action)}"
        policy = self.policy

        # ── Policy rules ──────────────────────────────────
        if policy == SafetyPolicy.PERMISSIVE:
            if risk_level >= RiskLevel.HIGH:
                decision = Decision.ALLOWED  # log but allow
                reason = f"Permissive: allowing {risk_level.name} action"
            else:
                decision = Decision.ALLOWED
                reason = ""

        elif policy == SafetyPolicy.BALANCED:
            if risk_level == RiskLevel.CRITICAL:
                decision = Decision.REQUIRES_CONFIRMATION
                reason = f"CRITICAL action requires confirmation"
            elif risk_level == RiskLevel.HIGH:
                decision = Decision.ALLOWED
                reason = f"Allowed with logging (HIGH)"
            else:
                decision = Decision.ALLOWED
                reason = ""

        elif policy == SafetyPolicy.STRICT:
            if risk_level >= RiskLevel.HIGH:
                decision = Decision.REQUIRES_CONFIRMATION
                reason = f"{risk_level.name} action requires confirmation"
            else:
                decision = Decision.ALLOWED
                reason = ""

        elif policy == SafetyPolicy.LOCKDOWN:
            if risk_level >= RiskLevel.HIGH:
                decision = Decision.BLOCKED
                reason = f"{risk_level.name} actions are blocked under lockdown"
            elif risk_level == RiskLevel.MEDIUM:
                decision = Decision.REQUIRES_CONFIRMATION
                reason = "MEDIUM actions require confirmation in lockdown"
            else:
                decision = Decision.ALLOWED
                reason = ""

        else:
            decision = Decision.ALLOWED
            reason = f"Unknown policy {policy}, defaulting to permissive"

        sd = SafetyDecision(
            decision=decision,
            risk_level=risk_level,
            policy=policy,
            reason=reason,
            action_id=action_id,
        )

        self._decisions.append(sd)
        if decision == Decision.REQUIRES_CONFIRMATION:
            self._pending_confirmation.append(sd)

        self._log_decision(action, sd)
        return sd

    def confirm(self, action_id: str) -> bool:
        """Mark a pending action as confirmed. Returns True if found."""
        for i, sd in enumerate(self._pending_confirmation):
            if sd.action_id == action_id:
                self._pending_confirmation.pop(i)
                logger.info("Action confirmed: %s", action_id)
                return True
        logger.warning("No pending confirmation for action: %s", action_id)
        return False

    def reject(self, action_id: str) -> bool:
        """Reject a pending confirmation."""
        for i, sd in enumerate(self._pending_confirmation):
            if sd.action_id == action_id:
                self._pending_confirmation.pop(i)
                logger.info("Action rejected: %s", action_id)
                return True
        return False

    def has_pending(self) -> bool:
        return len(self._pending_confirmation) > 0

    def pending_summary(self) -> List[Dict]:
        return [
            {
                "action_id": sd.action_id,
                "risk_level": sd.risk_level.name,
                "reason": sd.reason,
            }
            for sd in self._pending_confirmation
        ]

    def get_history(self, max_results: int = 20) -> List[SafetyDecision]:
        return self._decisions[-max_results:]

    def _log_decision(self, action: Action, sd: SafetyDecision) -> None:
        """Persist safety decision to disk for audit."""
        try:
            os.makedirs(_SAFETY_LOG_DIR, exist_ok=True)
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action_type": getattr(action, "action", "?"),
                "action_id": sd.action_id,
                "risk_level": sd.risk_level.name,
                "decision": sd.decision.value,
                "policy": sd.policy.value,
                "reason": sd.reason,
            }
            log_file = os.path.join(
                _SAFETY_LOG_DIR,
                f"safety_{datetime.now().strftime('%Y%m%d')}.jsonl",
            )
            with open(log_file, "a") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as exc:
            logger.debug("Failed to log safety decision: %s", exc)
