"""구독 경유 측정 실행기: 끝까지 잰 모델만 건너뛴다."""

import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("run_claudesub", Path(__file__).resolve().parents[1] / "scripts" / "run_claudesub.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)


def _w(d, name, r):
    (d / name).write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")


def test_only_fully_measured_models_are_skipped(tmp_path):
    parts = ["injection", "qa", "planning"]
    full = {"model": "claudesub:claude-haiku-5-5", "suite": "v2", **{p: {"stopped": None} for p in parts}}
    cut = {"model": "claudesub:claude-sonnet-5-5", "suite": "v2", "injection": {"stopped": None},
           "qa": {"stopped": "계정 잔액·한도 소진"}, "planning": {"stopped": None}}
    partial = {"model": "claudesub:claude-opus-5-5", "suite": "v2", "injection": {"stopped": None}}
    _w(tmp_path, "a_claudesub_h.json", full)
    _w(tmp_path, "b_claudesub_s.json", cut)
    _w(tmp_path, "c_claudesub_o.json", partial)
    (tmp_path / "d_claudesub_bad.json").write_text("{", encoding="utf-8")
    assert rc.done_models(tmp_path, parts) == {"claudesub:claude-haiku-5-5"}


def test_api_key_is_removed_and_ack_set(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(rc, "OUT", tmp_path)
    monkeypatch.setattr(rc, "MODELS", [])
    import os
    assert rc.main(["--models", "", "--no-git"]) == 0  # 잴 모델이 없으면 바로 끝난다
    assert "ANTHROPIC_API_KEY" not in os.environ and os.environ["TURNAROUND_EXTERNAL_LLM_ACK"] == "synthetic-only"


def test_other_parts_do_not_overwrite_default_result_file():
    assert rc.result_name("2026-11-09", "claudesub:claude-opus-5-5", rc.PARTS.split(",")) ==         "2026-11-09_claudesub_claude-opus-5-5.json"
    assert rc.result_name("2026-11-09", "claudesub:claude-opus-5-5", ["exception", "explain"]) ==         "2026-11-09_claudesub_claude-opus-5-5_exception-explain.json"
