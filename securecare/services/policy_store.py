"""Mock 'policy admin system' (the customer's system of record).

In a real engagement this would be an API call. Here it is an in-memory dict so the
class can focus on LangGraph + deployment. Dates are RELATIVE to today so the demo
policies never go stale, whenever students run the app.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Optional

# (start offset in days from today, term in days) are resolved in _build()
_POLICY_SPECS: Dict[str, Dict[str, Any]] = {
    "POL-458921": dict(policy_type="family_floater", sum_insured=500_000, deductible=10_000,
                       room_rent_limit_per_day=5_000, copay_percent=0, start_offset=-300,
                       term_days=365, status="active",
                       note="Standard family floater (happy path)"),
    "POL-100200": dict(policy_type="individual", sum_insured=300_000, deductible=5_000,
                       room_rent_limit_per_day=4_000, copay_percent=10, start_offset=-200,
                       term_days=365, status="active",
                       note="Individual policy with 10% co-pay"),
    "POL-123456": dict(policy_type="individual", sum_insured=200_000, deductible=5_000,
                       room_rent_limit_per_day=3_000, copay_percent=20, start_offset=-25,
                       term_days=365, status="active",
                       note="New policy: illness claims hit the 30-day waiting period"),
    "POL-555000": dict(policy_type="family_floater", sum_insured=700_000, deductible=0,
                       room_rent_limit_per_day=6_000, copay_percent=0, start_offset=-265,
                       term_days=365, status="lapsed",
                       note="Lapsed (premium unpaid): workflow rejects the claim"),
    "POL-777888": dict(policy_type="family_floater", sum_insured=1_000_000, deductible=0,
                       room_rent_limit_per_day=8_000, copay_percent=0, start_offset=-395,
                       term_days=365, status="active",
                       note="Expired 30 days ago: the form rejects the admission date"),
}


def _build(number: str, spec: Dict[str, Any], today: date) -> Dict[str, Any]:
    start = today + timedelta(days=spec["start_offset"])
    end = start + timedelta(days=spec["term_days"])
    return {
        "policy_number": number,
        "policy_type": spec["policy_type"],
        "sum_insured": spec["sum_insured"],
        "deductible": spec["deductible"],
        "room_rent_limit_per_day": spec["room_rent_limit_per_day"],
        "copay_percent": spec["copay_percent"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "status": spec["status"],
    }


def get_policy(policy_number: str, today: Optional[date] = None) -> Optional[Dict[str, Any]]:
    """Return policy details, or None if the number is unknown."""
    spec = _POLICY_SPECS.get((policy_number or "").strip().upper())
    if spec is None:
        return None
    return _build(policy_number.strip().upper(), spec, today or date.today())


def list_demo_policies(today: Optional[date] = None) -> list[Dict[str, Any]]:
    """All demo policies with a human note (used by the 'About' tab)."""
    today = today or date.today()
    rows = []
    for number, spec in _POLICY_SPECS.items():
        row = _build(number, spec, today)
        row["note"] = spec["note"]
        rows.append(row)
    return rows
