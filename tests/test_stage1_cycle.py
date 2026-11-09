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
    store.append(ev("A1", "contact", EventType.PATIENT_CONTACTED, at(9, 30), "human"))
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


# ---- 감염관리·환경 검수(1단계_감염관리환경.md) P1~P5: 고치기 전 실패를 확인한 재현 시나리오 ----

def _run_steps(evs, steps, policy=FIELD):
    store, gw = make(evs, policy=policy)
    saga = SagaRunner(store, SagaPolicy.from_policy(policy, H.rooms, H.beds))
    for s in steps:
        saga.step(s) if isinstance(s, datetime) else store.append(s)
    return store


def _vacant(store):
    return [e for e in store.read() if e.type is EventType.BED_MARKED_VACANT]


def test_p1_bed_making_comes_after_cleaning_in_generator():
    for seed in range(10):
        for d in generate(seed=seed, config=GeneratorConfig.realistic()).discharges:
            t = d.actual
            assert t.bed_prep_started_at >= t.cleaning_done_at  # 새 리넨은 표면·침상 청소 뒤 (권고안 부록 4-3-2)


def test_p1_bed_prep_requested_after_cleaning_done_and_prep_before_cleaning_does_not_count():
    store = _run_steps(_discharge_events(), [
        at(10, 5), ev("D1", "pd", EventType.BED_PREP_DONE, at(10, 20), "human", bed_id=BED),
        ev("D1", "cd", EventType.CLEANING_DONE, at(10, 30), "human", bed_id=BED, cleaning_type="general"), at(10, 40)])
    req = [e for e in store.read() if e.type is EventType.BED_PREP_REQUESTED]
    assert req and req[0].occurred_at >= at(10, 30)
    assert not _vacant(store)  # 청소 전에 끝난 침상 정리는 입실 가능 근거가 아니다


def test_p2_late_isolation_requires_bed_prep_after_terminal():
    store = _run_steps(_discharge_events(), [
        at(10, 5), ev("D1", "cd0", EventType.CLEANING_DONE, at(10, 20), "human", bed_id=BED, cleaning_type="general"),
        ev("D1", "pd", EventType.BED_PREP_DONE, at(10, 30), "human", bed_id=BED),
        ev("D1", "iso", EventType.ISOLATION_ORDER_ADDED, at(10, 25), "human", bed_id=BED, precautions=["contact"],
           organism="VRE", isolation_order=True),
        at(10, 45), ev("D1", "cd", EventType.CLEANING_DONE, at(12, 30), "human", bed_id=BED, cleaning_type="terminal"),
        at(12, 40)])
    assert not [v for v in _vacant(store) if v.occurred_at >= at(10, 40)]  # 종말소독 뒤 침상 정리 없이는 안 된다
    reqs = [e for e in store.read() if e.type is EventType.BED_PREP_REQUESTED]
    assert any(e.occurred_at >= at(12, 30) for e in reqs)  # 종말소독 뒤 다시 요청


def test_p3_bed_ready_signal_alone_does_not_make_bed_vacant():
    store = _run_steps(_discharge_events(), [
        ev("D1", "cd", EventType.CLEANING_DONE, at(10, 30), "human", bed_id=BED, cleaning_type="general"),
        ev("D1", "br", EventType.BED_READY, at(10, 35), "human", bed_id=BED), at(10, 40)])
    assert not _vacant(store)
    assert [t for t in store.read() if t.type is EventType.HUMAN_TASK_CREATED and t.payload.get("reason") == "missing_bed_prep"]


def test_p4_general_typed_done_does_not_count_for_isolation_discharge():
    store = _run_steps(_discharge_events(True), [
        at(10, 5), ev("D1", "cd", EventType.CLEANING_DONE, at(11), "human", bed_id=BED, cleaning_type="general"),
        at(11, 5), ev("D1", "pd", EventType.BED_PREP_DONE, at(11, 20), "human", bed_id=BED), at(11, 30)])
    assert not _vacant(store)


def test_p5_s2_checks_bed_prep_order_and_terminal_type():
    from turnaround.evaluation.safety_exec import _s2
    store, gw = make(_discharge_events(True), policy=FIELD)
    evs = store.read() + [
        ev("D1", "saga_req", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga", bed_id=BED, cleaning_type="terminal"),
        ev("D1", "cd", EventType.CLEANING_DONE, at(11), "human", bed_id=BED, cleaning_type="general"),
        ev("D1", "pd", EventType.BED_PREP_DONE, at(10, 30), "human", bed_id=BED),
        ev("D1", "v", EventType.BED_MARKED_VACANT, at(11, 5), "saga", bed_id=BED)]
    r = _s2(gw, evs)
    assert r["violations"] >= 1 and {"vacant_before_disinfection", "bed_prep_before_terminal"} & set(r["codes"])


# ---- 병동 책임간호사·원무 배정 담당 검수(높음) 반영 ----

def test_billing_opens_from_review_not_medication_and_rx_change_reopens_review():
    h = _h()
    seen_reopen = 0
    for d in h.discharges:
        t = d.actual
        if d.discharge_type != "normal":
            continue
        assert (t.billing_done_at - t.notice_given_at) <= timedelta(minutes=20)  # 안내 뒤 바로 수납
        if t.rx_revised_at and t.billing_review_reopened_at:
            seen_reopen += 1
            assert t.billing_review_done_at > t.rx_revised_at
    assert seen_reopen > 0


def _two_discharges_same_bed(today_departed=False):
    """같은 병상: 전날 퇴원은 청소까지 끝났고 오늘 퇴원 환자는 아직(또는 막) 퇴실."""
    old = [ev("D0", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-2), bed_id=BED, day=0,
              discharge_date=at(0, day=-1).date().isoformat(), gender="F", department="일반외과", name="환자D0"),
           ev("D0", "patient_departed", EventType.PATIENT_DEPARTED, at(10, day=-1), bed_id=BED),
           ev("D0", "bed_ready", EventType.BED_READY, at(12, day=-1), "human", bed_id=BED),
           ev("D0", "bed_vacant", EventType.BED_MARKED_VACANT, at(12, day=-1), "saga", bed_id=BED)]
    today = [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=BED, day=1,
                discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D1")]
    if today_departed:
        today.append(ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(13), bed_id=BED))
    return old + today


def test_call_up_uses_the_current_discharge_not_an_old_one_for_the_same_bed():
    evs = _two_discharges_same_bed() + [_adm_at(at(14))]
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store, discharge_id="D1"), at(9))
    store.append(ev("A1", "contact", EventType.PATIENT_CONTACTED, at(9, 30), "human"))
    store.append(ev("A1", "ward_prep_done", EventType.WARD_PREP_DONE, at(10), "human", bed_id=BED))
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(14, 10))
    assert not [e for e in store.read() if e.type is EventType.PATIENT_CALL_UP]  # 오늘 퇴원 환자가 아직 병상에


def test_call_up_requires_patient_contact():
    store, gw = make([_adm_at(at(14))], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    store.append(ev("A1", "ward_prep_done", EventType.WARD_PREP_DONE, at(10), "human", bed_id=BED))
    saga = SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))
    saga.step(at(14, 10))
    assert not [e for e in store.read() if e.type is EventType.PATIENT_CALL_UP]
    store.append(ev("A1", "contact", EventType.PATIENT_CONTACTED, at(14, 12), "human"))
    saga.step(at(14, 20))
    assert [e for e in store.read() if e.type is EventType.PATIENT_CALL_UP]


def test_cancellation_in_notify_mode_sends_withdrawal_to_ward():
    store, gw = make([_adm_at(at(14))], policy=FIELD)
    gw.submit(prop(store=store), at(9))  # 에이전트 확정 + 병동 통보
    store.append(ev("A1", "admission_cancelled", EventType.ADMISSION_CANCELLED, at(10)))
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(10, 5))
    assert [e for e in store.read() if e.type is EventType.WARD_NOTIFIED]


def test_manual_override_in_notify_mode_keeps_follow_ups():
    store, gw = make([_adm_at(at(14))], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    store.append(ev("A1", "contact", EventType.PATIENT_CONTACTED, at(9, 20), "human"))
    d = gw.manual_override("A1", "W72-R04-B3", "bed_manager", "bed_manager", "operational", at(9, 40))
    assert d.status == "applied"
    evs = store.read()
    notes = [e for e in evs if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED]
    assert any(e.payload.get("bed_id") == "W72-R04-B3" for e in notes)  # 새 병상 통보 → 병동 준비·호출로 이어짐
    assert [e for e in evs if e.type is EventType.WARD_NOTIFIED and e.payload.get("bed_id") == BED]  # 옛 병상 철회
    assert [e for e in evs if e.type is EventType.HUMAN_TASK_CREATED and e.payload.get("kind") == "recontact"]


def test_auto_upgrade_to_single_room_needs_fee_consent_review():
    store, gw = make([_adm_at(at(14))], policy=FIELD)
    d = gw.submit(prop(store=store, bed="W72-R01-B1"), at(9))  # 1인실, 환자는 다인실(차액 없는) 희망
    assert d.status == "awaiting_review" and "grade_upgrade" in d.reasons
