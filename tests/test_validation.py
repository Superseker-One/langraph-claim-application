from datetime import timedelta

import pytest

from securecare.samples import sample_raw_claim
from securecare.validation import validate_submission


def test_sample_claims_are_valid():
    assert validate_submission(sample_raw_claim("simple")).ok
    assert validate_submission(sample_raw_claim("accident")).ok


@pytest.mark.parametrize("path,value,key", [
    (("claimant", "mobile"), "12345", "claimant.mobile"),
    (("claimant", "name"), "R2d2", "claimant.name"),
    (("claimant", "email"), "not-an-email", "claimant.email"),
    (("claimant", "pincode"), "012345", "claimant.pincode"),
    (("hospitalization", "diagnosis"), "ab", "hospitalization.diagnosis"),
])
def test_field_level_errors(path, value, key):
    raw = sample_raw_claim()
    raw[path[0]][path[1]] = value
    result = validate_submission(raw)
    assert not result.ok and key in result.errors


def test_mobile_accepts_country_code_and_spaces():
    raw = sample_raw_claim()
    raw["claimant"]["mobile"] = "+91 98765-43210"
    assert validate_submission(raw).ok


def test_policy_format_and_not_found():
    raw = sample_raw_claim(); raw["policy_number"] = "POL-45892"
    assert "policy_number" in validate_submission(raw).errors
    raw["policy_number"] = "POL-999999"
    assert "not found" in validate_submission(raw).errors["policy_number"].lower()


def test_discharge_before_admission():
    raw = sample_raw_claim()
    h = raw["hospitalization"]
    h["discharge_date"] = h["admission_date"] - timedelta(days=1)
    assert "before the admission" in validate_submission(raw).errors["hospitalization.discharge_date"]


def test_admission_outside_policy_period():
    raw = sample_raw_claim(); raw["policy_number"] = "POL-777888"   # expired demo policy
    assert "outside the policy period" in validate_submission(raw).errors["hospitalization.admission_date"]


def test_bill_rules_use_row_ids_as_keys():
    raw = sample_raw_claim()
    raw["bill_items"][0]["amount"] = -5
    raw["bill_items"][1]["bill_number"] = raw["bill_items"][0]["bill_number"]      # duplicate
    raw["bill_items"][2]["bill_date"] = raw["hospitalization"]["admission_date"] - timedelta(days=3)
    errors = validate_submission(raw).errors
    assert "bill_items.1.amount" in errors
    assert "Duplicate" in errors["bill_items.2.bill_number"]
    assert "between admission" in errors["bill_items.3.bill_date"]


def test_conditional_accident_and_patient_sections():
    raw = sample_raw_claim("accident")
    raw["accident"]["mlc_number"] = "abc"
    raw["patient"]["name"] = ""
    errors = validate_submission(raw).errors
    assert "accident.mlc_number" in errors
    assert errors["patient.name"] == "This field is required."


def test_extensible_conditional_field_is_required_only_for_network_hospital():
    raw = sample_raw_claim()
    raw["extra_fields"]["tpa_reference"] = ""
    assert validate_submission(raw).errors["extra.tpa_reference"] == "This field is required."
    raw["extra_fields"]["is_network_hospital"] = "no"
    assert validate_submission(raw).ok


def test_declaration_and_required_blank_form():
    raw = sample_raw_claim(); raw["declaration"] = False
    assert "declaration" in validate_submission(raw).errors
    blank = {"claimant": {"name": "", "relationship": None, "mobile": "", "email": "", "city": "", "pincode": ""},
             "policy_number": "", "hospitalization": {"hospital_name": "", "admission_date": None, "discharge_date": None,
             "diagnosis": "", "admission_type": None, "is_accident": None},
             "bill_items": [{"row_id": 1, "category": None, "bill_number": "", "bill_date": None, "amount": None}],
             "documents": [], "extra_fields": {"room_category": "", "is_network_hospital": ""}, "declaration": False}
    errors = validate_submission(blank).errors
    assert errors["claimant.name"] == "This field is required."
    assert errors["bill_items.1.amount"] == "This field is required."
    assert errors["extra.room_category"] == "This field is required."


# ---------------------------------------------------------------- S-3: duplicate-bill detection
@pytest.mark.parametrize("variant", ["RM1001", "RM/1001", "rm-1001", "RM-01001", "RM-1001 ", "RM-0001001",
                                     "RM\u200b-1001", "ＲＭ-１００１", "rm/0001001"])
def test_obfuscated_duplicate_bill_numbers_are_caught(variant):
    raw = sample_raw_claim()                                    # row 1 is RM-1001
    raw["bill_items"][1]["bill_number"] = variant
    errors = validate_submission(raw).errors
    assert "Duplicate bill number" in errors.get("bill_items.2.bill_number", ""), (variant, errors)


@pytest.mark.parametrize("variant", ["RM 1001", "R.M-1001"])
def test_other_separators_never_slip_through_either(variant):
    raw = sample_raw_claim()
    raw["bill_items"][1]["bill_number"] = variant
    assert "bill_items.2.bill_number" in validate_submission(raw).errors


def test_different_bill_numbers_are_not_flagged_by_canonicalisation():
    from securecare.validation import canonical_bill_number
    assert canonical_bill_number("RM-1001") == canonical_bill_number("rm/01001") == "RM1001"
    assert canonical_bill_number("RM-1001") != canonical_bill_number("RM-1010")
    assert canonical_bill_number("RM-100") != canonical_bill_number("RM-1000")
    assert validate_submission(sample_raw_claim()).ok


def test_identical_bills_with_different_numbers_are_caught_by_category_date_amount():
    raw = sample_raw_claim()
    first = raw["bill_items"][0]
    raw["bill_items"][1].update(category=first["category"], bill_date=first["bill_date"], amount=first["amount"])
    errors = validate_submission(raw).errors
    assert "same category, date and amount as bill #1" in errors["bill_items.2.bill_number"]
    assert "Duplicate" in errors["bill_items.2.bill_number"]          # user-friendly, keyed to the bill number widget
    assert "bill_items.1.bill_number" not in errors


def test_same_amount_on_a_different_date_or_category_is_fine():
    raw = sample_raw_claim()
    raw["bill_items"][1].update(amount=raw["bill_items"][0]["amount"])
    assert validate_submission(raw).ok
    raw["bill_items"][1].update(category=raw["bill_items"][0]["category"])
    raw["bill_items"][1]["bill_date"] = raw["hospitalization"]["admission_date"]
    assert validate_submission(raw).ok


def test_unicode_variants_of_the_policy_number_use_the_cleaned_value_in_business_rules():
    raw = sample_raw_claim()
    raw["policy_number"] = "POL-999999​"                # cleaned to a not-found policy: rule must still run
    assert "not found" in validate_submission(raw).errors["policy_number"].lower()
