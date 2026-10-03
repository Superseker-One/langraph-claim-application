"""The business schema as a LangGraph STATE (single source of truth for the workflow).

Field kinds (same vocabulary as the Session 1 notebook):
    STATIC       claimant, hospitalization, policy          fixed structure
    DYNAMIC      bill_items, documents                      1..N entries
    CONDITIONAL  patient, accident_details                  only exist when a rule fires
    DERIVED      totals, status, missing_documents ...      computed by nodes, never typed by users
    EXTENSIBLE   extra_fields                               merged with a reducer; rules in config registry
"""
from __future__ import annotations

import operator
from typing import Any, Dict, List, TypedDict

from typing_extensions import Annotated, NotRequired

from securecare.schemas import ClaimSubmission


def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    """Reducer for EXTENSIBLE fields: merge instead of overwrite."""
    return {**(left or {}), **(right or {})}


# ---------------------------------------------------------------- static sections
class Claimant(TypedDict):
    name: str
    relationship_to_patient: str
    mobile: str
    email: str
    city: str
    pincode: str


class Hospitalization(TypedDict):
    hospital_name: str
    admission_date: str       # ISO yyyy-mm-dd
    discharge_date: str
    diagnosis: str
    admission_type: str
    is_accident: bool


class PolicyInfo(TypedDict):
    policy_number: str
    policy_type: str
    sum_insured: int
    deductible: int
    room_rent_limit_per_day: int
    copay_percent: int
    start_date: str
    end_date: str
    status: str


class BillItem(TypedDict):
    category: str
    bill_number: str
    bill_date: str
    amount: int


# ---------------------------------------------------------------- the graph state
class ClaimState(TypedDict, total=False):
    # control
    use_llm: bool

    # STATIC / DYNAMIC / CONDITIONAL (come from the validated form)
    policy_number: str
    claimant: Claimant
    hospitalization: Hospitalization
    patient: NotRequired[Dict[str, Any]]
    accident_details: NotRequired[Dict[str, str]]
    bill_items: List[BillItem]
    documents: List[str]

    # EXTENSIBLE
    extra_fields: Annotated[Dict[str, Any], merge_dicts]

    # DERIVED by nodes
    claim_id: str
    policy: PolicyInfo
    policy_issues: List[str]
    stay_days: int
    total_claimed: int
    room_rent_excess: int
    admissible_amount: int
    deductible_applied: int
    copay_amount: int
    payable_estimate: int
    required_documents: List[str]
    missing_documents: List[str]
    status: str    # rejected | pending_documents | officer_review | ready_for_review
    review_reasons: Annotated[List[str], operator.add]     # DYNAMIC: nodes append

    # LLM-assisted outputs (+ the bounded retry loop's bookkeeping)
    officer_summary: str
    claimant_letter: str
    comms_source: str          # "llm" | "template"
    comms_attempts: int
    comms_feedback: str
    comms_ok: bool
    comms_error: str

    # evals + audit trail
    checks_passed: bool
    check_failures: List[str]
    audit_log: Annotated[List[str], operator.add]


def build_initial_state(submission: ClaimSubmission, use_llm: bool = False) -> ClaimState:
    """Translate the validated form into the graph's starting state."""
    data = submission.model_dump(mode="json")
    claimant = data["claimant"]
    state: ClaimState = {
        "use_llm": use_llm,
        "policy_number": data["policy_number"],
        "claimant": {
            "name": claimant["name"],
            "relationship_to_patient": claimant["relationship"],
            "mobile": claimant["mobile"],
            "email": claimant["email"],
            "city": claimant["city"],
            "pincode": claimant["pincode"],
        },
        "hospitalization": data["hospitalization"],
        "bill_items": data["bill_items"],
        "documents": data["documents"],
        "extra_fields": data["extra_fields"],
    }
    if data.get("patient"):
        state["patient"] = data["patient"]
    if data.get("accident"):
        state["accident_details"] = {
            "mlc_number": data["accident"]["mlc_number"],
            "place": data["accident"]["place"],
        }
    return state
