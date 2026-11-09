"""3단계: 사례 사가 상태 머신 테스트.

설계 요구: 환자-병상 쌍마다 상태, 타이머, 보상 단계. 프로세스를 강제 종료해도 이어서 실행.
평가표: D1(재개 후 같은 최종 상태), D2(부수효과 중복 0건), D3(보상 잔여 0건, 연락 후 취소 담당 검토 전환), O4(미등록 감지).
"""

from datetime import datetime, timedelta

import pytest

from turnaround.events import Event, EventStore, EventType, NoiseConfig, hospital_to_events
from turnaround.saga import DischargeStage, MatchStage, SagaRunner, fold
from turnaround.sim import generate

DAY0 = datetime(2026, 3, 2)


def at(day, hh, mm=0):
    return DAY0 + timedelta(days=day, hours=hh, minutes=mm)


def e(case, key, etype, when, source="his", obs=None, **payload):
    return Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=when,
                 observed_at=obs or when, source=source, payload=payload)


def decided(case="D1", bed="W72-R03-B1", iso=False, day=0):
    return e(case, "discharge_decided", EventType.DISCHARGE_DECIDED, at(day - 1, 17), bed_id=bed, day=day,
             patient_id=f"SYN-P-{case}", name="가나다", gender="M", age=60, department="일반외과",
             isolation_order=iso, isolation_type="contact" if iso else None)


def registered(case="D1", bed="W72-R03-B1", day=0):
    return e(case, "discharge_registered", EventType.DISCHARGE_REGISTERED, at(day - 1, 17, 30), bed_id=bed)


def departed(case="D1", bed="W72-R03-B1", when=None, obs=None):
    return e(case, "patient_departed", EventType.PATIENT_DEPARTED, when or at(0, 11), obs=obs, bed_id=bed)


def prematch(adm="P1", disc="D1", bed="W72-R03-B1", when=None):
    return e(adm, f"prematch_created:{bed}", EventType.PREMATCH_CREATED, when or at(-1, 17, 40), "agent",
             bed_id=bed, discharge_id=disc)


def contacted(adm="P1", when=None):
    return e(adm, "patient_contacted", EventType.PATIENT_CONTACTED, when or at(0, 9), "human")


def run(events, until, step=timedelta(minutes=5), start=None, store=None):
    store = store or EventStore(":memory:")
    store.extend(events)
    runner = SagaRunner(store)
    t = start or min(x.observed_at for x in events)
    while t <= until:
        runner.step(t)
        t += step
    return store, runner


def saga_events(store, etype=None):
    return [x for x in store.read() if x.source == "saga" and (etype is None or x.type is etype)]


# ---------- 상태 접기 ----------

def test_stage_follows_observed_facts():
    s = fold([decided(), registered()])
    assert s.discharges["D1"].stage is DischargeStage.REGISTERED
    s = fold([decided(), registered(), departed()])
    assert s.discharges["D1"].stage is DischargeStage.DEPARTED


def test_stage_is_independent_of_arrival_order():
    a = [decided(), registered(), departed(),
         e("D1", "cleaning_started", EventType.CLEANING_STARTED, at(0, 11, 30), "human", bed_id="W72-R03-B1")]
    assert fold(a).discharges["D1"].stage == fold(list(reversed(a))).discharges["D1"].stage


def test_cancelled_overrides_stage():
    s = fold([decided(), registered(), e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 8))])
    assert s.discharges["D1"].stage is DischargeStage.CANCELLED


def test_match_stage():
    s = fold([decided(), prematch()])
    assert s.matches["P1"].stage is MatchStage.PREMATCHED
    s = fold([decided(), prematch(), contacted()])
    assert s.matches["P1"].stage is MatchStage.CONTACTED


# ---------- 명령: 퇴실 즉시 청소 요청 (AS-01) ----------

def test_cleaning_request_issued_at_departure_observation():
    store, _ = run([decided(), registered(), departed(when=at(0, 11), obs=at(0, 11, 2))], until=at(0, 11, 10))
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert len(req) == 1
    assert req[0].payload == {"bed_id": "W72-R03-B1", "cleaning_type": "general"}
    assert req[0].occurred_at == at(0, 11, 2)  # 퇴실을 안 시각에 바로


def test_isolation_discharge_forces_terminal_cleaning():
    store, _ = run([decided(iso=True, bed="W72-R01-B1"), registered(bed="W72-R01-B1"),
                    departed(bed="W72-R01-B1")], until=at(0, 11, 10))
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert req[0].payload["cleaning_type"] == "terminal"


def test_no_command_before_fact_is_observed():
    store, _ = run([decided(), registered(), departed(when=at(0, 11), obs=at(0, 12))], until=at(0, 11, 55),
                   start=at(-1, 17))
    assert saga_events(store, EventType.CLEANING_REQUEST_ISSUED) == []


# ---------- 타이머 ----------

def test_unregistered_discharge_gets_check_request_at_19():
    store, _ = run([decided()], until=at(-1, 19, 10), start=at(-1, 17))
    req = saga_events(store, EventType.REGISTRATION_CHECK_REQUESTED)
    assert len(req) == 1 and req[0].occurred_at == at(-1, 19)


def test_registered_discharge_gets_schedule_item_not_check():
    store, _ = run([decided(), registered()], until=at(-1, 19, 10), start=at(-1, 17))
    assert saga_events(store, EventType.REGISTRATION_CHECK_REQUESTED) == []
    assert len(saga_events(store, EventType.SCHEDULE_ITEM_CREATED)) == 1


def test_medication_delay_over_two_hours_triggers_recompute():
    order = e("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9, 30))
    store, _ = run([decided(), registered(), order], until=at(0, 11, 40), start=at(0, 9))
    rec = saga_events(store, EventType.READY_TIME_RECOMPUTED)
    assert [r.payload["reason"] for r in rec] == ["medication_delay"]
    assert rec[0].occurred_at == at(0, 11, 30)


def test_medication_on_time_triggers_nothing():
    evs = [decided(), registered(), e("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9, 30)),
           e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 10, 30))]
    store, _ = run(evs, until=at(0, 12), start=at(0, 9))
    assert saga_events(store, EventType.READY_TIME_RECOMPUTED) == []


def test_guardian_eta_update_triggers_recompute_once_per_update():
    upd = e("D1", "guardian_eta_updated", EventType.GUARDIAN_ETA_UPDATED, at(0, 10, 5), "human", eta="2026-03-02T13:00:00")
    store, _ = run([decided(), registered(), upd], until=at(0, 11), start=at(0, 10))
    assert [r.payload["reason"] for r in saga_events(store, EventType.READY_TIME_RECOMPUTED)] == ["guardian_eta"]


def test_cleaning_overdue_triggers_recompute():
    store, _ = run([decided(), registered(), departed(when=at(0, 11))], until=at(0, 13, 10), start=at(0, 10, 55))
    reasons = [r.payload["reason"] for r in saga_events(store, EventType.READY_TIME_RECOMPUTED)]
    assert reasons == ["cleaning_overdue"]


# ---------- 빈 병상 전환과 종말소독 (S2) ----------

def _turnover(iso, with_done=True):
    bed = "W72-R01-B1"
    evs = [decided(iso=iso, bed=bed), registered(bed=bed), departed(bed=bed, when=at(0, 11))]
    if with_done:
        evs.append(e("D1", "cleaning_done", EventType.CLEANING_DONE, at(0, 12, 30), "human", bed_id=bed,
                     cleaning_type="terminal" if iso else "general"))
    evs.append(e("D1", "bed_ready", EventType.BED_READY, at(0, 12, 40), "human", bed_id=bed))
    return evs


def test_bed_marked_vacant_after_cleaning_done():
    store, _ = run(_turnover(iso=True), until=at(0, 13), start=at(0, 10, 55))
    vac = saga_events(store, EventType.BED_MARKED_VACANT)
    assert len(vac) == 1 and vac[0].occurred_at >= at(0, 12, 30)


def test_isolation_bed_without_cleaning_done_is_not_vacated():
    store, _ = run(_turnover(iso=True, with_done=False), until=at(0, 13), start=at(0, 10, 55))
    assert saga_events(store, EventType.BED_MARKED_VACANT) == []
    tasks = saga_events(store, EventType.HUMAN_TASK_CREATED)
    assert [t.payload["reason"] for t in tasks] == ["missing_cleaning_done"]


def test_general_bed_ready_without_done_signal_is_vacated():
    store, _ = run(_turnover(iso=False, with_done=False), until=at(0, 13), start=at(0, 10, 55))
    assert len(saga_events(store, EventType.BED_MARKED_VACANT)) == 1


# ---------- 보상 단계 (D3) ----------

def test_cancel_before_contact_releases_schedule_and_prematch():
    evs = [decided(), registered(), prematch(),
           e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    assert len(saga_events(store, EventType.SCHEDULE_ITEM_RELEASED)) == 1
    rel = saga_events(store, EventType.PREMATCH_RELEASED)
    assert len(rel) == 1 and rel[0].case_id == "P1"
    assert len(saga_events(store, EventType.REMATCH_REQUESTED)) == 1
    assert saga_events(store, EventType.HUMAN_TASK_CREATED) == []
    s = fold(store.read())
    assert s.matches["P1"].stage is MatchStage.RELEASED


def test_cancel_after_contact_goes_to_review_with_human_task():
    evs = [decided(), registered(), prematch(), contacted(),
           e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    assert saga_events(store, EventType.PREMATCH_RELEASED) == []
    assert [x.case_id for x in saga_events(store, EventType.CASE_RECLASSIFIED_REVIEW)] == ["P1"]
    assert [x.payload["reason"] for x in saga_events(store, EventType.HUMAN_TASK_CREATED)] == ["recontact"]
    assert fold(store.read()).matches["P1"].stage is MatchStage.REVIEW


def test_admission_cancel_releases_prematch_and_requests_rematch_for_bed():
    evs = [decided(), registered(), prematch(),
           e("P1", "admission_cancelled", EventType.ADMISSION_CANCELLED, at(0, 8, 30))]
    store, _ = run(evs, until=at(0, 8, 40), start=at(-1, 17))
    assert [x.case_id for x in saga_events(store, EventType.PREMATCH_RELEASED)] == ["P1"]
    rr = saga_events(store, EventType.REMATCH_REQUESTED)
    assert len(rr) == 1 and rr[0].payload["bed_id"] == "W72-R03-B1"


# ---------- 재시작 후 재개 (D1, D2) ----------

def _signature(store):
    return sorted((x.event_id, x.type.value, tuple(sorted(x.payload.items())))
                  for x in store.read() if x.source == "saga" and x.type is not EventType.CASE_RESUMED)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_kill_and_resume_matches_uninterrupted_run(tmp_path, seed):
    h = generate(seed=seed)
    events = hospital_to_events(h, seed=seed)
    start, end = at(-1, 16), at(0, 18)

    ref, _ = run(events, until=end, start=start)

    path = tmp_path / "events.sqlite"
    s1 = EventStore(path)
    s1.extend(events)
    r1 = SagaRunner(s1)
    t, kill_at = start, at(0, 10, 40)  # 설계 PoC: 10:40 강제 종료
    while t <= kill_at:
        r1.step(t)
        t += timedelta(minutes=5)
    del r1, s1  # 강제 종료 흉내: close 없이 버림

    s2 = EventStore(path)
    r2 = SagaRunner(s2)
    resumed = r2.resume(t)
    assert resumed  # 진행 중이던 사례가 있다
    while t <= end:
        r2.step(t)
        t += timedelta(minutes=5)
    assert _signature(s2) == _signature(ref)
    s2.close()


def test_repeated_steps_do_not_duplicate_side_effects():
    evs = [decided(), registered(), departed()]
    store, runner = run(evs, until=at(0, 11, 30), start=at(-1, 17))
    n = len(saga_events(store))
    for _ in range(5):
        runner.step(at(0, 11, 30))
    assert len(saga_events(store)) == n


def test_noisy_stream_produces_same_saga_commands_as_clean_for_unaffected_cases():
    h = generate(seed=4)
    clean = hospital_to_events(h, seed=4)
    noisy = hospital_to_events(h, seed=4, noise=NoiseConfig(duplicates=10))
    a, _ = run(clean, until=at(4, 20), start=at(-1, 16), step=timedelta(minutes=15))
    b, _ = run(noisy, until=at(4, 20), start=at(-1, 16), step=timedelta(minutes=15))
    assert _signature(a) == _signature(b)  # 중복 도착은 결과를 바꾸지 않는다


# ---------- 미등록 감지 재현율 (O4) ----------

@pytest.mark.parametrize("seed", range(0, 10))
def test_every_unregistered_discharge_is_detected(seed):
    h = generate(seed=seed)
    evs = hospital_to_events(h, seed=seed)
    store, _ = run(evs, until=at(4, 0), start=at(-1, 16), step=timedelta(minutes=15))
    flagged = {x.case_id for x in saga_events(store, EventType.REGISTRATION_CHECK_REQUESTED)}
    expected = {d.id for d in h.discharges if d.registered_at is None}
    # 사가는 "인지한 시각"으로 판정한다. 18:58 등록도 3분 늦게 인지되면 19시 확인 대상이다
    late_registered = {x.case_id for x in evs if x.type is EventType.DISCHARGE_REGISTERED and x.observed_at.hour >= 19}
    assert expected <= flagged
    assert flagged - expected <= late_registered  # 오경보는 19시 이후 늦게 등록된 건만


# ---------- 중단 중 만료된 타이머 (C01 실행기가 찾은 버그) ----------

@pytest.mark.parametrize("fact,due_evs,check", [
    # 퇴원약: 09:30 오더 → 기한 11:30. 중단(11:00~12:00) 중 11:45에 퇴원약 준비
    ("medication", [e("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9, 30)),
                    e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 11, 45))],
     ("recompute:medication_delay", EventType.READY_TIME_RECOMPUTED)),
    # 청소 지연: 09:00 퇴실 → 기한 11:00. 중단(10:30~12:00) 중 11:20에 청소 완료
    ("cleaning", [departed(when=at(0, 9)),
                  e("D1", "cleaning_done", EventType.CLEANING_DONE, at(0, 11, 20), "human", bed_id="W72-R03-B1",
                    cleaning_type="general")],
     ("recompute:cleaning_overdue", EventType.READY_TIME_RECOMPUTED)),
])
def test_timer_that_expired_during_downtime_still_fires_on_resume(fact, due_evs, check):
    evs = [decided(), registered()] + due_evs
    store = EventStore(":memory:")
    store.extend(evs)
    r = SagaRunner(store)
    down = at(0, 11) if fact == "medication" else at(0, 10, 30)
    t = at(-1, 17)
    while t < down:
        r.step(t)
        t += timedelta(minutes=5)
    SagaRunner(store).resume(at(0, 12))
    ids = {x.event_id for x in saga_events(store, check[1])}
    assert f"D1:saga:{check[0]}" in ids


def test_late_registration_after_19_still_gets_check_on_resume():
    evs = [decided(), e("D1", "discharge_registered", EventType.DISCHARGE_REGISTERED, at(-1, 19, 20), bed_id="W72-R03-B1")]
    store = EventStore(":memory:")
    store.extend(evs)
    SagaRunner(store).resume(at(-1, 20))  # 18:00~20:00 중단
    assert len(saga_events(store, EventType.REGISTRATION_CHECK_REQUESTED)) == 1


# ---------- 병동 수용 회신 철회 통지 (사용자 결정: notify_ward 채택) ----------

def ward_accepted(adm="P1", bed="W72-R03-B1"):
    return e(adm, f"ward_accepted:{bed}", EventType.WARD_ACCEPTED, at(-1, 18), "human", bed_id=bed)


def test_release_after_ward_acceptance_notifies_ward():
    evs = [decided(), registered(), prematch(), ward_accepted(),
           e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    n = saga_events(store, EventType.WARD_NOTIFIED)
    assert len(n) == 1 and n[0].case_id == "P1" and n[0].payload["bed_id"] == "W72-R03-B1"


def test_release_without_ward_acceptance_does_not_notify():
    evs = [decided(), registered(), prematch(), e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    assert saga_events(store, EventType.WARD_NOTIFIED) == []


def test_admission_cancel_after_ward_acceptance_notifies_ward():
    evs = [decided(), registered(), prematch(), ward_accepted(),
           e("P1", "admission_cancelled", EventType.ADMISSION_CANCELLED, at(0, 8, 30))]
    store, _ = run(evs, until=at(0, 8, 40), start=at(-1, 17))
    assert len(saga_events(store, EventType.WARD_NOTIFIED)) == 1


# ---------- LP-20 퇴실 청소 요청 속성 ----------

def _decided_with(organism, precautions, bed="W72-R01-B1"):
    ev = decided(iso=True, bed=bed)
    return ev.model_copy(update={"payload": {**ev.payload, "organism": organism, "precautions": precautions}})


@pytest.mark.parametrize("organism,precautions,extra", [
    ("INFLUENZA", ["droplet"], {"aerosol_wait_min": 104}),  # 병상 정보 없으면 일반실 4회
    ("MEASLES", ["airborne"], {"aerosol_wait_min": 104}),
    ("CDIFF", ["contact"], {"sporicidal": True}),
    ("MRSA", ["contact"], {}),
])
def test_cleaning_request_carries_precaution_requirements(organism, precautions, extra):
    store, _ = run([_decided_with(organism, precautions), registered(bed="W72-R01-B1"),
                    departed(bed="W72-R01-B1")], until=at(0, 11, 10))
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert req[0].payload == {"bed_id": "W72-R01-B1", "cleaning_type": "terminal", **extra}


def test_aerosol_wait_uses_room_ach_from_layout():
    from turnaround.saga import SagaPolicy
    from turnaround.sim.generator import generate
    h = generate(seed=0)
    p = SagaPolicy.for_layout(h.rooms, h.beds)
    assert p.aerosol_wait("W71-R01-B1") == 35 and p.aerosol_wait("W72-R01-B1") == 104
    assert SagaPolicy(aerosol_wait_override_min=60).aerosol_wait("W71-R01-B1") == 60


# ---------- 4단계 보강: 지연 사유 기록, 수납 대기 공유 ----------

def test_guardian_eta_update_records_delay_reason():
    upd = e("D1", "guardian_eta_updated", EventType.GUARDIAN_ETA_UPDATED, at(0, 10, 5), "human", eta="2026-03-02T13:00:00")
    store, _ = run([decided(), registered(), upd], until=at(0, 10, 30), start=at(0, 10))
    rec = saga_events(store, EventType.DELAY_REASON_RECORDED)
    assert [r.payload["reason"] for r in rec] == ["guardian"]


def test_billing_wait_over_threshold_shares_info_once_when_next_patient_waits():
    evs = [decided(), registered(), prematch(),
           e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 9, 30))]
    store, _ = run(evs, until=at(0, 11), start=at(0, 9))
    share = saga_events(store, EventType.SHARE_WAITING_INFO)
    assert len(share) == 1 and share[0].payload["target"] == "원무" and share[0].occurred_at == at(0, 10, 30)
    assert [r.payload["reason"] for r in saga_events(store, EventType.DELAY_REASON_RECORDED)] == ["billing"]


def test_billing_on_time_or_no_waiting_patient_shares_nothing():
    on_time = [decided(), registered(), prematch(), e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 9, 30)),
               e("D1", "billing_done", EventType.BILLING_DONE, at(0, 10))]
    store, _ = run(on_time, until=at(0, 11), start=at(0, 9))
    assert saga_events(store, EventType.SHARE_WAITING_INFO) == []
    nobody = [decided(), registered(), e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 9, 30))]
    store, _ = run(nobody, until=at(0, 11), start=at(0, 9))
    assert saga_events(store, EventType.SHARE_WAITING_INFO) == []


# ---------- EVT-5 회차: 퇴원 취소 뒤 재퇴원 결정 ----------

def test_discharge_redecided_after_cancel_starts_a_new_round():
    second = e("D1", "discharge_decided:r2", EventType.DISCHARGE_DECIDED, at(0, 17), bed_id="W72-R03-B1", day=1,
               patient_id="SYN-P-D1", name="가나다", gender="M", age=60, department="일반외과",
               isolation_order=False, round=2)
    evs = [decided(), registered(), e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 8)), second,
           e("D1", "discharge_registered:r2", EventType.DISCHARGE_REGISTERED, at(0, 17, 30), bed_id="W72-R03-B1", round=2),
           departed(when=at(1, 11)).model_copy(update={"event_id": "D1:patient_departed:r2", "payload": {"bed_id": "W72-R03-B1", "round": 2}})]
    store, _ = run(evs, until=at(1, 11, 10), start=at(-1, 17))
    s = fold(store.read())
    assert s.discharges["D1"].round == 2
    assert s.discharges["D1"].stage is not DischargeStage.CANCELLED
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert [r.event_id for r in req] == ["D1:r2:saga:cleaning_request"]
    assert len(saga_events(store, EventType.SCHEDULE_ITEM_CREATED)) == 2  # 회차마다 예정표


def test_loosening_correction_creates_confirmation_task_once():
    corr = e("P1", "correction:1", EventType.PATIENT_INFO_CORRECTED, at(0, 8), "his", field="precautions", value=[])
    adm_ev = e("P1", "admission_requested", EventType.ADMISSION_REQUESTED, at(-1, 15), "his",
               isolation_order=True, precautions=["contact"], organism="MRSA")
    store, _ = run([adm_ev, corr], until=at(0, 8, 30), start=at(0, 7, 55))
    tasks = saga_events(store, EventType.HUMAN_TASK_CREATED)
    assert [t.payload["reason"] for t in tasks] == ["confirm_correction"]
    assert tasks[0].payload["correction_id"] == "P1:correction:1"


# ---------- SAG-7 확정·직접 수정 배정 인식, GW-11 고정 배치 감시 ----------

def _committed(adm="P1", bed="W72-R03-B1", manual=False, when=None, actor=None):
    payload = {"bed_id": bed, "proposal_id": "x"}
    if manual:
        payload["manual"] = True
    evs = [Event(event_id=f"gw:{adm}:committed", case_id=adm, type=EventType.MATCH_COMMITTED,
                 occurred_at=when or at(-1, 18), observed_at=when or at(-1, 18), source="gateway", payload=payload)]
    if manual:
        evs.append(Event(event_id=f"gw:{adm}:applied", case_id=adm, type=EventType.MANUAL_OVERRIDE_APPLIED,
                         occurred_at=when or at(-1, 18), observed_at=when or at(-1, 18), source="gateway",
                         payload={"bed_id": bed, "actor": actor or "u1", "reason_code": "vip"}))
    return evs


def test_saga_compensates_gateway_committed_match_on_discharge_cancel():
    evs = [decided(), registered(), *_committed(), e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    assert [x.case_id for x in saga_events(store, EventType.PREMATCH_RELEASED)] == ["P1"]


def test_saga_compensates_manual_placement_too_and_alerts_actor():
    evs = [decided(), registered(), *_committed(manual=True, actor="u7"),
           e("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11))]
    store, _ = run(evs, until=at(0, 11, 10), start=at(-1, 17))
    tasks = saga_events(store, EventType.HUMAN_TASK_CREATED)
    assert any(t.payload.get("reason") == "pinned_placement_review" and t.payload.get("assignee") == "u7" for t in tasks)


def test_pinned_placement_gets_alert_when_patient_gets_isolation_order():
    evs = [*_committed(manual=True, actor="u7"),
           e("P1", "isolation_order_added", EventType.ISOLATION_ORDER_ADDED, at(0, 9), "his",
             organism="MTB", precautions=["airborne"])]
    store, _ = run(evs, until=at(0, 9, 10), start=at(-1, 17, 55))
    tasks = [t for t in saga_events(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("reason") == "pinned_placement_review"]
    assert len(tasks) == 1 and tasks[0].payload["assignee"] == "u7" and tasks[0].payload["trigger"] == "isolation_order_added"


# ---------- SAG-6 퇴원약: 처방 발행 기준 타이머, 처방 없음 알림, 수정 사유 (LP-27) ----------

def rx(kind, when, **kw):
    etype = {"issued": EventType.DISCHARGE_RX_ISSUED, "dispensed": EventType.DISCHARGE_RX_DISPENSED,
             "revised": EventType.DISCHARGE_RX_REVISED}[kind]
    return e("D1", f"rx_{kind}:{when:%H%M}", etype, when, **kw)


def test_medication_timer_starts_from_rx_issue_not_order():
    evs = [decided(), registered(), e("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9)),
           rx("issued", at(0, 10)), e("D1", "medication_ready", EventType.MEDICATION_READY, at(0, 11, 30))]
    store, _ = run(evs, until=at(0, 12, 30), start=at(0, 8))
    assert not saga_events(store, EventType.READY_TIME_RECOMPUTED)  # 처방 발행 10:00 + 2시간 안에 도착


def test_medication_late_after_rx_issue_triggers_recompute():
    evs = [decided(), registered(), e("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9)),
           rx("issued", at(0, 9, 10))]
    store, _ = run(evs, until=at(0, 11, 30), start=at(0, 8))
    rec = saga_events(store, EventType.READY_TIME_RECOMPUTED)
    assert [r.payload["reason"] for r in rec] == ["medication_delay"]
    assert rec[0].occurred_at == at(0, 11, 10)


def test_no_rx_by_policy_time_alerts_ward_once():
    store, _ = run([decided(), registered()], until=at(0, 12), start=at(0, 8))
    tasks = [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]
    assert len(tasks) == 1 and tasks[0].occurred_at == at(0, 11) and tasks[0].payload["assignee"] == "ward"


def test_rx_issued_in_time_means_no_alert():
    store, _ = run([decided(), registered(), rx("issued", at(0, 10, 50))], until=at(0, 12), start=at(0, 8))
    assert not [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]


def test_rx_revision_is_recorded_as_delay_reason():
    evs = [decided(), registered(), rx("issued", at(0, 9, 30)), rx("revised", at(0, 10, 15), reason="discharge_hold")]
    store, _ = run(evs, until=at(0, 10, 30), start=at(0, 9))
    rec = saga_events(store, EventType.DELAY_REASON_RECORDED)
    assert [(r.payload["reason"], r.payload.get("detail")) for r in rec] == [("rx_revised", "discharge_hold")]


# ---------- SAG-9 사망 퇴원(임종실 전실), 자의 퇴원 (LP-28) ----------

def eol(case="D9", bed="W72-R03-B2", when=None, **kw):
    return e(case, "eol_transfer", EventType.END_OF_LIFE_ROOM_TRANSFER, when or at(0, 14), bed_id=bed,
             isolation_order=False, **kw)


def test_eol_transfer_without_decision_vacates_bed_and_requests_cleaning():
    store, _ = run([eol()], until=at(0, 14, 10), start=at(0, 14))
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert [(r.case_id, r.payload["bed_id"], r.occurred_at) for r in req] == [("D9", "W72-R03-B2", at(0, 14))]
    assert fold(store.read()).discharges["D9"].stage is DischargeStage.CLEANING_REQUESTED


def test_death_suppresses_registration_medication_guardian_billing_alerts():
    evs = [eol(when=at(0, 9)), e("D9", "order_confirmed", EventType.ORDER_CONFIRMED, at(0, 9)),
           e("D9", "guardian_eta_updated", EventType.GUARDIAN_ETA_UPDATED, at(0, 9, 30), "human", eta="2026-03-02T15:00")]
    store, _ = run(evs, until=at(0, 20), start=at(0, 9))
    kinds = {x.type for x in saga_events(store)}
    assert not kinds & {EventType.REGISTRATION_CHECK_REQUESTED, EventType.DELAY_REASON_RECORDED,
                        EventType.SHARE_WAITING_INFO, EventType.SCHEDULE_ITEM_CREATED}
    reasons = {x.payload.get("reason") for x in saga_events(store, EventType.READY_TIME_RECOMPUTED)}
    assert reasons <= {"cleaning_overdue", "cleaning_done"}  # 청소 지연만 남는다
    assert not [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]


def test_death_after_decision_still_suppresses_later_alerts():
    evs = [decided(), registered(), eol(case="D1", bed="W72-R03-B1", when=at(0, 8))]
    store, _ = run(evs, until=at(0, 12), start=at(0, 8))
    assert not [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]


def test_ama_discharge_skips_rx_missing_alert():
    ama = decided().model_copy(update={"payload": {**decided().payload, "discharge_type": "ama"}})
    store, _ = run([ama, registered()], until=at(0, 12), start=at(0, 8))
    assert not [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]


def test_death_information_never_leaves_in_saga_commands():
    store, _ = run([eol(when=at(0, 9))], until=at(0, 12), start=at(0, 9))
    for x in saga_events(store):
        assert "death" not in str(x.payload) and "discharge_type" not in x.payload


# ---------- SAG-10 병동 사망, 급변 전동 ----------

def test_death_on_ward_vacates_bed_only_at_body_transfer():
    evs = [e("D9", "death", EventType.DEATH_CONFIRMED, at(0, 3), bed_id="W72-R03-B2", isolation_order=False)]
    store, _ = run(evs, until=at(0, 4), start=at(0, 3))
    assert not saga_events(store, EventType.CLEANING_REQUEST_ISSUED)  # 면회·사후 처치 중: 아직 사용 중
    store.append(e("D9", "body", EventType.BODY_TRANSFERRED, at(0, 4, 30), bed_id="W72-R03-B2"))
    SagaRunner(store).step(at(0, 4, 35))
    req = saga_events(store, EventType.CLEANING_REQUEST_ISSUED)
    assert [r.occurred_at for r in req] == [at(0, 4, 30)]
    assert not saga_events(store, EventType.REGISTRATION_CHECK_REQUESTED)


def test_unplanned_icu_transfer_frees_bed_and_suppresses_discharge_alerts():
    evs = [decided(), registered(),
           e("D1", "icu", EventType.UNPLANNED_TRANSFER_OUT, at(0, 2), bed_id="W72-R03-B1", destination="icu")]
    store, _ = run(evs, until=at(0, 12), start=at(0, 1))
    assert [r.occurred_at for r in saga_events(store, EventType.CLEANING_REQUEST_ISSUED)] == [at(0, 2)]
    assert not [x for x in saga_events(store, EventType.HUMAN_TASK_CREATED) if x.payload.get("kind") == "rx_missing"]


def test_bed_hold_releases_prematch_and_frees_bed_on_release():
    evs = [e("D9", "icu", EventType.UNPLANNED_TRANSFER_OUT, at(0, 2), bed_id="W72-R03-B1", destination="icu", hold=True),
           prematch(adm="P1", disc="D7", bed="W72-R03-B1", when=at(-1, 17, 40))]
    store, _ = run(evs, until=at(0, 3), start=at(0, 2))
    assert not saga_events(store, EventType.CLEANING_REQUEST_ISSUED)  # 잡아 둔 동안은 비지 않는다
    rel = [x for x in saga_events(store, EventType.PREMATCH_RELEASED) if x.payload.get("reason") == "bed_on_hold"]
    assert rel and saga_events(store, EventType.REMATCH_REQUESTED)
    store.append(e("D9", "release", EventType.BED_HOLD_RELEASED, at(0, 6), bed_id="W72-R03-B1"))
    SagaRunner(store).step(at(0, 6, 5))
    assert [r.occurred_at for r in saga_events(store, EventType.CLEANING_REQUEST_ISSUED)] == [at(0, 6)]
