"""End-to-end (AppTest) checks for S-4/S-5/S-6/S-10/S-12: banners, key lifetime on error paths, throttle."""
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from securecare.agents.extractor import ClaimExtraction, ExtractedBillItem
from securecare.ui import autofill, limits

APP = str(Path(__file__).resolve().parent.parent / "app.py")
FAKE_KEY = "sk-or-v1-" + "A1b2C3d4" * 4
PAYLOAD = "[Verify your claim](http://x) ![px](http://y/p.png) :red[**URGENT**]"


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def _click(at: AppTest, label: str) -> AppTest:
    return [b for b in at.button if b.label == label][0].click().run()


def _dump(at) -> str:
    return repr(at.session_state)


def _key_widgets(at):
    return [t for t in at.text_input if t.key and t.key.startswith("openrouter_key_")]


def _live(text: str) -> bool:
    """True if the banner text still contains a live link/image/directive (unescaped brackets, contiguous URL)."""
    return bool(re.search(r"(?<!\\)\[|https?://|:red\[", text))


def _ready_claim(at: AppTest) -> AppTest:
    _click(at, "Load sample")
    return at.checkbox(key="declaration").check().run()


def _submit_with_key(at: AppTest) -> AppTest:
    _key_widgets(at)[0].set_value(FAKE_KEY).run()
    _ready_claim(at)
    return _click(at, "Submit claim")


@pytest.fixture(autouse=True)
def _no_gap(monkeypatch):
    monkeypatch.setattr(limits, "MIN_SECONDS_BETWEEN_AI_CALLS", 0)       # tests do several AI actions in a row


# ---------------------------------------------------------------- S-5 / S-4 / S-6: autofill
def _patch_autofill(monkeypatch, extraction=None, error=None):
    def fake_extract(llm, text):
        if error:
            raise error
        return extraction
    monkeypatch.setattr(autofill, "extract_claim_from_text", fake_extract)
    monkeypatch.setattr(autofill, "build_llm", lambda key, model="x": object())


def _autofill(at: AppTest, key: str = FAKE_KEY) -> AppTest:
    _key_widgets(at)[0].set_value(key).run()
    at.text_area(key="autofill_text").set_value("Hi, I am Other Name").run()
    return _click(at, "Extract and fill the form")


def test_autofill_banner_is_plain_text_declaration_reset_and_key_flushed(monkeypatch):
    ex = ClaimExtraction(claimant_name="Other Name", hospital_name="City Care Hospital",
                         bill_items=[ExtractedBillItem(category="room", amount=10 ** 400)],
                         missing_information=[PAYLOAD, "Mobile number", "Bill dates", "Click http://evil.example"])
    _patch_autofill(monkeypatch, ex)
    at = _app()
    at.text_input(key="claimant.name").set_value("Typed Name").run()
    at.checkbox(key="declaration").check().run()
    _autofill(at)
    assert not at.exception
    banner = " ".join(s.value for s in at.success)
    assert "Still missing: Mobile number, Bill dates." in banner                 # allow-listed labels only
    assert "Verify your claim" not in banner and "evil.example" not in banner and not _live(banner)
    assert "Replaced what you had typed in: Full name." in banner                # says what was overwritten
    assert "tick the declaration again" in banner
    assert at.text_input(key="claimant.name").value == "Other Name"
    assert at.checkbox(key="declaration").value is False                         # never left ticked by autofill
    assert FAKE_KEY not in _dump(at) and _key_widgets(at)[0].value in ("", None)


def test_autofill_error_text_is_redacted_escaped_and_the_key_is_flushed(monkeypatch):
    _patch_autofill(monkeypatch, error=RuntimeError(f"401 for {FAKE_KEY} {PAYLOAD}"))
    at = _app()
    _autofill(at)
    assert not at.exception
    err = " ".join(e.value for e in at.error)
    assert "AI call failed" in err and FAKE_KEY not in err and "A1b2C3d4" not in err and not _live(err)
    assert FAKE_KEY not in _dump(at), "the key must be gone right after a FAILED autofill, not on the next rerun"


def test_autofill_with_a_malformed_key_still_flushes_it(monkeypatch):
    _patch_autofill(monkeypatch, ClaimExtraction())
    at = _app()
    _autofill(at, key="sk-or-v1-" + "a" * 20 + "\tTAB")
    assert any("does not look like" in e.value for e in at.error)
    assert "TAB" not in _dump(at) and _key_widgets(at)[0].value in ("", None)


def test_autofill_keeps_the_key_only_when_opted_in(monkeypatch):
    _patch_autofill(monkeypatch, error=RuntimeError("boom"))
    at = _app()
    at.checkbox(key="keep_key").check().run()
    _autofill(at)
    assert _key_widgets(at)[0].value == FAKE_KEY


def test_autofill_is_throttled_per_session_and_new_claim_does_not_reset_it(monkeypatch):
    _patch_autofill(monkeypatch, ClaimExtraction(claimant_name="Other Name"))
    monkeypatch.setattr(limits, "MAX_AI_CALLS_PER_SESSION", 1)
    at = _app()
    _autofill(at)
    assert at.session_state[limits.AI_CALLS_KEY] == 1
    _click(at, "New claim")
    assert at.session_state[limits.AI_CALLS_KEY] == 1
    _autofill(at)
    assert any("AI actions allowed" in w.value for w in at.warning)
    assert at.session_state[limits.AI_CALLS_KEY] == 1


# ---------------------------------------------------------------- S-6: key lifetime on submit error paths
def test_key_is_gone_after_a_forced_workflow_exception(monkeypatch):
    def boom(graph, state):
        raise RuntimeError(f"upstream failure with {FAKE_KEY} {PAYLOAD}")
    monkeypatch.setattr("securecare.graph.run_claim", boom)
    at = _app()
    _submit_with_key(at)
    assert not at.exception                                       # no raw traceback with the key in it
    err = " ".join(e.value for e in at.error)
    assert "Workflow error" in err and FAKE_KEY not in err and not _live(err)
    assert FAKE_KEY not in _dump(at)
    assert _key_widgets(at)[0].value in ("", None)


def test_exception_while_building_the_client_is_handled_and_redacted(monkeypatch):
    def bad_build(api_key, model="x"):
        raise ValueError(f"cannot build client for {api_key}")
    monkeypatch.setattr("securecare.agents.llm.build_llm", bad_build)
    at = _app()
    _submit_with_key(at)
    assert not at.exception, "build_llm must run inside the try block"
    err = " ".join(e.value for e in at.error)
    assert "Workflow error" in err and FAKE_KEY not in err
    assert FAKE_KEY not in _dump(at)


def test_key_is_flushed_when_validation_blocks_the_submit():
    at = _app()
    _key_widgets(at)[0].set_value(FAKE_KEY).run()
    _click(at, "Submit claim")                                    # blank form: validation failure path
    assert any("highlighted" in e.value for e in at.error)
    assert FAKE_KEY not in _dump(at)


def test_kept_key_survives_a_failed_submit(monkeypatch):
    monkeypatch.setattr("securecare.graph.run_claim", lambda g, s: (_ for _ in ()).throw(RuntimeError("boom")))
    at = _app()
    at.checkbox(key="keep_key").check().run()
    _submit_with_key(at)
    assert _key_widgets(at)[0].value == FAKE_KEY


def test_submit_ai_budget_falls_back_to_templates_with_a_notice(monkeypatch):
    monkeypatch.setattr(limits, "MAX_AI_CALLS_PER_SESSION", 0)
    used = []
    monkeypatch.setattr("securecare.agents.llm.build_llm", lambda k, m="x": used.append(k))
    at = _app()
    _submit_with_key(at)
    assert used == [] and any("AI actions allowed" in w.value for w in at.warning)
    assert any("template text" in c.value for c in at.caption)


# ---------------------------------------------------------------- S-4 / S-5 / S-1: result screen
def _result_page():
    at = _app()
    _ready_claim(at)
    return _click(at, "Submit claim")


def test_result_screen_neutralises_hostile_state_and_survives_lone_surrogates():
    at = _result_page()
    run = at.session_state["last_run"]
    run.state.update(comms_error=PAYLOAD + f" {FAKE_KEY}", comms_source="llm",
                     claimant_letter="Dear \ud800 claimant,‮ claim received.", review_reasons=[PAYLOAD],
                     status="strange\ud800status")
    at.session_state["last_run"] = run
    at.run()
    assert not at.exception
    warning = " ".join(w.value for w in at.warning)
    assert "The AI step failed" in warning and not _live(warning) and "A1b2C3d4" not in warning
    assert not _live(" ".join(m.value for m in at.markdown if "Why an officer" in m.value))
    assert not _live(" ".join(i.value for i in at.info))


def test_ai_caption_is_honest_about_what_was_checked():
    at = _result_page()
    run = at.session_state["last_run"]
    run.state["comms_source"] = "llm"
    at.session_state["last_run"] = run
    at.run()
    caption = " ".join(c.value for c in at.caption)
    assert "AI-drafted" in caption and "automatically checked" in caption and "officer review" in caption
    assert "verified by the workflow" not in caption


def test_flash_banner_only_dispatches_known_kinds():
    at = _app()
    at.session_state["flash"] = {"kind": "__class__", "text": "x"}
    at.run()
    assert not at.exception


# ---------------------------------------------------------------- S-12: demo notice
def test_demo_notice_is_shown_at_the_top_of_the_form():
    at = _app()
    notice = [i.value for i in at.info if "Demo only" in i.value]
    assert notice and "real personal or medical information" in notice[0] and "AI provider" in notice[0]
    assert not at.warning                                            # still a clean boot (existing smoke test)


# ---------------------------------------------------------------- S-2 / S-5: form widgets match the limits
def test_documents_widget_says_self_declared_and_officer_verified():
    at = _app()
    docs = at.multiselect(key="documents")
    assert docs.label == "Documents you have and will submit"
    assert "self-declared" in docs.help and "officer verifies" in docs.help and "nothing is uploaded" in docs.help.lower()


def test_text_widgets_use_the_shared_field_limits():
    from securecare.config import FIELD_LIMITS
    at = _app()
    for key, limit in (("claimant.name", 60), ("claimant.email", 100), ("hospitalization.hospital_name", 100),
                       ("hospitalization.diagnosis", 200), ("policy_number", 10)):
        assert FIELD_LIMITS[key] == limit and at.text_input(key=key).max_chars == limit, key
