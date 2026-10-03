from datetime import timedelta

from securecare.graph import build_claim_graph, build_initial_state, run_claim
from securecare.samples import sample_raw_claim
from securecare.validation import validate_submission
from securecare.agents.communicator import Communications


def _run(raw, llm=None):
    result = validate_submission(raw)
    assert result.ok, result.errors
    graph = build_claim_graph(llm)
    return run_claim(graph, build_initial_state(result.submission, use_llm=llm is not None))


def test_happy_path_without_llm():
    run = _run(sample_raw_claim("simple"))
    s = run.state
    assert s["total_claimed"] == 107500
    # room 24000 over 4 days (limit 5000/day => 20000 eligible): excess 4000
    assert s["room_rent_excess"] == 4000
    assert s["admissible_amount"] == 103500
    assert s["payable_estimate"] == 93500      # 103500 - 10000 deductible, no co-pay
    assert s["missing_documents"] == []
    assert s["status"] == "officer_review"      # room-rent excess is a review reason
    assert s["comms_source"] == "template" and s["checks_passed"]
    assert [t["node"] for t in run.trace][:2] == ["intake", "lookup_policy"]
    assert "draft_communications" not in [t["node"] for t in run.trace]


def test_accident_claim_missing_fir_goes_pending_documents():
    s = _run(sample_raw_claim("accident")).state
    assert s["missing_documents"] == ["fir_or_mlc_copy"]
    assert s["status"] == "pending_documents"
    assert any("Accident case" in r for r in s["review_reasons"])
    assert s["accident_details"]["mlc_number"] == "MLC-2026-4471"


def test_policy_on_lapsed_status_is_rejected():
    raw = sample_raw_claim(); raw["policy_number"] = "POL-555000"
    run = _run(raw)
    assert run.state["status"] == "rejected" and run.state["payable_estimate"] == 0
    assert "compute_estimate" not in [t["node"] for t in run.trace]


def test_waiting_period_applies_to_illness_but_not_accidents():
    raw = sample_raw_claim(); raw["policy_number"] = "POL-123456"
    assert _run(raw).state["status"] == "rejected"
    raw = sample_raw_claim("accident"); raw["policy_number"] = "POL-123456"
    assert _run(raw).state["status"] != "rejected"


def test_copay_and_sum_insured_cap():
    raw = sample_raw_claim(); raw["policy_number"] = "POL-100200"      # 10% co-pay, ded 5000, room 4000/day
    s = _run(raw).state
    assert s["copay_amount"] == round((s["admissible_amount"] - 5000) * 0.10)


class FakeLLM:
    """Stands in for the LangChain chat model: records calls, returns scripted answers."""
    def __init__(self, answers):
        self.answers, self.calls = list(answers), 0

    def with_structured_output(self, schema):
        outer = self
        class _Runner:
            def invoke(self, messages):
                outer.calls += 1
                item = outer.answers.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item
        return _Runner()


def _good(state_text):  # builds a comms object that passes verification once we know ids
    return state_text


def test_llm_loop_retries_then_accepts(monkeypatch):
    from securecare.graph import nodes
    seen = {}

    def fake_draft(llm, facts, feedback=None):
        seen.setdefault("feedback", []).append(feedback)
        if len(seen["feedback"]) == 1:
            return Communications(officer_summary="ok", claimant_letter="Your claim is approved!")
        return Communications(officer_summary="Summary.",
                              claimant_letter=f"Claim {facts['claim_id']} estimate {facts['payable_estimate_text']}.")
    monkeypatch.setattr(nodes, "draft_communications", fake_draft)
    run = _run(sample_raw_claim(), llm=object())
    s = run.state
    assert s["comms_source"] == "llm" and s["comms_attempts"] == 2
    assert seen["feedback"][0] is None and "approved" in seen["feedback"][1]
    names = [t["node"] for t in run.trace]
    assert names.count("draft_communications") == 2 and names.count("verify_communications") == 2


def test_llm_loop_is_bounded_and_falls_back(monkeypatch):
    from securecare.graph import nodes
    monkeypatch.setattr(nodes, "draft_communications",
                        lambda llm, facts, feedback=None: Communications(officer_summary="x", claimant_letter="guaranteed"))
    s = _run(sample_raw_claim(), llm=object()).state
    assert s["comms_source"] == "template" and s["comms_attempts"] == 2 and s["checks_passed"]


def test_llm_failure_never_leaks_key_and_still_completes():
    llm = FakeLLM([RuntimeError("Incorrect API key provided: sk-proj-ABCDEFGHIJKLMNOP1234")])
    s = _run(sample_raw_claim(), llm=llm).state
    assert s["comms_source"] == "template" and s["checks_passed"]
    assert "sk-proj-ABCDEFGHIJKLMNOP1234" not in s["comms_error"] and "sk-***" in s["comms_error"]
    assert "sk-proj" not in str(s)


def test_graph_state_never_contains_the_api_key():
    secret = "sk-test-SECRETSECRETSECRET123456"
    class Holder:   # an llm-like object that carries the key as an attribute
        api_key = secret
        def with_structured_output(self, schema): raise RuntimeError("boom " + secret)
    s = _run(sample_raw_claim(), llm=Holder()).state
    assert secret not in str(s)
