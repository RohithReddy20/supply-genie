from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str


def check_human_approval_required(
    require_human_approval: bool,
    is_customer_facing: bool,
    approved_by_human: bool,
) -> SafetyDecision:
    if not require_human_approval:
        return SafetyDecision(allowed=True, reason="Policy disabled")
    if not is_customer_facing:
        return SafetyDecision(allowed=True, reason="Internal action")
    if approved_by_human:
        return SafetyDecision(allowed=True, reason="Approved by human")
    return SafetyDecision(allowed=False, reason="Human approval required")
