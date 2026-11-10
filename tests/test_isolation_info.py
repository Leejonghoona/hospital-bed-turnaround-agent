"""S2: 격리 정보 처리 (DECISIONS 2026-11-09 "격리 정보 처리 정정", "선제 격리는 알림으로", 인터뷰 2 Q7·Q4).

- 결과 대기만으로는 아무것도 막지 않는다.
- 고위험 조건·과거력이 있으면 근거를 붙여 알림만 한다(격리 여부는 사람).
- 양성 결과가 나왔는데 정해진 시간 안에 격리 오더가 없으면 감염관리실에 불일치 알림과 작업. 확정은 멈추지 않는다.
"""

from datetime import timedelta

from test_saga import at, e, saga_events
from turnaround.events import EventStore, EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner


def adm(case="P1", **kw):
    payload = {"patient_id": f"SYN-{case}", "name": "가나다", "gender": "F", "age": 60, "department": "일반외과",
               "isolation_order": False, "precautions": []}
    payload.update(kw)
    return e(case, "admission_requested", EventType.ADMISSION_REQUESTED, at(-1, 15), **payload)


def run(events, until, policy, start=None):
    store = EventStore(":memory:")
    store.extend(events)
    r = SagaRunner(store, policy)
    t = start or min(x.observed_at for x in events)
    while t <= until:
        r.step(t)
        t += timedelta(minutes=5)
    return store


FIELD = SagaPolicy.from_policy(load_policy(profile="field_default"))


def test_risk_alert_once_with_basis_and_no_blocking():
    store = run([adm(mdro_history=["VRE"], recent_other_hospital_stay=True)], at(0, 9), FIELD)
    alerts = saga_events(store, EventType.ISOLATION_RISK_ALERTED)
    assert len(alerts) == 1
    assert alerts[0].payload["basis"] == ["mdro_history:VRE", "recent_other_hospital_stay"]
    assert set(alerts[0].payload["to"]) == {"bed_manager", "ipc"}
    assert not saga_events(store, EventType.CASE_RECLASSIFIED_REVIEW)


def test_no_risk_alert_under_default_policy_or_without_conditions():
    assert not saga_events(run([adm(mdro_history=["VRE"])], at(0, 9), SagaPolicy()), EventType.ISOLATION_RISK_ALERTED)
    assert not saga_events(run([adm()], at(0, 9), FIELD), EventType.ISOLATION_RISK_ALERTED)


def _positive(case="P1", when=None):
    return e(case, "screen:VRE", EventType.SCREENING_RESULT_REPORTED, when or at(0, 14), organism="VRE",
             result="positive")


def test_positive_without_order_raises_mismatch_and_ipc_task_after_window():
    store = run([adm(), _positive()], at(0, 15, 30), FIELD)
    mm = saga_events(store, EventType.ISOLATION_REGISTRATION_MISMATCH)
    assert [m.occurred_at for m in mm] == [at(0, 15)] and mm[0].payload["organism"] == "VRE"
    tasks = [t for t in saga_events(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("reason") == "isolation_mismatch"]
    assert len(tasks) == 1 and tasks[0].payload["assignee"] == "ipc"


def test_no_mismatch_when_order_arrives_in_time_or_result_negative():
    order = e("P1", "iso", EventType.ISOLATION_ORDER_ADDED, at(0, 14, 30), "his", precautions=["contact"], organism="VRE")
    assert not saga_events(run([adm(), _positive(), order], at(0, 16), FIELD), EventType.ISOLATION_REGISTRATION_MISMATCH)
    neg = e("P1", "screen:CRE", EventType.SCREENING_RESULT_REPORTED, at(0, 14), organism="CRE", result="negative")
    assert not saga_events(run([adm(), neg], at(0, 16), FIELD), EventType.ISOLATION_REGISTRATION_MISMATCH)


def test_field_profile_moves_rx_alert_to_9():
    assert FIELD.rx_expected_by.hour == 9 and SagaPolicy().rx_expected_by.hour == 11


# ---------- S3: 늦은 격리 오더, 입원 측 (설계 개정 3.7 (나)) ----------

from test_saga import contacted, prematch  # noqa: E402


def _layout_policy():
    from turnaround.sim import generate
    h = generate(seed=0)
    return SagaPolicy.for_layout(h.rooms, h.beds)


def _iso(case="P1", when=None, prec=("contact",), org="VRE"):
    return e(case, "iso_late", EventType.ISOLATION_ORDER_ADDED, when or at(0, 10), "his", precautions=list(prec),
             organism=org)


def test_late_order_before_contact_releases_multi_room_match_and_requests_rematch():
    store = run([adm(), prematch(bed="W72-R04-B1"), _iso()], at(0, 10, 10), _layout_policy())
    rel = saga_events(store, EventType.PREMATCH_RELEASED)
    assert [(r.payload["bed_id"], r.payload["reason"]) for r in rel] == [("W72-R04-B1", "isolation_order_added")]
    assert saga_events(store, EventType.REMATCH_REQUESTED)


def test_late_order_after_contact_goes_to_review_and_recontact():
    store = run([adm(), prematch(bed="W72-R04-B1"), contacted(), _iso()], at(0, 10, 10), _layout_policy())
    assert not saga_events(store, EventType.PREMATCH_RELEASED)
    rev = saga_events(store, EventType.CASE_RECLASSIFIED_REVIEW)
    assert rev and rev[0].payload["reason"] == "isolation_order_after_contact"
    assert [t for t in saga_events(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("reason") == "recontact"]


def test_late_order_after_commit_before_arrival_creates_human_task_only():
    commit = e("P1", "gw:p1:committed", EventType.MATCH_COMMITTED, at(0, 9, 30), "gateway", bed_id="W72-R04-B1")
    store = run([adm(), prematch(bed="W72-R04-B1"), contacted(), commit, _iso()], at(0, 10, 10), _layout_policy())
    tasks = [t for t in saga_events(store, EventType.HUMAN_TASK_CREATED)
             if t.payload.get("reason") == "isolation_order_after_commit"]
    assert len(tasks) == 1 and not saga_events(store, EventType.PREMATCH_RELEASED)


def test_late_order_is_ignored_when_matched_bed_still_fits():
    store = run([adm(), prematch(bed="W72-R01-B1"), _iso()], at(0, 10, 10), _layout_policy())  # 1인실
    assert not saga_events(store, EventType.PREMATCH_RELEASED)
    airborne = run([adm(), prematch(bed="W72-R01-B1"), _iso(prec=("airborne",), org="MTB")], at(0, 10, 10),
                   _layout_policy())
    assert saga_events(airborne, EventType.PREMATCH_RELEASED)  # 공기주의는 음압실이어야 한다


# ---------- S4: 늦은 격리 정보, 병상 측과 재원 환자 (설계 개정 3.7 (다)) ----------

from test_saga import decided, departed, registered  # noqa: E402


def _disc_iso(bed="W72-R04-B1", when=None):
    return e("D1", "iso_late", EventType.ISOLATION_ORDER_ADDED, when or at(0, 13), "his", bed_id=bed,
             precautions=["contact"], organism="VRE")


def _clean(kind, when, bed="W72-R04-B1", key=None):
    return e("D1", key or f"cleaning_done:{kind}", EventType.CLEANING_DONE, when, "human", bed_id=bed, cleaning_type=kind)


def test_vacancy_already_marked_is_revoked_then_restored_after_terminal_done():
    bed = "W72-R04-B1"
    evs = [decided(bed=bed), registered(bed=bed), departed(bed=bed, when=at(0, 11)), _clean("general", at(0, 11, 50)),
           e("D1", "bed_ready", EventType.BED_READY, at(0, 12), "human", bed_id=bed), _disc_iso(bed)]
    store = run(evs, at(0, 13, 10), _layout_policy(), start=at(0, 10, 55))
    vac = saga_events(store, EventType.BED_MARKED_VACANT)
    rev = saga_events(store, EventType.BED_VACANCY_REVOKED)
    assert [v.occurred_at for v in vac] == [at(0, 12)] and [r.occurred_at for r in rev] == [at(0, 13)]
    assert rev[0].payload["reason"] == "late_isolation"
    store.append(_clean("terminal", at(0, 14, 30)))
    SagaRunner(store, _layout_policy()).step(at(0, 14, 35))
    assert [v.occurred_at for v in saga_events(store, EventType.BED_MARKED_VACANT)] == [at(0, 12), at(0, 14, 30)]


def test_next_patient_already_committed_to_that_bed_goes_to_a_person():
    bed = "W72-R04-B1"
    commit = e("P1", "gw:p1:committed", EventType.MATCH_COMMITTED, at(0, 12, 30), "gateway", bed_id=bed)
    evs = [decided(bed=bed), registered(bed=bed), departed(bed=bed, when=at(0, 11)), adm(), prematch(bed=bed),
           contacted(), commit, _disc_iso(bed)]
    store = run(evs, at(0, 13, 10), _layout_policy(), start=at(0, 10, 55))
    tasks = [t for t in saga_events(store, EventType.HUMAN_TASK_CREATED)
             if t.payload.get("reason") == "late_isolation_next_committed"]
    assert len(tasks) == 1 and tasks[0].case_id == "P1"


def test_arrived_patient_isolated_in_multi_room_requests_contact_trace_for_roommates():
    arrive = lambda c, b, t: e(c, "arrived", EventType.PATIENT_ARRIVED, t, "his", bed_id=b)  # noqa: E731
    commit = lambda c, b: e(c, "gw:committed", EventType.MATCH_COMMITTED, at(0, 9), "gateway", bed_id=b)  # noqa: E731
    evs = [adm("P1"), commit("P1", "W72-R04-B1"), arrive("P1", "W72-R04-B1", at(0, 15)),
           adm("P2"), commit("P2", "W72-R04-B2"), arrive("P2", "W72-R04-B2", at(0, 15, 30)),
           decided(case="D3", bed="W72-R04-B3"),
           e("P1", "iso_after_arrival", EventType.ISOLATION_ORDER_ADDED, at(1, 10), "his", precautions=["contact"],
             organism="CRE")]
    store = run(evs, at(1, 10, 10), _layout_policy(), start=at(1, 9, 55))
    ct = saga_events(store, EventType.CONTACT_TRACE_REQUESTED)
    assert len(ct) == 1 and ct[0].payload["room_id"] == "W72-R04" and ct[0].payload["to"] == "ipc"
    assert sorted((c["case"], c["bed_id"]) for c in ct[0].payload["contacts"]) == [("D3", "W72-R04-B3"),
                                                                                   ("P2", "W72-R04-B2")]
    assert "name" not in str(ct[0].payload)
