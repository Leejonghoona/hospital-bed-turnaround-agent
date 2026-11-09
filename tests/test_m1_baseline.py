"""M1 1층: 평범한 입원은 에이전트가 배정·확정·통보 (로드맵 07, DECISIONS 2026-11-09).

- 생성기 "현실 구성" 모드: 검토 사례를 고정 10건이 아니라 비율로 넣는다.
- 지표: 사람 손 없이 확정까지 간 입원 비율(일반 경로 처리율), 사례당 사람 동작.
"""

from datetime import datetime, time

from turnaround.events import Event, EventType
from turnaround.evaluation.human_load import human_load
from turnaround.sim import GeneratorConfig, generate


def test_realistic_mix_uses_rates_not_fixed_special_cases():
    cfg = GeneratorConfig(days=10, discharges_per_day=3, admission_count=30, admission_mix="realistic")
    kinds = []
    for seed in range(20):
        h = generate(seed=seed, config=cfg)
        assert len(h.admissions) == 30
        kinds.append(sum(1 for a in h.admissions if a.patient.organism or a.from_icu or a.wants_single_room
                         or a.patient.age is not None and a.patient.age < 15
                         or (a.integrated_care_requested and a.integrated_care_consent is None)))
    # 시험 구성은 30건 중 늘 9건 이상이 검토 사례, 현실 구성은 평균이 훨씬 낮고 시드마다 달라진다
    assert sum(kinds) / len(kinds) < 9 and len(set(kinds)) > 1


def test_test_mix_is_unchanged_by_default():
    h = generate(seed=42)
    assert h.config.admission_mix == "test"
    assert sum(1 for a in h.admissions if a.patient.organism) >= 3


def _e(seq, case, etype, minute, source="gateway", **p):
    return Event(event_id=f"e{seq}", case_id=case, type=etype, occurred_at=datetime(2026, 3, 3, 9, minute),
                 observed_at=datetime(2026, 3, 3, 9, minute), source=source, payload=p, seq=seq)


def test_human_load_counts_agent_commit_as_no_touch():
    ev = [_e(1, "A1", EventType.ADMISSION_REQUESTED, 0, "his"), _e(2, "A2", EventType.ADMISSION_REQUESTED, 0, "his"),
          _e(3, "A3", EventType.ADMISSION_REQUESTED, 0, "his"),
          _e(4, "A1", EventType.MATCH_COMMITTED, 5, committed_by="agent"),
          _e(5, "A2", EventType.MANAGER_APPROVED, 6, "human"),
          _e(6, "A2", EventType.MATCH_COMMITTED, 6, committed_by="human")]
    r = human_load(ev)
    assert r["no_touch_committed"] == 1
    assert r["no_touch_ratio_of_admissions"] == round(1 / 3, 3)
    assert r["by_kind"]["agent_commit"] == 1 and r["by_kind"]["official_commit"] == 1


def test_field_profile_does_not_review_close_scores_but_legacy_does():
    from turnaround.gateway import load_policy
    assert "close_scores" in load_policy().review_reasons
    field = load_policy(profile="field_default")
    assert "close_scores" not in field.review_reasons
    assert {"isolation", "single_room_preference", "no_bed", "missing_input"} <= set(field.review_reasons)
