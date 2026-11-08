"""9단계 화면 목업 데이터와 사람 개입 지표 (EVL-5, H1, H2, SEC-2)."""

import py_compile
from datetime import time
from pathlib import Path

import pytest

from turnaround.evaluation.human_load import human_load
from turnaround.gateway import load_policy
from turnaround.ui.demo import bed_board, build_demo, review_queue, vendor_view, ward_requests


@pytest.fixture(scope="module")
def demo():
    return build_demo(seed=42, until=time(10, 0))


def test_demo_replays_through_fhir_adapter(demo):
    assert demo.fhir_entries > demo.store.last_seq() / 2 > 0
    assert len(bed_board(demo)) == 24


def test_vendor_sees_only_bed_eta_type(demo):
    for row in vendor_view(build_demo(seed=42, until=time(14, 0))):
        assert set(row) == {"bed_id", "eta", "cleaning_type"}


def test_review_cards_are_complete(demo):
    """H2: 근거(사유, 설명), 대안(최대 2개), 마감이 있다."""
    for c in review_queue(demo):
        assert c["reasons"] and c["rationale"] and c["due"] and len(c["alternatives"]) <= 2


def test_ward_requests_have_bed(demo):
    assert all(r["bed"] for r in ward_requests(demo))


def test_human_load_splits_judgement_and_accountable(demo):
    hl = human_load(demo.store.read())
    assert hl["admission_cases"] > 0 and hl["accountable_per_case"] >= 0
    assert hl["fully_automatic_ratio"] in (None, 0.0)  # 자율화 1단계: 모든 확정에 담당 확인이 있다


@pytest.mark.slow
def test_autonomy_stage2_reduces_judgement_interventions():
    """H1: 단계가 오르면 판단 개입이 준다 (책임 개입은 그대로)."""
    p = load_policy()
    p2 = p.model_copy(update={"autonomy_stage": 2, "promoted_departments": ["소화기내과", "일반외과", "정형외과", "호흡기내과"]})
    a = human_load(build_demo(seed=42, until=time(18), policy=p).store.read())
    b = human_load(build_demo(seed=42, until=time(18), policy=p2).store.read())
    assert b["judgement_per_case"] < a["judgement_per_case"]
    assert b["fully_automatic_ratio"] > 0


def test_app_compiles():
    py_compile.compile(str(Path(__file__).resolve().parents[1] / "src" / "turnaround" / "ui" / "app.py"), doraise=True)
