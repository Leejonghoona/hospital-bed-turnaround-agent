"""E4 청소 지연(XD-019), E5 병상 없음·중환자실 하향(XD-032) (현장 조사 예외3건_현장대응.md, DECISIONS 2026-11-09).

감지는 사가, 선택은 머리(FakeLLM 결정표), 검사·실행은 게이트웨이. 기다림·올림에는 함께 할 행동이 붙는다.
"""

from test_brain import _Script
from test_gateway import H, adm, at, ev, make, prop
from turnaround.agent import FakeLLM
from turnaround.agent.brain import ExceptionBrain
from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner

FIELD = load_policy(profile="field_default")
BED = "W72-R02-B1"  # 1인실


def _types(store, t):
    return [e for e in store.read() if e.type is t]


def _discharge(isolation=True, waiting=True):
    evs = [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=BED, day=1,
              discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D1",
              isolation_order=isolation, precautions=["contact"] if isolation else [],
              organism="VRE" if isolation else None),
           ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(9), bed_id=BED)]
    if waiting:
        evs += [adm(gender="F", dept="일반외과"),
                ev("A1", f"prematch_created:{BED}", EventType.PREMATCH_CREATED, at(9, 30), "agent", bed_id=BED,
                   discharge_id="D1")]
    return evs


def _run(evs, until, policy=FIELD):
    store, gw = make(evs, policy=policy)
    saga = SagaRunner(store, SagaPolicy.from_policy(policy, H.rooms, H.beds))
    saga.step(until)
    return store, gw, saga


def test_cleaning_overdue_raises_e4_with_context_and_shares_eta_with_waiting_side():
    store, gw, _ = _run(_discharge(), at(12, 5))  # 격리 퇴실 180분 기준 초과
    raised = [e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == "cleaning_overdue"]
    assert len(raised) == 1
    ctx = raised[0].payload["context"]
    assert ctx["isolation_discharge"] is True and ctx["waiting_admissions"] == ["A1"]
    share = [e for e in _types(store, EventType.SHARE_WAITING_INFO) if e.payload.get("reason") == "cleaning_overdue"]
    assert share and set(share[0].payload["to"]) == {"bed_manager", "ward"}


def test_e4_first_choice_is_a_re_request_that_keeps_terminal_grade():
    store, gw, _ = _run(_discharge(), at(12, 5))
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(12, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["re_request_cleaning"]
    rr = [e for e in _types(store, EventType.CLEANING_REQUEST_ISSUED) if e.source == "gateway"]
    assert rr and rr[0].payload["cleaning_type"] == "terminal" and rr[0].payload["reminder"] is True
    note = [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("kind") == "cleaning_rerequest_notice"]
    assert note and note[0].payload["assignee"] == "ward"


def test_bed_prep_first_is_refused_for_isolation_discharge_but_allowed_for_general():
    store, gw, _ = _run(_discharge(), at(12, 5))
    ExceptionBrain(gw, _Script({"action": "request_bed_prep_first", "params": {}, "rationale": "x"}), FIELD).step(at(12, 10))
    rej = _types(store, EventType.ACTION_REJECTED)
    assert rej and rej[0].payload["reasons"] == ["terminal_cleaning_pending"]
    assert not _types(store, EventType.BED_PREP_REQUESTED)
    store2, gw2, _ = _run(_discharge(isolation=False), at(11, 5))  # 일반 120분
    ExceptionBrain(gw2, _Script({"action": "request_bed_prep_first", "params": {}, "rationale": "x"}), FIELD).step(at(11, 10))
    assert _types(store2, EventType.BED_PREP_REQUESTED)


def test_e4_second_round_escalates_or_proposes_never_downgrades():
    store, gw, saga = _run(_discharge(), at(12, 5))
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    brain.step(at(12, 10))
    saga.step(at(13, 40))  # 1.5배 기준 초과: 2차
    brain.step(at(13, 45))
    acts = [e.payload["action"] for e in _types(store, EventType.ACTION_PROPOSED)]
    assert acts[0] == "re_request_cleaning" and acts[1] in ("propose_rematch", "escalate")
    assert all(e.payload.get("cleaning_type") == "terminal" for e in _types(store, EventType.CLEANING_REQUEST_ISSUED))


def test_e4_closes_when_cleaning_done():
    store, gw, saga = _run(_discharge(), at(12, 5))
    store.append(ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(12, 20), "human", bed_id=BED,
                    cleaning_type="terminal"))
    saga.step(at(12, 25))
    assert [e for e in _types(store, EventType.EXCEPTION_RESOLVED) if e.payload["how"] == "condition_cleared"]


def test_rematch_on_cleaning_overdue_releases_with_withdrawal_notice():
    store, gw, _ = _run(_discharge(isolation=False), at(11, 5))
    ExceptionBrain(gw, _Script({"action": "rematch_now", "params": {}, "rationale": "x"}), FIELD).step(at(11, 10))
    rel = [e for e in _types(store, EventType.PREMATCH_RELEASED) if e.source == "gateway"]
    assert rel and rel[0].case_id == "A1" and rel[0].payload["reason"] == "cleaning_overdue"
    assert [e for e in _types(store, EventType.WARD_NOTIFIED) if e.payload.get("reason") == "rematch_withdrawn"]


# ---- E5 ----

def _no_bed(icu=True):
    store, gw = make([adm(case="A9", gender="F", dept="일반외과", icu=icu)], policy=FIELD)
    gw.submit(prop(pid="p9", case="A9", bed=None, store=store), at(9, 20))
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(9, 25))
    return store, gw


def test_no_bed_raises_e5_and_icu_stepdown_escalates_with_card_and_nudges():
    store, gw = _no_bed()
    raised = [e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == "no_bed"]
    assert len(raised) == 1 and raised[0].payload["context"]["from_icu"] is True
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(9, 30))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]
    kinds = {(t.payload.get("kind"), t.payload.get("assignee")) for t in _types(store, EventType.HUMAN_TASK_CREATED)}
    assert ("brain_escalate", "bed_manager") in kinds and ("nudge_late_discharges", "ward") in kinds


def test_no_bed_non_icu_waits_with_companions_not_bare():
    store, gw = _no_bed(icu=False)
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(9, 30))
    acts = [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)]
    assert acts and acts[0] in ("wait_for_next_ready", "share_eta")
    assert [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("reason", "").startswith("no_bed")]


def test_repeated_no_bed_reports_raise_one_exception_per_case():
    """다시 찾을 때마다 병상 없음이 나와도 예외(와 함께 할 알림)는 사례마다 하나 (알림 폭주 방지, 2026-11-09 측정에서 발견)."""
    store, gw = _no_bed(icu=False)
    for i, t in enumerate((at(9, 40), at(10, 10), at(10, 40))):
        gw.submit(prop(pid=f"p9b{i}", case="A9", bed=None, store=store), t)
    SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds)).step(at(10, 45))
    assert len([e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == "no_bed"]) == 1
