"""평가 골격: 회귀 시나리오 라벨 30개의 스키마와 구성 검사."""

from collections import Counter
from pathlib import Path

import pytest

from turnaround.evaluation.scenario import (
    ACTION_TYPES,
    Category,
    Tier,
    load_scenarios,
)
from turnaround.sim import generate

DEV_DIR = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "dev"
EXPECTED_PER_CATEGORY = {
    Category.SAFETY: 6, Category.GATEWAY: 5, Category.SAGA: 5,
    Category.TIER: 6, Category.DELAY: 4, Category.SECURITY: 4,
}
METRIC_IDS = {
    "S1", "S2", "S3", "S4", "D1", "D2", "D3", "G1", "G2", "G3", "G4", "T1", "T2", "T3",
    "E1", "E2", "E3", "P1", "P2", "P3", "P4", "I1", "I2", "C1", "O1", "O2", "O3", "O4", "H1", "H2", "M1",
}


@pytest.fixture(scope="module")
def scenarios():
    return load_scenarios(DEV_DIR)


def test_thirty_scenarios_load(scenarios):
    assert len(scenarios) == 30


def test_ids_are_unique_and_match_filenames(scenarios):
    ids = [s.id for s in scenarios]
    assert len(ids) == len(set(ids))
    assert sorted(p.stem for p in DEV_DIR.glob("*.yaml")) == sorted(ids)


def test_category_counts(scenarios):
    assert Counter(s.category for s in scenarios) == EXPECTED_PER_CATEGORY


def test_id_prefix_matches_category(scenarios):
    prefix = {Category.SAFETY: "A", Category.GATEWAY: "B", Category.SAGA: "C",
              Category.TIER: "D", Category.DELAY: "E", Category.SECURITY: "F"}
    for s in scenarios:
        assert s.id.startswith(prefix[s.category]), s.id


def test_design_poc_regression_list_is_covered(scenarios):
    """설계 문서 PoC 각주의 회귀 시나리오 목록이 모두 있는지."""
    required_tags = {
        "isolation_order_added", "same_name", "discharge_cancel", "admission_cancel",
        "unregistered_discharge", "guardian_delay", "cleaning_delay", "terminal_cleaning_missing",
        "concurrent_match", "unauthorized_query", "injection",
    }
    tags = {t for s in scenarios for t in s.tags}
    assert required_tags <= tags, required_tags - tags


def test_every_scenario_has_gold_and_provenance(scenarios):
    for s in scenarios:
        assert s.gold.must_actions or s.gold.must_not, s.id
        assert s.design_ref.strip(), s.id
        assert s.provenance.rule_source.strip(), s.id
        assert s.provenance.author in {"claude-draft", "human"}


def test_actions_use_known_vocabulary(scenarios):
    for s in scenarios:
        for a in s.gold.must_actions + s.gold.must_not:
            assert a.type in ACTION_TYPES, (s.id, a.type)


def test_invariant_refs_are_known_metrics(scenarios):
    for s in scenarios:
        assert set(s.metrics) <= METRIC_IDS, s.id


def test_acceptable_beds_exist_in_synthetic_layout(scenarios):
    bed_ids = {b.id for b in generate(seed=0).beds}
    for s in scenarios:
        for beds in s.gold.acceptable_beds.values():
            assert set(beds) <= bed_ids, s.id


def test_review_cases_are_labelled_review(scenarios):
    """D군(확인 등급)은 등급 라벨이 반드시 있어야 한다."""
    for s in scenarios:
        if s.category is Category.TIER:
            assert s.gold.tier_by_case, s.id


def test_isolation_related_scenarios_forbid_downgrade(scenarios):
    for s in scenarios:
        if "isolation_order_added" in s.tags or "injection" in s.tags:
            assert any(t is Tier.REVIEW for t in s.gold.tier_by_case.values()), s.id


def test_status_is_draft_or_approved(scenarios):
    assert {s.status for s in scenarios} <= {"draft", "approved"}


def test_review_sheet_is_up_to_date():
    """docs/라벨_검토표.md가 YAML과 어긋나면 실패한다."""
    from turnaround.evaluation.review_sheet import render

    sheet = Path(__file__).resolve().parents[1] / "docs" / "라벨_검토표.md"
    assert sheet.read_text(encoding="utf-8") == render(DEV_DIR)


# ---------- 홀드아웃 (LP-16~22 신규 시나리오, 30개에 포함하지 않음) ----------
HOLDOUT_DIR = DEV_DIR.parent / "holdout"


def test_holdout_scenarios_load_and_are_separate():
    hold = load_scenarios(HOLDOUT_DIR)
    assert [s.id for s in hold] == ["H01", "H02", "H03", "H04", "H05"]
    assert all(s.fixture.get("split") == "holdout" for s in hold)
    assert not {s.id for s in hold} & {p.stem for p in DEV_DIR.glob("*.yaml")}


def test_holdout_beds_exist():
    bed_ids = {b.id for b in generate(seed=0).beds}
    for s in load_scenarios(HOLDOUT_DIR):
        for beds in s.gold.acceptable_beds.values():
            assert set(beds) <= bed_ids, s.id
