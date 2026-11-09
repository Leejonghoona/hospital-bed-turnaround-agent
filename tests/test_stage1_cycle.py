"""1단계 사이클 완성 (08 계획): 현장형 퇴원 순서, 청소 두 갈래와 입실 가능 기준, 당일 오전 배정, 입실 구간.

모두 현실 기준선 프리셋과 현장 프로필에서만 켠다(시험 구성·legacy는 그대로).
"""

from datetime import datetime, time, timedelta

from test_gateway import H, adm, at, ev, make, prop
from turnaround.events import EventType
from turnaround.events.replay import hospital_to_events
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.models import RoomType

FIELD = load_policy(profile="field_default")


def _adm_at(req: datetime, case="A1"):
    """입원 예정(수속) 시각이 있는 입원 요청. 예약은 전날 15:00."""
    e = adm(case=case, when=datetime.combine(req.date() - timedelta(days=1), time(15)))
    return e.model_copy(update={"payload": {**e.payload, "requested_at": req.isoformat()}})


def _h(seed=3):
    return generate(seed=seed, config=GeneratorConfig.realistic())


def test_field_discharge_sequence_order():
    for d in _h().discharges:
        if d.discharge_type != "normal":
            continue
        t = d.actual
        assert t.order_confirmed_at <= t.billing_review_done_at <= t.notice_given_at <= t.billing_done_at
        assert t.billing_done_at <= t.receipt_checked_at <= t.medication_handed_at <= t.departed_at
        assert t.medication_ready_at <= t.medication_handed_at


def test_test_preset_has_no_field_sequence_fields():
    d = generate(seed=42).discharges[0]
    assert d.actual.billing_review_done_at is None and d.actual.bed_prep_done_at is None


def test_split_cleaning_and_readiness_scope():
    h = _h()
    rooms = {r.id: r for r in h.rooms}
    multi, single = [], []
    for d in h.discharges:
        t = d.actual
        assert t.bed_prep_done_at is not None and t.bed_ready_at == max(t.bed_prep_done_at, t.cleaning_done_at)
        if d.cleaning_type.value == "terminal":
            assert t.bed_prep_started_at >= t.cleaning_done_at  # 종말소독 끝난 뒤 침상 정리 (H34)
        rt = rooms[h.beds[[b.id for b in h.beds].index(d.bed_id)].room_id].room_type
        dur = (t.cleaning_done_at - t.cleaning_started_at).total_seconds() / 60
        if d.cleaning_type.value == "general":
            (multi if rt is RoomType.MULTI else single).append(dur)
    assert multi and single and sum(multi) / len(multi) < sum(single) / len(single)  # 다인실 침상 단위가 더 짧다


def test_replay_emits_field_sequence_and_bed_prep_events():
    h = _h()
    ev_ = hospital_to_events(h, seed=3)
    types = {e.type for e in ev_}
    assert {EventType.BILLING_REVIEW_DONE, EventType.DISCHARGE_NOTICE_GIVEN, EventType.RECEIPT_CHECKED,
            EventType.MEDICATION_HANDED_OVER, EventType.BED_PREP_DONE} <= types


BED = "W72-R04-B2"


def _discharge_events(isolation=False):
    return [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=BED, day=1,
               discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D1",
               isolation_order=isolation, precautions=["contact"] if isolation else [],
               organism="VRE" if isolation else None),
            ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(10), bed_id=BED)]


def test_saga_requests_bed_prep_and_vacancy_needs_both_tracks():
    evs = _discharge_events() + [ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(10, 30), "human", bed_id=BED,
                                    cleaning_type="general")]
    store, gw = make(evs, policy=FIELD)
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    saga.step(at(10, 40))
    t = lambda x: [e for e in store.read() if e.type is x]  # noqa: E731
    assert t(EventType.BED_PREP_REQUESTED)
    assert not t(EventType.BED_MARKED_VACANT)  # 침상 정리가 아직
    store.append(ev("D1", "bed_prep_done", EventType.BED_PREP_DONE, at(10, 45), "human", bed_id=BED))
    saga.step(at(10, 50))
    assert t(EventType.BED_MARKED_VACANT)


def test_isolation_bed_prep_requested_only_after_terminal_cleaning():
    store, gw = make(_discharge_events(isolation=True), policy=FIELD)
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    saga.step(at(10, 40))
    assert not [e for e in store.read() if e.type is EventType.BED_PREP_REQUESTED]
    store.append(ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(12), "human", bed_id=BED,
                    cleaning_type="terminal"))
    saga.step(at(12, 5))
    assert [e for e in store.read() if e.type is EventType.BED_PREP_REQUESTED]


def test_legacy_saga_unchanged_no_bed_prep():
    pol = load_policy()
    store, gw = make(_discharge_events(), policy=pol)
    SagaRunner(store, SagaPolicy.from_policy(pol, H.rooms, H.beds)).step(at(10, 40))
    assert not [e for e in store.read() if e.type is EventType.BED_PREP_REQUESTED]


def test_same_day_morning_assignment_waits_for_the_day():
    from turnaround.agent.tools import PlanningTools
    tomorrow = at(15, day=1)
    store, gw = make([_adm_at(tomorrow)], policy=FIELD)
    tools = PlanningTools(gw, H.wards)
    assert tools.pending_admissions(store.read(), now=at(18)) == []  # 전날 저녁: 아직
    assert tools.pending_admissions(store.read(), now=at(7, 30, day=1)) == []  # 당일 08:00 전
    assert tools.pending_admissions(store.read(), now=at(8, day=1)) == ["A1"]
    legacy = load_policy()
    store2, gw2 = make([_adm_at(tomorrow)], policy=legacy)
    assert PlanningTools(gw2, H.wards).pending_admissions(store2.read(), now=at(18)) == ["A1"]


def test_call_up_when_bed_ready_ward_prepared_and_patient_checked_in():
    evs = _discharge_events() + [
        ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(10, 30), "human", bed_id=BED, cleaning_type="general"),
        ev("D1", "bed_prep_done", EventType.BED_PREP_DONE, at(10, 40), "human", bed_id=BED),
        ev("D1", "bed_ready", EventType.BED_READY, at(10, 41), "human", bed_id=BED),
        _adm_at(at(14))]
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store), at(9))
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    saga.step(at(13))
    t = lambda x: [e for e in store.read() if e.type is x]  # noqa: E731
    assert not t(EventType.PATIENT_CALL_UP)  # 병동 준비 전, 수속 전
    store.append(ev("A1", "ward_prep_done", EventType.WARD_PREP_DONE, at(13, 10), "human", bed_id=BED))
    saga.step(at(13, 30))
    assert not t(EventType.PATIENT_CALL_UP)  # 수속(입원 예정 시각) 전
    saga.step(at(14, 5))
    up = t(EventType.PATIENT_CALL_UP)
    assert len(up) == 1 and up[0].payload["bed_id"] == BED and up[0].payload["to"] == "admission_office"
