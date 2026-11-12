"""화면이 읽는 측정 요약이 원본(evals/results/)과 같은지 확인한다."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not (ROOT / "evals" / "results" / "final" / "m1.json").exists(), reason="측정 원본 없음")
def test_summary_matches_sources():
    import importlib.util
    spec = importlib.util.spec_from_file_location("summ", ROOT / "scripts" / "summarize_results.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    saved = yaml.safe_load((ROOT / "config" / "results_summary.yaml").read_text(encoding="utf-8"))
    assert saved == m.summarize(), "python scripts/summarize_results.py 로 요약을 다시 만드세요"


def test_summary_headline_values():
    s = yaml.safe_load((ROOT / "config" / "results_summary.yaml").read_text(encoding="utf-8"))
    assert s["note"] == "합성 데이터 기준 추정"
    assert s["no_touch"]["ratio"] == 0.823  # 2026-11-12 재측정 (README와 같은 파일)
    assert s["entry_wait"]["change"] == -0.144
    assert s["entry_breakdown"]["change"] == -0.32 and s["entry_breakdown"]["ready_on"] > s["entry_breakdown"]["ready_off"]
    assert s["safety"]["violations"] == 0 and s["safety"]["holdout"]["violations"] == 0


def test_summary_keeps_realistic_breakdown_separate():
    s = yaml.safe_load((ROOT / "config" / "results_summary.yaml").read_text(encoding="utf-8"))
    r = s["entry_breakdown_realistic"]
    assert s["entry_breakdown"]["off_min"] == 33.7  # 기존 키는 그대로
    assert r["seeds"] == "0~49" and r["safety_violations_off"] == 0 and r["safety_violations_on"] == 0
    assert r["on_min"] < r["off_min"] and r["done_to_ready_on"] < r["done_to_ready_off"]
