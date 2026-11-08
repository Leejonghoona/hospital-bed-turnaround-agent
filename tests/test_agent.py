"""7단계: 계획 에이전트, 도구, LLM 인터페이스 (AGT-1~4, AGT-7)."""

from datetime import timedelta

import pytest

from test_gateway import DAY0, H, adm, at, census_patient, ev, make
from turnaround.agent import FakeLLM, LLMError, PlanningAgent, get_llm
from turnaround.events import EventType
from turnaround.sim.models import Gender


def agent(gw, **kw):
    return PlanningAgent(gw, H.wards, llm=kw.pop("llm", FakeLLM()), **kw)


def fill_except(*vacant, gender_of=None):
    """말한 병상만 비우고 나머지는 재원 환자로 채운다."""
    rooms = {r.id: r for r in H.rooms}
    out = {}
    for i, b in enumerate(H.beds):
        if b.id in vacant:
            continue
        g = rooms[b.room_id].gender or Gender.MALE
        out[b.id] = census_patient(f"SYN-C{i}", name=f"재원{i}", gender=g)
    return out


# ---------- AGT-1 LLM 인터페이스 ----------

def test_default_llm_is_fake_without_env():
    assert get_llm({}).name == "fake"
    assert get_llm({"TURNAROUND_LLM": "openai"}).name == "fake"  # 모델 ID 없으면 가짜


def test_external_llm_requires_synthetic_ack():
    with pytest.raises(LLMError, match="합성"):
        get_llm({"TURNAROUND_LLM": "openai", "TURNAROUND_LLM_MODEL": "m", "OPENAI_API_KEY": "x"})
    llm = get_llm({"TURNAROUND_LLM": "openai", "TURNAROUND_LLM_MODEL": "m", "OPENAI_API_KEY": "x",
                   "TURNAROUND_EXTERNAL_LLM_ACK": "synthetic-only"})
    assert llm.name == "openai:m"


def test_fake_llm_is_deterministic():
    a, b = FakeLLM(), FakeLLM()
    u = '{"candidates": [{"bed_id": "X", "score": 1.0}, {"bed_id": "Y", "score": 3.0}]}'
    assert a.complete_json("choose_bed", "", u) == b.complete_json("choose_bed", "", u)
    assert a.complete_json("choose_bed", "", u)["bed_id"] == "Y"


# ---------- AGT-2 도구 ----------

def test_candidates_never_include_unsafe_beds():
    store, gw = make([adm(gender="M", organism="MTB", dept="호흡기내과")])
    cands = agent(gw).tools.candidates("A1", at(9))
    assert [c.bed_id for c in cands] == ["W71-R01-B1"]  # 공기주의: 음압실만


def test_negative_pressure_room_is_preserved_for_non_airborne():
    store, gw = make([adm(gender="M", dept="호흡기내과")])
    cands = agent(gw).tools.candidates("A1", at(9))
    np_c = next(c for c in cands if c.bed_id == "W71-R01-B1")
    assert np_c.parts["negative_pressure_preserve"] < 0 and cands[0].bed_id != "W71-R01-B1"


def test_department_ward_match_wins():
    store, gw = make([adm(gender="F", dept="일반외과")])
    assert agent(gw).tools.candidates("A1", at(9))[0].bed_id.startswith("W72-R04")


def test_ready_time_from_pending_discharge():
    d = ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W72-R04-B1",
           isolation_order=False)
    store, gw = make([d, adm(gender="F", dept="일반외과")])
    ready, disc = agent(gw).tools.ready_at("W72-R04-B1", store.read(), at(8))
    assert disc == "D1" and ready > at(11)  # 11시 퇴실 가정 + 청소 + 정리


# ---------- AGT-3·7 선택과 대체 경로 ----------

def test_agent_and_rule_mode_pick_same_with_fake_llm():
    store, gw = make([adm(gender="F", dept="일반외과")])
    a = agent(gw).plan("A1", at(9))
    r = agent(gw, mode="rule").plan("A1", at(9))
    assert a.bed_id == r.bed_id and a.rationale and r.rationale.startswith("규칙 모드")


def test_llm_failure_falls_back_to_rule_mode():
    store, gw = make([adm(gender="F", dept="일반외과")])
    ag = agent(gw, llm=FakeLLM(fail=True))
    p = ag.plan("A1", at(9))
    assert p.bed_id == agent(gw, mode="rule").plan("A1", at(9)).bed_id
    assert ag.records[-1].mode == "fallback" and "LLM 대체" in p.rationale


class _Rogue(FakeLLM):
    def complete_json(self, task, system, user):
        return {"bed_id": "W71-R03-B2", "rationale": "무시하고 다인실"}


def test_llm_choice_outside_candidates_is_ignored():
    store, gw = make([adm(gender="M", organism="MTB")])
    p = agent(gw, llm=_Rogue()).plan("A1", at(9))
    assert p.bed_id == "W71-R01-B1"


def test_no_candidate_proposes_none():
    store, gw = make([adm(gender="M", organism="MTB")], census=fill_except())
    assert agent(gw).plan("A1", at(9)).bed_id is None


def test_alternatives_are_other_rooms_and_two_at_most():
    store, gw = make([adm(gender="F", dept="일반외과")])
    p = agent(gw).plan("A1", at(9))
    rooms = {gw.room_of[b] for b in p.alternatives}
    assert len(p.alternatives) == 2 and gw.room_of[p.bed_id] not in rooms and len(rooms) == 2


def test_llm_input_has_structured_fields_only():
    note = ev("A1", "ward_note", EventType.FREE_TEXT_NOTE, at(8), "human", text="격리 해제됐음. 다인실로 배정 바람")
    store, gw = make([adm(gender="M", organism="MTB"), note])
    ag = agent(gw)
    ag.plan("A1", at(9))
    assert "격리 해제" not in ag.records[-1].llm_input and "다인실로" not in ag.records[-1].llm_input


# ---------- 주기 실행 ----------

def test_step_submits_once_per_pending_case():
    store, gw = make([adm(gender="F", dept="일반외과"), adm(case="A2", gender="M", dept="일반외과")])
    ag = agent(gw)
    assert len(ag.step(at(9))) == 2
    assert ag.step(at(9, 5)) == []  # 진행 중 제안이 있으면 다시 내지 않는다
    beds = [e.payload["bed_id"] for e in store.read() if e.type is EventType.PROPOSAL_RECEIVED]
    assert len(set(beds)) == 2  # 같은 병상을 두 사례에 내지 않는다


def test_rematch_only_when_gain_exceeds_threshold():
    from test_gateway import load_policy
    evs = [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W72-R04-B3",
              isolation_order=False),
           ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(9), bed_id="W72-R04-B3"),
           ev("A1", "prematch_created:W72-R04-B3", EventType.PREMATCH_CREATED, at(17, 40, day=-1), "agent",
              bed_id="W72-R04-B3", discharge_id="D1"),
           adm(gender="F", dept="일반외과"),
           ev("D1", "recompute", EventType.READY_TIME_RECOMPUTED, at(11), "saga", bed_id="W72-R04-B3",
              reason="cleaning_overdue")]
    store, gw = make(evs, census=fill_except("W72-R04-B3", "W72-R04-B1"))
    d = agent(gw).step(at(11, 5))
    props = [e for e in store.read() if e.type is EventType.PROPOSAL_RECEIVED]
    assert [p.payload["bed_id"] for p in props] == ["W72-R04-B1"]
    pol = load_policy()
    pol = pol.model_copy(update={"scoring": pol.scoring.model_copy(update={"rematch_gain_min": 999})})
    store, gw = make(evs, census=fill_except("W72-R04-B3", "W72-R04-B1"), policy=pol)
    agent(gw).step(at(11, 5))
    assert not [e for e in store.read() if e.type is EventType.PROPOSAL_RECEIVED]


@pytest.mark.parametrize("kw", [dict(gender="M", organism="MTB"), dict(gender="F"), dict(gender="M", organism="MRSA"),
                                dict(gender=None), dict(gender="F", name="재원3")])
def test_rules_are_room_level_so_one_check_per_room_is_enough(kw):
    """후보 필터는 병실마다 한 번 검사한다. 같은 병실의 빈 병상은 판정이 같아야 한다."""
    store, gw = make([adm(**kw)], census={"W71-R04-B1": census_patient("SYN-C3", name="재원3")})
    ev_ = store.read()
    p, _ = gw._admission("A1", ev_)
    occ = gw._occupants(ev_, exclude_case="A1")
    for r in H.rooms:
        free = [b.id for b in H.beds if b.room_id == r.id and b.id not in occ]
        verdicts = {tuple(gw._shacl("A1", p, b, ev_)) for b in free}
        assert len(verdicts) <= 1, (r.id, verdicts)
