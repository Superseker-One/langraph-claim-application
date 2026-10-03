"""S-2 (conditional sections) and S-8 (input hygiene) at the schema / validation level."""
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from securecare import schemas
from securecare.config import MAX_EXTRA_VALUE_CHARS
from securecare.samples import sample_raw_claim
from securecare.schemas import ClaimSubmission
from securecare.validation import validate_submission


# ---------------------------------------------------------------- S-2: conditional sections are enforced
def test_accident_section_is_mandatory_when_is_accident():
    raw = sample_raw_claim("accident")
    del raw["accident"]
    with pytest.raises(ValidationError) as exc:
        ClaimSubmission.model_validate(raw)
    assert any(e["type"] == "accident_required" for e in exc.value.errors())
    errors = validate_submission(raw).errors
    assert "accident.mlc_number" in errors and "MLC" in errors["accident.mlc_number"]


def test_patient_section_is_mandatory_when_claimant_is_not_the_patient():
    raw = sample_raw_claim("accident")
    del raw["patient"]
    with pytest.raises(ValidationError) as exc:
        ClaimSubmission.model_validate(raw)
    assert any(e["type"] == "patient_required" for e in exc.value.errors())
    assert "patient.name" in validate_submission(raw).errors


def test_both_missing_sections_are_reported_together():
    raw = sample_raw_claim("accident")
    del raw["patient"], raw["accident"]
    errors = validate_submission(raw).errors
    assert "patient.name" in errors and "accident.mlc_number" in errors


def test_patient_dob_cannot_be_after_admission():
    raw = sample_raw_claim("accident")
    raw["patient"]["dob"] = raw["hospitalization"]["admission_date"] + timedelta(days=1)
    assert "after the admission" in validate_submission(raw).errors["patient.dob"]
    raw["patient"]["dob"] = raw["hospitalization"]["admission_date"]          # born on the admission day is fine
    assert validate_submission(raw).ok


def test_mlc_year_must_be_plausible():
    raw = sample_raw_claim("accident")
    adm_year = raw["hospitalization"]["admission_date"].year
    for bad_year in (adm_year - 1, schemas._today().year + 1):
        raw["accident"]["mlc_number"] = f"MLC-{bad_year}-4471"
        assert "year" in validate_submission(raw).errors["accident.mlc_number"]
    raw["accident"]["mlc_number"] = f"FIR-{adm_year}-4471"
    assert validate_submission(raw).ok


def test_self_claim_does_not_need_a_patient():
    assert validate_submission(sample_raw_claim("simple")).ok


# ---------------------------------------------------------------- S-8: ASCII digits only
@pytest.mark.parametrize("path,value,key", [
    (("claimant", "mobile"), "9٠١٢٣٤٥٦٧٨", "claimant.mobile"),   # Arabic-Indic zero..
    (("claimant", "mobile"), "98765०43210", "claimant.mobile"),                                         # Devanagari digit
    (("claimant", "pincode"), "5٦٠٠٠١", "claimant.pincode"),
])
def test_non_ascii_digits_are_rejected(path, value, key):
    raw = sample_raw_claim()
    raw[path[0]][path[1]] = value
    assert key in validate_submission(raw).errors


def test_non_ascii_tpa_reference_is_rejected_and_fullwidth_is_normalised():
    raw = sample_raw_claim()
    raw["extra_fields"]["tpa_reference"] = "TPA-٧٧٨١"
    assert "extra.tpa_reference" in validate_submission(raw).errors
    raw["extra_fields"]["tpa_reference"] = "tpa-７７８１"                       # full-width digits fold to ASCII
    result = validate_submission(raw)
    assert result.ok and result.submission.extra_fields["tpa_reference"] == "TPA-7781"


def test_policy_number_with_non_ascii_digits_is_rejected():
    raw = sample_raw_claim()
    raw["policy_number"] = "POL-45892١"
    assert "policy_number" in validate_submission(raw).errors


# ---------------------------------------------------------------- S-8: cleaning + min_length after cleaning
def test_zero_width_only_hospital_name_is_required_not_accepted():
    raw = sample_raw_claim()
    raw["hospitalization"]["hospital_name"] = "​​​​"
    assert validate_submission(raw).errors["hospitalization.hospital_name"] == "This field is required."
    raw["hospitalization"]["hospital_name"] = "A​B"                          # 2 visible chars < min 3
    assert "at least 3" in validate_submission(raw).errors["hospitalization.hospital_name"]


def test_free_text_is_cleaned_before_it_is_stored():
    raw = sample_raw_claim()
    raw["hospitalization"]["hospital_name"] = "City‮ Care\n\tHospital​"
    raw["hospitalization"]["diagnosis"] = "Acute\x00 appendicitis­"
    sub = validate_submission(raw).submission
    assert sub.hospitalization.hospital_name == "City Care Hospital"
    assert sub.hospitalization.diagnosis == "Acute appendicitis"


def test_email_length_is_capped_at_254():
    raw = sample_raw_claim()
    raw["claimant"]["email"] = "a" * 250 + "@x.com"
    assert "claimant.email" in validate_submission(raw).errors
    raw["claimant"]["email"] = "a" * 60 + "@example.com"
    assert validate_submission(raw).ok


@pytest.mark.parametrize("name", [
    "Rajesh. Great news claim approved and guaranteed in full", "Claim approved. Estimated payable 5",
    "Rajesh Kumar Singh Rathore Verma Extra", "Rajesh.Kumar", "R2d2", "Rajesh! Kumar", "Rajesh  ;  Kumar",
])
def test_name_cannot_hold_sentences(name):
    raw = sample_raw_claim()
    raw["claimant"]["name"] = name
    assert "claimant.name" in validate_submission(raw).errors


@pytest.mark.parametrize("name", ["Rajesh Kumar", "A. P. J. Abdul Kalam", "Mary-Jane O'Neil", "Dr. Anita Sharma", "Md. Ali"])
def test_ordinary_names_still_pass(name):
    raw = sample_raw_claim()
    raw["claimant"]["name"] = name
    assert validate_submission(raw).ok


def test_extra_fields_are_restricted_to_registry_keys_and_short_values():
    raw = sample_raw_claim()
    raw["extra_fields"]["evil_key"] = "x"
    assert "extra_fields" in validate_submission(raw).errors
    raw = sample_raw_claim()
    raw["extra_fields"]["employer_code"] = "A" * (MAX_EXTRA_VALUE_CHARS + 1)
    assert "extra.employer_code" in validate_submission(raw).errors
    raw = sample_raw_claim()
    raw["extra_fields"]["employer_code"] = " ab​c123 "
    assert validate_submission(raw).submission.extra_fields["employer_code"] == "ABC123"      # cleaned + upper-case


def test_inactive_conditional_extra_values_are_dropped():
    raw = sample_raw_claim()
    raw["extra_fields"].update(is_network_hospital="no", tpa_reference="TPA-1111")
    result = validate_submission(raw)
    assert result.ok and "tpa_reference" not in result.submission.extra_fields


# ---------------------------------------------------------------- S-8: IST-aware 'today'
def _fake_clock(monkeypatch, *utc_args):
    """Freeze the real clock that schemas._today() reads (a UTC instant), without patching _today itself."""
    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            base = datetime(*utc_args, tzinfo=timezone.utc)
            return base.astimezone(tz) if tz else base.replace(tzinfo=None)
    monkeypatch.setattr(schemas, "datetime", FakeDatetime)


def test_indian_user_can_enter_todays_date_just_after_midnight_ist(monkeypatch):
    # 19:00 UTC on 9 June is 00:30 IST on 10 June: a UTC server's date.today() would reject 10 June
    _fake_clock(monkeypatch, 2027, 6, 9, 19, 0)
    assert schemas._today() == date(2027, 6, 10)
    raw = sample_raw_claim()
    raw["hospitalization"]["discharge_date"] = date(2027, 6, 10)
    assert "future" not in validate_submission(raw).errors.get("hospitalization.discharge_date", "")
    raw["hospitalization"]["discharge_date"] = date(2027, 6, 11)
    assert "future" in validate_submission(raw).errors["hospitalization.discharge_date"]


def test_today_rolls_over_at_ist_midnight_not_utc_midnight(monkeypatch):
    _fake_clock(monkeypatch, 2027, 6, 9, 18, 29)          # 23:59 IST on the 9th
    assert schemas._today() == date(2027, 6, 9)
    _fake_clock(monkeypatch, 2027, 6, 9, 18, 30)          # 00:00 IST on the 10th
    assert schemas._today() == date(2027, 6, 10)
