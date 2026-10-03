"""End-to-end UI tests using Streamlit's AppTest (no browser, no network)."""
import os
from pathlib import Path

from streamlit.testing.v1 import AppTest

from securecare.agents.communicator import Communications

APP = str(Path(__file__).resolve().parent.parent / "app.py")
FAKE_KEY = "sk-or-v1-" + "A1b2C3d4" * 4


def _app() -> AppTest:
    return AppTest.from_file(APP, default_timeout=30).run()


def _click(at: AppTest, label: str) -> AppTest:
    return [b for b in at.button if b.label == label][0].click().run()


def _dump(at) -> str:
    """Everything stored in this visitor's session, as one string."""
    return repr(at.session_state)


def _captions(at):
    return [c.value for c in at.caption if "⚠️" in c.value]


def test_app_boots_without_errors():
    at = _app()
    assert not at.exception and not at.warning


def test_inline_validation_alerts_as_user_types():
    at = _app()
    at.text_input(key="claimant.mobile").set_value("12345").run()
    assert any("10-digit" in c for c in _captions(at))
    at.text_input(key="policy_number").set_value("POL-45892").run()
    assert any("POL-" in c for c in _captions(at))
    at.text_input(key="policy_number").set_value("POL-999999").run()
    assert any("not found" in c for c in _captions(at))


def test_conditional_sections_appear_and_disappear():
    at = _app()
    assert not any(t.key == "patient.name" for t in at.text_input)
    at.selectbox(key="claimant.relationship").set_value("parent").run()
    assert any(t.key == "patient.name" for t in at.text_input)
    at.radio(key="hospitalization.is_accident").set_value("Yes").run()
    assert any(t.key == "accident.mlc_number" for t in at.text_input)
    at.radio(key="hospitalization.is_accident").set_value("No").run()
    assert not any(t.key == "accident.mlc_number" for t in at.text_input)


def test_dynamic_bill_rows():
    at = _app()
    assert sum(1 for t in at.text_input if t.key and t.key.startswith("bill_items.")) == 1
    _click(at, "➕ Add another bill")
    assert sum(1 for t in at.text_input if t.key and t.key.startswith("bill_items.")) == 2


def test_blocked_submit_shows_all_required_errors():
    at = _app()
    _click(at, "Submit claim")
    assert any("highlighted" in e.value for e in at.error)
    assert any("required" in c for c in _captions(at))


def test_full_flow_without_key_uses_templates():
    at = _app()
    _click(at, "Load sample")
    at.checkbox(key="declaration").check().run()
    _click(at, "Submit claim")
    assert not at.exception
    assert any("Claim CLM-" in h.value for h in at.header)
    assert any("template text" in c.value for c in at.caption)


def test_accident_sample_shows_missing_fir_document():
    at = _app()
    _click(at, "Load accident sample")
    at.checkbox(key="declaration").check().run()
    _click(at, "Submit claim")
    assert any("FIR / MLC copy" in w.value for w in at.warning)


# ------------------------------------------------------------------ API-KEY SAFETY
def _patch_llm(monkeypatch, record):
    class Fake:
        def with_structured_output(self, schema):
            class R:
                def invoke(_, messages):
                    return Communications(officer_summary="Summary.", claimant_letter="placeholder")
            return R()
    def fake_build(api_key, model="x"):
        record.append(api_key)
        return Fake()
    monkeypatch.setattr("securecare.agents.llm.build_llm", fake_build)


def _key_widgets(at):
    return [t for t in at.text_input if t.key and t.key.startswith("openrouter_key_")]


def test_key_is_flushed_after_submit_by_default(monkeypatch):
    used = []
    _patch_llm(monkeypatch, used)
    at = _app()
    _key_widgets(at)[0].set_value(FAKE_KEY).run()
    _click(at, "Load sample")
    at.checkbox(key="declaration").check().run()
    _click(at, "Submit claim")
    assert used == [FAKE_KEY], "the key must actually be used for the run"
    assert _key_widgets(at)[0].value in ("", None), "the key box must be empty afterwards"
    assert FAKE_KEY not in _dump(at), "key must not linger anywhere in the session"


def test_key_can_be_kept_for_the_session_when_opted_in(monkeypatch):
    _patch_llm(monkeypatch, [])
    at = _app()
    at.checkbox(key="keep_key").check().run()
    _key_widgets(at)[0].set_value(FAKE_KEY).run()
    _click(at, "Load sample")
    at.checkbox(key="declaration").check().run()
    _click(at, "Submit claim")
    assert _key_widgets(at)[0].value == FAKE_KEY
    _click(at, "Clear key now")
    assert _key_widgets(at)[0].value in ("", None)


def test_key_never_reaches_environment_or_other_sessions(monkeypatch):
    _patch_llm(monkeypatch, [])
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    alice = _app()
    _key_widgets(alice)[0].set_value(FAKE_KEY).run()
    _click(alice, "Load sample")
    alice.checkbox(key="declaration").check().run()
    _click(alice, "Submit claim")

    bob = _app()                                   # a different visitor of the same running app
    assert _key_widgets(bob)[0].value in ("", None)
    assert FAKE_KEY not in _dump(bob)
    assert "OPENROUTER_API_KEY" not in os.environ
    assert not any(FAKE_KEY in v for v in os.environ.values())


def test_bad_key_shape_skips_ai_and_warns(monkeypatch):
    used = []
    _patch_llm(monkeypatch, used)
    at = _app()
    _key_widgets(at)[0].set_value("hello").run()
    _click(at, "Load sample")
    at.checkbox(key="declaration").check().run()
    _click(at, "Submit claim")
    assert used == [] and any("does not look like" in w.value for w in at.warning)
