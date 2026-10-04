"""Graph NODES: one function per business step. A node reads the state and returns ONLY the
keys it changes (partial update). Deterministic nodes hold the business rules; the LLM node
(draft_communications) only words the result.

    intake -> lookup_policy -> (reject_claim | compute_estimate -> check_documents -> decide_status
           -> [draft_communications <-> verify_communications] -> final_checks)
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from securecare.agents.communicator import (
    Communications, check_communications, draft_communications, facts_from_state, template_communications,
    withheld_fields,
)
from securecare.config import (
    ACCIDENT_REQUIRED_DOC, ALWAYS_REQUIRED_DOCS, CATEGORY_REQUIRED_DOCS, HIGH_VALUE_THRESHOLD,
    LONG_STAY_DAYS, MAX_LLM_ATTEMPTS, OTHER_CATEGORY_REVIEW_PERCENT, WAITING_PERIOD_DAYS,
)
from securecare.graph.state import ClaimState
from securecare.security import describe_exception, secret_from_client
from securecare.services.policy_store import get_policy
from securecare.textsafe import clean_block

VALID_STATUSES = {"rejected", "pending_documents", "officer_review", "ready_for_review"}
MAX_STORED_TEXT_CHARS = 5000      # hard bound on LLM text kept in state (far above the checked caps)


def _stay_days(state: ClaimState) -> int:
    h = state["hospitalization"]
    return max((date.fromisoformat(h["discharge_date"]) - date.fromisoformat(h["admission_date"])).days, 1)


# ============================================================ 1. intake
def intake(state: ClaimState) -> Dict[str, Any]:
    """Open the claim: system-generated id and basic derived facts. The id comes from the `secrets`
    module (40 random bits), so ids cannot be guessed or enumerated from a previous one."""
    claim_id = f"CLM-{date.today().year}-{secrets.token_hex(5).upper()}"
    return {
        "claim_id": claim_id,
        "stay_days": _stay_days(state),
        "audit_log": [f"intake: opened {claim_id}"],
    }


# ============================================================ 2. lookup_policy
def lookup_policy(state: ClaimState) -> Dict[str, Any]:
    """Fetch the policy from the system of record and apply eligibility rules.

    The accident exemption from the waiting period is self-declared (is_accident) and MLC/FIR numbers are
    not verified, so it never lets a claim through silently: such claims stay eligible but get an explicit
    review reason and an audit entry, which routes them to an officer."""
    policy = get_policy(state["policy_number"])
    hosp = state["hospitalization"]
    issues: List[str] = []
    notes: List[str] = []
    update: Dict[str, Any] = {}

    if policy is None:
        issues.append("Policy number not found in the policy system")
    else:
        update["policy"] = policy
        adm = date.fromisoformat(hosp["admission_date"])
        start, end = date.fromisoformat(policy["start_date"]), date.fromisoformat(policy["end_date"])
        inside_waiting = adm < start + timedelta(days=WAITING_PERIOD_DAYS)
        if policy["status"] != "active":
            issues.append(f"Policy status is '{policy['status']}' (premium or renewal pending)")
        elif not (start <= adm <= end):
            issues.append("Admission date is outside the policy period")
        elif not hosp["is_accident"] and inside_waiting:
            issues.append(f"Admission falls inside the {WAITING_PERIOD_DAYS}-day initial waiting period (accidents are exempt)")
        elif hosp["is_accident"]:
            if inside_waiting:
                notes.append("Accident claim inside the waiting period: verify MLC/FIR before any payment")
            if hosp.get("admission_type") == "planned":
                notes.append("Accident claim with a planned admission: verify MLC/FIR and circumstances before any payment")

    update["policy_issues"] = issues
    update["audit_log"] = [f"lookup_policy: {'; '.join(issues) if issues else 'eligible'}"]
    if notes:
        update["review_reasons"] = notes
        update["audit_log"] += [f"lookup_policy: accident waiver NOT applied silently ({n})" for n in notes]
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

    other_total = sum(i["amount"] for i in items if i["category"] == "other")

    admissible = total - room_excess
    deductible_applied = min(policy["deductible"], admissible)
    after_deductible = admissible - deductible_applied
    copay = (after_deductible * policy["copay_percent"] + 50) // 100     # integer half-up (round() is banker's)
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
    reasons = []
    if room_excess > 0:
        reasons.append(f"Room rent above policy limit (excess ₹{room_excess:,} not payable)")
    if total > 0 and other_total * 100 > total * OTHER_CATEGORY_REVIEW_PERCENT:
        # room rent is capped per day, "other" is not: a big "other" share may be room bills in disguise
        reasons.append(f"{other_total * 100 // total}% of the claim is in the 'other' category: "
                       "check that room rent or other capped charges are not filed there")
    if reasons:
        update["review_reasons"] = reasons
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
    # '>=' so a claim exactly at the threshold is reviewed. This only sees ONE claim: splitting a large
    # claim into several smaller ones needs a claims ledger (persistence), which this demo does not have.
    if state["total_claimed"] >= HIGH_VALUE_THRESHOLD:
        reasons.append(f"High-value claim (₹{HIGH_VALUE_THRESHOLD:,} or more)")
    if state["stay_days"] > LONG_STAY_DAYS:
        reasons.append(f"Long hospital stay ({state['stay_days']} days)")
    if state.get("extra_fields", {}).get("is_network_hospital") == "no":
        reasons.append("Non-network hospital: reimbursement route")
    hidden = withheld_fields(state)
    if hidden:
        reasons.append(f"Free text in {', '.join(hidden)} looks like an instruction or contains amounts or links: "
                       "check it manually")

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
            # LLM text is untrusted: strip hidden/control/surrogate characters BEFORE it is stored or checked
            return {"officer_summary": clean_block(comms.officer_summary, MAX_STORED_TEXT_CHARS),
                    "claimant_letter": clean_block(comms.claimant_letter, MAX_STORED_TEXT_CHARS),
                    "comms_source": "llm", "comms_attempts": attempts,
                    "audit_log": [f"draft_communications: LLM draft #{attempts}"]}
        except Exception as exc:  # noqa: BLE001 - any LLM/network/auth failure must not crash the claim
            comms = template_communications(state)
            message = describe_exception(exc, secret_from_client(llm), max_len=300)
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
    """Last deterministic safety net before the claim is handed to an officer. It re-checks the money,
    the status and the final texts (whatever their source) against each other."""
    failures: List[str] = []
    status = state.get("status")
    amounts = {k: state.get(k, 0) for k in (
        "total_claimed", "payable_estimate", "admissible_amount", "deductible_applied", "copay_amount",
        "room_rent_excess")}
    total, payable = amounts["total_claimed"], amounts["payable_estimate"]
    missing = state.get("missing_documents", [])

    if status not in VALID_STATUSES:
        failures.append("status is not a known value")
    if any(not isinstance(v, (int, float)) or v < 0 for v in amounts.values()):
        failures.append("a calculated amount is negative or not a number")
    elif payable > total:
        failures.append("payable estimate exceeds total claimed")
    sum_insured = state.get("policy", {}).get("sum_insured")
    if isinstance(sum_insured, (int, float)) and isinstance(payable, (int, float)) and payable > sum_insured:
        failures.append("payable estimate exceeds the sum insured")
    if status == "pending_documents" and not missing:
        failures.append("status is pending_documents but no document is missing")
    if missing and status not in ("pending_documents", "rejected"):
        failures.append("documents are missing but the status is not pending_documents")
    if status == "rejected" and payable:
        failures.append("rejected claim has a payable amount")
    if not state.get("claim_id"):
        failures.append("claim id is missing")
    if not state.get("claimant_letter") or not state.get("officer_summary"):
        failures.append("communications missing")
    else:
        comms = Communications(officer_summary=state["officer_summary"], claimant_letter=state["claimant_letter"])
        failures.extend(f"communications: {p}" for p in check_communications(comms, state)[:5])

    update: Dict[str, Any] = {
        "checks_passed": not failures,
        "check_failures": failures,
        "audit_log": [f"final_checks: {'passed' if not failures else 'FAILED ' + '; '.join(failures)}"],
    }
    if failures:
        update["status"] = "officer_review"
        update["review_reasons"] = [f"Automated check failed: {f}" for f in failures]
    return update
