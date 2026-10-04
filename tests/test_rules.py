"""S-2 / S-9 / S-12: business-rule hardening in the graph nodes, claim ids and the final safety net."""
import re

import pytest

from securecare.agents.communicator import Communications
from securecare.config import HIGH_VALUE_THRESHOLD, OTHER_CATEGORY_REVIEW_PERCENT
from securecare.graph import build_claim_graph, build_initial_state, nodes, run_claim
from securecare.samples import sample_raw_claim
from securecare.services.policy_store import get_policy
from securecare.validation import validate_submission


def _run(raw, llm=None):
    result = validate_submission(raw)
    assert result.ok, result.errors
    return run_claim(build_claim_graph(llm), build_initial_state(result.submission, use_llm=llm is not None))


def _estimate(items, policy_number="POL-100200", days=4):
    policy = get_policy(policy_number)
    return nodes.compute_estimate({"policy": policy, "bill_items": items, "stay_days": days})


def _items(*pairs):
    return [{"category": c, "bill_number": f"B-{i}", "bill_date": "2026-01-01", "amount": a}
            for i, (c, a) in enumerate(pairs, start=1)]


# ---------------------------------------------------------------- S-2: the accident waiver is not silent
def test_accident_inside_waiting_period_is_not_rejected_but_routed_to_an_officer():
    raw = sample_raw_claim("accident")
    raw["policy_number"] = "POL-123456"                 # policy started 25 days ago: still in the 30-day window
    raw["documents"].append("fir_or_mlc_copy")           # complete documents, so only the waiver matters
    s = _run(raw).state
    assert s["status"] == "officer_review"
    assert "Accident claim inside the waiting period: verify MLC/FIR before any payment" in s["review_reasons"]
    assert any("accident waiver" in line and "waiting period" in line for line in s["audit_log"])


def test_accident_outside_the_waiting_period_has_no_waiver_reason():
    raw = sample_raw_claim("accident")
    s = _run(raw).state
    assert not any("waiting period" in r for r in s["review_reasons"])


def test_planned_admission_for_an_accident_is_flagged():
    raw = sample_raw_claim("accident")
    raw["hospitalization"]["admission_type"] = "planned"
    raw["documents"].append("fir_or_mlc_copy")
    s = _run(raw).state
    assert s["status"] == "officer_review"
    assert any("planned admission" in r for r in s["review_reasons"])
    assert any("planned admission" in line for line in s["audit_log"])


def test_illness_inside_the_waiting_period_is_still_rejected():
    raw = sample_raw_claim()
    raw["policy_number"] = "POL-123456"
    assert _run(raw).state["status"] == "rejected"


# ---------------------------------------------------------------- S-2: room rent hidden as "other"
def test_large_other_share_gets_a_review_reason():
    big = _estimate(_items(("room", 30000), ("other", 40000)))
    assert any("'other' category" in r for r in big["review_reasons"])
    assert any("57%" in r for r in big["review_reasons"])


def test_other_share_at_the_threshold_is_not_flagged():
    edge = _estimate(_items(("room", 100 - OTHER_CATEGORY_REVIEW_PERCENT), ("other", OTHER_CATEGORY_REVIEW_PERCENT)))
    assert not any("'other'" in r for r in edge.get("review_reasons", []))
    over = _estimate(_items(("room", 100 - OTHER_CATEGORY_REVIEW_PERCENT - 1), ("other", OTHER_CATEGORY_REVIEW_PERCENT + 1)))
    assert any("'other'" in r for r in over["review_reasons"])


def test_other_category_review_routes_the_claim_to_an_officer():
    raw = sample_raw_claim()
    raw["bill_items"] = [
        {"row_id": 1, "category": "other", "bill_number": "OT-1", "bill_date": raw["hospitalization"]["discharge_date"], "amount": 20000},
        {"row_id": 2, "category": "pharmacy", "bill_number": "PH-1", "bill_date": raw["hospitalization"]["discharge_date"], "amount": 10000},
    ]
    assert _run(raw).state["status"] == "officer_review"


# ---------------------------------------------------------------- S-2: high value is '>='
def _decide(total):
    state = {"hospitalization": {"is_accident": False}, "total_claimed": total, "stay_days": 3,
             "extra_fields": {"is_network_hospital": "yes"}, "missing_documents": []}
    return nodes.decide_status(state)


def test_claim_exactly_at_the_high_value_threshold_goes_to_an_officer():
    assert _decide(HIGH_VALUE_THRESHOLD)["status"] == "officer_review"
    assert _decide(HIGH_VALUE_THRESHOLD - 1)["status"] == "ready_for_review"
    assert any("High-value" in r for r in _decide(HIGH_VALUE_THRESHOLD)["review_reasons"])


# ---------------------------------------------------------------- S-9: integer half-up co-pay
@pytest.mark.parametrize("claimed,copay", [(105, 11), (115, 12), (125, 13), (104, 10), (106, 11)])
def test_copay_rounds_half_up(claimed, copay):
    policy = {"room_rent_limit_per_day": 10**6, "deductible": 0, "copay_percent": 10, "sum_insured": 10**6}
    update = nodes.compute_estimate({"policy": policy, "bill_items": _items(("pharmacy", claimed)), "stay_days": 1})
    assert update["copay_amount"] == copay                    # 10.5 -> 11, 11.5 -> 12, 12.5 -> 13 (not banker's 12)
    assert update["payable_estimate"] == claimed - copay


def test_copay_with_zero_percent_and_after_deductible_base():
    policy = get_policy("POL-100200")                          # 10% co-pay, deductible 5000
    update = nodes.compute_estimate({"policy": policy, "bill_items": _items(("pharmacy", 5105)), "stay_days": 1})
    assert update["copay_amount"] == 11                        # (5105 - 5000) * 10% = 10.5 -> 11
    assert _estimate(_items(("pharmacy", 100)), "POL-458921")["copay_amount"] == 0


# ---------------------------------------------------------------- S-13: stay + sum insured edge cases
def test_same_day_stay_counts_as_one_day_for_the_room_limit():
    raw = sample_raw_claim()
    day = raw["hospitalization"]["discharge_date"]
    raw["hospitalization"]["admission_date"] = day
    raw["bill_items"][0].update(bill_date=day, amount=24000)
    raw["bill_items"][1]["bill_date"] = day
    raw["bill_items"][2]["bill_date"] = day
    s = _run(raw).state
    assert s["stay_days"] == 1
    assert s["room_rent_excess"] == 24000 - 5000               # one day x 5000/day limit


def test_payable_is_capped_at_the_sum_insured_and_exact_cap_is_allowed():
    policy = get_policy("POL-100200")                          # sum insured 300000, 10% co-pay, deductible 5000
    huge = nodes.compute_estimate({"policy": policy, "bill_items": _items(("pharmacy", 1_000_000)), "stay_days": 1})
    assert huge["payable_estimate"] == policy["sum_insured"]
    # find the claim whose payable equals the sum insured exactly: payable = x - 5000 - 10% => x = 5000 + 300000/0.9
    exact = nodes.compute_estimate({"policy": {**policy, "sum_insured": 895_500},
                                    "bill_items": _items(("pharmacy", 1_000_000)), "stay_days": 1})
    assert exact["payable_estimate"] == 895_500
    just_under = nodes.compute_estimate({"policy": {**policy, "sum_insured": 895_501},
                                         "bill_items": _items(("pharmacy", 1_000_000)), "stay_days": 1})
    assert just_under["payable_estimate"] == 895_500


def test_deductible_larger_than_the_claim_gives_zero_not_negative():
    update = nodes.compute_estimate({"policy": get_policy("POL-100200"), "bill_items": _items(("pharmacy", 1000)),
                                     "stay_days": 1})
    assert update["payable_estimate"] == 0 and update["deductible_applied"] == 1000


# ---------------------------------------------------------------- S-12: claim ids
def test_claim_ids_use_secrets_with_the_year_prefix():
    ids = {_run(sample_raw_claim()).state["claim_id"] for _ in range(25)}
    assert len(ids) == 25
    for claim_id in ids:
        assert re.fullmatch(r"CLM-\d{4}-[0-9A-F]{10}", claim_id)
    assert not hasattr(nodes, "random") and hasattr(nodes, "secrets")


def test_claim_id_comes_from_the_secrets_module(monkeypatch):
    monkeypatch.setattr(nodes.secrets, "token_hex", lambda n: "0123456789abcdef"[:2 * n])
    assert nodes.intake({"hospitalization": {"admission_date": "2026-01-01", "discharge_date": "2026-01-03"}}
                        )["claim_id"].endswith("-0123456789".upper())


# ---------------------------------------------------------------- S-12: final_checks has teeth
def _good_state():
    s = _run(sample_raw_claim()).state
    return {k: v for k, v in s.items() if k != "audit_log"}


def test_final_checks_passes_a_consistent_run():
    assert nodes.final_checks(_good_state())["checks_passed"] is True


@pytest.mark.parametrize("mutation,expected", [
    ({"payable_estimate": 10**9, "total_claimed": 10**9 + 1}, "exceeds the sum insured"),
    ({"payable_estimate": 10**6}, "exceeds total claimed"),
    ({"deductible_applied": -5}, "negative"),
    ({"status": "pending_documents"}, "no document is missing"),
    ({"missing_documents": ["final_bill"]}, "documents are missing"),
    ({"status": "rejected"}, "rejected claim has a payable amount"),
    ({"status": "bogus"}, "not a known value"),
    ({"claim_id": ""}, "claim id is missing"),
    ({"claimant_letter": ""}, "communications missing"),
    ({"claimant_letter": "Dear claimant, your claim was received."}, "claim id"),
    ({"officer_summary": "Fine."}, "claim id"),
    ({"claimant_letter": "Claim CLM-X: sanctioned"}, "sanction"),
])
def test_final_checks_failure_branch(mutation, expected):
    state = {**_good_state(), **mutation}
    update = nodes.final_checks(state)
    assert update["checks_passed"] is False
    assert any(expected in f for f in update["check_failures"]), update["check_failures"]
    assert update["status"] == "officer_review"
    assert all(r.startswith("Automated check failed: ") for r in update["review_reasons"])
    assert "FAILED" in update["audit_log"][0]


def test_final_checks_failure_reaches_the_graph_result(monkeypatch):
    monkeypatch.setattr(nodes, "template_communications",
                        lambda state: Communications(officer_summary="No id here.", claimant_letter="Nor here."))
    s = _run(sample_raw_claim()).state
    assert s["checks_passed"] is False and s["status"] == "officer_review"
    assert any("claim id" in f for f in s["check_failures"])
