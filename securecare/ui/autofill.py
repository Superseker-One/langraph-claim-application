"""Optional AI autofill: paste the claimant's email, the extractor agent pre-fills the form.
The user still reviews everything and the normal validation still runs."""
from __future__ import annotations

from datetime import date
from typing import Any

import streamlit as st

from securecare.agents.extractor import ClaimExtraction, extract_claim_from_text
from securecare.agents.llm import build_llm
from securecare.config import (
    ADMISSION_TYPES, BILL_CATEGORIES, MAX_BILL_ITEMS, MAX_FREE_TEXT_CHARS, RELATIONSHIPS,
)
from securecare.security import looks_like_openrouter_key, redact_secrets
from securecare.ui.sidebar import Settings, flush_key_after_use, get_api_key


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def apply_extraction(ex: ClaimExtraction) -> int:
    """Write extracted values into widget state. Returns how many fields were filled."""
    ss, filled = st.session_state, 0

    def put(key: str, value: Any, allowed: list | None = None) -> None:
        nonlocal filled
        if value in (None, ""):
            return
        if allowed is not None and value not in allowed:
            return
        ss[key] = value
        filled += 1

    rel = (ex.relationship or "").lower() or None
    put("claimant.name", ex.claimant_name)
    put("claimant.relationship", rel, RELATIONSHIPS)
    put("claimant.mobile", ex.mobile)
    put("claimant.email", ex.email)
    put("policy_number", (ex.policy_number or "").upper() or None)
    put("hospitalization.hospital_name", ex.hospital_name)
    put("hospitalization.admission_date", _iso(ex.admission_date))
    put("hospitalization.discharge_date", _iso(ex.discharge_date))
    put("hospitalization.diagnosis", ex.diagnosis)
    put("hospitalization.admission_type", (ex.admission_type or "").lower() or None, ADMISSION_TYPES)
    if ex.is_accident is not None:
        put("hospitalization.is_accident", "Yes" if ex.is_accident else "No")
    if ex.is_accident:
        put("accident.mlc_number", ex.mlc_number)
        put("accident.place", ex.accident_place)

    items = [i for i in ex.bill_items if i.category.lower() in BILL_CATEGORIES and i.amount > 0][:MAX_BILL_ITEMS]
    if items:
        start = ss.get("next_row_id", 2)
        ids = list(range(start, start + len(items)))
        for key in [k for k in list(ss.keys()) if k.startswith("bill_items.")]:
            del ss[key]
        ss["bill_row_ids"], ss["next_row_id"] = ids, start + len(items)
        for rid, item in zip(ids, items):
            ss[f"bill_items.{rid}.category"] = item.category.lower()
            ss[f"bill_items.{rid}.amount"] = item.amount
            filled += 2
    return filled


def render_autofill(settings: Settings) -> None:
    with st.expander("✨ Autofill from your email (optional, uses AI)"):
        st.caption(
            "Paste the message you would send to the claims desk. We extract the details and pre-fill "
            "the form below. Bill numbers and bill dates are never guessed, so add them yourself."
        )
        text = st.text_area("Your message", key="autofill_text", height=140, max_chars=MAX_FREE_TEXT_CHARS,
                            placeholder="Hi, this is Rajesh Kumar, policy POL-458921. I was admitted to City Care "
                                        "Hospital from 10 to 14 September ... room 24,000, surgery 65,000 ...")
        if not st.button("Extract and fill the form", disabled=not text.strip()):
            return

        key = get_api_key()
        if not key:
            st.warning("Enter your OpenRouter API key in the sidebar to use autofill.")
            return
        if not looks_like_openrouter_key(key):
            st.error("That does not look like an OpenRouter API key (it should start with 'sk-or-').")
            return
        try:
            with st.spinner("Reading your message…"):
                extraction = extract_claim_from_text(build_llm(key, settings.model), text)
        except Exception as exc:  # noqa: BLE001
            st.error(f"AI call failed: {redact_secrets(f'{type(exc).__name__}: {exc}')[:250]}")
            return
        finally:
            key = ""                                   # drop our reference to the secret
            flush_key_after_use(settings)              # and wipe the widget that held it

        count = apply_extraction(extraction)
        missing = ", ".join(extraction.missing_information) or "nothing"
        st.session_state["flash"] = {"kind": "success", "text":
                                     f"Filled {count} field(s) from your message. Still missing: {missing}. "
                                     "Please review every field."}
        st.rerun()
