"""개인정보·IT 2회차 검토 재현 결함 (2026-11-09, `docs/DECISIONS.md`). 모든 값은 합성이다. 실제 모델은 부르지 않는다."""

import json
from dataclasses import dataclass, field

import pytest

from test_gateway import DAY0, H, adm, at, make
from turnaround.agent import FakeLLM


# ---------------- 1. 외부 전송 관문은 LLM 래퍼 한 곳에서 모든 경로를 막는다 ----------------

@dataclass
class _Ext(FakeLLM):
    """외부 모델처럼 표시한 가짜(실제 호출 없음). 받은 입력을 기록한다."""
    name: str = "ext:fake"
    sent: list = field(default_factory=list)
    external = True

    def complete_json(self, task, system, user):
        self.sent.append(user)
        return super().complete_json(task, system, user)


def _planner(llm, **kw):
    from turnaround.agent import PlanningAgent
    store, gw = make([adm(gender="F", dept="일반외과")])
    return PlanningAgent(gw, H.wards, llm=llm, **kw)


@pytest.mark.parametrize("mode", ["explain", "select"])
def test_planner_refuses_hospital_data_to_external_llm(mode):
    llm = _Ext()
    with pytest.raises(PermissionError):
        _planner(llm, mode=mode).plan("A1", at(9))
    assert not llm.sent


@pytest.mark.parametrize("mode", ["explain", "select"])
def test_planner_sends_synthetic_data(mode):
    llm = _Ext()
    p = _planner(llm, mode=mode, data_class="synthetic").plan("A1", at(9))
    assert p.bed_id and len(llm.sent) == 1


def test_brain_refuses_hospital_data_to_external_llm():
    from test_brain import FIELD, _setup
    from turnaround.agent.brain import ExceptionBrain
    store, gw = _setup()
    llm = _Ext()
    with pytest.raises(PermissionError):
        ExceptionBrain(gw, llm, FIELD).step(at(10, 10))
    assert not llm.sent


def test_brain_sends_synthetic_data():
    from test_brain import FIELD, _setup
    from turnaround.agent.brain import ExceptionBrain
    store, gw = _setup()
    llm = _Ext()
    ExceptionBrain(gw, llm, FIELD, data_class="synthetic").step(at(10, 10))
    assert llm.sent


def test_planner_passes_its_data_class_to_brain():
    """계획 에이전트가 머리를 부를 때 합성 표시가 이어진다(병원 기본값으로 되돌아가 막히지 않는다)."""
    from test_brain import _setup
    from turnaround.agent import PlanningAgent
    store, gw = _setup()
    if not gw.policy.brain.enabled:
        pytest.skip("머리가 꺼진 정책")
    llm = _Ext()
    PlanningAgent(gw, H.wards, llm=llm, data_class="synthetic").step(at(10, 10))
    assert any('"exception"' in s for s in llm.sent)


def test_qa_refuses_hospital_data_to_external_llm():
    from test_agent import _qa_store
    from turnaround.agent import QueryAgent
    store, recs = _qa_store()
    llm = _Ext()
    with pytest.raises(PermissionError):
        QueryAgent(store, llm, recs).ask("Q1", "bed_manager", "내일 퇴원 예정 중 담당 교수가 오전 수술인 건?", at(14))
    assert not llm.sent


def test_qa_sends_synthetic_data():
    from test_agent import _qa_store
    from turnaround.agent import QueryAgent
    store, recs = _qa_store()
    llm = _Ext()
    a = QueryAgent(store, llm, recs, data_class="synthetic").ask(
        "Q1", "bed_manager", "내일 퇴원 예정 중 담당 교수가 오전 수술인 건?", at(14))
    assert a.status == "answered" and llm.sent


def test_internal_fake_llm_is_not_wrapped():
    from turnaround.agent import PlanningAgent, QueryAgent
    fake = FakeLLM()
    store, gw = make([adm(gender="F", dept="일반외과")])
    assert PlanningAgent(gw, H.wards, llm=fake).llm is fake
    assert QueryAgent(store, fake).llm is fake
    assert _planner(fake).plan("A1", at(9)).bed_id  # 병원 데이터 기본값이어도 원내 가짜 모델은 통과


def test_get_llm_wraps_external_models_fail_closed():
    from turnaround.agent.llm import GuardedLLM, get_llm
    env = {"TURNAROUND_LLM": "openai", "TURNAROUND_LLM_MODEL": "m", "OPENAI_API_KEY": "x",
           "TURNAROUND_EXTERNAL_LLM_ACK": "synthetic-only"}
    llm = get_llm(env)
    assert isinstance(llm, GuardedLLM) and llm.data_class == "hospital" and llm.name == "openai:m"
    with pytest.raises(PermissionError):  # 정책 deny: 병원 데이터는 네트워크로 나가기 전에 막힌다
        llm.complete_json("parse_question", "s", '{"question": "x"}')
    assert get_llm(env, data_class="synthetic").data_class == "synthetic"
    assert isinstance(get_llm({}), FakeLLM)


def test_guarded_llm_masks_string_values_before_sending():
    from turnaround.agent.llm import GuardedLLM
    inner = _Ext()
    g = GuardedLLM(inner, "synthetic")
    g.complete_json("extract", "s", json.dumps({"text": "보호자 김민수님 2시 도착, 010-1234-5678",
                                                "reference_time": "2026-11-09T08:00:00"}, ensure_ascii=False))
    sent = json.loads(inner.sent[0])
    assert "김민수" not in sent["text"] and "010-1234-5678" not in sent["text"] and "2시 도착" in sent["text"]
    assert sent["reference_time"] == "2026-11-09T08:00:00"


def test_guarded_llm_rebinds_without_double_wrapping():
    from turnaround.agent.llm import GuardedLLM, bind_data_class
    inner = _Ext()
    g = GuardedLLM(inner, "hospital")
    s = bind_data_class(g, "synthetic")
    assert isinstance(s, GuardedLLM) and s.inner is inner and s.data_class == "synthetic"
    assert bind_data_class(g, None) is g
    assert bind_data_class(FakeLLM(), "hospital").__class__ is FakeLLM
    assert isinstance(bind_data_class(inner, None), GuardedLLM)  # 감싸지 않은 외부 모델은 병원 기본값으로 감싼다
    assert bind_data_class(inner, None).data_class == "hospital"


@dataclass
class _Mapper:
    name: str = "ext:mapper"
    usage: object = None
    sent: list = field(default_factory=list)
    external = True

    def complete_json(self, task, system, user):
        self.sent.append(user)
        return {"columns": {}, "values": {}, "event_codes": {}}


def test_mapping_synthetic_path_is_not_blocked_twice():
    """매핑 경로가 합성이라고 밝히면, get_llm이 병원 기본값으로 감싼 모델이어도 막히지 않는다."""
    from turnaround.agent.llm import GuardedLLM
    from turnaround.agent.mapping_agent import propose_mapping
    inner = _Mapper()
    rows = [{"SEX": s, "WARD": w} for s, w in zip("MFMF", ("71병동", "71병동", "72병동", "72병동"))]
    draft = propose_mapping({"t": rows}, llm=GuardedLLM(inner, "hospital"), data_class="synthetic")
    assert inner.sent and draft.evidence.get("llm") == "ok"
    with pytest.raises(PermissionError):
        propose_mapping({"t": rows}, llm=GuardedLLM(_Mapper(), "synthetic"))  # 매핑 기본은 병원 데이터
