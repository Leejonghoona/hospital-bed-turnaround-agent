"""2단계 남은 예외 (08 계획): E2 확장·E6·E7·E8·E9·E10. 감지 → 머리(FakeLLM) → 게이트웨이 검사·실행."""

from datetime import datetime, time, timedelta

from test_brain import _Script
from test_gateway import H, adm, at, ev, make, prop
from turnaround.agent import FakeLLM
from turnaround.agent.brain import ExceptionBrain
from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner

FIELD = load_policy(profile="field_default")
BED = "W72-R04-B2"


def _types(store, t):
    return [e for e in store.read() if e.type is t]


def _raised(store, trig):
    return [e for e in _types(store, EventType.EXCEPTION_RAISED) if e.payload["trigger"] == trig]


def _adm_at(req, case="A1", **kw):
    e = adm(case=case, when=datetime.combine(req.date() - timedelta(days=1), time(15)), **kw)
    return e.model_copy(update={"payload": {**e.payload, "requested_at": req.isoformat()}})


def _dc(extra=()):
    return [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=BED, day=1,
               discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D1"),
            *extra]


def _saga(store):
    return SagaRunner(store, SagaPolicy.from_policy(FIELD, H.rooms, H.beds))


def _brain(gw, llm=None):
    return ExceptionBrain(gw, llm or FakeLLM(), FIELD, wards=H.wards)


def test_e2_stalled_steps_remind_the_step_owner():
    evs = _dc([ev("D1", "order_confirmed", EventType.ORDER_CONFIRMED, at(8)),
               ev("D1", "rx_issued", EventType.DISCHARGE_RX_ISSUED, at(8))])  # 현장 프로필 = 현장형 순서
    store, gw = make(evs, policy=FIELD)
    _saga(store).step(at(10, 5))
    steps = {e.payload["stage"] for e in _raised(store, "sequence_stalled")}
    assert {"billing_review", "medication"} <= steps
    _brain(gw).step(at(10, 10))
    tasks = {(t.payload.get("kind"), t.payload.get("assignee")) for t in _types(store, EventType.HUMAN_TASK_CREATED)}
    assert ("step_nudge:billing_review", "billing") in tasks and ("step_nudge:medication", "pharmacy") in tasks


def test_e6_discharge_cancelled_before_contact_rematches():
    evs = _dc([_adm_at(at(14)), ev("A1", f"prematch_created:{BED}", EventType.PREMATCH_CREATED, at(8), "agent",
                                   bed_id=BED, discharge_id="D1"),
               ev("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(9))])
    store, gw = make(evs, policy=FIELD)
    _saga(store).step(at(9, 5))
    r = _raised(store, "discharge_date_changed")
    assert len(r) == 1 and r[0].payload["stage"] == "before_contact"
    _brain(gw).step(at(9, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)][0] in ("rematch_now", "escalate")


def test_e6_after_contact_never_auto_rematches():
    evs = _dc([_adm_at(at(14)), ev("A1", f"prematch_created:{BED}", EventType.PREMATCH_CREATED, at(8), "agent",
                                   bed_id=BED, discharge_id="D1"),
               ev("A1", "patient_contacted", EventType.PATIENT_CONTACTED, at(8, 30), "human"),
               ev("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(9))])
    store, gw = make(evs, policy=FIELD)
    _saga(store).step(at(9, 5))
    _brain(gw, _Script({"action": "rematch_now", "params": {}, "rationale": "x"})).step(at(9, 10))
    assert [e.payload["reasons"] for e in _types(store, EventType.ACTION_REJECTED)][0] == ["stage_locked"]


def test_e7_loosening_correction_is_not_replanned_before_confirmation():
    evs = [_adm_at(at(14), organism="MRSA"),
           ev("A1", "fix", EventType.PATIENT_INFO_CORRECTED, at(9), "human", field="precautions", value=[])]
    store, gw = make(evs, policy=FIELD)
    _saga(store).step(at(9, 5))
    r = _raised(store, "info_changed_pending")
    assert r and r[0].payload["context"]["correction_direction"] == "loosen"
    _brain(gw, _Script({"action": "replan_now", "params": {}, "rationale": "x"})).step(at(9, 10))
    assert _types(store, EventType.ACTION_REJECTED)[0].payload["reasons"] == ["loosen_unconfirmed"]


def test_e7_tightening_correction_replans():
    evs = [_adm_at(at(14)), ev("A1", "fix", EventType.PATIENT_INFO_CORRECTED, at(9), "human", field="gender", value="M")]
    store, gw = make(evs, policy=FIELD)
    _saga(store).step(at(9, 5))
    _brain(gw).step(at(9, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["replan_now"]
    assert _types(store, EventType.REMATCH_REQUESTED)


def test_e8_objection_after_contact_goes_to_brain_and_proposes():
    store, gw = make([_adm_at(at(14))], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    store.append(ev("A1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9, 20), "human"))
    gw.ward_objection("p1", "staffing", at(9, 40))
    _saga(store).step(at(9, 45))
    assert _raised(store, "ward_objection")
    _brain(gw).step(at(9, 50))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_APPROVAL_REQUESTED)] == ["propose_rematch"]


def test_e9_promise_at_risk_re_requests_cleaning():
    evs = _dc([_adm_at(at(15)), ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(12), bed_id=BED)])
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store, discharge_id="D1"), at(9))
    store.append(ev("A1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9, 30), "human"))
    _saga(store).step(at(14, 5))
    assert _types(store, EventType.PROMISE_AT_RISK) and _raised(store, "promise_at_risk")
    _brain(gw).step(at(14, 10))
    assert "re_request_cleaning" in [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)]
    # 청소 지연(E4)과 약속 위험(E9)이 함께 떠도 같은 청소에 재요청은 한 번 (작업 꼬임 방지)
    assert len([e for e in _types(store, EventType.CLEANING_REQUEST_ISSUED) if e.payload.get("reminder")]) == 1


def test_e10_call_up_stalled_reminds_ward():
    store, gw = make([_adm_at(at(13))], policy=FIELD)  # 빈 병상(퇴원 없음) → 바로 준비
    gw.submit(prop(store=store), at(9))
    _saga(store).step(at(13, 25))
    assert _raised(store, "call_up_stalled")
    _brain(gw).step(at(13, 30))
    tasks = [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("kind") == "call_up_reminder"]
    assert tasks and tasks[0].payload["assignee"] == "ward"


def test_all_ten_gold_set_triggers_are_implemented():
    import json
    trig = {json.loads(l)["trigger"] for l in open("evals/model_eval/exception_decisions_v2_1.jsonl", encoding="utf-8")}
    assert trig <= set(FIELD.exception_actions), trig - set(FIELD.exception_actions)
