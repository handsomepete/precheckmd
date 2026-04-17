"""Risk classification.

Read/log operations are low risk. Restarts and writes are medium. Anything
the model self-tagged as ``high`` stays high. Plans containing any
financial-impact metadata are escalated to medium minimum.
"""

from __future__ import annotations

from enum import Enum

LOW_RISK_OPS: frozenset[str] = frozenset(
    {
        "get_state",
        "systemctl_status",
        "docker_ps",
        "docker_logs",
        "journalctl_tail",
        "read_file",
    }
)

MEDIUM_RISK_OPS: frozenset[str] = frozenset(
    {
        "systemctl_restart",
        "call_service",  # HA service calls can change physical state
    }
)


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

    def at_least(self, other: "RiskLevel") -> bool:
        order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        return order[self] >= order[other]


def classify(action: dict) -> RiskLevel:
    """Compute the effective risk level for an action.

    The model may declare ``risk_level``; we take the max of the declared
    value and the operation-derived baseline.
    """
    op = action.get("operation", "")
    declared = action.get("risk_level", "low")
    declared_level = RiskLevel(declared if declared in {"low", "medium", "high"} else "low")

    if op in LOW_RISK_OPS:
        baseline = RiskLevel.LOW
    elif op in MEDIUM_RISK_OPS:
        baseline = RiskLevel.MEDIUM
    else:
        baseline = RiskLevel.HIGH  # unknown ops escalate

    if action.get("parameters", {}).get("financial_impact"):
        baseline = max(baseline, RiskLevel.MEDIUM, key=lambda r: ["low", "medium", "high"].index(r.value))

    return max(declared_level, baseline, key=lambda r: ["low", "medium", "high"].index(r.value))


def needs_approval(action: dict, risk: RiskLevel) -> bool:
    """Approval required for medium/high risk, system state changes, or
    actions explicitly flagged via requires_approval=true."""
    if action.get("requires_approval", False):
        return True
    if risk.at_least(RiskLevel.MEDIUM):
        return True
    return False
