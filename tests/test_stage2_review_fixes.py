"""2단계 검수 반영 (환자안전 4/10, 현장 간호사 4/10·원무 3/10, `docs/검토/단계검수/2단계_*.md`).
검수자가 재현한 상황을 그대로 시험으로 옮겼다(고치기 전 실패)."""

from datetime import datetime, time, timedelta

import pytest

from test_brain import _Script
from test_gateway import H, adm, at, ev, make, prop
from turnaround.agent import FakeLLM
from turnaround.agent.brain import ExceptionBrain
from turnaround.events import EventType as T
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner, fold

FIELD = load_policy(profile="field_default")
BED = "W72-R04-B2"


def types(store, t):
    return [e for e in store.read() if e.type is t]


def saga(store, pol=FIELD):
    return SagaRunner(store, SagaPolicy.from_policy(pol, H.rooms, H.beds))


def brain(gw, llm=None, pol=FIELD):
    return ExceptionBrain(gw, llm or FakeLLM(), pol, wards=H.wards)


def adm_at(req, case="A1", **kw):
    e = adm(case=case, when=datetime.combine(req.date() - timedelta(days=1), time(15)), **kw)
    return e.model_copy(update={"payload": {**e.payload, "requested_at": req.isoformat()}})


def dc(iso=False, extra=(), bed=BED, case="D1", **kw):
    return [ev(case, "discharge_decided", T.DISCHARGE_DECIDED, at(17, day=-1), bed_id=bed, day=1,
               discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name=f"환자{case}",
               isolation_order=iso, precautions=["contact"] if iso else [], organism="VRE" if iso else None, **kw),
            *extra]


def contacted(at_, promised=None, case="A1"):
    p = {"promised_at": promised.isoformat()} if promised else {}
    return ev(case, "patient_contacted", T.PATIENT_CONTACTED, at_, "human", **p)


# ---------------- 환자안전 H1: 확정 매칭은 자동으로 풀지 않는다 ----------------

def test_h1_committed_match_is_not_auto_released_by_cleaning_overdue():
    evs = dc(False, [ev("D1", "dep", T.PATIENT_DEPARTED, at(9), bed_id=BED), adm_at(at(14)),
                     ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(9, 30), "agent", bed_id=BED,
                        discharge_id="D1"),
                     ev("A1", "committed", T.MATCH_COMMITTED, at(9, 40), "gateway", bed_id=BED, discharge_id="D1",
                        committed_by="agent")])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(11, 5))
    brain(gw, _Script({"action": "rematch_now", "params": {}, "rationale": "x"})).step(at(11, 10))
    assert not types(store, T.PREMATCH_RELEASED)
    assert fold(store.read()).matches["A1"].active
    assert any("stage_locked" in e.payload["reasons"] for e in types(store, T.ACTION_REJECTED))


# ---------------- H2: 격리·이동 보조 퇴실 환자에게 라운지 제안 금지 ----------------

def _guardian_case(**kw):
    evs = dc(True, [ev("D1", "billing", T.BILLING_DONE, at(10)), ev("D1", "edu", T.EDUCATION_DONE, at(10)),
                    ev("D1", "eta", T.GUARDIAN_ETA_SET, at(9), eta=at(15).isoformat()), adm_at(at(14)),
                    ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(9, 30), "agent", bed_id=BED,
                       discharge_id="D1")], **kw)
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(10, 5))
    return store, gw


def test_h2_isolation_discharge_never_gets_lounge():
    store, gw = _guardian_case()
    r = [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "guardian_delay_same_day"]
    assert r and r[0].payload["context"].get("isolation_discharge") is True
    b = brain(gw)
    assert b._input(r[0], store.read(), at(10, 10))["case_state"].get("isolation_discharge") is True
    b.step(at(10, 10))
    assert not types(store, T.LOUNGE_OFFERED)
    store2, gw2 = _guardian_case()
    brain(gw2, _Script({"action": "offer_lounge", "params": {}, "rationale": "x"})).step(at(10, 10))
    assert not types(store2, T.LOUNGE_OFFERED)
    assert any("lounge_unsafe" in e.payload["reasons"] for e in types(store2, T.ACTION_REJECTED))


# ---------------- H3 · 현장 H1: 퇴실 전 청소 재요청 금지, 등급은 격리로 다시 계산 ----------------

@pytest.mark.parametrize("iso", [True, False])
def test_h3_no_cleaning_re_request_before_departure(iso):
    evs = dc(iso, [adm_at(at(15)), ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(8), "agent", bed_id=BED,
                                      discharge_id="D1"), contacted(at(9, 30), promised=at(13))])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(12, 5))
    r = [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "promise_at_risk"]
    assert r and r[0].payload["stage"] == "occupant_not_departed"
    brain(gw).step(at(12, 10))
    brain(gw, _Script({"action": "re_request_cleaning", "params": {}, "rationale": "x"})).step(at(12, 40))
    assert not types(store, T.CLEANING_REQUEST_ISSUED)
    store.append(ev("D1", "dep", T.PATIENT_DEPARTED, at(12, 30), bed_id=BED))
    saga(store).step(at(12, 35))
    reqs = types(store, T.CLEANING_REQUEST_ISSUED)
    assert [e.payload["cleaning_type"] for e in reqs] == ["terminal" if iso else "general"]


def test_h3_re_request_keeps_isolation_grade_even_if_last_request_was_general():
    evs = dc(True, [ev("D1", "dep", T.PATIENT_DEPARTED, at(9), bed_id=BED),
                    ev("D1", "creq", T.CLEANING_REQUEST_ISSUED, at(9, 5), bed_id=BED, cleaning_type="general"),
                    adm_at(at(14)), ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(9, 30), "agent",
                                       bed_id=BED, discharge_id="D1")])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(12, 10))  # 종말소독 지연 기준 180분
    brain(gw, _Script({"action": "re_request_cleaning", "params": {}, "rationale": "x"})).step(at(12, 15))
    rem = [e for e in types(store, T.CLEANING_REQUEST_ISSUED) if e.payload.get("reminder")]
    assert rem and all(e.payload["cleaning_type"] == "terminal" for e in rem)


def test_cleaning_overdue_timer_starts_at_first_real_request_not_reminder():
    evs = dc(False, [ev("D1", "rem", T.CLEANING_REQUEST_ISSUED, at(8), "gateway", bed_id=BED, cleaning_type="general",
                        reminder=True), ev("D1", "dep", T.PATIENT_DEPARTED, at(11, 18), bed_id=BED)])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(11, 20))
    saga(store).step(at(12, 0))
    assert not [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "cleaning_overdue"]


# ---------------- H4: 승인할 때 다시 확인한다 ----------------

def _objection_after_contact(pol=FIELD):
    store, gw = make([adm_at(at(14))], policy=pol)
    gw.submit(prop(store=store), at(9))
    store.append(contacted(at(9, 20)))
    gw.ward_objection("p1", "staffing", at(9, 40))
    saga(store, pol).step(at(9, 45))
    brain(gw, pol=pol).step(at(9, 50))
    return store, gw


def test_h4_approval_after_arrival_is_rejected():
    store, gw = _objection_after_contact()
    req = types(store, T.ACTION_APPROVAL_REQUESTED)
    assert [e.payload["action"] for e in req] == ["propose_rematch"]
    store.append(ev("A1", "arrived", T.PATIENT_ARRIVED, at(10, 30), "human", bed_id=BED))
    d = gw.approve_action(req[0].payload["exception_id"], "bed_manager", at(11))
    assert d.status == "rejected" and "stage_locked" in d.reasons
    assert not types(store, T.PREMATCH_RELEASED) and fold(store.read()).matches["A1"].active


def test_h4_approver_role_is_checked():
    store, gw = _objection_after_contact()
    req = types(store, T.ACTION_APPROVAL_REQUESTED)
    d = gw.approve_action(req[0].payload["exception_id"], "anyone", at(10))
    assert d.status == "rejected" and "approver_role" in d.reasons
    assert gw.approve_action(req[0].payload["exception_id"], "bed_manager", at(10)).status == "executed"


# ---------------- H5: 사람 승인이 정의인 행동은 설정으로 풀 수 없다 ----------------

@pytest.mark.parametrize("action", ["propose_rematch", "request_recontact", "request_postpone_contact"])
def test_h5_human_defined_actions_cannot_be_set_auto(action):
    with pytest.raises(ValueError):
        type(FIELD).model_validate({**FIELD.model_dump(), "autonomy": {**FIELD.autonomy, action: "auto"}})


# ---------------- H6 · M1: 알림 행동은 예외를 닫지 않고, 마감 넘으면 결정적으로 올린다 ----------------

def test_h6_call_up_stalled_reminder_then_escalate():
    store, gw = make([adm_at(at(13))], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    store.append(contacted(at(9, 30), promised=at(13)))
    s, b = saga(store), brain(gw)
    t = at(13, 25)
    while t < at(17):
        s.step(t)
        b.step(t)
        t += timedelta(minutes=10)
    acts = [e.payload["action"] for e in types(store, T.ACTION_EXECUTED) if e.payload["exception_id"].startswith("E10")]
    assert acts[0] == "remind_ward" and "escalate" in acts
    hows = [e.payload["how"] for e in types(store, T.EXCEPTION_RESOLVED) if e.payload["exception_id"].startswith("E10")]
    assert "remind_ward" not in hows


def test_m1_wait_after_deadline_is_escalated_not_repeated():
    evs = dc(False, [adm_at(at(15)), ev("D1", "dep", T.PATIENT_DEPARTED, at(12), bed_id=BED)])
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store, discharge_id="D1"), at(9))
    store.append(contacted(at(9, 30), promised=at(15)))
    s = saga(store)
    s.step(at(14, 5))
    bw = brain(gw, _Script({"action": "wait", "params": {}, "rationale": "x"}))
    t = at(14, 10)
    while t < at(23):
        bw.step(t)
        t += timedelta(minutes=15)
    e9 = [e for e in types(store, T.RECHECK_SCHEDULED) if e.payload["exception_id"].startswith("E9")]
    esc = [e for e in types(store, T.HUMAN_TASK_CREATED) if e.payload.get("kind") == "brain_escalate"
           and str(e.payload.get("exception_id", "")).startswith("E9")]
    assert len(e9) <= 4 and esc
    assert any(e.payload["exception_id"].startswith("E9") for e in types(store, T.EXCEPTION_RESOLVED))


def test_h6_promise_at_risk_second_judgement_reaches_recontact():
    evs = dc(False, [adm_at(at(15)), ev("D1", "dep", T.PATIENT_DEPARTED, at(12), bed_id=BED),
                     ev("D1", "creq", T.CLEANING_REQUEST_ISSUED, at(12, 5), bed_id=BED, cleaning_type="general")])
    store, gw = make(evs, policy=FIELD)
    gw.submit(prop(store=store, discharge_id="D1"), at(9))
    store.append(contacted(at(9, 30), promised=at(15)))
    s, b = saga(store), brain(gw)
    t = at(14, 5)
    while t < at(16, 30):
        s.step(t)
        b.step(t)
        t += timedelta(minutes=10)
    e9 = [e.payload["action"] for e in types(store, T.ACTION_PROPOSED) if e.payload["exception_id"].startswith("E9")]
    assert "request_recontact" in e9 or "escalate" in e9


# ---------------- 현장 H2: 조건이 풀리면 작업을 닫는다 ----------------

def test_field_h2_tasks_close_when_exception_condition_clears():
    from turnaround.saga.worklist import open_tasks
    evs = dc(False, [adm_at(at(14)), ev("D1", "order", T.ORDER_CONFIRMED, at(8)),
                     ev("D1", "rx", T.DISCHARGE_RX_ISSUED, at(8)),
                     ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(8, 30), "agent", bed_id=BED,
                        discharge_id="D1")])
    store, gw = make(evs, policy=FIELD)
    s, b = saga(store), brain(gw)
    s.step(at(10, 5))
    b.step(at(10, 10))
    nudge = [e for e in types(store, T.HUMAN_TASK_CREATED) if str(e.payload.get("kind", "")).startswith("step_nudge")]
    assert nudge and nudge[0].payload.get("exception_id")
    store.append(ev("D1", "med", T.MEDICATION_READY, at(10, 30)))
    store.append(ev("D1", "review", T.BILLING_REVIEW_DONE, at(10, 30)))
    s.step(at(10, 35))
    assert not set(e.event_id for e in nudge) & set(open_tasks(store.read(), at(10, 40)))


# ---------------- 현장 H3: 역할을 나누고 모르는 역할은 실패 ----------------

def test_field_h3_pharmacy_and_billing_are_own_roles():
    from turnaround.saga.worklist import _role, load_operations
    ops = load_operations()
    for r in ("pharmacy", "cashier", "discharge_review"):
        assert r in ops.roles and r in ops.escalation
    e = ev("D1", "t", T.HUMAN_TASK_CREATED, at(9), "gateway", kind="step_nudge:medication", assignee="pharmacy")
    assert _role(e, ops) == "pharmacy"
    from turnaround.saga.exceptions_more import STEP_OWNER
    assert set(STEP_OWNER.values()) <= set(ops.roles) | {"ward"}


def test_field_h3_unknown_role_fails_loudly():
    from turnaround.saga.worklist import _role, load_operations
    with pytest.raises(ValueError):
        _role(ev("D1", "t", T.HUMAN_TASK_CREATED, at(9), "gateway", kind="x", assignee="nobody"), load_operations())


# ---------------- 현장 H4: 약속은 환자에게 안내한 시각 ----------------

def test_field_h4_no_promise_risk_without_announced_time():
    evs = dc(False, [adm_at(at(11)), ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(8), "agent",
                                       bed_id=BED, discharge_id="D1"), contacted(at(8, 30))])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(12, 5))
    assert not [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "promise_at_risk"]


def test_field_h4_no_promise_risk_when_bed_ready_before_contact_or_promise_already_passed():
    evs = dc(False, [adm_at(at(11)), ev("D1", "dep", T.PATIENT_DEPARTED, at(9), bed_id=BED),
                     ev("D1", "vac", T.BED_MARKED_VACANT, at(14, 30), bed_id=BED),
                     ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(8), "agent", bed_id=BED,
                        discharge_id="D1"), contacted(at(14, 45), promised=at(11))])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(15, 0))
    assert not [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "promise_at_risk"]


def test_field_h4_promise_uses_announced_time():
    evs = dc(False, [adm_at(at(11)), ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(8), "agent",
                                       bed_id=BED, discharge_id="D1"), contacted(at(9), promised=at(15))])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(13, 0))
    assert not [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "promise_at_risk"]
    saga(store).step(at(14, 5))
    r = [e for e in types(store, T.EXCEPTION_RAISED) if e.payload["trigger"] == "promise_at_risk"]
    assert r and r[0].payload["context"]["promise_at"] == "15:00"


def test_demo_contact_carries_announced_time():
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    st = build_demo(seed=0, day=3, until=time(18), via_fhir=False, policy=FIELD, config=GeneratorConfig.realistic(),
                    occupancy="full")
    cs = [e for e in st.store.read() if e.type is T.PATIENT_CONTACTED]
    assert cs and all(e.payload.get("promised_at") for e in cs)


# ---------------- 현장 H5: 차액 동의는 환자·등급마다 한 번 ----------------

def test_field_h5_fee_consent_task_once_per_admission_and_grade():
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    st = build_demo(seed=3, day=3, until=time(18), via_fhir=False, policy=FIELD, config=GeneratorConfig.realistic(),
                    occupancy="full")
    fee = [e for e in st.store.read() if e.type is T.HUMAN_TASK_CREATED and e.payload.get("kind") == "fee_consent"]
    keys = [(e.case_id, e.payload.get("room_grade")) for e in fee]
    assert len(keys) == len(set(keys))
    assert all(e.payload.get("assignee") == "bed_manager" for e in fee)


# ---------------- 환자안전 중간: M4 병실 단위 종말소독, M5 주의 교체는 완화 ----------------

def test_m4_room_scope_terminal_cleaning_blocks_neighbour_bed_prep():
    b1 = "W72-R04-B1"
    evs = [ev("D0", "discharge_decided", T.DISCHARGE_DECIDED, at(17, day=-1), bed_id=b1, day=1,
              discharge_date=at(0).date().isoformat(), gender="F", department="일반외과", name="환자D0",
              isolation_order=True, precautions=["contact"], organism="CRE"),
           ev("D0", "dep", T.PATIENT_DEPARTED, at(9), bed_id=b1)]
    evs += dc(False, [ev("D1", "dep", T.PATIENT_DEPARTED, at(9), bed_id=BED), adm_at(at(14)),
                      ev("A1", f"prematch_created:{BED}", T.PREMATCH_CREATED, at(9, 30), "agent", bed_id=BED,
                         discharge_id="D1")])
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(11, 5))
    brain(gw, _Script({"action": "request_bed_prep_first", "params": {}, "rationale": "x"})).step(at(11, 10))
    assert not [e for e in types(store, T.BED_PREP_REQUESTED) if e.source == "gateway"]


def test_m5_precaution_swap_is_loosen():
    from turnaround.events.corrections import correction_direction
    assert correction_direction({"precautions": ["contact"]}, "precautions", ["droplet"]) == "loosen"
    assert correction_direction({"precautions": ["contact"]}, "precautions", ["contact", "droplet"]) == "tighten"


def test_m6_brain_skips_exception_awaiting_approval():
    store, gw = _objection_after_contact()
    b = brain(gw)
    t = at(9, 55)
    for _ in range(12):
        b.step(t)
        t += timedelta(minutes=5)
    assert len([e for e in types(store, T.ACTION_PROPOSED) if e.payload["exception_id"].startswith("E8")]) == 1


def test_m7_fallback_escalation_card_has_rejection_and_ipc_for_airborne():
    evs = [adm(gender="F", dept="일반외과"), ev("A1", "pm", T.PREMATCH_CREATED, at(17, 40, day=-1), "agent",
                                               bed_id="W72-R04-B1"),
           ev("A1", "iso", T.ISOLATION_ORDER_ADDED, at(10), precautions=["airborne"], organism="MTB")]
    store, gw = make(evs, policy=FIELD)
    saga(store).step(at(10, 5))

    class Bad(FakeLLM):
        def complete_json(self, task, system, user):
            if task == "choose_exception_action":
                return {"action": "release_isolation", "params": {}}
            return super().complete_json(task, system, user)
    brain(gw, Bad()).step(at(10, 10))
    t = [e for e in types(store, T.HUMAN_TASK_CREATED) if e.payload.get("kind") == "brain_escalate"]
    assert t and t[0].payload["card"].get("rejected_action") == "release_isolation"
    assert t[0].payload["card"].get("rejected_reason") == "not_allowed"
    assert "ipc" in (t[0].payload.get("also_notify") or [])
