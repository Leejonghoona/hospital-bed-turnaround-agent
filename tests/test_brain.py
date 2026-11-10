"""S5: 예외 판단 머리 v0 (E1 늦은 격리 정보). DECISIONS "U8 확장", 설계 개정 4절.

감지는 사가(결정적), 선택은 LLM(테스트는 FakeLLM 결정표), 검사·실행은 게이트웨이(결정적).
안전을 조이는 필수 행동(매칭 해제, 청소 상향 등)은 사가가 먼저 하고, 머리는 그다음 할 일만 고른다.
"""

from datetime import datetime, timedelta

from test_gateway import H, adm, at, ev, make
from turnaround.agent import FakeLLM
from turnaround.agent.brain import ExceptionBrain
from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner

FIELD = load_policy(profile="field_default")


def _setup(contacted=False, policy=FIELD):
    evs = [adm(gender="F", dept="일반외과"),
           ev("A1", "prematch_created:W72-R04-B1", EventType.PREMATCH_CREATED, at(17, 40, day=-1), "agent",
              bed_id="W72-R04-B1")]
    if contacted:
        evs.append(ev("A1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9), "human"))
    evs.append(ev("A1", "iso_late", EventType.ISOLATION_ORDER_ADDED, at(10), "his", precautions=["contact"],
                  organism="VRE"))
    store, gw = make(evs, policy=policy)
    saga = SagaRunner(store, SagaPolicy.from_policy(policy, H.rooms, H.beds))
    saga.step(at(10, 5))
    return store, gw


def _types(store, *ts):
    return [e for e in store.read() if e.type in ts]


def test_exception_raised_once_under_field_profile_and_not_by_default():
    store, _ = _setup()
    raised = _types(store, EventType.EXCEPTION_RAISED)
    assert len(raised) == 1 and raised[0].payload["trigger"] == "late_isolation"
    assert raised[0].payload["stage"] == "before_contact"
    store2, _ = _setup(policy=load_policy(profile="legacy", allow_legacy=True))
    assert not _types(store2, EventType.EXCEPTION_RAISED)


def test_before_contact_brain_chooses_rematch_and_gateway_executes():
    store, gw = _setup()
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    ex = _types(store, EventType.ACTION_EXECUTED)
    assert [e.payload["action"] for e in ex] == ["rematch_now"]
    assert _types(store, EventType.EXCEPTION_RESOLVED)


def test_after_contact_brain_proposes_rematch_for_human_approval():
    """사용자 검토(XD-028): 연락 뒤에도 다시 찾기는 하되 추천안으로 사람 승인을 받고, 재연락은 사람이 한다."""
    store, gw = _setup(contacted=True)
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    assert not _types(store, EventType.ACTION_EXECUTED)
    req = _types(store, EventType.ACTION_APPROVAL_REQUESTED)
    assert [e.payload["action"] for e in req] == ["propose_rematch"]
    assert not _types(store, EventType.REMATCH_REQUESTED)
    # 늦은 격리의 병상 결정은 감염관리실이 승인한다 (DECISIONS 최고점 루프 2회차)
    assert gw.approve_action(req[0].payload["exception_id"], "bed_manager", at(10, 20)).reasons == ["approver_role"]
    d = gw.approve_action(req[0].payload["exception_id"], "ipc", at(10, 20), approver_id="ipc-1")
    assert d.status == "executed"
    assert _types(store, EventType.ACTION_APPROVED) and _types(store, EventType.REMATCH_REQUESTED)
    assert _types(store, EventType.EXCEPTION_RESOLVED)


class _Script(FakeLLM):
    def __init__(self, out):
        super().__init__()
        self.out = out

    def complete_json(self, task, system, user):
        if task == "choose_exception_action":
            return self.out
        return super().complete_json(task, system, user)


def test_action_outside_list_is_rejected_and_falls_back_to_escalate():
    store, gw = _setup()
    ExceptionBrain(gw, _Script({"action": "official_commit", "params": {}, "rationale": "x"}), FIELD).step(at(10, 10))
    rej = _types(store, EventType.ACTION_REJECTED)
    assert rej and rej[0].payload["reasons"] == ["not_allowed"]
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]
    assert [e.payload["mode"] for e in _types(store, EventType.ACTION_PROPOSED)] == ["llm", "fallback"]


def test_llm_failure_falls_back_to_escalate():
    store, gw = _setup()
    ExceptionBrain(gw, FakeLLM(fail=True), FIELD).step(at(10, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]


def test_stale_basis_is_rejected_and_exception_stays_open():
    store, gw = _setup()
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    choice = brain.choose(brain.open_exceptions(store.read())[0], store.read(), at(10, 10))
    store.append(ev("A1", "info_fix", EventType.PATIENT_INFO_CORRECTED, at(10, 11), "human", field="gender", value="F"))
    d = gw.submit_action(choice, at(10, 12))
    assert d.status == "rejected" and d.reasons == ["stale_basis"]
    assert not _types(store, EventType.EXCEPTION_RESOLVED)


def test_human_approval_action_is_requested_not_executed():
    store, gw = _setup()
    ExceptionBrain(gw, _Script({"action": "propose_surveillance_hold", "params": {}, "rationale": "x"}),
                   FIELD).step(at(10, 10))
    assert _types(store, EventType.ACTION_APPROVAL_REQUESTED)
    assert not _types(store, EventType.ACTION_EXECUTED)


def test_brain_is_idempotent_and_input_has_no_names():
    store, gw = _setup()
    llm = FakeLLM()
    brain = ExceptionBrain(gw, llm, FIELD)
    brain.step(at(10, 10))
    brain.step(at(10, 15))
    assert len(_types(store, EventType.ACTION_EXECUTED)) == 1
    assert brain.records and "환자A1" not in brain.records[-1] and '"name"' not in brain.records[-1]


def test_fake_table_never_rematches_after_contact_commit_or_arrival_for_any_trigger():
    from turnaround.agent.llm import _fake_exception_action
    for trig in ("late_isolation", "discharge_date_changed", "ward_objection"):
        for stage in ("after_contact", "after_commit", "after_arrival"):
            out = _fake_exception_action({"exception": {"trigger": trig, "stage": stage},
                                          "allowed_actions": [{"id": "rematch_now"}, {"id": "propose_rematch"},
                                                              {"id": "escalate"}]})
            assert out["action"] in ("escalate", "propose_rematch"), (trig, stage)  # 자동 재매칭은 없다


def test_escalation_roles_exclude_outsourced_housekeeping_by_default():
    """외주 미화와 그 책임자에게 직접 올리지 않는다. 청소 지연은 책임간호사·원무에게 (DECISIONS 2026-11-09)."""
    roles = load_policy(profile="legacy", allow_legacy=True).hospital_profile.escalation_roles
    assert {"ward", "bed_manager", "billing", "pharmacy", "admission_office"} <= set(roles)
    assert "environment_lead" not in roles and "environment_vendor" not in roles
    store, gw = _setup()
    choice = ExceptionBrain(gw, FakeLLM(), FIELD).fallback(dict(exception_id=f"E1:A1:W72-R04-B1", trigger="late_isolation",
                                                                case="A1", basis_seq=10**6))
    d = gw.submit_action(choice.model_copy(update={"params": {"role": "environment_lead"}}), at(10, 10))
    assert d.status == "rejected" and d.reasons == ["bad_params"]


# ---- 연락·확정·입실 뒤 자동 재매칭 잠금 (Haiku 측정 금지 20건 중 14건, 2026-11-09) ----

def test_auto_rematch_is_locked_after_contact_commit_arrival():
    from turnaround.gateway.profile import STAGE_LOCKED_ACTIONS
    for st in ("after_contact", "after_commit", "after_arrival"):
        assert "rematch_now" in STAGE_LOCKED_ACTIONS[st]


def test_brain_never_shows_rematch_now_after_contact():
    import json
    store, gw = _setup(contacted=True)
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    ex = brain.open_exceptions(store.read())[0]
    ctx = brain._input(ex, store.read(), at(10, 10))
    ids = [a["id"] for a in ctx["allowed_actions"]]
    assert "rematch_now" not in ids and "propose_rematch" in ids


def test_gateway_rejects_auto_rematch_after_contact_even_if_chosen():
    store, gw = _setup(contacted=True)
    ExceptionBrain(gw, _Script({"action": "rematch_now", "params": {}, "rationale": "x"}), FIELD).step(at(10, 10))
    rej = _types(store, EventType.ACTION_REJECTED)
    assert rej and rej[0].payload["reasons"] == ["stage_locked"]
    assert not _types(store, EventType.REMATCH_REQUESTED)
    # 거부 뒤 대체 경로(올림)는 그대로 돈다
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]


def test_stage_lock_cannot_be_unlocked_by_hospital_policy():
    import pytest
    from turnaround.gateway.policy import Policy
    raw = FIELD.model_dump()
    raw["exception_actions"]["late_isolation"]["stage_allowed"] = {"after_contact": ["rematch_now", "escalate"]}
    with pytest.raises(ValueError, match="잠금"):
        Policy.model_validate(raw)


# ---- 예외 판단 입력을 정답 세트와 맞춤 (측정 조건 = 운영 조건, 2026-11-09) ----

def test_brain_input_has_common_case_state_and_tool_outputs_for_late_isolation():
    store, gw = _setup()
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    ex = brain.open_exceptions(store.read())[0]
    ctx = brain._input(ex, store.read(), at(10, 10))
    cs, to = ctx["case_state"], ctx["tool_outputs"]
    assert {"now_local", "shift", "day_type", "contacted", "committed"} <= set(cs)
    assert cs["now_local"] == "10:10" and cs["shift"] == "day" and cs["contacted"] is False
    assert {"rematch_candidates", "best_candidate_room_type", "best_ready_in_min", "negative_pressure_free"} <= set(to)


def test_brain_input_keys_are_within_gold_set_vocabulary():
    """운영 입력의 키가 정답 세트 입력에 쓰인 키 안에 있어야 측정 조건과 같다."""
    import json
    keys = {"case_state": set(), "tool_outputs": set()}
    for line in open("evals/model_eval/exception_decisions_v2_1.jsonl", encoding="utf-8"):
        d = json.loads(line)
        if d["trigger"] == "late_isolation":
            keys["case_state"] |= set(d["input"]["case_state"])
            keys["tool_outputs"] |= set(d["input"].get("tool_outputs") or {})
    store, gw = _setup()
    brain = ExceptionBrain(gw, FakeLLM(), FIELD)
    ctx = brain._input(brain.open_exceptions(store.read())[0], store.read(), at(10, 10))
    assert set(ctx["tool_outputs"]) <= keys["tool_outputs"]
    assert set(ctx["case_state"]) - {"scarce_room"} <= keys["case_state"] | {"organism", "precaution"}


def test_ready_p90_is_not_earlier_than_p50():
    from turnaround.agent.tools import PlanningTools
    store, gw = _setup()
    t = PlanningTools(gw, H.wards)
    for b in ("W72-R04-B1", "W72-R02-B1"):
        assert t.ready_at(b, store.read(), at(10), q="p90")[0] >= t.ready_at(b, store.read(), at(10))[0]
