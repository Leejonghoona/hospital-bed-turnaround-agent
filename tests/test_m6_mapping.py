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
