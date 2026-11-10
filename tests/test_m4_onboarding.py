"""M4 B: 병원 기록으로 스스로 세팅 (로드맵 07, DECISIONS 2026-11-09).

숨은 규칙을 가진 합성 병원의 과거 기록(이벤트 내보내기)만 보고 병원 프로필 초안을 만든다.
판정은 결정적 통계(CLAUDE.md 2번). 확신이 낮은 항목은 사람에게 묻는 질문으로 남긴다.
"""

import yaml

from turnaround.agent.onboarding import infer_profile
from turnaround.gateway import load_policy
from turnaround.sim.history import HiddenRules, hospital_history


def test_history_is_synthetic_events_only():
    rules = HiddenRules.random(seed=1)
    ev = hospital_history(rules, seed=1, days=30)
    assert ev and all(e.payload.get("name") is None for e in ev)  # 이름 없음
    assert {e.type.value for e in ev} >= {"admission_requested", "match_committed", "patient_departed"}


def test_onboarding_recovers_hidden_rules_on_most_hospitals():
    items = ("assignment_timing", "ward_confirmation", "departure_due_at", "lounge_enabled", "cleaning_split",
             "admission_window", "dept_ward")
    hits = {k: 0 for k in items}
    n = 20
    for seed in range(n):
        rules = HiddenRules.random(seed=seed)
        out = infer_profile(hospital_history(rules, seed=seed, days=30))
        for k in items:
            hits[k] += out.matches(rules, k)
    for k in items:
        assert hits[k] / n >= 0.9, (k, hits[k])


def test_low_evidence_becomes_a_question_not_a_guess():
    rules = HiddenRules.random(seed=3)
    out = infer_profile(hospital_history(rules, seed=3, days=2))
    assert out.questions  # 기록이 적으면 묻는다
    assert all(q["item"] and q["why"] for q in out.questions)


def test_inferred_profile_loads_as_a_policy(tmp_path):
    rules = HiddenRules.random(seed=5)
    out = infer_profile(hospital_history(rules, seed=5, days=30))
    f = tmp_path / "onboarded.yaml"
    f.write_text(yaml.safe_dump(out.profile_yaml(), allow_unicode=True), encoding="utf-8")
    pol = load_policy(profile=str(f))
    assert pol.hospital_profile.ward_confirmation.mode == out.values["ward_confirmation"]
    assert pol.hospital_profile.assignment.timing == out.values["assignment_timing"]


def test_messy_hospital_gets_questions_not_wrong_answers():
    wrong = asked = 0
    for seed in range(30):
        rules = HiddenRules.random(seed=seed).model_copy(update={"compliance": 0.6})
        out = infer_profile(hospital_history(rules, seed=seed, days=30))
        for k in ("assignment_timing", "ward_confirmation"):
            if k in out.values and not out.matches(rules, k):
                wrong += 1
            asked += k not in out.values
    assert wrong == 0 and asked >= 40
