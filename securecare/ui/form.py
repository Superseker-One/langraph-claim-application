"""The claim form. Widgets are bound to st.session_state with dotted keys that match the
validation layer ('claimant.mobile', 'bill_items.3.amount', 'extra.tpa_reference' ...), so a
validation error maps straight back to the widget that caused it.

Flow on every rerun:  collect_raw() -> validate_submission() -> render widgets with inline errors.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import streamlit as st

from securecare.config import (
    ADMISSION_TYPES, BILL_CATEGORIES, DOCUMENT_LABELS, EXTRA_FIELD_REGISTRY, GENDERS, MAX_BILL_ITEMS,
    RELATIONSHIPS, active_extra_fields,
)
from securecare.samples import sample_raw_claim
from securecare.validation import REQUIRED_MSG, ValidationResult, validate_submission

MIN_DATE = date(1900, 1, 1)
FORM_KEY_PREFIXES = ("claimant.", "patient.", "hospitalization.", "accident.", "bill_items.", "extra.")
FORM_KEYS = ("policy_number", "documents", "declaration", "show_all_errors", "last_run",
             "bill_row_ids", "next_row_id", "autofill_text")


# ====================================================================== session helpers
def _ids() -> list[int]:
    return st.session_state.setdefault("bill_row_ids", [1])


def _get(key: str, default: Any = None) -> Any:
    return st.session_state.get(key, default)


def add_bill_row() -> None:
    nxt = st.session_state.get("next_row_id", 2)
    if len(_ids()) < MAX_BILL_ITEMS:
        _ids().append(nxt)
        st.session_state["next_row_id"] = nxt + 1


def remove_bill_row(row_id: int) -> None:
    if len(_ids()) > 1:
        _ids().remove(row_id)
        for field in ("category", "bill_number", "bill_date", "amount"):
            st.session_state.pop(f"bill_items.{row_id}.{field}", None)


def reset_form() -> None:
    """Callback for 'New claim'. Leaves the API-key widget and sidebar settings alone."""
    for key in list(st.session_state.keys()):
        if key.startswith(FORM_KEY_PREFIXES) or key in FORM_KEYS:
            del st.session_state[key]


def apply_raw_to_session(raw: Dict[str, Any]) -> None:
    """Write a raw claim dict into widget state (used by 'Load sample')."""
    reset_form()
    ss = st.session_state
    for field, value in raw["claimant"].items():
        ss[f"claimant.{field}"] = value
    if raw.get("patient"):
        for field, value in raw["patient"].items():
            ss[f"patient.{field}"] = value
    ss["policy_number"] = raw["policy_number"]
    for field, value in raw["hospitalization"].items():
        ss[f"hospitalization.{field}"] = "Yes" if (field == "is_accident" and value) else (
            "No" if field == "is_accident" else value)
    if raw.get("accident"):
        ss["accident.mlc_number"], ss["accident.place"] = raw["accident"]["mlc_number"], raw["accident"]["place"]
    ids = [item["row_id"] for item in raw["bill_items"]]
    ss["bill_row_ids"], ss["next_row_id"] = ids, max(ids) + 1
    for item in raw["bill_items"]:
        for field in ("category", "bill_number", "bill_date", "amount"):
            ss[f"bill_items.{item['row_id']}.{field}"] = item[field]
    for name, value in raw["extra_fields"].items():
        ss[f"extra.{name}"] = value
    ss["documents"] = raw["documents"]
    ss["declaration"] = False          # the user must tick this themselves


def load_sample(kind: str) -> None:
    apply_raw_to_session(sample_raw_claim(kind))


# ====================================================================== raw collection
def collect_raw() -> Dict[str, Any]:
    """Read the CURRENT widget values from session_state into the dict the validators expect."""
    claimant = {f: _get(f"claimant.{f}", "") for f in ("name", "mobile", "email", "city", "pincode")}
    claimant["relationship"] = _get("claimant.relationship")

    hosp = {f: _get(f"hospitalization.{f}", "") for f in ("hospital_name", "diagnosis")}
    hosp.update(
        admission_date=_get("hospitalization.admission_date"),
        discharge_date=_get("hospitalization.discharge_date"),
        admission_type=_get("hospitalization.admission_type"),
        is_accident={"Yes": True, "No": False}.get(_get("hospitalization.is_accident")),
    )

    bill_items = [
        {"row_id": rid,
         "category": _get(f"bill_items.{rid}.category"),
         "bill_number": _get(f"bill_items.{rid}.bill_number", ""),
         "bill_date": _get(f"bill_items.{rid}.bill_date"),
         "amount": _get(f"bill_items.{rid}.amount")}
        for rid in _ids()
    ]

    answers = {f["name"]: _get(f"extra.{f['name']}") for f in EXTRA_FIELD_REGISTRY}
    extras: Dict[str, Any] = {f["name"]: answers[f["name"]] or "" for f in active_extra_fields(answers)}

    raw: Dict[str, Any] = {
        "claimant": claimant,
        "policy_number": _get("policy_number", ""),
        "hospitalization": hosp,
        "bill_items": bill_items,
        "documents": _get("documents", []),
        "extra_fields": extras,
        "declaration": bool(_get("declaration", False)),
    }
    if claimant["relationship"] not in (None, "self"):
        raw["patient"] = {"name": _get("patient.name", ""), "dob": _get("patient.dob"),
                          "gender": _get("patient.gender")}
    if hosp["is_accident"] is True:
        raw["accident"] = {"mlc_number": _get("accident.mlc_number", ""), "place": _get("accident.place", "")}
    return raw


# ====================================================================== rendering
def _show_error(errors: Dict[str, str], key: str) -> None:
    msg = errors.get(key)
    show_all = st.session_state.get("show_all_errors")
    if key == "declaration" and not show_all:
        return                                   # only nag about the declaration after a submit attempt
    if msg and (show_all or msg != REQUIRED_MSG):
        st.caption(f":red[⚠️ {msg}]")


def _text(label: str, key: str, errors: Dict[str, str], placeholder: str = "", max_chars: int = 120) -> None:
    st.text_input(label, key=key, placeholder=placeholder, max_chars=max_chars)
    _show_error(errors, key)


def _select(label: str, key: str, options: list[str], errors: Dict[str, str]) -> None:
    st.selectbox(label, options, index=None, key=key, placeholder="Select…")
    _show_error(errors, key)


def _date(label: str, key: str, errors: Dict[str, str]) -> None:
    st.date_input(label, value=None, key=key, min_value=MIN_DATE, format="DD/MM/YYYY")
    _show_error(errors, key)


def render_claim_form() -> ValidationResult:
    """Render the whole form and return the validation result for the current values."""
    raw = collect_raw()
    result = validate_submission(raw)
    errors = result.errors

    # ---------------------------------------------------------------- 1. claimant
    st.subheader("1 · Claimant")
    c1, c2 = st.columns(2)
    with c1:
        _text("Full name", "claimant.name", errors, "Rajesh Kumar", 60)
        _text("Mobile number", "claimant.mobile", errors, "9876543210", 16)
        _text("City", "claimant.city", errors, "Bengaluru", 50)
    with c2:
        _select("Relationship to patient", "claimant.relationship", RELATIONSHIPS, errors)
        _text("Email", "claimant.email", errors, "name@example.com", 100)
        _text("Pincode", "claimant.pincode", errors, "560001", 6)

    if "patient" in raw:                                   # CONDITIONAL section
        st.markdown("**Patient details** (needed because the claimant is not the patient)")
        p1, p2, p3 = st.columns(3)
        with p1:
            _text("Patient name", "patient.name", errors, max_chars=60)
        with p2:
            st.date_input("Patient date of birth", value=None, key="patient.dob", min_value=MIN_DATE,
                          max_value=date.today(), format="DD/MM/YYYY")
            _show_error(errors, "patient.dob")
        with p3:
            _select("Patient gender", "patient.gender", GENDERS, errors)

    # ---------------------------------------------------------------- 2. policy + hospitalisation
    st.subheader("2 · Policy and hospitalisation")
    _text("Policy number", "policy_number", errors, "POL-458921", 10)
    h1, h2 = st.columns(2)
    with h1:
        _text("Hospital name", "hospitalization.hospital_name", errors, "City Care Hospital")
        _date("Admission date", "hospitalization.admission_date", errors)
        _select("Admission type", "hospitalization.admission_type", ADMISSION_TYPES, errors)
    with h2:
        _text("Diagnosis (as on discharge summary)", "hospitalization.diagnosis", errors, "Acute appendicitis", 200)
        _date("Discharge date", "hospitalization.discharge_date", errors)
        st.radio("Was the hospitalisation due to an accident?", ["Yes", "No"], index=None,
                 horizontal=True, key="hospitalization.is_accident")
        _show_error(errors, "hospitalization.is_accident")

    if "accident" in raw:                                  # CONDITIONAL section
        st.markdown("**Accident details** (MLC / FIR is mandatory for accident claims)")
        a1, a2 = st.columns(2)
        with a1:
            _text("MLC / FIR number", "accident.mlc_number", errors, "MLC-2026-4471", 20)
        with a2:
            _text("Place of accident", "accident.place", errors, max_chars=100)

    # ---------------------------------------------------------------- 3. bills (DYNAMIC 1..N)
    st.subheader("3 · Hospital bills")
    st.caption("Add one row per bill. Bill dates must fall between admission and discharge.")
    for position, rid in enumerate(_ids(), start=1):
        b = st.columns([1.3, 1.4, 1.3, 1.2, 0.5])
        with b[0]:
            st.selectbox(f"Category #{position}", BILL_CATEGORIES, index=None, key=f"bill_items.{rid}.category",
                         placeholder="Select…")
            _show_error(errors, f"bill_items.{rid}.category")
        with b[1]:
            st.text_input(f"Bill number #{position}", key=f"bill_items.{rid}.bill_number", placeholder="RM-1001",
                          max_chars=20)
            _show_error(errors, f"bill_items.{rid}.bill_number")
        with b[2]:
            st.date_input(f"Bill date #{position}", value=None, key=f"bill_items.{rid}.bill_date",
                          min_value=MIN_DATE, format="DD/MM/YYYY")
            _show_error(errors, f"bill_items.{rid}.bill_date")
        with b[3]:
            st.number_input(f"Amount (₹) #{position}", value=None, step=500, format="%d",
                            key=f"bill_items.{rid}.amount", placeholder="0")
            _show_error(errors, f"bill_items.{rid}.amount")
        with b[4]:
            st.write("")
            st.write("")
            st.button("🗑", key=f"del_{rid}", on_click=remove_bill_row, args=(rid,),
                      disabled=len(_ids()) == 1, help="Remove this bill")
    _show_error(errors, "bill_items")
    st.button("➕ Add another bill", on_click=add_bill_row, disabled=len(_ids()) >= MAX_BILL_ITEMS)

    # ---------------------------------------------------------------- 4. extensible fields (registry)
    st.subheader("4 · Additional details")
    seen: Dict[str, Any] = {}
    cols = st.columns(2)
    for i, spec in enumerate(EXTRA_FIELD_REGISTRY):
        condition = spec.get("condition")
        if condition is not None and not condition(seen):
            continue
        key = f"extra.{spec['name']}"
        with cols[i % 2]:
            if spec["kind"] == "select":
                _select(spec["label"], key, spec["options"], errors)
            else:
                _text(spec["label"], key, errors, spec.get("placeholder", ""), 20)
        seen[spec["name"]] = st.session_state.get(key)

    # ---------------------------------------------------------------- 5. documents + declaration
    st.subheader("5 · Documents and declaration")
    st.multiselect("Documents you will upload", list(DOCUMENT_LABELS), key="documents",
                   format_func=lambda d: DOCUMENT_LABELS[d],
                   help="Missing documents will not block submission. The workflow tells you what is still needed.")
    st.checkbox("I declare that the information given is true and complete.", key="declaration")
    _show_error(errors, "declaration")
    return result
