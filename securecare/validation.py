"""Form validation: turns a raw dict (from the UI) into either a clean ClaimSubmission
or a {widget_key: message} dict. Pure Python, so it is easy to unit-test.

Three layers, in order:
  1. Pydantic models (schemas.py)       -> field formats, required fields, cross-field dates
  2. Extra-field registry (config.py)   -> extensible / conditional fields
  3. Business rules vs the policy store -> policy exists, admission inside policy period,
                                           bill dates inside the hospital stay, duplicate bills

Widget keys follow the same dotted paths the UI uses, e.g. 'claimant.mobile',
'hospitalization.admission_date', 'bill_items.3.amount', 'extra.tpa_reference'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, Optional

from pydantic import ValidationError

from securecare.config import active_extra_fields
from securecare.schemas import POLICY_RE, ClaimSubmission
from securecare.services.policy_store import get_policy

REQUIRED_MSG = "This field is required."


@dataclass
class ValidationResult:
    submission: Optional[ClaimSubmission] = None
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors and self.submission is not None


# ------------------------------------------------------------------ helpers
def _raw_value(raw: Dict[str, Any], loc: tuple) -> Any:
    cur: Any = raw
    for part in loc:
        try:
            cur = cur[part]
        except (KeyError, IndexError, TypeError):
            return None
    return cur


def _widget_key(loc: tuple, raw: Dict[str, Any]) -> str:
    if loc and loc[0] == "bill_items":
        if len(loc) >= 3:
            row_id = raw["bill_items"][loc[1]].get("row_id", loc[1])
            return f"bill_items.{row_id}.{loc[2]}"
        return "bill_items"
    if len(loc) >= 2 and loc[0] in {"claimant", "patient", "hospitalization", "accident"}:
        return f"{loc[0]}.{loc[1]}"
    return str(loc[0]) if loc else "form"


def _clean_message(msg: str) -> str:
    return msg[len("Value error, "):] if msg.startswith("Value error, ") else msg


def _map_pydantic_errors(exc: ValidationError, raw: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    for err in exc.errors():
        loc = tuple(err["loc"])
        key = _widget_key(loc, raw)
        value = _raw_value(raw, loc)
        if err["type"] == "missing" or value in (None, "", []):
            message = REQUIRED_MSG
        else:
            message = _clean_message(err["msg"])
        errors.setdefault(key, message)
    return errors


# ------------------------------------------------------------------ layer 2: extensible fields
def validate_extras(extras: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    for spec in active_extra_fields(extras):
        name = spec["name"]
        value = (extras.get(name) or "").strip()
        key = f"extra.{name}"
        if not value:
            if spec.get("required"):
                errors[key] = REQUIRED_MSG
            continue
        if spec["kind"] == "select" and value not in spec.get("options", []):
            errors[key] = "Choose one of the listed options."
        elif spec["kind"] == "text":
            candidate = value.upper()
            pattern = spec.get("pattern")
            allow_na = name == "employer_code" and candidate == "NA"
            if pattern and not allow_na and not re.fullmatch(pattern, candidate):
                errors[key] = spec.get("pattern_hint", "Invalid format.")
    return errors


# ------------------------------------------------------------------ layer 3: business rules
def _business_rules(raw: Dict[str, Any]) -> Dict[str, str]:
    errors: Dict[str, str] = {}
    hosp = raw.get("hospitalization", {})
    adm, dis = hosp.get("admission_date"), hosp.get("discharge_date")

    policy_number = str(raw.get("policy_number") or "").strip().upper()
    if POLICY_RE.match(policy_number):
        policy = get_policy(policy_number)
        if policy is None:
            errors["policy_number"] = "Policy not found. Check the number on your policy card."
        elif isinstance(adm, date):
            start, end = date.fromisoformat(policy["start_date"]), date.fromisoformat(policy["end_date"])
            if not (start <= adm <= end):
                errors["hospitalization.admission_date"] = (
                    f"Admission date is outside the policy period ({start} to {end})."
                )

    seen: Dict[str, int] = {}
    for item in raw.get("bill_items", []):
        rid = item.get("row_id")
        bill_date = item.get("bill_date")
        if isinstance(bill_date, date) and isinstance(adm, date) and isinstance(dis, date):
            if not (adm <= bill_date <= dis):
                errors[f"bill_items.{rid}.bill_date"] = "Bill date must fall between admission and discharge dates."
        number = str(item.get("bill_number") or "").strip().upper()
        if number:
            if number in seen:
                errors[f"bill_items.{rid}.bill_number"] = "Duplicate bill number. Each bill can be claimed once."
            seen[number] = rid
    return errors


# ------------------------------------------------------------------ public API
def validate_submission(raw: Dict[str, Any]) -> ValidationResult:
    errors: Dict[str, str] = {}
    submission: Optional[ClaimSubmission] = None

    try:
        submission = ClaimSubmission.model_validate(raw)
    except ValidationError as exc:
        errors.update(_map_pydantic_errors(exc, raw))

    for key, msg in {**validate_extras(raw.get("extra_fields", {})), **_business_rules(raw)}.items():
        errors.setdefault(key, msg)

    if errors:
        return ValidationResult(submission=None, errors=errors)
    return ValidationResult(submission=submission, errors={})
