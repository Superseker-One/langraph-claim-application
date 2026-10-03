"""Central configuration: constants, business-rule thresholds and the extensible field registry.

Nothing in this file touches Streamlit, the network or secrets.
Changing a rule or adding a form field should usually mean editing THIS file only.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, TypedDict

APP_TITLE = "SecureCare Claims Assistant"
APP_TAGLINE = "Beta · reimbursement claim intake powered by LangGraph"

# ----------------------------- choices used by form + schemas -----------------------------
RELATIONSHIPS = ["self", "spouse", "child", "parent", "other"]
GENDERS = ["female", "male", "other"]
ADMISSION_TYPES = ["planned", "emergency"]
BILL_CATEGORIES = ["room", "surgery", "pharmacy", "diagnostics", "other"]

DOCUMENT_LABELS: Dict[str, str] = {
    "discharge_summary": "Discharge summary",
    "final_bill": "Final hospital bill",
    "pharmacy_bills": "Pharmacy bills",
    "investigation_reports": "Investigation / lab reports",
    "operation_notes": "Operation theatre notes",
    "fir_or_mlc_copy": "FIR / MLC copy",
    "id_proof": "ID proof",
}

# ----------------------------- LLM settings (no secrets here) -----------------------------
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# OpenRouter model slugs ("vendor/model"). Any slug from openrouter.ai/models that supports
# tool calling works: add it here and it shows up in the sidebar.
MODEL_OPTIONS = ["openai/gpt-4o-mini", "openai/gpt-4.1-mini", "openai/gpt-4.1-nano"]
DEFAULT_MODEL = MODEL_OPTIONS[0]
LLM_TIMEOUT_SECONDS = 30
MAX_LLM_ATTEMPTS = 2          # bounded retry loop in the graph (draft -> verify -> draft ...)
MAX_FREE_TEXT_CHARS = 4000    # cap for the "paste your email" autofill

# ----------------------------- business rules -----------------------------
MAX_STAY_DAYS = 90
MAX_BILL_ITEMS = 20
MAX_BILL_AMOUNT = 5_000_000
HIGH_VALUE_THRESHOLD = 200_000     # claims above this go to an officer
LONG_STAY_DAYS = 30
WAITING_PERIOD_DAYS = 30           # initial waiting period (accidents are exempt)


# ----------------------------- document rules (derived requirement) -----------------------------
ALWAYS_REQUIRED_DOCS = ["discharge_summary", "final_bill"]
CATEGORY_REQUIRED_DOCS = {
    "surgery": "operation_notes",
    "pharmacy": "pharmacy_bills",
    "diagnostics": "investigation_reports",
}
ACCIDENT_REQUIRED_DOC = "fir_or_mlc_copy"


# ----------------------------- EXTENSIBLE FIELD REGISTRY -----------------------------
class ExtraField(TypedDict, total=False):
    name: str
    label: str
    kind: str                      # "select" | "text"
    options: List[str]             # for select
    required: bool
    pattern: str                   # regex (full match) for text
    pattern_hint: str
    placeholder: str
    condition: Optional[Callable[[Dict[str, Any]], bool]]   # evaluated on answers collected so far


EXTRA_FIELD_REGISTRY: List[ExtraField] = [
    {
        "name": "room_category", "label": "Room category", "kind": "select",
        "options": ["general", "semi_private", "single_ac"], "required": True, "condition": None,
    },
    {
        "name": "is_network_hospital", "label": "Is the hospital in SecureCare's network?",
        "kind": "select", "options": ["yes", "no"], "required": True, "condition": None,
    },
    {
        "name": "tpa_reference", "label": "TPA reference number", "kind": "text",
        "required": True, "pattern": r"TPA-\d{4,8}", "pattern_hint": "Format: TPA-7781",
        "placeholder": "TPA-7781",
        # conditional extensible field: only for network hospitals
        "condition": lambda answers: answers.get("is_network_hospital") == "yes",
    },
    {
        "name": "employer_code", "label": "Employer / group code (or NA)", "kind": "text",
        "required": False, "pattern": r"[A-Z0-9]{3,10}", "pattern_hint": "3-10 letters/digits, or NA",
        "placeholder": "NA", "condition": None,
    },
]


def active_extra_fields(answers: Dict[str, Any]) -> List[ExtraField]:
    """Registry fields that apply given the answers collected so far (order matters)."""
    active: List[ExtraField] = []
    seen: Dict[str, Any] = {}
    for field in EXTRA_FIELD_REGISTRY:
        condition = field.get("condition")
        if condition is None or condition(seen):
            active.append(field)
            seen[field["name"]] = answers.get(field["name"])
    return active
