"""Ready-made sample claims (relative dates) for demos, the 'Load sample' buttons and tests."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, Optional


def sample_raw_claim(kind: str = "simple", today: Optional[date] = None) -> Dict[str, Any]:
    """Return a raw form dict (same shape ui/form.collect_raw() produces) that passes validation.

    kind = "simple"   -> illness claim on POL-458921, all documents -> ready_for_review
           "accident" -> accident claim, claimant is the child's parent, MLC details
    """
    today = today or date.today()
    adm, dis = today - timedelta(days=20), today - timedelta(days=16)
    raw: Dict[str, Any] = {
        "claimant": {"name": "Rajesh Kumar", "relationship": "self", "mobile": "9876543210",
                     "email": "rajesh.kumar@example.com", "city": "Bengaluru", "pincode": "560001"},
        "policy_number": "POL-458921",
        "hospitalization": {"hospital_name": "City Care Hospital", "admission_date": adm,
                            "discharge_date": dis, "diagnosis": "Acute appendicitis",
                            "admission_type": "emergency", "is_accident": False},
        "bill_items": [
            {"row_id": 1, "category": "room", "bill_number": "RM-1001", "bill_date": dis, "amount": 24000},
            {"row_id": 2, "category": "surgery", "bill_number": "SG-2002", "bill_date": adm + timedelta(days=1), "amount": 65000},
            {"row_id": 3, "category": "pharmacy", "bill_number": "PH-3003", "bill_date": dis, "amount": 18500},
        ],
        "documents": ["discharge_summary", "final_bill", "operation_notes", "pharmacy_bills"],
        "extra_fields": {"room_category": "single_ac", "is_network_hospital": "yes",
                         "tpa_reference": "TPA-7781", "employer_code": "NA"},
        "declaration": True,
    }
    if kind == "accident":
        raw["claimant"].update({"name": "Anita Sharma", "relationship": "parent",
                                "mobile": "9811122233", "email": "anita.sharma@example.com"})
        raw["patient"] = {"name": "Aarav Sharma", "dob": date(2015, 5, 12), "gender": "male"}
        raw["hospitalization"].update({"hospital_name": "Sunrise Hospital", "diagnosis": "Fracture of left radius",
                                       "is_accident": True})
        raw["accident"] = {"mlc_number": "MLC-2026-4471", "place": "School playground"}
        raw["bill_items"] = [
            {"row_id": 1, "category": "room", "bill_number": "RM-5001", "bill_date": dis, "amount": 12000},
            {"row_id": 2, "category": "surgery", "bill_number": "SG-5002", "bill_date": adm, "amount": 95000},
        ]
        raw["documents"] = ["discharge_summary", "final_bill", "operation_notes"]   # FIR copy missing on purpose
        raw["extra_fields"] = {"room_category": "semi_private", "is_network_hospital": "no", "employer_code": "NA"}
    return raw
