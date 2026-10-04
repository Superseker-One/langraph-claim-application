"""Optional AI autofill: paste the claimant's email, the extractor agent pre-fills the form.
The user still reviews everything and the normal validation still runs.

Whatever the model returns is UNTRUSTED: every value is type-checked, cleaned and clamped to the limits of
the matching form widget before it is written into widget state; anything invalid is dropped."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, List, Optional

import streamlit as st

from securecare import schemas
from securecare.agents.extractor import ClaimExtraction, extract_claim_from_text
from securecare.agents.llm import build_llm
from securecare.config import (
    ADMISSION_TYPES, BILL_CATEGORIES, FIELD_LIMITS, MAX_BILL_AMOUNT, MAX_BILL_ITEMS, MAX_FREE_TEXT_CHARS,
    MAX_MISSING_INFO_ITEMS, RELATIONSHIPS,
)
from securecare.security import describe_exception, looks_like_openrouter_key
from securecare.textsafe import clean_line
from securecare.ui.form import MIN_DATE
from securecare.ui.limits import ai_blocked_message, record_ai_call
from securecare.ui.safe import set_flash
from securecare.ui.sidebar import Settings, flush_key_after_use, get_api_key

# Human names for the widgets autofill can fill (used to say which typed values were overwritten).
FIELD_LABELS = {
    "claimant.name": "Full name", "claimant.relationship": "Relationship", "claimant.mobile": "Mobile number",
    "claimant.email": "Email", "policy_number": "Policy number",
    "hospitalization.hospital_name": "Hospital name", "hospitalization.admission_date": "Admission date",
    "hospitalization.discharge_date": "Discharge date", "hospitalization.diagnosis": "Diagnosis",
    "hospitalization.admission_type": "Admission type", "hospitalization.is_accident": "Accident (yes/no)",
    "accident.mlc_number": "MLC / FIR number", "accident.place": "Place of accident",
}
BILL_ROWS_LABEL = "Bill rows"

# The ONLY things the extractor's "missing information" list may show: label -> accepted spellings.
_MISSING_ALIASES = {
    "Full name": ("claimant name", "name", "full name", "your name"),
    "Relationship": ("relationship", "relationship to patient"),
    "Mobile number": ("mobile", "mobile number", "phone", "phone number", "contact number"),
    "Email": ("email", "email address"),
    "City": ("city",), "Pincode": ("pincode", "pin code", "postal code"),
    "Policy number": ("policy number", "policy no", "policy"),
    "Hospital name": ("hospital", "hospital name"),
    "Admission date": ("admission date", "date of admission"), "Discharge date": ("discharge date", "date of discharge"),
    "Diagnosis": ("diagnosis",), "Admission type": ("admission type", "planned or emergency"),
    "Accident (yes/no)": ("is accident", "accident"), "MLC / FIR number": ("mlc number", "fir number", "mlc fir number", "mlc"),
    "Place of accident": ("accident place", "place of accident"),
    "Bill items": ("bill items", "bills", "bill amounts", "amounts"),
    "Bill numbers": ("bill number", "bill numbers"), "Bill dates": ("bill date", "bill dates"),
    "Patient details": ("patient name", "patient details", "patient dob", "patient date of birth", "patient gender"),
    "Documents": ("documents", "supporting documents"),
}
_MISSING_LOOKUP = {alias: label for label, aliases in _MISSING_ALIASES.items() for alias in aliases}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", clean_line(value).lower()).strip()


def known_missing_fields(items: Iterable[object], limit: int = MAX_MISSING_INFO_ITEMS) -> List[str]:
    """Keep only 'missing information' entries that are one of OUR field names/labels (any case, any
    separators: 'Mobile number', 'mobile_number'); everything else the model wrote is dropped. At most
    `limit` entries, one per field; the entry is shown as the model wrote it (cleaned, <= 40 chars)."""
    kept: List[str] = []
    fields_seen: List[str] = []
    for item in items or ():
        label = _MISSING_LOOKUP.get(_norm(item))
        if label and label not in fields_seen:
            fields_seen.append(label)
            kept.append(clean_line(item, 40))
        if len(kept) >= limit:
            break
    return kept


def _iso(value: Any) -> date | None:
    """A plausible calendar date: ISO text, not before the widget minimum, not in the future."""
    try:
        parsed = date.fromisoformat(clean_line(value, 10)) if value else None
    except ValueError:
        return None
    return parsed if parsed and MIN_DATE <= parsed <= schemas._today() else None


def _text(value: Any, key: str) -> Optional[str]:
    """A printable one-line string clamped to the widget's max_chars, or None."""
    if not isinstance(value, str):
        return None
    cleaned = clean_line(value, FIELD_LIMITS.get(key, 120))
    return cleaned if cleaned and cleaned.isprintable() else None


def _word(value: Any) -> Optional[str]:
    """Short single-token field (relationship, admission type, bill category), at most 20 characters."""
    return _text(value, "extra")


def _amount(value: Any) -> Optional[int]:
    """A whole rupee amount within 1..MAX_BILL_AMOUNT (a 400-digit int would crash the number widget)."""
    ok = isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_BILL_AMOUNT
    return value if ok else None


@dataclass
class AutofillReport:
    filled: int = 0
    overwritten: List[str] = field(default_factory=list)     # labels of fields that held a DIFFERENT typed value


def apply_extraction_with_report(ex: ClaimExtraction) -> AutofillReport:
    """Write extracted values into widget state (after type-check + clamp). Reports what was overwritten."""
    ss, report = st.session_state, AutofillReport()

    def put(key: str, value: Any, allowed: list | None = None) -> None:
        if value in (None, ""):
            return
        if allowed is not None and value not in allowed:
            return
        old = ss.get(key)
        if old not in (None, "", []) and old != value and FIELD_LABELS.get(key, key) not in report.overwritten:
            report.overwritten.append(FIELD_LABELS.get(key, key))
        ss[key] = value
        report.filled += 1

    rel = _word(ex.relationship)
    put("claimant.name", _text(ex.claimant_name, "claimant.name"))
    put("claimant.relationship", rel.lower() if rel else None, RELATIONSHIPS)
    put("claimant.mobile", _text(ex.mobile, "claimant.mobile"))
    put("claimant.email", _text(ex.email, "claimant.email"))
    policy = _text(ex.policy_number, "policy_number")
    put("policy_number", policy.upper() if policy else None)
    put("hospitalization.hospital_name", _text(ex.hospital_name, "hospitalization.hospital_name"))
    put("hospitalization.admission_date", _iso(ex.admission_date))
    put("hospitalization.discharge_date", _iso(ex.discharge_date))
    put("hospitalization.diagnosis", _text(ex.diagnosis, "hospitalization.diagnosis"))
    adm_type = _word(ex.admission_type)
    put("hospitalization.admission_type", adm_type.lower() if adm_type else None, ADMISSION_TYPES)
    if isinstance(ex.is_accident, bool):
        put("hospitalization.is_accident", "Yes" if ex.is_accident else "No")
        if ex.is_accident:
            put("accident.mlc_number", _text(ex.mlc_number, "accident.mlc_number"))
            put("accident.place", _text(ex.accident_place, "accident.place"))

    items = []
    for item in ex.bill_items:
        category = _word(item.category)
        amount = _amount(item.amount)
        if category and category.lower() in BILL_CATEGORIES and amount is not None:
            items.append((category.lower(), amount))
        if len(items) >= MAX_BILL_ITEMS:
            break
    if items:
        start = ss.get("next_row_id", 2)
        ids = list(range(start, start + len(items)))
        old_keys = [k for k in list(ss.keys()) if k.startswith("bill_items.")]
        if any(ss.get(k) not in (None, "", []) for k in old_keys):
            report.overwritten.append(BILL_ROWS_LABEL)
        for key in old_keys:
            del ss[key]
        ss["bill_row_ids"], ss["next_row_id"] = ids, start + len(items)
        for rid, (category, amount) in zip(ids, items):
            ss[f"bill_items.{rid}.category"] = category
            ss[f"bill_items.{rid}.amount"] = amount
            report.filled += 2

    if report.filled:
        ss["declaration"] = False        # autofill must never leave the legal declaration ticked
    return report


def apply_extraction(ex: ClaimExtraction) -> int:
    """Write extracted values into widget state. Returns how many fields were filled."""
    return apply_extraction_with_report(ex).filled


def render_autofill(settings: Settings) -> None:
    with st.expander("Autofill from your email (optional, uses AI)"):
        st.caption(
            "Paste the message you would send to the claims desk. We extract the details and pre-fill "
            "the form below. Bill numbers and bill dates are never guessed, so add them yourself. "
            "Autofill can overwrite what you already typed and always clears the declaration tick. "
            "The text you paste is sent to the AI provider: demo data only."
        )
        text = st.text_area("Your message", key="autofill_text", height=140, max_chars=MAX_FREE_TEXT_CHARS,
                            placeholder="Hi, this is Rajesh Kumar, policy POL-458921. I was admitted to City Care "
                                        "Hospital from 10 to 14 September ... room 24,000, surgery 65,000 ...")
        if not st.button("Extract and fill the form", disabled=not text.strip()):
            return

        key = get_api_key()
        extraction = None
        try:                                           # `finally` below wipes the key on EVERY path
            if not key:
                st.warning("Enter your OpenRouter API key in the sidebar to use autofill.")
                return
            if not looks_like_openrouter_key(key):
                set_flash("error", "That does not look like an OpenRouter API key (it should start with 'sk-or-').")
                st.rerun()
            blocked = ai_blocked_message()
            if blocked:
                set_flash("warning", blocked)
                st.rerun()
            record_ai_call()
            try:
                with st.spinner("Reading your message…"):
                    extraction = extract_claim_from_text(build_llm(key, settings.model), text)
            except Exception as exc:  # noqa: BLE001
                set_flash("error", "AI call failed: " + describe_exception(exc, key))
                st.rerun()
        finally:
            key = ""                                   # drop our reference to the secret
            flush_key_after_use(settings)              # wipe the widget that held it (then the rerun drops it)

        report = apply_extraction_with_report(extraction)
        missing = ", ".join(known_missing_fields(extraction.missing_information)) or "nothing"
        overwritten = f" Replaced what you had typed in: {', '.join(report.overwritten)}." if report.overwritten else ""
        set_flash("success",
                  f"Filled {report.filled} field(s) from your message.{overwritten} Still missing: {missing}. "
                  "Please review every field and tick the declaration again: autofill never accepts it for you.")
        st.rerun()
