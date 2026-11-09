"""M6-2·3: 서로 다른 병원 내보내기 형식 → 매핑 → 같은 정규 사건 (로드맵 07)."""

import pytest

from turnaround.events.replay import hospital_to_events
from turnaround.ontology.mapping import apply_mapping
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.exports import FORMATS, export, gold_mapping


def _canonical(seed):
    h = generate(seed=seed, config=GeneratorConfig.realistic())
    ev = [e.model_copy(update={"observed_at": e.occurred_at}) for e in hospital_to_events(h, seed=seed)]
    return h, ev


def _key(e):
    return (e.event_id, e.type, e.occurred_at, e.source, tuple(sorted((k, str(v)) for k, v in e.payload.items())))


def test_formats_really_differ():
    h, ev = _canonical(1)
    cols = [set(next(iter(export(h, ev, f).values()))[0]) for f in FORMATS]
    assert not (cols[0] & cols[1]) and not (cols[1] & cols[2])


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_gold_mapping_restores_canonical_events_and_layout(fmt, seed):
    h, ev = _canonical(seed)
    out = apply_mapping(gold_mapping(fmt), export(h, ev, fmt))
    assert sorted(map(_key, out.events)) == sorted(map(_key, ev))
    assert out.rooms == h.rooms and out.beds == h.beds
    assert {w.id: w.departments for w in out.wards} == {w.id: w.departments for w in h.wards}


@pytest.mark.parametrize("fmt", FORMATS)
def test_mapping_agent_draft_plus_answers_restores_exactly(fmt):
    from turnaround.evaluation.mapping_eval import score
    h, ev = _canonical(2)
    r = score(export(h, ev, fmt), gold_mapping(fmt), ev, h)
    assert r["columns"]["wrong"] == 0 and r["codes"]["wrong"] == 0
    assert r["exact_after_answers"]


def test_trap_discharge_time_column_is_resolved_by_event_order():
    """'퇴원일시'·DSCH_DT는 수납 완료 시각이다. 이름이 아니라 핵심 온톨로지의 사건 순서로 가린다."""
    from turnaround.agent.mapping_agent import propose_mapping
    h, ev = _canonical(1)
    for fmt, table, col in (("emr_a", "TB_DSCH", "DSCH_DT"), ("emr_b", "퇴원현황", "퇴원일시")):
        d = propose_mapping(export(h, ev, fmt))
        assert d.columns[(table, col)] == "t:billing_done"
        checks = d.evidence["order_checks"][f"{table}.{col}"]
        assert checks["t:billing_done"] == 0 and checks["t:patient_departed"] > 0


def test_llm_input_has_no_raw_identifiers():
    import json
    from turnaround.agent.mapping_agent import profile
    h, ev = _canonical(1)
    for fmt in FORMATS:
        text = json.dumps(profile(export(h, ev, fmt)), ensure_ascii=False)
        assert h.discharges[0].patient.name not in text and "SYN-P" not in text


def test_safety_codes_always_go_to_a_human():
    from turnaround.agent.mapping_agent import propose_mapping
    h, ev = _canonical(1)
    d = propose_mapping(export(h, ev, "emr_b"))
    ids = {q.id for q in d.questions}
    assert any(i.startswith("code:patient.precautions:") for i in ids)
    assert any(i.startswith("code:room_type:") for i in ids)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_first_holdout_format_after_generic_fixes(seed):
    """emr_d는 어휘 고정 뒤 만든 첫 홀드아웃이다. 첫 실행 실패(시각 형식·true/false·짧은 말 오인)를 일반 규칙으로 고쳤으므로
    지금은 더 이상 홀드아웃이 아니다(RESULTS 기록). 틀린 값 0, 답 뒤 완전 복원을 회귀로 지킨다."""
    from turnaround.evaluation.mapping_eval import score
    h, ev = _canonical(seed)
    r = score(export(h, ev, "emr_d"), gold_mapping("emr_d"), ev, h)
    assert r["columns"]["wrong"] == 0 and r["codes"]["wrong"] == 0
    assert "wrong" not in r["format"].values()
    assert r["exact_after_answers"]


def test_service_replay_on_mapped_data_equals_canonical():
    """서로 다른 병원 형식을 매핑해 넣어도 같은 서비스가 같은 배정과 같은 안전 검사를 낸다 (M6-5)."""
    from datetime import time

    from turnaround.evaluation.mapping_eval import gold_answers
    from turnaround.agent.mapping_agent import MappingDraft, answer, followup_questions, propose_mapping
    from turnaround.events import EventType
    from turnaround.evaluation.safety_exec import executed_safety
    from turnaround.gateway import load_policy
    from turnaround.ontology.mapping import Mapped
    from turnaround.sim.exports import HOLDOUT_FORMATS
    from turnaround.ui.demo import build_demo
    seed, cfg, pol = 2, GeneratorConfig.realistic(), load_policy(profile="field_default")
    h, ev = _canonical(seed)

    def run(mapped):
        st = build_demo(seed=seed, day=3, until=time(23, 45), via_fhir=False, policy=pol, config=cfg, mapped=mapped)
        out = st.store.read()
        commits = sorted((e.case_id, e.payload["bed_id"]) for e in out if e.type is EventType.MATCH_COMMITTED)
        s = executed_safety(st.gw, out)
        return commits, (s["S1"]["violations"], s["S2"]["violations"])

    base = run(Mapped(wards=h.wards, rooms=h.rooms, beds=h.beds, events=ev))
    assert base[0] and base[1] == (0, 0)
    for fmt in FORMATS + HOLDOUT_FORMATS:
        tables = export(h, ev, fmt)
        gold = gold_mapping(fmt)
        d = propose_mapping(tables)
        if any(q.id == "layout" for q in d.questions):
            lay = next(t for t, tm in gold.tables.items() if tm.kind == "layout")
            d = propose_mapping(tables, hints={"layout": (lay, gold.bed_parts)})
        m = answer(d, gold_answers(d, gold))
        f = followup_questions(m, tables)
        if f:
            d2 = MappingDraft(mapping=m, questions=f, evidence={}, columns={})
            m = answer(d2, gold_answers(d2, gold))
        assert run(apply_mapping(m, tables)) == base, fmt


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_second_holdout_after_structure_question(seed):
    """emr_e(두 번째 홀드아웃): 첫 실행에서 병상 표를 못 찾고도 묻지 않은 결함을 고쳤다(병상 표 질문). 지금은 회귀용."""
    from turnaround.evaluation.mapping_eval import score
    h, ev = _canonical(seed)
    r = score(export(h, ev, "emr_e"), gold_mapping("emr_e"), ev, h)
    assert r["structure_questions"] == 1
    assert r["columns"]["wrong"] == 0 and r["codes"]["wrong"] == 0 and "wrong" not in r["format"].values()
    assert r["exact_after_answers"]
