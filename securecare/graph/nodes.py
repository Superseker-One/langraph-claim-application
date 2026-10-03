"""Graph NODES: one function per business step. A node reads the state and returns ONLY the
keys it changes (partial update). Deterministic nodes hold the business rules; the LLM node
(draft_communications) only words the result.

    intake -> lookup_policy -> (reject_claim | compute_estimate -> check_documents -> decide_status
           -> [draft_communications <-> verify_communications] -> final_checks)
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Any, Callable, Dict, Optional

from securecare.agents.communicator import (
    Communications, check_communications, draft_communications, facts_from_state, template_communications,
)
from securecare.config import (
    ACCIDENT_REQUIRED_DOC, ALWAYS_REQUIRED_DOCS, CATEGORY_REQUIRED_DOCS, HIGH_VALUE_THRESHOLD,
    LONG_STAY_DAYS, MAX_LLM_ATTEMPTS, WAITING_PERIOD_DAYS,
)
from securecare.graph.state import ClaimState
from securecare.security import redact_secrets
from securecare.services.policy_store import get_policy

VALID_STATUSES = {"rejected", "pending_documents", "officer_review", "ready_for_review"}


def _stay_days(state: ClaimState) -> int:
    h = state["hospitalization"]
    return max((date.fromisoformat(h["discharge_date"]) - date.fromisoformat(h["admission_date"])).days, 1)


# ============================================================ 1. intake
def intake(state: ClaimState) -> Dict[str, Any]:
    """Open the claim: system-generated id and basic derived facts."""
    claim_id = f"CLM-{date.today().year}-{random.randint(100000, 999999)}"
    return {
        "claim_id": claim_id,
        "stay_days": _stay_days(state),
        "audit_log": [f"intake: opened {claim_id}"],
    }


# ============================================================ 2. lookup_policy
def lookup_policy(state: ClaimState) -> Dict[str, Any]:
    """Fetch the policy from the system of record and apply eligibility rules."""
    policy = get_policy(state["policy_number"])
    issues = []
    update: Dict[str, Any] = {}

    if policy is None:
        issues.append("Policy number not found in the policy system")
    else:
        update["policy"] = policy
        adm = date.fromisoformat(state["hospitalization"]["admission_date"])
        start, end = date.fromisoformat(policy["start_date"]), date.fromisoformat(policy["end_date"])
        if policy["status"] != "active":
            issues.append(f"Policy status is '{policy['status']}' (premium or renewal pending)")
        elif not (start <= adm <= end):
            issues.append("Admission date is outside the policy period")
        elif not state["hospitalization"]["is_accident"] and adm < start + timedelta(days=WAITING_PERIOD_DAYS):
            issues.append(f"Admission falls inside the {WAITING_PERIOD_DAYS}-day initial waiting period (accidents are exempt)")

    update["policy_issues"] = issues
    update["audit_log"] = [f"lookup_policy: {'; '.join(issues) if issues else 'eligible'}"]
    return update


# ============================================================ 3a. reject_claim (terminal branch)
def reject_claim(state: ClaimState) -> Dict[str, Any]:
    total = sum(item["amount"] for item in state["bill_items"])
    interim = {**state, "status": "rejected", "total_claimed": total, "payable_estimate": 0,
               "missing_documents": [], "review_reasons": state.get("policy_issues", [])}
    comms = template_communications(interim)
    return {
        "status": "rejected",
        "total_claimed": total,
        "payable_estimate": 0,
        "missing_documents": [],
        "review_reasons": state.get("policy_issues", []),
        "officer_summary": comms.officer_summary,
        "claimant_letter": comms.claimant_letter,
        "comms_source": "template",
        "checks_passed": True,
        "check_failures": [],
        "audit_log": ["reject_claim: eligibility failed"],
    }


# ============================================================ 3b. compute_estimate (DERIVED fields)
def compute_estimate(state: ClaimState) -> Dict[str, Any]:
    """Deterministic money maths. Never delegate calculations to an LLM."""
    policy = state["policy"]
    items = state["bill_items"]
    days = state["stay_days"]

    total = sum(i["amount"] for i in items)
    room_total = sum(i["amount"] for i in items if i["category"] == "room")
    room_eligible = min(room_total, policy["room_rent_limit_per_day"] * days)
    room_excess = room_total - room_eligible

    admissible = total - room_excess
    deductible_applied = min(policy["deductible"], admissible)
    after_deductible = admissible - deductible_applied
    copay = round(after_deductible * policy["copay_percent"] / 100)
    payable = min(after_deductible - copay, policy["sum_insured"])

    update: Dict[str, Any] = {
        "total_claimed": total,
        "room_rent_excess": room_excess,
        "admissible_amount": admissible,
        "deductible_applied": deductible_applied,
        "copay_amount": copay,
        "payable_estimate": max(payable, 0),
        "audit_log": [f"compute_estimate: total={total}, payable={max(payable, 0)}"],
    }
    if room_excess > 0:
        update["review_reasons"] = [f"Room rent above policy limit (excess ₹{room_excess:,} not payable)"]
    return update


# ============================================================ 4. check_documents
def check_documents(state: ClaimState) -> Dict[str, Any]:
    """Required documents are DERIVED from the claim's content."""
    required = list(ALWAYS_REQUIRED_DOCS)
    for category, doc in CATEGORY_REQUIRED_DOCS.items():
        if any(i["category"] == category for i in state["bill_items"]) and doc not in required:
            required.append(doc)
    if state["hospitalization"]["is_accident"]:
        required.append(ACCIDENT_REQUIRED_DOC)
    missing = [d for d in required if d not in state.get("documents", [])]
    return {
        "required_documents": required,
        "missing_documents": missing,
        "audit_log": [f"check_documents: {len(missing)} missing"],
    }


# ============================================================ 5. decide_status
def decide_status(state: ClaimState) -> Dict[str, Any]:
    reasons = []
    if state["hospitalization"]["is_accident"]:
        reasons.append("Accident case: verify MLC / FIR details")
    if state["total_claimed"] > HIGH_VALUE_THRESHOLD:
        reasons.append(f"High-value claim (above ₹{HIGH_VALUE_THRESHOLD:,})")
    if state["stay_days"] > LONG_STAY_DAYS:
        reasons.append(f"Long hospital stay ({state['stay_days']} days)")
    if state.get("extra_fields", {}).get("is_network_hospital") == "no":
        reasons.append("Non-network hospital: reimbursement route")

    if state["missing_documents"]:
        status = "pending_documents"
    elif reasons or state.get("review_reasons"):
        status = "officer_review"
    else:
        status = "ready_for_review"
    return {"status": status, "review_reasons": reasons, "audit_log": [f"decide_status: {status}"]}


# ============================================================ 6a. template path (no API key)
def write_template_communications(state: ClaimState) -> Dict[str, Any]:
    """Deterministic letters, used when the visitor did not provide an API key."""
    comms = template_communications(state)
    return {"officer_summary": comms.officer_summary, "claimant_letter": comms.claimant_letter,
            "comms_source": "template", "audit_log": ["write_template_communications: no LLM, used template"]}


# ============================================================ 6b. LLM node factory + 7. verify
def make_draft_communications(llm: Optional[Any]) -> Callable[[ClaimState], Dict[str, Any]]:
    """The LLM client is INJECTED when the graph is built, so the API key never enters the
    graph state, the run config, or any cache."""

    def draft_communications_node(state: ClaimState) -> Dict[str, Any]:
        attempts = state.get("comms_attempts", 0) + 1
        if llm is None:
            comms = template_communications(state)
            return {"officer_summary": comms.officer_summary, "claimant_letter": comms.claimant_letter,
                    "comms_source": "template", "comms_attempts": attempts,
                    "audit_log": ["draft_communications: no LLM, used template"]}
        try:
            comms = draft_communications(llm, facts_from_state(state), state.get("comms_feedback"))
            return {"officer_summary": comms.officer_summary, "claimant_letter": comms.claimant_letter,
                    "comms_source": "llm", "comms_attempts": attempts,
                    "audit_log": [f"draft_communications: LLM draft #{attempts}"]}
        except Exception as exc:  # noqa: BLE001 - any LLM/network/auth failure must not crash the claim
            comms = template_communications(state)
            message = redact_secrets(f"{type(exc).__name__}: {exc}")[:300]
            return {"officer_summary": comms.officer_summary, "claimant_letter": comms.claimant_letter,
                    "comms_source": "template", "comms_attempts": MAX_LLM_ATTEMPTS,
                    "comms_error": message,
                    "audit_log": ["draft_communications: LLM failed, used template"]}

    return draft_communications_node


def verify_communications(state: ClaimState) -> Dict[str, Any]:
    """Eval step: never trust LLM text blindly. Bounded retry, then fall back to the template."""
    if state.get("comms_source") != "llm":
        return {"comms_ok": True, "audit_log": ["verify_communications: template text, nothing to verify"]}

    comms = Communications(officer_summary=state["officer_summary"], claimant_letter=state["claimant_letter"])
    problems = check_communications(comms, state)
    if not problems:
        return {"comms_ok": True, "audit_log": ["verify_communications: passed"]}

    if state.get("comms_attempts", 1) < MAX_LLM_ATTEMPTS:
        return {"comms_ok": False, "comms_feedback": "; ".join(problems),
                "audit_log": [f"verify_communications: failed ({len(problems)}), retrying"]}

    fallback = template_communications(state)
    return {"comms_ok": True, "comms_source": "template",
            "officer_summary": fallback.officer_summary, "claimant_letter": fallback.claimant_letter,
            "audit_log": ["verify_communications: still failing, used template"]}


# ============================================================ 8. final_checks
def final_checks(state: ClaimState) -> Dict[str, Any]:
    """Last deterministic safety net before the claim is handed to an officer."""
    failures = []
    if state.get("status") not in VALID_STATUSES:
        failures.append("status is not a known value")
    if state.get("payable_estimate", 0) > state.get("total_claimed", 0):
        failures.append("payable estimate exceeds total claimed")
    if not state.get("claimant_letter") or not state.get("officer_summary"):
        failures.append("communications missing")

    update: Dict[str, Any] = {
        "checks_passed": not failures,
        "check_failures": failures,
        "audit_log": [f"final_checks: {'passed' if not failures else 'FAILED ' + '; '.join(failures)}"],
    }
    if failures:
        update["status"] = "officer_review"
        update["review_reasons"] = [f"Automated check failed: {f}" for f in failures]
    return update
