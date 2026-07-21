"""
UACC Safety — risk classification + deterministic gating.

Provides:
- RiskLevel: LOW, MEDIUM, HIGH, CRITICAL
- RiskClassifier: maps actions → RiskLevel based on type, target text, context
- SafetyGate: enforces policies, blocks/queues actions, logs decisions
"""

from uacc.safety.classifier import RiskClassifier, RiskLevel
from uacc.safety.gate import SafetyDecision, SafetyGate

__all__ = ["RiskLevel", "RiskClassifier", "SafetyGate", "SafetyDecision"]
