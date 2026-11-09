"""M2 A: 퇴원 병목을 미는 에이전트 (로드맵 07, DECISIONS 2026-11-09).

감지는 사가(결정적), 행동 선택은 머리(LLM, 테스트는 FakeLLM), 실행은 게이트웨이.
반응형 재생: 에이전트 행동이 그 뒤 퇴원 시각을 바꾼다. 효과 크기는 설계 가정이라 민감도와 함께 낸다.
"""

from datetime import datetime, time, timedelta

from test_gateway import H, at, ev, make
from turnaround.agent import FakeLLM
from turnaround.agent.brain import ExceptionBrain
from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.reactive import Levers, ReactiveEffects, retime

FIELD = load_policy(profile="field_default")
M = timedelta(minutes=1)


def _discharge(kind="late_order"):
    h = generate(seed=3, config=GeneratorConfig.realistic())
    for d in h.discharges:
        if d.discharge_type != "normal":
            continue
        t = d.actual
        if kind == "late_order" and t.order_confirmed_at.time() > time(9, 40):
            return d
        if kind == "guardian" and t.guardian_arrived_at > max(t.billing_done_at, t.education_done_at) + 60 * M:
            return d
    raise AssertionError("조건에 맞는 합성 퇴원 없음")


def test_retime_without_levers_is_identity():
    d = _discharge()
    assert retime(d, Levers(), ReactiveEffects()) == d.actual


def test_order_nudge_moves_order_and_downstream_earlier_never_later():
    d = _discharge()
    nudge = datetime.combine(d.actual.order_confirmed_at.date(), time(9, 0))
    t2 = retime(d, Levers(order_nudge_at=nudge), ReactiveEffects())
    assert nudge < t2.order_confirmed_at <= d.actual.order_confirmed_at
    for f in ("medication_ready_at", "billing_done_at", "departed_at", "bed_ready_at"):
        assert getattr(t2, f) <= getattr(d.actual, f), f


def test_zero_effect_nudge_changes_nothing():
    d = _discharge()
    nudge = datetime.combine(d.actual.order_confirmed_at.date(), time(9, 0))
    assert retime(d, Levers(order_nudge_at=nudge), ReactiveEffects(order_nudge_gain=0.0)) == d.actual


def test_lounge_vacates_bed_before_guardian_arrives():
    d = _discharge("guardian")
    t = d.actual
    ready = max(t.billing_done_at, t.education_done_at)
    t2 = retime(d, Levers(lounge_at=ready + 5 * M), ReactiveEffects())
    assert t2.departed_at < t.departed_at and t2.departed_at >= ready + 5 * M
    assert t2.guardian_arrived_at == t.guardian_arrived_at  # 보호자는 라운지로 온다
    assert t2.bed_ready_at < t.bed_ready_at


def test_immediate_cleaning_request_shortens_bed_prep():
    d = _discharge()
    t2 = retime(d, Levers(cleaning_request_at=d.actual.departed_at), ReactiveEffects())
    assert t2.departed_at == d.actual.departed_at and t2.bed_ready_at <= d.actual.bed_ready_at


# ---- 사가 감지와 머리 선택 ----

def _dc_events(order_at=None, guardian_eta=None, ready=False):
    base = [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, 30, day=-1), bed_id="W72-R04-B2",
               day=1, discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D1")]
    base.append(ev("D1", "guardian_eta_set", EventType.GUARDIAN_ETA_SET, at(8, 10), "human",
                   eta=(guardian_eta or at(10)).isoformat()))
    if order_at:
        base.append(ev("D1", "order_confirmed", EventType.ORDER_CONFIRMED, order_at))
        base.append(ev("D1", "billing_review_done", EventType.BILLING_REVIEW_DONE, order_at + timedelta(minutes=30)))
    if ready:
        base += [ev("D1", "rx_issued", EventType.DISCHARGE_RX_ISSUED, at(8, 5)),
                 ev("D1", "medication_ready", EventType.MEDICATION_READY, at(9, 40)),
                 ev("D1", "education_done", EventType.EDUCATION_DONE, at(9, 30), "human"),
                 ev("D1", "billing_done", EventType.BILLING_DONE, at(10, 0))]
    return base


def _types(store, t):
    return [e for e in store.read() if e.type is t]


def test_order_late_raises_exception_in_field_and_brain_nudges_with_impact():
    store, gw = make(_dc_events(), policy=FIELD)
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(9, 5))
    raised = [e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == "sequence_stalled"]
    assert len(raised) == 1
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(9, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["remind_owner"]
    task = [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("kind") == "order_nudge"]
    assert task and task[0].payload["assignee"] == "ward" and "next_admission_waiting" in task[0].payload


def test_order_late_is_still_a_plain_task_in_legacy():
    pol = load_policy()
    store, gw = make(_dc_events(), policy=pol)
    SagaRunner(store, SagaPolicy.from_policy(pol, H.rooms, H.beds)).step(at(11, 5))
    assert not _types(store, EventType.EXCEPTION_RAISED)
    assert [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("kind") == "rx_missing"]


def test_guardian_after_ready_offers_lounge_only_when_someone_waits_for_the_bed():
    evs = _dc_events(order_at=at(8, 0), guardian_eta=at(13), ready=True)
    store, gw = make(evs, policy=FIELD)
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(10, 5))
    raised = [e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == "guardian_delay_same_day"]
    assert len(raised) == 1 and raised[0].payload["context"]["next_admission_waiting"] is False
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["wait"]
    assert not _types(store, EventType.LOUNGE_OFFERED)


def test_guardian_after_ready_with_waiting_patient_offers_lounge():
    from turnaround.agent.llm import _fake_exception_action
    out = _fake_exception_action({"exception": {"trigger": "guardian_delay_same_day"},
                                  "case_state": {"next_admission_waiting": True},
                                  "allowed_actions": [{"id": "offer_lounge"}, {"id": "wait"}]})
    assert out["action"] == "offer_lounge"


# ---- 반응형 재생 공급기 ----

def test_reactive_feed_retimes_only_pending_events_after_a_nudge():
    from turnaround.events import EventStore
    from turnaround.sim.reactive import ReactiveReplay
    h = generate(seed=3, config=GeneratorConfig.realistic())
    d = _discharge()
    feed = ReactiveReplay(h, 3, ReactiveEffects())
    store = EventStore(":memory:")
    nudge = datetime.combine(d.actual.order_confirmed_at.date(), time(9, 0))
    feed.deliver(store, nudge)
    before = {e.event_id for e in store.read()}
    store.append(ev(d.id, "x:executed", EventType.ACTION_EXECUTED, nudge, "gateway", action="remind_owner",
                    exception_id=f"E2:{d.id}"))
    feed.react(store, nudge)
    pend = {e.event_id: e for e in feed.pending if e.case_id == d.id}
    assert pend[f"{d.id}:order_confirmed"].occurred_at < d.actual.order_confirmed_at
    assert all(e.observed_at > nudge for e in feed.pending)  # 지난 일은 바꾸지 않는다
    assert not (before & set(pend))
    assert feed.applied and feed.applied[0]["case"] == d.id


def test_wait_schedules_a_new_recheck_each_time_and_does_not_spin():
    evs = _dc_events(order_at=at(8, 0), guardian_eta=at(14), ready=True)
    store, gw = make(evs, policy=FIELD)
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    saga.step(at(10, 5))
    for m in range(10, 60, 5):
        brain.step(at(10, m))
    assert len(_types(store, EventType.ACTION_PROPOSED)) == 1  # 다시 볼 시각 전에는 다시 고르지 않는다
    brain.step(at(12, 15))  # 다시 볼 시각(최대 120분) 뒤
    assert len(_types(store, EventType.ACTION_PROPOSED)) == 2
    assert len(_types(store, EventType.RECHECK_SCHEDULED)) == 2


def test_exception_closes_when_condition_clears():
    evs = _dc_events(order_at=at(8, 0), guardian_eta=at(14), ready=True)
    store, gw = make(evs, policy=FIELD)
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    saga.step(at(10, 5))
    store.append(ev("D1", "guardian_arrived", EventType.GUARDIAN_ARRIVED, at(11), "human"))
    saga.step(at(11, 5))
    res = [e for e in _types(store, EventType.EXCEPTION_RESOLVED) if e.payload["exception_id"] == "E3:D1"]
    assert res and res[0].payload["how"] == "condition_cleared"
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(11, 10))
    assert not _types(store, EventType.ACTION_PROPOSED)


# ---- 기다림에는 늘 함께 할 행동 (현장 조사 예외3건_현장대응.md, 사용자 원칙 "가만히 기다리지 않는다") ----

def test_every_wait_action_declares_companions():
    import pytest
    pol = load_policy(profile="field_default")
    for trig, rule in pol.exception_actions.items():
        for a in rule.allowed:
            if a.startswith("wait"):
                assert rule.companions.get(a), (trig, a)
    bad = pol.model_dump()
    bad["exception_actions"]["late_isolation"]["companions"] = {}
    from turnaround.gateway.policy import Policy
    with pytest.raises(ValueError, match="함께 할 행동"):
        Policy.model_validate(bad)


def test_wait_for_ipc_also_alerts_ward_and_reminds_ipc():
    from test_brain import _Script, _setup
    store, gw = _setup()
    ExceptionBrain(gw, _Script({"action": "wait_for_ipc", "params": {}, "rationale": "x"}), FIELD).step(at(10, 10))
    tasks = {(t.payload.get("kind"), t.payload.get("assignee")) for t in _types(store, EventType.HUMAN_TASK_CREATED)}
    assert ("provisional_precautions_check", "ward") in tasks and ("ipc_reminder", "ipc") in tasks
    assert _types(store, EventType.RECHECK_SCHEDULED)


def test_wait_asks_ward_to_reconfirm_eta():
    evs = _dc_events(order_at=at(8, 0), guardian_eta=at(13), ready=True)
    store, gw = make(evs, policy=FIELD)
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(10, 5))
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    kinds = {t.payload.get("kind") for t in _types(store, EventType.HUMAN_TASK_CREATED)}
    assert "guardian_eta_reconfirm" in kinds
