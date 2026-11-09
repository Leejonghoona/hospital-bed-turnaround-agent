"""3단계 검수 반영 (의료정보 5/10·온톨로지 5/10·개인정보 4/10·LLMOps 5/10, `docs/검토/단계검수/3단계_*.md`).
검수자 재현을 시험으로 옮겼다(고치기 전 실패)."""

import json

import pytest

from test_stage3_onboarding import _canonical, _key, _run
from turnaround.sim.exports import export, gold_mapping


class _SpyLLM:
    """외부 모델처럼 표시한 가로채기 객체: 무엇이 나가는지 본다(실제 호출 없음)."""
    name = "openai:spy"
    external = True

    def __init__(self):
        self.sent = []

    def complete_json(self, task, system, user):
        self.sent.append(user)
        return {"columns": {}, "values": {}, "event_codes": {}}


# ---------------- 개인정보 A-1·A-2 ----------------

def test_small_table_raw_values_never_go_to_external_llm():
    from turnaround.agent.mapping_agent import propose_mapping
    tables = {"tiny": [{"PT_NO": "00012345", "PT_NM": "홍길동", "AGE": "71", "MEMO": "보호자 010-1234-5678",
                        "ORG": "MRSA"}]}
    spy = _SpyLLM()
    propose_mapping(tables, llm=spy, data_class="synthetic")
    blob = "".join(spy.sent)
    for raw in ("00012345", "홍길동", "010-1234-5678", "MRSA", "71"):
        assert raw not in blob


def test_repeated_codes_still_go_but_singletons_do_not():
    from turnaround.agent.mapping_agent import llm_profile, profile
    rows = [{"SEX": s, "NM": f"이름{i}"} for i, s in enumerate("MFMFMF")] + [{"SEX": "X", "NM": "이름9"}]
    p = llm_profile(profile({"t": rows}))
    assert p["t"]["columns"]["SEX"]["codes"] == ["F", "M"]
    assert "codes" not in p["t"]["columns"]["NM"]


def test_external_llm_refused_for_hospital_data_under_deny_policy():
    from turnaround.agent.mapping_agent import propose_mapping
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(1)
    t = export(h, ev, "emr_a")
    spy = _SpyLLM()
    with pytest.raises(PermissionError):
        propose_mapping(t, llm=spy)  # 기본은 병원 데이터
    with pytest.raises(PermissionError):
        OnboardingSession(t, llm=spy, hospital="H")
    assert not spy.sent


# ---------------- 개인정보 A-3 · 온톨로지 B-1 · LLMOps B-2: 사전 누수 ----------------

def test_entries_not_exercised_by_gate_stay_local_and_provenance_is_opaque():
    from turnaround.agent.lexicon import Lexicon, promote
    cand = Lexicon(columns={"김철수교수": "patient.department", "SEX": "patient.gender"},
                   values={"department": {"KCS01": "감염내과"}, "discharge_type": {"D": "ama"}},
                   provenance={"column:김철수교수": "서울가상병원", "column:SEX": "서울가상병원"})
    shared, rep = promote(Lexicon(), cand)
    assert "김철수교수" not in shared.columns and "KCS01" not in shared.values.get("department", {})
    assert "D" not in shared.values.get("discharge_type", {})
    assert rep["local_only"] >= 3
    assert "서울가상병원" not in json.dumps(shared.model_dump(mode="json"), ensure_ascii=False)


def test_identifier_answers_are_never_learned():
    from turnaround.agent.lexicon import learn
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(1)
    s = OnboardingSession(export(h, ev, "emr_d"), hospital="H-D1")
    _run(s, gold_mapping("emr_d"))
    lx = learn(s)
    assert not {k for k in lx.columns.values() if k in ("patient.name", "patient.patient_id", "case")}


# ---------------- 승인·지문 (개인정보 A-4 · 온톨로지 B-3) ----------------

def _pkg(fmt="emr_b", seed=2):
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(seed)
    s = OnboardingSession(export(h, ev, fmt), hospital="H")
    _run(s, gold_mapping(fmt))
    return s.finalize(day=3)


def test_fingerprint_covers_proof_and_approval_record_is_signed():
    from turnaround.agent.onboarding_flow import OnboardingError
    pkg = _pkg()
    forged = pkg.__class__(**{**pkg.__dict__, "proof": {**pkg.proof, "order_violations": 28}})
    assert not forged.verify()
    with pytest.raises(OnboardingError):
        pkg.approve(by="")
    with pytest.raises(OnboardingError):
        pkg.approve(by="아무개")  # 승인 역할이 아니다
    ok = pkg.approve(by="medical_informatics", acknowledge_profile_questions=True)
    assert ok.verify() and ok.audit and ok.audit[-1]["event"] == "approved"
    forged_ok = ok.__class__(**{**ok.__dict__, "approved_by": "medical_informatics2"})
    assert not forged_ok.verify()
    assert {"core_ttl", "code", "llm", "lexicon"} <= set(ok.provenance)


def test_unresolved_profile_questions_need_explicit_acknowledgement():
    from turnaround.agent.onboarding_flow import OnboardingError
    pkg = _pkg()
    assert pkg.profile_questions
    with pytest.raises(OnboardingError):
        pkg.approve(by="medical_informatics")


# ---------------- 변화 감지 (의료정보 A-1·A-2) ----------------

def _approved(fmt="emr_a", seed=2):
    from turnaround.agent.onboarding_flow import OnboardingSession
    h, ev = _canonical(seed)
    tables = export(h, ev, fmt)
    s = OnboardingSession(tables, hospital="H")
    _run(s, gold_mapping(fmt))
    return tables, s.finalize(day=3).approve(by="medical_informatics", acknowledge_profile_questions=True)


def test_drift_detects_bool_representation_change():
    from turnaround.agent.onboarding_flow import detect_drift
    tables, pkg = _approved()
    m = pkg.mapping
    t, yes, no = None, m.bool_values[0], m.bool_values[1]
    changed = {k: [dict(r) for r in v] for k, v in tables.items()}
    hit = 0
    for tn, rows in changed.items():
        for r in rows:
            for c, v in r.items():
                if v == yes:
                    r[c], hit = "1", hit + 1
                elif v == no:
                    r[c] = "0"
    assert hit
    assert any(q.id.startswith("fmt:bool") for q in detect_drift(pkg, changed))


def test_drift_detects_emptied_safety_column():
    from turnaround.agent.onboarding_flow import detect_drift
    tables, pkg = _approved()
    t0, col = next((t, c) for t, tm in pkg.mapping.tables.items() for c, k in tm.columns.items()
                   if k == "patient.precautions" and any(r.get(c) for r in tables[t]))
    changed = {k: [dict(r) for r in v] for k, v in tables.items()}
    for r in changed[t0]:
        r[col] = ""
    assert any(q.id == f"fill:{t0}:{col}" for q in detect_drift(pkg, changed))


def test_gone_safety_column_cannot_be_waved_through():
    from turnaround.agent.onboarding_flow import OnboardingSession
    tables, pkg = _approved()
    t0, col = next((t, c) for t, tm in pkg.mapping.tables.items() for c, k in tm.columns.items()
                   if k == "patient.precautions")
    changed = {k: [dict(r) for r in v] for k, v in tables.items()}
    for r in changed[t0]:
        r.pop(col, None)
    re = OnboardingSession.revise(pkg, changed)
    while (g := re.current()) is not None:
        re.submit({q.id: None for q in g.questions})
    new = re.finalize(day=3)
    assert not new.proof["passed"] and any("안전" in f for f in new.proof["failures"])
    assert col not in new.mapping.tables[t0].columns


# ---------------- 질문 흐름 (의료정보 A-3·A-4) ----------------

def test_partial_save_and_code_master_bulk_import():
    from turnaround.agent.onboarding_flow import OnboardingSession, oracle
    h, ev = _canonical(1)
    gold = gold_mapping("emr_c")
    s = OnboardingSession(export(h, ev, "emr_c"), hospital="H")
    while (g := s.current()) is not None and g.stage != "code":
        s.submit(oracle(gold, g))
    g = s.current()
    first = g.questions[0]
    s.submit({first.id: oracle(gold, g)[first.id]})  # 일부만 답해도 저장된다
    assert s.current().stage == "code" and first.id not in {q.id for q in s.current().questions}
    n = s.import_code_master({k: dict(v) for k, v in gold.codes.items()})
    assert n > 0
    while (g := s.current()) is not None:
        s.submit(oracle(gold, g))
    from turnaround.ontology.mapping import apply_mapping
    out = apply_mapping(s.mapping, export(h, ev, "emr_c"))
    assert sorted(map(_key, out.events)) == sorted(map(_key, ev))


def test_none_is_not_approval_for_safety_code_confirmation():
    from turnaround.agent.onboarding_flow import OnboardingSession, oracle
    h, ev = _canonical(1)
    gold = gold_mapping("emr_a")
    s = OnboardingSession(export(h, ev, "emr_a"), hospital="H")
    g = s.current()
    safety = [q for q in g.questions if "안전 속성" in q.why]
    assert safety
    s.submit({q.id: None for q in safety})
    assert {q.id for q in safety} <= {q.id for q in s.current().questions}  # 아직 답하지 않은 것으로 남는다


# ---------------- 의미 뒤바뀜 (온톨로지 B-2) ----------------

@pytest.mark.parametrize("key,a,b", [("patient.gender", "M", "F"), ("room_type", "negative_pressure", "multi")])
def test_swapped_code_meaning_fails_plausibility(key, a, b):
    from turnaround.agent.onboarding_flow import replay_proof
    h, ev = _canonical(2)
    tables, gold = export(h, ev, "emr_b"), gold_mapping("emr_b")
    swap = {k: ({src: (b if v == a else a if v == b else v) for src, v in cm.items()} if k == key else cm)
            for k, cm in gold.codes.items()}
    proof, _, _ = replay_proof(gold.model_copy(update={"codes": swap}), tables, day=3)
    assert not proof["passed"] and proof["plausibility"], proof
    ok, _, _ = replay_proof(gold, tables, day=3)
    assert ok["passed"] and not ok["plausibility"]
