"""SAG-8 교대·마감: 운영 달력, 시간대별 역할 매핑, 인계, 마감 초과 에스컬레이션. DAT-8 근무 인원표."""

from datetime import date, datetime, timedelta

from turnaround.events import Event, EventStore, EventType
from turnaround.saga.worklist import OperatingCalendar, WorklistRunner, load_operations, open_tasks
from turnaround.sim import generate
from turnaround.sim.staffing import synthetic_staffing

DAY0 = datetime(2026, 3, 2)  # 월요일


def at(hh, mm=0, day=0):
    return DAY0 + timedelta(days=day, hours=hh, minutes=mm)


def task(key, when, kind="rx_missing", assignee="ward", **kw):
    return Event(event_id=f"D1:{key}", case_id="D1", type=EventType.HUMAN_TASK_CREATED, occurred_at=when,
                 observed_at=when, source="saga", payload={"kind": kind, "assignee": assignee, "bed_id": "W71-R03-B1",
                                                           **kw})


def done(key, when, actor="n1"):
    return Event(event_id=f"D1:{key}:done", case_id="D1", type=EventType.HUMAN_TASK_DONE, occurred_at=when,
                 observed_at=when, source="human", payload={"task_id": f"D1:{key}", "actor": actor})


OPS = load_operations()


# ---------- 운영 달력 ----------

def test_calendar_weekends_and_holidays():
    cal = OperatingCalendar(holidays={date(2026, 3, 3)})
    assert cal.is_workday(date(2026, 3, 2)) and not cal.is_workday(date(2026, 3, 3))
    assert not cal.is_workday(date(2026, 3, 7))  # 토요일
    assert cal.next_workday_noon(at(16, day=1)) == at(12, day=2)  # 화(공휴일) → 수 정오
    assert cal.next_workday_noon(at(16, day=4)) == at(12, day=7)  # 금 → 월


def test_nursing_shift_boundaries():
    cal = OPS.calendar
    assert cal.shift(at(7)) == "day" and cal.shift(at(14, 59)) == "day"
    assert cal.shift(at(15)) == "evening" and cal.shift(at(23)) == "night" and cal.shift(at(6, 59)) == "night"


# ---------- 시간대별 역할 매핑 (LP-25) ----------

def test_bed_manager_role_maps_by_time():
    assert OPS.holder("bed_manager", at(10)) == "admission_office"  # 평일 주간 입원 원무
    assert OPS.holder("bed_manager", at(20)) == "night_admin"  # 야간 당직 원무
    assert OPS.holder("bed_manager", at(10, day=5)) == "night_admin"  # 토요일
    assert OPS.holder("ipc", at(10)) == "ipc" and OPS.holder("ipc", at(2)) == "id_oncall"
    assert OPS.holder("ward", at(16)) == "charge_nurse:evening"


# ---------- 마감 초과 에스컬레이션 ----------

def test_overdue_task_escalates_once_to_upper_role():
    store = EventStore(":memory:")
    store.append(task("rx_missing", at(11)))  # rx_missing 마감 30분
    r = WorklistRunner(store, OPS)
    r.step(at(11, 20))
    assert not [e for e in store.read() if e.type is EventType.TASK_ESCALATED]
    r.step(at(11, 31))
    r.step(at(11, 45))
    esc = [e for e in store.read() if e.type is EventType.TASK_ESCALATED]
    assert len(esc) == 1 and esc[0].payload["to"] == "nursing_supervisor"
    assert esc[0].occurred_at == at(11, 30)


def test_done_task_does_not_escalate():
    store = EventStore(":memory:")
    store.extend([task("rx_missing", at(11)), done("rx_missing", at(11, 10))])
    WorklistRunner(store, OPS).step(at(12))
    assert not [e for e in store.read() if e.type is EventType.TASK_ESCALATED]
    assert open_tasks(store.read()) == {}


def test_explicit_due_in_payload_wins():
    store = EventStore(":memory:")
    store.append(task("post_review", at(16, day=3), kind="post_review", assignee="ipc", due=at(12, day=4).isoformat()))
    r = WorklistRunner(store, OPS)
    r.step(at(11, day=4))
    assert not [e for e in store.read() if e.type is EventType.TASK_ESCALATED]
    r.step(at(12, 5, day=4))
    assert [e.payload["to"] for e in store.read() if e.type is EventType.TASK_ESCALATED] == ["ipc_lead"]


# ---------- 교대 인계 ----------

def test_open_ward_task_is_handed_over_at_shift_change():
    store = EventStore(":memory:")
    store.append(task("rx_missing", at(14, 50)))
    r = WorklistRunner(store, OPS)
    r.step(at(14, 55))
    r.step(at(15, 5))
    r.step(at(15, 10))
    ho = [e for e in store.read() if e.type is EventType.TASK_HANDED_OVER]
    assert len(ho) == 1
    assert ho[0].payload["from"] == "charge_nurse:day" and ho[0].payload["to"] == "charge_nurse:evening"
    assert ho[0].occurred_at == at(15)


def test_runner_is_restart_safe():
    store = EventStore(":memory:")
    store.append(task("rx_missing", at(14, 50)))
    WorklistRunner(store, OPS).step(at(15, 40))
    n = len(store.read())
    WorklistRunner(store, OPS).step(at(15, 40))  # 재시작해도 같은 명령을 다시 만들지 않는다
    assert len(store.read()) == n


# ---------- DAT-8 근무 인원표 ----------

def test_synthetic_staffing_covers_every_ward_shift_and_day():
    h = generate(seed=0)
    st = synthetic_staffing(h, seed=0)
    for w in h.wards:
        for day in range(h.config.days):
            for shift in ("day", "evening", "night"):
                n = st.nurses_on(w.id, DAY0.date() + timedelta(days=day), shift)
                assert 1 <= n <= 6
    d = DAY0.date()
    assert st.nurses_on("W71", d, "night") <= st.nurses_on("W71", d, "day")  # 야간이 주간보다 적거나 같다


def test_pending_review_is_a_task_until_resolved():
    rv = Event(event_id="gw:p1:review", case_id="A1", type=EventType.CASE_ROUTED_REVIEW, occurred_at=at(10),
               observed_at=at(10), source="gateway", payload={"reasons": ["isolation"], "proposal_id": "p1"})
    store = EventStore(":memory:")
    store.append(rv)
    WorklistRunner(store, OPS).step(at(11, 5))  # 검토 마감 60분
    esc = [e for e in store.read() if e.type is EventType.TASK_ESCALATED]
    assert [e.payload["to"] for e in esc] == ["admission_lead"]
    store2 = EventStore(":memory:")
    store2.extend([rv, Event(event_id="gw:p1:approved", case_id="A1", type=EventType.MANAGER_APPROVED,
                             occurred_at=at(10, 20), observed_at=at(10, 20), source="gateway", payload={})])
    WorklistRunner(store2, OPS).step(at(11, 5))
    assert not [e for e in store2.read() if e.type is EventType.TASK_ESCALATED]


def test_gateway_post_review_due_respects_holidays():
    import sys
    sys.path.insert(0, "tests")
    from test_gateway import H, adm
    from turnaround.gateway import Gateway, load_policy
    from turnaround.saga.worklist import OperatingCalendar
    store = EventStore(":memory:")
    store.append(adm(gender="M", organism="MRSA"))
    gw = Gateway(store, H.rooms, H.beds, load_policy(), calendar=OperatingCalendar(holidays={date(2026, 3, 6)}))
    gw.manual_override("A1", "W71-R03-B2", actor="u", role="bed_manager", reason="clinical", now=at(16, day=3),
                       reason_text="커튼과 1m 간격")
    t = [e for e in store.read() if e.payload.get("kind") == "post_review"][-1]
    assert t.payload["due"] == at(12, day=7).isoformat()  # 금요일 휴무 → 월요일
