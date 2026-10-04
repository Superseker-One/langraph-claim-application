"""S-4 (untrusted text in banners), S-5 (autofill clamping), S-10 (AI throttle): unit-level UI helpers."""
import re

import pytest

from securecare.agents.extractor import ClaimExtraction, ExtractedBillItem
from securecare.config import FIELD_LIMITS, MAX_BILL_AMOUNT, MAX_BILL_ITEMS, MAX_MISSING_INFO_ITEMS
from securecare.ui import autofill, limits
from securecare.ui.safe import safe_exception, safe_text

PAYLOAD = "[Verify your claim](http://x) ![px](http://y/p.png) :red[**URGENT**]"
FAKE_KEY = "sk-or-v1-" + "A1b2C3d4" * 4


# ---------------------------------------------------------------- S-4: safe_text
def _live_markdown(text: str) -> list[str]:
    """What would still be live Markdown after rendering: unescaped brackets/asterisks/angle brackets,
    contiguous auto-link openers, emoji shortcodes."""
    hits = []
    if re.search(r"(?<!\\)[\[\]*_<>`#|~$]", text):
        hits.append("unescaped markdown punctuation")
    if re.search(r"https?://|www\.[a-z]|[\w.]+@[\w.]+", text, re.I):
        hits.append("contiguous auto-link")
    if re.search(r":[a-z0-9_+\-]+:", text, re.I) or re.search(r":[a-z]+\[", text):
        hits.append("emoji shortcode / directive")
    return hits


def test_markdown_link_image_and_colour_payload_is_neutralised():
    out = safe_text(PAYLOAD)
    assert _live_markdown(out) == [], out
    assert "Verify your claim" in out and "URGENT" in out            # still readable, just not live
    assert "![" not in out and "](" not in out.replace("\\](", "")


@pytest.mark.parametrize("payload", [
    "<img src=x onerror=alert(1)>", "<script>alert(1)</script>", "&lt;b&gt;", "# Heading", "- list", "1. list",
    "| a | b |", "~~strike~~", "$$x^2$$", "`code`", "__bold__", ":smile: :tada:", "http://evil.example",
    "www.evil.com", "me@evil.com", "javascript:alert(1)", "[a][b]\n\n[b]: http://x",
])
def test_other_markdown_and_html_constructs_are_neutralised(payload):
    out = safe_text(payload)
    assert _live_markdown(out) == [], (payload, out)
    assert "\n" not in out
    assert not re.match(r"^\s*(?:[-+]|\d+[.)]) ", out)


def test_safe_text_strips_hidden_characters_caps_length_and_handles_non_strings():
    assert safe_text("a​b‮c\x00d\ud800e") == "abcde"
    out = safe_text("x" * 1000, max_len=50)
    assert len(out) <= 51 and out.endswith("…")
    assert safe_text(None) == "" and safe_text(12345) == "12345"
    safe_text("𐏿 lone surrogates").encode("utf-8")


def test_safe_text_redacts_keys_before_escaping_so_the_token_stays_contiguous():
    out = safe_text(f"401 Unauthorized for key {FAKE_KEY}_x*y[1]")
    assert "A1b2C3d4" not in out and "sk-" in out
    exact = safe_text("auth failed: my-odd_secret*value", secret="my-odd_secret*value")
    assert "odd" not in exact


def test_safe_exception_redacts_escapes_and_caps():
    exc = RuntimeError(f"{PAYLOAD} {FAKE_KEY} " + "z" * 500)
    out = safe_exception(exc, secret=FAKE_KEY, max_len=200)
    assert FAKE_KEY not in out and "A1b2C3d4" not in out and _live_markdown(out) == []
    assert out.startswith("RuntimeError")
    assert len(out) < 3 * 200                      # raw text is capped first; escapes only add a few characters


# ---------------------------------------------------------------- S-10: throttle
def test_throttle_enforces_gap_and_session_cap(monkeypatch):
    monkeypatch.setattr(limits, "MAX_AI_CALLS_PER_SESSION", 3)
    monkeypatch.setattr(limits, "MIN_SECONDS_BETWEEN_AI_CALLS", 5)
    state = {}
    assert limits.ai_blocked_message(state, now=100.0) is None
    limits.record_ai_call(state, now=100.0)
    assert "wait 3 second" in limits.ai_blocked_message(state, now=102.0)
    assert limits.ai_blocked_message(state, now=105.0) is None
    limits.record_ai_call(state, now=105.0)
    limits.record_ai_call(state, now=111.0)
    assert state[limits.AI_CALLS_KEY] == 3
    assert "3 AI actions" in limits.ai_blocked_message(state, now=500.0)


def test_new_claim_does_not_reset_the_ai_budget():
    from securecare.ui.form import FORM_KEY_PREFIXES, FORM_KEYS
    for key in (limits.AI_CALLS_KEY, limits.AI_LAST_CALL_KEY):
        assert key not in FORM_KEYS and not key.startswith(FORM_KEY_PREFIXES)


# ---------------------------------------------------------------- S-5: autofill clamping
@pytest.fixture
def session(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(autofill.st, "session_state", store)
    return store


def test_huge_integer_amount_is_dropped_instead_of_crashing(session):
    ex = ClaimExtraction(bill_items=[ExtractedBillItem(category="room", amount=10 ** 400),
                                     ExtractedBillItem(category="surgery", amount=65000)])
    report = autofill.apply_extraction_with_report(ex)
    assert session["bill_items.2.amount"] == 65000 and "bill_items.3.amount" not in session
    assert report.filled == 2


@pytest.mark.parametrize("amount", [0, -5, MAX_BILL_AMOUNT + 1, 10 ** 400])
def test_out_of_range_amounts_are_dropped(session, amount):
    ex = ClaimExtraction(bill_items=[ExtractedBillItem(category="room", amount=amount)])
    autofill.apply_extraction(ex)
    assert not any(k.startswith("bill_items.") for k in session)


def test_bill_amount_bounds_are_inclusive(session):
    ex = ClaimExtraction(bill_items=[ExtractedBillItem(category="room", amount=1),
                                     ExtractedBillItem(category="Other", amount=MAX_BILL_AMOUNT),
                                     ExtractedBillItem(category="travel", amount=500)])
    autofill.apply_extraction(ex)
    assert session["bill_items.2.amount"] == 1 and session["bill_items.3.amount"] == MAX_BILL_AMOUNT
    assert session["bill_items.3.category"] == "other" and "bill_items.4.category" not in session


def test_bill_rows_are_capped(session):
    ex = ClaimExtraction(bill_items=[ExtractedBillItem(category="room", amount=100)] * (MAX_BILL_ITEMS + 10))
    autofill.apply_extraction(ex)
    assert len(session["bill_row_ids"]) == MAX_BILL_ITEMS


def test_lone_surrogates_and_control_characters_never_reach_widget_state(session):
    ex = ClaimExtraction(claimant_name="Ra\ud800jesh\x00 Kumar‮", hospital_name="City\ud83d Care",
                         diagnosis="Fever​\n\nand cough")
    autofill.apply_extraction(ex)
    assert session["claimant.name"] == "Rajesh Kumar"
    assert session["hospitalization.hospital_name"] == "City Care"
    assert session["hospitalization.diagnosis"] == "Fever and cough"
    for value in session.values():
        if isinstance(value, str):
            value.encode("utf-8")


def test_over_long_strings_are_clamped_to_the_form_limits(session):
    ex = ClaimExtraction(hospital_name="H" * 3_000_000, email="e" * 1_000_000 + "@x.com", claimant_name="N" * 500,
                         diagnosis="D" * 10_000, mobile="9" * 100, policy_number="p" * 100,
                         is_accident=True, mlc_number="M" * 100, accident_place="P" * 1000)
    autofill.apply_extraction(ex)
    for key, limit in (("hospitalization.hospital_name", FIELD_LIMITS["hospitalization.hospital_name"]),
                       ("claimant.email", FIELD_LIMITS["claimant.email"]), ("claimant.name", 60),
                       ("hospitalization.diagnosis", 200), ("claimant.mobile", 16), ("policy_number", 10),
                       ("accident.mlc_number", 20), ("accident.place", 100)):
        assert 0 < len(session[key]) <= limit, key
    assert session["policy_number"] == "P" * 10


def test_invalid_enums_and_dates_are_dropped(session):
    ex = ClaimExtraction(relationship="cousin-twice-removed", admission_type="whenever", admission_date="0001-01-01",
                         discharge_date="2999-12-31")
    assert autofill.apply_extraction(ex) == 0
    assert session == {}


def test_autofill_clears_the_declaration_and_reports_overwritten_fields(session):
    session.update({"declaration": True, "claimant.name": "Typed Name", "claimant.email": "same@example.com",
                    "bill_items.1.amount": 500})
    ex = ClaimExtraction(claimant_name="Other Name", email="same@example.com",
                         bill_items=[ExtractedBillItem(category="room", amount=900)])
    report = autofill.apply_extraction_with_report(ex)
    assert session["declaration"] is False
    assert report.overwritten == ["Full name", autofill.BILL_ROWS_LABEL]      # unchanged email is not 'overwritten'


def test_nothing_extracted_leaves_the_declaration_alone(session):
    session["declaration"] = True
    autofill.apply_extraction(ClaimExtraction())
    assert session["declaration"] is True


# ---------------------------------------------------------------- S-4: allow-list for 'missing information'
def test_missing_information_is_mapped_to_known_labels_only():
    items = [PAYLOAD, "Mobile number", "policy number", "BILL DATES", "claimant_name", "Click https://x.co to claim",
             "Mobile number", "pin code"]
    assert autofill.known_missing_fields(items) == ["Mobile number", "policy number", "BILL DATES", "claimant_name",
                                                    "pin code"]


def test_missing_information_count_is_capped():
    labels = [l for l in autofill._MISSING_ALIASES]
    assert len(autofill.known_missing_fields(labels * 3)) == MAX_MISSING_INFO_ITEMS
    assert autofill.known_missing_fields(None) == [] and autofill.known_missing_fields(["x"] * 100) == []
