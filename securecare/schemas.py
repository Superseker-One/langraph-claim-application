"""Pydantic models for the claim FORM (what the user types), with field-level validation.

Mirrors the 'business schema' from the notebooks:
    static sections   -> ClaimantIn, HospitalizationIn
    conditional       -> PatientIn (if not 'self'), AccidentIn (if accident)
    dynamic (1..N)    -> BillItemIn list
    extensible        -> extra_fields dict (rules live in config.EXTRA_FIELD_REGISTRY)

Validation messages are written for end users: they are shown next to the form field.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from securecare.config import (
    DOCUMENT_LABELS, MAX_BILL_AMOUNT, MAX_BILL_ITEMS, MAX_STAY_DAYS,
)

NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,59}$")
CITY_RE = re.compile(r"^[A-Za-z][A-Za-z .'\-]{1,49}$")
MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")
POLICY_RE = re.compile(r"^POL-\d{6}$")
BILL_NO_RE = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{2,19}$")
ACCIDENT_REF_RE = re.compile(r"^(MLC|FIR)-\d{4}-\d{3,6}$")


def _today() -> date:  # separate function so tests can monkeypatch "today"
    return date.today()


class _Section(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


# ------------------------------------------------------------------ static sections
class ClaimantIn(_Section):
    name: str
    relationship: Literal["self", "spouse", "child", "parent", "other"]
    mobile: str
    email: str
    city: str
    pincode: str

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("Use 2-60 letters (spaces, . ' - allowed). No digits.")
        return v

    @field_validator("mobile", mode="before")
    @classmethod
    def _mobile(cls, v):
        if isinstance(v, str):
            v = re.sub(r"[\s\-]", "", v)
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
        if not CITY_RE.match(v):
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
        if not NAME_RE.match(v):
            raise ValueError("Use 2-60 letters (spaces, . ' - allowed). No digits.")
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
            raise ValueError("Use the format MLC-2026-4471 or FIR-2026-1234.")
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
            raise ValueError(f"Unknown document type: {', '.join(unknown)}")
        return v

    @field_validator("declaration")
    @classmethod
    def _declaration(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("You must accept the declaration to submit.")
        return v
