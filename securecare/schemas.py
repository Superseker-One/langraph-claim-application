"""Pydantic models for the claim FORM (what the user types), with field-level validation.

Mirrors the 'business schema' from the notebooks:
    static sections   -> ClaimantIn, HospitalizationIn
    conditional       -> PatientIn (if not 'self'), AccidentIn (if accident)  [enforced by ClaimSubmission]
    dynamic (1..N)    -> BillItemIn list
    extensible        -> extra_fields dict (rules live in config.EXTRA_FIELD_REGISTRY)

Validation messages are written for end users: they are shown next to the form field.
Every free-text value is cleaned first (NFKC, no control/zero-width/bidi characters, one line) and
length limits are enforced AFTER cleaning. Digits are ASCII-only ([0-9], never \\d).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import (
    BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, field_validator, model_validator,
)
from pydantic_core import InitErrorDetails, PydanticCustomError

from securecare.config import (
    DOCUMENT_LABELS, EXTRA_FIELD_REGISTRY, MAX_BILL_AMOUNT, MAX_BILL_ITEMS, MAX_EXTRA_VALUE_CHARS, MAX_STAY_DAYS,
    ExtraField, active_extra_fields,
)
from securecare.textsafe import clean_line

_WORD = r"[A-Za-z]+(?:['\-][A-Za-z]+)*\.?"
NAME_RE = re.compile(rf"^{_WORD}(?: {_WORD}){{0,4}}\Z")        # up to 5 words
CITY_RE = re.compile(rf"^{_WORD}(?: {_WORD}){{0,3}}\Z")        # up to 4 words
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}\Z")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\Z")
EMAIL_MAX_CHARS = 254
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}\Z")
POLICY_RE = re.compile(r"^POL-[0-9]{6}\Z")
BILL_NO_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{2,19}\Z")
ACCIDENT_REF_RE = re.compile(r"^(MLC|FIR)-([0-9]{4})-[0-9]{3,6}\Z")
_IST = timezone(timedelta(hours=5, minutes=30))
_MAX_DOT_ABBREVIATION = 4      # 'Dr.', 'Md.', 'K.' are fine; 'Rajesh. Great news ...' is not a name


def _today() -> date:  # separate function so tests can monkeypatch "today"
    """Today in India (IST, UTC+5:30). Streamlit Cloud runs in UTC, so date.today() would be a day behind
    for Indian users between 00:00 and 05:30; a fixed offset needs no tz database."""
    return datetime.now(_IST).date()


def _words_ok(value: str, pattern: "re.Pattern[str]", min_len: int, max_len: int) -> bool:
    """Letters only, few words, and a '.' only after a short abbreviation (so a name cannot hold sentences)."""
    if not (min_len <= len(value) <= max_len) or not pattern.match(value):
        return False
    return all(len(w.rstrip(".")) <= _MAX_DOT_ABBREVIATION for w in value.split(" ") if w.endswith("."))


def is_person_name(value: str) -> bool:
    return _words_ok(value, NAME_RE, 2, 60)


def normalise_extra_value(spec: ExtraField, value: object) -> str:
    """Clean an extensible-field value; text fields are upper-cased (their patterns are upper-case)."""
    cleaned = clean_line(value)
    return cleaned.upper() if spec.get("kind") == "text" else cleaned


class _Section(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _clean_free_text(cls, v: Any) -> Any:
        """Shared first step for every string field: strip control/zero-width/bidi chars, collapse
        whitespace. Runs BEFORE min_length/max_length, so a 'blank' zero-width value is empty."""
        return clean_line(v) if isinstance(v, str) else v


# ------------------------------------------------------------------ static sections
class ClaimantIn(_Section):
    name: str
    relationship: Literal["self", "spouse", "child", "parent", "other"]
    mobile: str
    email: str = Field(max_length=EMAIL_MAX_CHARS)
    city: str
    pincode: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not is_person_name(v):
            raise ValueError("Use a real name: 2-60 letters, up to 5 words. No digits or sentences.")
        return v

    @field_validator("mobile", mode="before")
    @classmethod
    def _mobile(cls, v):
        if isinstance(v, str):
            v = re.sub(r"[\s\-]", "", clean_line(v))
            if v.startswith("+91"):
                v = v[3:]
        if not isinstance(v, str) or not MOBILE_RE.match(v):
            raise ValueError("Enter a valid 10-digit Indian mobile number starting with 6-9.")
        return v

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        if not EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address, e.g. name@example.com.")
        return v.lower()

    @field_validator("city")
    @classmethod
    def _city(cls, v: str) -> str:
        if not _words_ok(v, CITY_RE, 2, 50):
            raise ValueError("Enter a valid city name (letters only).")
        return v

    @field_validator("pincode")
    @classmethod
    def _pincode(cls, v: str) -> str:
        if not PINCODE_RE.match(v):
            raise ValueError("Pincode must be 6 digits and cannot start with 0.")
        return v


class HospitalizationIn(_Section):
    hospital_name: str = Field(min_length=3, max_length=100)
    admission_date: date
    discharge_date: date
    diagnosis: str = Field(min_length=3, max_length=200)
    admission_type: Literal["planned", "emergency"]
    is_accident: bool

    @field_validator("admission_date")
    @classmethod
    def _admission(cls, v: date) -> date:
        if v > _today():
            raise ValueError("Admission date cannot be in the future.")
        return v

    @field_validator("discharge_date")
    @classmethod
    def _discharge(cls, v: date, info: ValidationInfo) -> date:
        if v > _today():
            raise ValueError("Discharge date cannot be in the future.")
        admission = info.data.get("admission_date")
        if admission is not None:
            if v < admission:
                raise ValueError("Discharge date cannot be before the admission date.")
            if (v - admission).days > MAX_STAY_DAYS:
                raise ValueError(f"Hospital stay cannot exceed {MAX_STAY_DAYS} days.")
        return v


# ------------------------------------------------------------------ conditional sections
class PatientIn(_Section):
    """Only needed when the claimant is not the patient."""
    name: str
    dob: date
    gender: Literal["female", "male", "other"]

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not is_person_name(v):
            raise ValueError("Use a real name: 2-60 letters, up to 5 words. No digits or sentences.")
        return v

    @field_validator("dob")
    @classmethod
    def _dob(cls, v: date) -> date:
        if v >= _today():
            raise ValueError("Date of birth must be in the past.")
        if v < _today() - timedelta(days=365 * 110):
            raise ValueError("Date of birth looks too old. Please re-check.")
        return v


class AccidentIn(_Section):
    """Only needed when the hospitalisation was due to an accident."""
    mlc_number: str
    place: str = Field(min_length=3, max_length=100)

    @field_validator("mlc_number")
    @classmethod
    def _ref(cls, v: str) -> str:
        v = v.upper()
        if not ACCIDENT_REF_RE.match(v):
            raise ValueError(f"Use the format MLC-{_today().year}-4471 or FIR-{_today().year}-1234.")
        return v


# ------------------------------------------------------------------ dynamic section (1..N)
class BillItemIn(_Section):
    row_id: int = Field(default=0, exclude=True)   # UI bookkeeping, never sent to the graph
    category: Literal["room", "surgery", "pharmacy", "diagnostics", "other"]
    bill_number: str
    bill_date: date
    amount: int = Field(gt=0, le=MAX_BILL_AMOUNT)

    @field_validator("bill_number")
    @classmethod
    def _bill_no(cls, v: str) -> str:
        v = v.upper()
        if not BILL_NO_RE.match(v):
            raise ValueError("Bill number: 3-20 letters/digits (/ and - allowed).")
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def _whole_rupees(cls, v):
        if isinstance(v, float):
            if not v.is_integer():
                raise ValueError("Enter a whole number of rupees.")
            return int(v)
        return v


# ------------------------------------------------------------------ the complete submission
def _section_error(error_type: str, message: str, loc: tuple, value: Any = None) -> InitErrorDetails:
    return InitErrorDetails(type=PydanticCustomError(error_type, message), loc=loc, input=value)


class ClaimSubmission(_Section):
    claimant: ClaimantIn
    patient: Optional[PatientIn] = None
    policy_number: str
    hospitalization: HospitalizationIn
    accident: Optional[AccidentIn] = None
    bill_items: List[BillItemIn] = Field(min_length=1, max_length=MAX_BILL_ITEMS)
    documents: List[str] = Field(default_factory=list)
    extra_fields: Dict[str, str] = Field(default_factory=dict)
    declaration: bool

    @field_validator("policy_number")
    @classmethod
    def _policy(cls, v: str) -> str:
        v = v.upper()
        if not POLICY_RE.match(v):
            raise ValueError("Policy number must look like POL-458921 (POL- plus 6 digits).")
        return v

    @field_validator("documents")
    @classmethod
    def _docs(cls, v: List[str]) -> List[str]:
        unknown = [d for d in v if d not in DOCUMENT_LABELS]
        if unknown:
            raise ValueError("Unknown document type selected.")
        return v

    @field_validator("extra_fields")
    @classmethod
    def _extras(cls, v: Dict[str, str]) -> Dict[str, str]:
        """Only registry keys, cleaned and normalised, short; values of inactive conditional fields are dropped."""
        specs = {f["name"]: f for f in EXTRA_FIELD_REGISTRY}
        if any(k not in specs for k in v):
            raise ValueError("Unknown additional field.")
        cleaned = {k: normalise_extra_value(specs[k], val) for k, val in v.items()}
        if any(len(val) > MAX_EXTRA_VALUE_CHARS for val in cleaned.values()):
            raise ValueError(f"Additional details can be at most {MAX_EXTRA_VALUE_CHARS} characters.")
        active = {f["name"] for f in active_extra_fields(cleaned)}
        return {k: val for k, val in cleaned.items() if k in active}

    @field_validator("declaration")
    @classmethod
    def _declaration(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must accept the declaration to submit.")
        return v

    @model_validator(mode="after")
    def _conditional_sections(self) -> "ClaimSubmission":
        """The conditional sections are MANDATORY when their condition holds (not just optional extras)."""
        errors: List[InitErrorDetails] = []
        adm = self.hospitalization.admission_date
        if self.hospitalization.is_accident and self.accident is None:
            errors.append(_section_error(
                "accident_required", "Accident details (MLC / FIR number and place) are required for accident claims.",
                ("accident", "mlc_number")))
        if self.claimant.relationship != "self" and self.patient is None:
            errors.append(_section_error(
                "patient_required", "Patient details are required when the claimant is not the patient.",
                ("patient", "name")))
        if self.patient is not None and self.patient.dob > adm:
            errors.append(_section_error(
                "dob_after_admission", "Patient date of birth cannot be after the admission date.",
                ("patient", "dob"), self.patient.dob))
        if self.accident is not None:
            year = int(ACCIDENT_REF_RE.match(self.accident.mlc_number).group(2))   # format already validated
            if not (adm.year <= year <= _today().year):
                allowed = str(adm.year) if adm.year == _today().year else f"{adm.year} to {_today().year}"
                errors.append(_section_error(
                    "mlc_year_implausible", f"The year in the MLC / FIR number should be {allowed}.",
                    ("accident", "mlc_number"), self.accident.mlc_number))
        if errors:
            raise ValidationError.from_exception_data(type(self).__name__, errors)
        return self
