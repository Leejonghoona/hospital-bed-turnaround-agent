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
    store2, _ = _setup(policy=load_policy())
    assert not _types(store2, EventType.EXCEPTION_RAISED)


def test_before_contact_brain_chooses_rematch_and_gateway_executes():
    store, gw = _setup()
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    ex = _types(store, EventType.ACTION_EXECUTED)
    assert [e.payload["action"] for e in ex] == ["rematch_now"]
    assert _types(store, EventType.EXCEPTION_RESOLVED)


def test_after_contact_brain_escalates_to_bed_manager():
    store, gw = _setup(contacted=True)
    ExceptionBrain(gw, FakeLLM(), FIELD).step(at(10, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]
    tasks = [t for t in _types(store, EventType.HUMAN_TASK_CREATED) if t.payload.get("reason") == "brain_escalate"]
    assert len(tasks) == 1 and tasks[0].payload["assignee"] == "bed_manager"


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
                                          "allowed_actions": [{"id": "rematch_now"}, {"id": "escalate"}]})
            assert out["action"] == "escalate", (trig, stage)


def test_escalation_roles_exclude_outsourced_housekeeping_by_default():
    """외주 미화와 그 책임자에게 직접 올리지 않는다. 청소 지연은 책임간호사·원무에게 (DECISIONS 2026-11-09)."""
    roles = load_policy().hospital_profile.escalation_roles
    assert {"ward", "bed_manager", "billing", "pharmacy", "admission_office"} <= set(roles)
    assert "environment_lead" not in roles and "environment_vendor" not in roles
    store, gw = _setup()
    choice = ExceptionBrain(gw, FakeLLM(), FIELD).fallback(dict(exception_id=f"E1:A1:W72-R04-B1", trigger="late_isolation",
                                                                case="A1", basis_seq=10**6))
    d = gw.submit_action(choice.model_copy(update={"params": {"role": "environment_lead"}}), at(10, 10))
    assert d.status == "rejected" and d.reasons == ["bad_params"]
