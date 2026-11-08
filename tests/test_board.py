"""점수판과 에이전트 없음 기준선 테스트."""

import json
from pathlib import Path

from turnaround.evaluation.baselines import no_agent_baseline
from turnaround.evaluation.board import HEADER, append_results_row, run_board, scenario_set_hash

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "evals" / "scenarios" / "dev"


def test_baseline_reports_segments_with_denominator():
    b = no_agent_baseline(range(0, 5))
    assert b["n_discharges"] == 150
    for key in ("pre_departure_min", "bed_prep_min", "cleaning_request_delay_min"):
        assert b[key]["p50"] <= b[key]["p90"]
    assert 0 <= b["noon_departure_ratio"] <= 1
    assert b["assumptions"] == []  # 기존 방식이라 개입 가정 없음


def test_scenario_set_hash_is_stable_and_content_sensitive(tmp_path):
    h1 = scenario_set_hash(DEV)
    assert h1 == scenario_set_hash(DEV)
    for p in DEV.glob("*.yaml"):
        (tmp_path / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    assert scenario_set_hash(tmp_path) == h1
    first = sorted(tmp_path.glob("*.yaml"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n# 변경\n", encoding="utf-8")
    assert scenario_set_hash(tmp_path) != h1


def test_board_marks_unimplemented_scenarios_and_reports_safety():
    r = run_board(DEV, seeds=range(0, 10), commit="test")
    assert r["split"] == "dev"
    assert r["llm"] == "none"
    assert r["scenarios"]["total"] == 30
    assert r["scenarios"]["passed"] == 6  # 3단계 사가로 채점 가능한 C01, C02, C03, C05, E01, E03
    assert r["scenarios"]["not_implemented"] == 24
    assert r["scenarios"]["labels_approved"] == 30  # 사용자 승인 (2026-11-08)
    assert r["scenarios"]["passed"] + r["scenarios"]["failed"] + r["scenarios"]["not_implemented"] == 30
    s1 = r["safety"]["S1"]
    assert s1["violations"] == 0 and s1["denominator"] > 0 and s1["upper_bound_95"] > 0
    assert r["safety"]["S2"]["denominator"] == 30  # 시드 10개 × 격리 퇴실 3건
    assert r["safety"]["S4"] == {"leaks": 0, "cuts": 150, "source": "event_store_noisy_replay"}
    json.dumps(r, ensure_ascii=False)  # 직렬화 가능


def test_results_row_is_appended_under_scoreboard_header(tmp_path):
    md = tmp_path / "RESULTS.md"
    md.write_text("# 결과\n\n## 점수판 기록 v1 (옛 표)\n| a |\n", encoding="utf-8")
    r = run_board(DEV, seeds=range(0, 3), commit="abc1234")
    append_results_row(md, r)
    append_results_row(md, r)
    text = md.read_text(encoding="utf-8")
    assert text.count(HEADER) == 1
    assert text.count("| abc1234 |") == 2
