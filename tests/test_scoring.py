"""시나리오 채점기 테스트: 행동 매칭, 개수 조건, 금지 행동, 확인 등급."""

from datetime import datetime

from turnaround.evaluation.scenario import Action, Gold, Tier
from turnaround.evaluation.scoring import Produced, actions_from_events, score
from turnaround.events import Event, EventType

T = datetime(2026, 3, 2, 11, 0)


def P(t, case=None, bed=None, **params):
    return Produced(type=t, case=case, bed=bed, params=params)


def G(**kw):
    return Gold(**kw)


def test_must_action_matches_on_given_fields_only():
    g = G(must_actions=[Action(type="release_prematch", case="P1")])
    assert score(g, [P("release_prematch", case="P1", bed="W72-R03-B1")]).passed


def test_missing_must_action_fails_with_reason():
    r = score(G(must_actions=[Action(type="release_prematch", case="P1")]), [])
    assert not r.passed and "release_prematch" in r.failures[0]


def test_params_must_match():
    g = G(must_actions=[Action(type="send_cleaning_request", params={"cleaning_type": "terminal"})])
    assert not score(g, [P("send_cleaning_request", cleaning_type="general")]).passed
    assert score(g, [P("send_cleaning_request", cleaning_type="terminal")]).passed


def test_count_requires_exact_number():
    g = G(must_actions=[Action(type="recompute_ready_time", bed="B", params={"count": 1})])
    assert score(g, [P("recompute_ready_time", bed="B")]).passed
    assert not score(g, [P("recompute_ready_time", bed="B")] * 2).passed


def test_must_not_without_count_fails_on_any_match():
    g = G(must_not=[Action(type="create_human_task", case="P1")])
    assert score(g, [P("create_human_task", case="P2")]).passed
    assert not score(g, [P("create_human_task", case="P1")]).passed


def test_must_not_count_over():
    g = G(must_not=[Action(type="execute_side_effect", params={"count_over": 1})])
    assert score(g, [P("execute_side_effect")]).passed
    assert not score(g, [P("execute_side_effect")] * 2).passed


def test_doing_nothing_fails_when_gold_has_must_actions():
    g = G(must_actions=[Action(type="resume_case", case="D1")], must_not=[Action(type="execute_side_effect", params={"count_over": 1})])
    assert not score(g, []).passed


def test_tier_review_requires_review_routing():
    g = G(tier_by_case={"P1": Tier.REVIEW})
    assert not score(g, []).passed
    assert score(g, [P("reclassify_review", case="P1")]).passed
    assert score(g, [P("route_review", case="P1")]).passed


def test_actions_from_saga_events_with_alias():
    ev = [
        Event(event_id="SYN-A001:saga:prematch_released:B", case_id="SYN-A001", type=EventType.PREMATCH_RELEASED,
              occurred_at=T, observed_at=T, source="saga", payload={"bed_id": "B", "reason": "x"}),
        Event(event_id="SYN-D001:saga:cleaning_request", case_id="SYN-D001", type=EventType.CLEANING_REQUEST_ISSUED,
              occurred_at=T, observed_at=T, source="saga", payload={"bed_id": "B", "cleaning_type": "terminal"}),
        Event(event_id="SYN-D001:patient_departed", case_id="SYN-D001", type=EventType.PATIENT_DEPARTED,
              occurred_at=T, observed_at=T, source="his", payload={"bed_id": "B"}),
    ]
    acts = actions_from_events(ev, alias={"SYN-A001": "P1", "SYN-D001": "D1"})
    assert P("release_prematch", case="P1", bed="B", reason="x") in acts
    assert P("send_cleaning_request", case="D1", bed="B", cleaning_type="terminal") in acts
    assert P("execute_side_effect", case="D1", bed="B") in acts  # 외부 부수효과
    assert len(acts) == 3  # HIS 이벤트는 행동이 아니다
