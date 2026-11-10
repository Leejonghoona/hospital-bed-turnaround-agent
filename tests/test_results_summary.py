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
    assert s["no_touch"]["ratio"] == 0.724
    assert s["entry_wait"]["change"] == -0.13
    assert s["safety"]["violations"] == 0 and s["safety"]["holdout"]["violations"] == 0
