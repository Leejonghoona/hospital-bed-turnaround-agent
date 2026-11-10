"""3단계: 온보딩 흐름 (08 계획). 업로드 → 질문 묶음(구조 → 형식 → 열 → 코드) → 답 → 재생 증명 → 승인 → 버전 고정
→ 변화 감지·재질문, 학습 사전(회귀 관문 뒤 공용 사전), 진료과 → 병동을 배정에 쓴다. 합성 데이터 기준."""

import pytest

from turnaround.events.replay import hospital_to_events
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.exports import export, gold_mapping


def _canonical(seed):
    h = generate(seed=seed, config=GeneratorConfig.realistic())
    ev = [e.model_copy(update={"observed_at": e.occurred_at}) for e in hospital_to_events(h, seed=seed)]
    return h, ev


def _key(e):
    return (e.event_id, e.type, e.occurred_at, e.source, tuple(sorted((k, str(v)) for k, v in e.payload.items())))


def _run(session, gold):
    from turnaround.agent.onboarding_flow import oracle
    stages = []
    while (g := session.current()) is not None:
        stages.append(g.stage)
        session.submit(oracle(gold, g))
    return stages


# ---------- 질문 묶음 순서 ----------

def test_question_groups_come_in_order_and_each_is_answered_before_next():
    from turnaround.agent.onboarding_flow import STAGES, OnboardingSession
    h, ev = _canonical(1)
    s = OnboardingSession(export(h, ev, "emr_e"), hospital="H-E1")
    stages = _run(s, gold_mapping("emr_e"))
    assert stages[0] == "structure"  # emr_e는 병상 표를 못 찾아 구조부터 묻는다
    order = [STAGES.index(x) for x in stages]
    assert order == sorted(order)
    assert s.done and s.mapping is not None


def test_format_answer_is_used_before_column_questions():
    """시각 형식을 먼저 물어 답을 받으면, 순서 검사로 가릴 수 있는 열은 더 묻지 않는다(형식 → 열 순서의 이유)."""
    from turnaround.agent.mapping_agent import propose_mapping
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(1)
    tables = export(h, ev, "emr_d")
    before = propose_mapping(tables)
    assert any(q.id == "fmt:time" for q in before.questions)
    s = OnboardingSession(tables, hospital="H-D1")
    g = s.current()
    assert g.stage == "format"
    s.submit({"fmt:time": gold_mapping("emr_d").time_format,
              **({"fmt:sep": gold_mapping("emr_d").list_sep} if any(q.id == "fmt:sep" for q in g.questions) else {})})
    cols_after = [q for q in s.draft.questions if q.id.startswith("col:") and "시각 형식" in q.why]
    assert not cols_after


def test_duplicate_questions_are_asked_once():
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(1)
    s = OnboardingSession(export(h, ev, "emr_a"), hospital="H-A1")
    ids = [q.id for g in s.groups() for q in g.questions]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("fmt", ["emr_a", "emr_b", "emr_c", "emr_d", "emr_e"])
def test_session_with_correct_answers_restores_canonical(fmt):
    from turnaround.agent.onboarding_flow import OnboardingSession
    from turnaround.ontology.mapping import apply_mapping
    h, ev = _canonical(2)
    tables = export(h, ev, fmt)
    s = OnboardingSession(tables, hospital=f"H-{fmt}")
    _run(s, gold_mapping(fmt))
    out = apply_mapping(s.mapping, tables)
    assert sorted(map(_key, out.events)) == sorted(map(_key, ev))
    assert out.beds == h.beds and out.rooms == h.rooms


# ---------- 재생 증명 · 승인 · 버전 고정 ----------

def test_finalize_gives_replay_proof_and_frozen_version():
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(2)
    tables = export(h, ev, "emr_b")
    s = OnboardingSession(tables, hospital="H-B2")
    _run(s, gold_mapping("emr_b"))
    pkg = s.finalize(day=3)
    assert pkg.proof["passed"], pkg.proof
    assert pkg.proof["order_violations"] == 0 and pkg.proof["safety_violations"] == 0
    assert pkg.proof["commits"] > 0
    assert pkg.status == "proposed" and len(pkg.version) == 12
    assert pkg.verify()
    assert "hospital_profile" in pkg.profile_yaml
    ok = pkg.approve(by_role="medical_informatics", user_id="mi-1", acknowledge_profile_questions=True).approve(by_role="ipc_lead", user_id="ipc-lead-1", acknowledge_profile_questions=True)
    assert ok.status == "approved" and ok.approved_by == "medical_informatics+ipc_lead" and ok.version == pkg.version
    tampered = ok.__class__(**{**ok.__dict__, "mapping": ok.mapping.model_copy(update={"time_format": "%Y"})})
    assert not tampered.verify()


def test_unanswered_session_cannot_finalize_and_failed_proof_cannot_be_approved():
    from turnaround.agent.onboarding_flow import OnboardingError, OnboardingSession
    h, ev = _canonical(1)
    s = OnboardingSession(export(h, ev, "emr_a"), hospital="H")
    with pytest.raises(OnboardingError):
        s.finalize()
    _run(s, gold_mapping("emr_a"))
    pkg = s.finalize(day=3)
    bad = pkg.__class__(**{**pkg.__dict__, "proof": {**pkg.proof, "passed": False}})
    with pytest.raises(OnboardingError):
        bad.approve(by="x")


def test_wrong_answer_is_caught_by_replay_proof():
    """사람이 시각 열을 잘못 답하면(퇴실 ↔ 수납) 사건 순서 검사가 증명에서 걸러 승인할 수 없다."""
    from turnaround.agent.onboarding_flow import OnboardingSession, oracle
    h, ev = _canonical(1)
    tables = export(h, ev, "emr_d")
    gold = gold_mapping("emr_d")
    s = OnboardingSession(tables, hospital="H")
    while (g := s.current()) is not None:
        a = oracle(gold, g)
        for qid in list(a):
            if a[qid] == "t:patient_departed":
                a[qid] = "t:billing_done"
            elif a[qid] == "t:billing_done":
                a[qid] = "t:patient_departed"
        s.submit(a)
    pkg = s.finalize(day=3)
    assert not pkg.proof["passed"] and pkg.proof["order_violations"] > 0


# ---------- 변화 감지 · 재질문 ----------

def test_drift_detects_new_column_new_code_and_bad_time_then_requestions():
    from turnaround.agent.onboarding_flow import OnboardingSession, detect_drift
    h, ev = _canonical(2)
    tables = export(h, ev, "emr_a")
    s = OnboardingSession(tables, hospital="H")
    _run(s, gold_mapping("emr_a"))
    pkg = s.finalize(day=3).approve(by_role="medical_informatics", user_id="mi-1", acknowledge_profile_questions=True).approve(by_role="ipc_lead", user_id="ipc-lead-1", acknowledge_profile_questions=True)
    assert detect_drift(pkg, tables) == []
    changed = {t: [dict(r) for r in rows] for t, rows in tables.items()}
    t0 = next(t for t, tm in pkg.mapping.tables.items() if tm.kind == "cases")
    changed[t0][0]["NEW_FLAG"] = "Y"
    gcol = next(c for c, k in pkg.mapping.tables[t0].columns.items() if k == "patient.gender")
    changed[t0][0][gcol] = "9"
    tcol = next(c for c, k in pkg.mapping.tables[t0].columns.items() if k.startswith("t:") and changed[t0][1].get(c))
    changed[t0][1][tcol] = "2026/11/09 10:00"
    qs = detect_drift(pkg, changed)
    kinds = {q.id.split(":")[0] for q in qs}
    assert {"col", "code", "fmt"} <= kinds
    re = OnboardingSession.revise(pkg, changed)
    assert re.current() is not None and re.base_version == pkg.version


def test_drift_answers_make_a_new_version_that_needs_new_approval():
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(2)
    tables = export(h, ev, "emr_b")
    s = OnboardingSession(tables, hospital="H")
    _run(s, gold_mapping("emr_b"))
    pkg = s.finalize(day=3).approve(by_role="medical_informatics", user_id="mi-1", acknowledge_profile_questions=True).approve(by_role="ipc_lead", user_id="ipc-lead-1", acknowledge_profile_questions=True)
    changed = {t: [dict(r) for r in rows] for t, rows in tables.items()}
    t0 = next(t for t, tm in pkg.mapping.tables.items() if tm.kind == "cases")
    for r in changed[t0]:
        r["비고"] = "x"
    re = OnboardingSession.revise(pkg, changed)
    while (g := re.current()) is not None:
        re.submit({q.id: None for q in g.questions})  # 새 열은 쓰지 않는다
    new = re.finalize(day=3)
    assert new.version != pkg.version and new.status == "proposed" and new.parent == pkg.version
    assert "비고" in new.ignored.get(t0, ())


# ---------- 학습 사전 (회귀 관문) ----------

def test_learning_curve_same_vendor_second_hospital_asks_fewer_open_questions():
    from turnaround.agent.lexicon import Lexicon, learn, promote
    from turnaround.agent.onboarding_flow import OnboardingSession
    h1, ev1 = _canonical(1)
    s1 = OnboardingSession(export(h1, ev1, "emr_d"), hospital="H-D1")
    _run(s1, gold_mapping("emr_d"))
    shared, report = promote(Lexicon(), learn(s1))
    assert report["accepted"] > 0 and report["rejected"] == 0
    h2, ev2 = _canonical(2)
    t2 = export(h2, ev2, "emr_d")
    without = OnboardingSession(t2, hospital="H-D2")
    with_lex = OnboardingSession(t2, hospital="H-D2", lexicon=shared)
    assert with_lex.open_questions() < without.open_questions() // 4
    _run(with_lex, gold_mapping("emr_d"))
    from turnaround.ontology.mapping import apply_mapping
    out = apply_mapping(with_lex.mapping, t2)
    assert sorted(map(_key, out.events)) == sorted(map(_key, ev2))


def test_safety_codes_are_still_asked_with_learned_lexicon():
    from turnaround.agent.lexicon import Lexicon, learn, promote
    from turnaround.agent.onboarding_flow import OnboardingSession
    h1, ev1 = _canonical(1)
    s1 = OnboardingSession(export(h1, ev1, "emr_a"), hospital="H-A1")
    _run(s1, gold_mapping("emr_a"))
    shared, _ = promote(Lexicon(), learn(s1))
    h2, ev2 = _canonical(2)
    s2 = OnboardingSession(export(h2, ev2, "emr_a"), hospital="H-A2", lexicon=shared)
    safety = [q for g in s2.groups() for q in g.questions if "안전 속성" in q.why]
    assert safety and all(q.proposal is not None for q in safety)


def test_wrong_learned_entry_is_rejected_by_regression_gate():
    from turnaround.agent.lexicon import Lexicon, promote
    bad = Lexicon(columns={"SEX": "patient.age"}, values={"room_type": {"NP": "single"}})
    shared, report = promote(Lexicon(), bad)
    assert report["rejected"] == 2 and report["accepted"] == 0
    assert shared.columns == {} and shared.values == {}


def test_conflicting_entry_is_not_learned_silently():
    from turnaround.agent.lexicon import Lexicon, promote
    base = Lexicon(columns={"x_col": "patient.age"})
    shared, report = promote(base, Lexicon(columns={"x_col": "patient.name"}))
    assert shared.columns["x_col"] == "patient.age" and report["conflicts"] == ["column:x_col"]


def test_learned_ignore_is_still_confirmed_not_dropped():
    """앞선 병원이 쓰지 않은 열도 조용히 버리지 않는다. 제안(쓰지 않음)을 붙여 확인만 받는다."""
    from turnaround.agent.lexicon import Lexicon
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(1)
    tables = export(h, ev, "emr_b")
    t0 = next(iter(tables))
    for r in tables[t0]:
        r["비고"] = "x"
    s = OnboardingSession(tables, hospital="H", lexicon=Lexicon(ignore={"비고"}))
    q = next(q for g in s.groups() for q in g.questions if q.id == f"col:{t0}:비고")
    assert q.proposal == "__ignore__"


# ---------- 진료과 → 병동 ----------

def test_profile_ward_departments_fill_missing_ward_departments_for_scoring():
    from turnaround.agent.tools import PlanningTools
    from turnaround.gateway import load_policy
    from turnaround.ui.demo import build_demo
    pol = load_policy(profile="field_default")
    st = build_demo(seed=1, day=3, policy=pol, config=GeneratorConfig.realistic())
    wards = [w.model_copy(update={"departments": []}) for w in st.agent.tools.wards.values()]
    hp = pol.hospital_profile.model_copy(update={"ward_departments": {w.id: list(st.agent.tools.wards[w.id].departments)
                                                                      for w in wards}})
    st.gw.policy = st.gw.policy.model_copy(update={"hospital_profile": hp})
    t = PlanningTools(st.gw, wards)
    assert all(t.wards[w.id].departments == st.agent.tools.wards[w.id].departments for w in wards)
    assert PlanningTools(st.gw, list(st.agent.tools.wards.values())).wards == st.agent.tools.wards
