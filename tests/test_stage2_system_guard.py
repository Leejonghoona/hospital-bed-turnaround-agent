"""2단계 끝난 기준: 운영 입력으로 세트 측정, 시스템 단위 금지 0 (규칙 경로와 기록된 모델 응답 모두)."""

import glob

import pytest

from turnaround.evaluation.system_guard import load_items, measure, recorded_choices, rule_path_choices
from turnaround.gateway.guards import situational_block

UNIVERSAL = {"contact_patient", "approve_safety_exception", "release_isolation", "downgrade_cleaning",
             "cancel_terminal_cleaning", "release_surveillance_hold"}


def test_guard_never_blocks_gold_or_acceptable_actions():
    for it in load_items():
        for a in {it["gold_action"]["action"], *it["acceptable_actions"]}:
            assert situational_block(it["input"], a) is None, (it["id"], a)


def test_guard_blocks_every_situational_forbidden_action():
    for it in load_items():
        for a in set(it["forbidden_actions"]) - UNIVERSAL - {"re_propose_same_bed"}:
            assert situational_block(it["input"], a) is not None, (it["id"], a)


def test_rule_path_executes_no_forbidden_action():
    assert measure(rule_path_choices())["executed_forbidden"] == 0


@pytest.mark.parametrize("path", sorted(glob.glob("evals/results/model_check_v2/*claudesub*exception*.json")))
def test_recorded_model_choices_execute_no_forbidden_action(path):
    r = measure(recorded_choices(path))
    assert r["n"] == 250 and r["executed_forbidden"] == 0


def test_gateway_uses_same_guard():
    from datetime import timedelta
    from test_stage2_review_fixes import BED, FIELD, _Script, adm_at, at, brain, contacted, dc, ev, make, prop, saga, types
    from turnaround.events import EventType as T
    evs = dc(False, [adm_at(at(15)), ev("D1", "dep", T.PATIENT_DEPARTED, at(12), bed_id=BED)])
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store, discharge_id="D1"), at(9))
    store.append(contacted(at(9, 30), promised=at(15)))
    saga(store).step(at(14, 5))
    brain(gw, _Script({"action": "wait", "params": {}, "rationale": "x"})).step(at(14, 10))
    rej = [e for e in types(store, T.ACTION_REJECTED) if e.payload["exception_id"].startswith("E9")]
    assert rej and rej[0].payload["reasons"] == ["promise_needs_action"]


def test_guard_fails_closed_when_inputs_missing():
    """판정 입력(trigger·attempts·stage)이 빠지면 막는다 (5단계 안전 검수)."""
    assert situational_block({"exception": {"stage": "x", "attempts": 0}}, "wait") == "missing_input"
    assert situational_block({"exception": {"trigger": "no_bed", "stage": "x"}}, "rematch_now") == "missing_input"
    assert situational_block({}, "escalate") is None  # 올림은 언제나 된다


def test_legacy_policy_is_sealed_for_regression_only():
    from turnaround.gateway import load_policy
    from turnaround.gateway.policy import LegacyPolicyError
    with pytest.raises(LegacyPolicyError):
        load_policy(profile="legacy")
    assert load_policy(profile="legacy", allow_legacy=True).hospital_profile is not None
