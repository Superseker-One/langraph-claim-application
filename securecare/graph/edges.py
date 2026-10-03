"""Graph EDGES: routing functions for conditional edges (= business rules / decisions).
Each returns the NAME of a branch; builder.py maps branch names to nodes."""
from __future__ import annotations

from securecare.graph.state import ClaimState


def route_after_policy(state: ClaimState) -> str:
    """Eligibility rule: any policy issue ends the happy path."""
    return "reject" if state.get("policy_issues") else "continue"


def route_llm(state: ClaimState) -> str:
    """Intelligence is optional: without an API key the workflow still completes."""
    return "draft" if state.get("use_llm") else "skip"


def route_after_verify(state: ClaimState) -> str:
    """The loop: re-draft while verification fails. verify_communications guarantees the
    loop ends (bounded by MAX_LLM_ATTEMPTS), so this can never spin forever."""
    return "done" if state.get("comms_ok") else "retry"
