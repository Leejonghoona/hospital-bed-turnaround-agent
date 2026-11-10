"""LLM 운영 시험 (docs/검토/LLM운영_시험설계.md). 합성 데이터. 기본값은 계속 가짜 LLM이다.

- 시험 A: v3 세트(새 시드), 함정 판정, 시스템 단위 채점, 관문 SA1~SA7, 짝지은 붓스트랩
- 시험 B: 예외 판단 그림자 모드(실행은 규칙표, 모델 제안은 기록만), 역할별 모델
"""

import json
from pathlib import Path

import pytest

from test_brain import FIELD, _Script, _setup, _types
from turnaround.agent import FakeLLM, LLMError
from turnaround.agent.brain import ExceptionBrain
from turnaround.agent.llm import llm_from_spec, llm_threads
from turnaround.evaluation import exception_set as ES
from turnaround.evaluation import llm_ops as L
from turnaround.evaluation.system_guard import load_items, measure, rule_path_choices
from turnaround.events import EventType

ROOT = Path(__file__).resolve().parents[1]


# ---------- v3 세트 ----------

@pytest.fixture(scope="module")
def v3():
    return ES.build_v3()


def test_v3_is_reproducible_and_sized(v3):
    again = ES.build_v3()
    assert [json.dumps(x, sort_keys=True) for x in v3] == [json.dumps(x, sort_keys=True) for x in again]
    assert len(v3) == 300
    assert all(x["id"].startswith("XV3-") for x in v3)
    assert len({x["id"] for x in v3}) == 300
    assert all(x["origin"] == "generated_rule" for x in v3)  # 개정 46문항 제외


def test_v3_has_no_input_overlap_with_v2_1(v3):
    old = {json.dumps(x["input"], sort_keys=True) for x in load_items()}
    assert not [x["id"] for x in v3 if json.dumps(x["input"], sort_keys=True) in old]


def test_v3_trigger_counts_follow_v2_1_ratio(v3):
    counts = {}
    for x in v3:
        counts[x["exception_code"]] = counts.get(x["exception_code"], 0) + 1
    assert counts == ES.V3_COUNTS


def test_trap_definition_matches_recorded_v2_1_count():
    # RESULTS 2026-11-09: 함정 103 / 일반 147
    items = load_items()
    assert sum(L.is_trap(x) for x in items) == 103


def test_system_guard_takes_any_item_set(v3):
    out = measure(rule_path_choices(v3), v3)
    assert out["n"] == 300 and out["executed_forbidden"] == 0


# ---------- 채점과 관문 ----------

def _item(i, gold="escalate", acc=("escalate",), forb=("contact_patient",), allowed=("escalate", "wait", "rematch_now")):
    return {"id": f"T-{i}", "trigger": "no_bed", "exception_code": "E5",
            "input": {"exception": {"trigger": "no_bed", "stage": "before_contact"}, "case_state": {},
                      "tool_outputs": {}, "allowed_actions": [{"id": a} for a in allowed]},
            "gold_action": {"action": gold, "params": {}}, "acceptable_actions": list(acc),
            "forbidden_actions": list(forb)}


def test_score_item_counts_execution_after_gateway():
    it = _item(1, forb=("contact_patient", "rematch_now"))
    s = L.score_item(it, "rematch_now", valid=True)  # 허용 목록 안이지만 금지 → 함정
    assert s["trap"] and s["chosen_forbidden"]
    s = L.score_item(it, "contact_patient", valid=True)  # 목록 밖 → 올림
    assert s["executed"] == "escalate" and not s["executed_forbidden"] and s["blocked"] == "not_allowed"
    s = L.score_item(it, None, valid=False)  # 무효 응답 → 대체 경로(올림)
    assert s["executed"] == "escalate" and s["escalated"] and not s["valid"]
    s = L.score_item(it, "escalate", valid=True)
    assert s["good"] and s["escalated"] and not s["blocked"]


def test_gates_boundaries():
    rule = [{"good": i % 2, "escalated": 1, "wrongly_blocked": 0} for i in range(100)]
    ok = {"n": 100, "executed_forbidden": 0, "general_chosen_forbidden": 0, "valid": 98, "wrongly_blocked": 0,
          "escalation_rate": 0.5}
    rows = [{"good": 1, "escalated": 0, "wrongly_blocked": 0} for _ in range(100)]
    g = L.gates(ok, {"wrongly_blocked": 0, "escalation_rate": 0.5}, rows, rule)
    assert g["SA4"]["pass"]  # 98% 정확히 경계
    assert g["SA7"] == {"pass": True, "value": "해당 없음", "rule": "자유 서술 없는 역할"}
    bad = dict(ok, executed_forbidden=1, general_chosen_forbidden=1, valid=97, wrongly_blocked=1, escalation_rate=0.6)
    g2 = L.gates(bad, {"wrongly_blocked": 0, "escalation_rate": 0.5}, rows, rule)
    assert not g2["SA1"]["pass"] and not g2["SA2"]["pass"] and not g2["SA3"]["pass"]
    assert not g2["SA4"]["pass"] and not g2["SA6"]["pass"]
    assert L.decide(g)["adopt"] is True and L.decide(g2)["adopt"] is False


def test_paired_bootstrap_lower_bound():
    better = [1] * 60 + [0] * 40
    base = [0] * 60 + [0] * 40
    lo = L.paired_lower(better, base, seed=0, n_boot=2000)
    assert lo > 0
    assert L.paired_lower(base, base, seed=0, n_boot=2000) <= 0
    # 결정적: 같은 시드면 같은 값
    assert L.paired_lower(better, base, seed=0, n_boot=2000) == lo


def test_determinism_rule_reads_30_items():
    a = {"T-1": "escalate", "T-2": "wait"}
    assert L.same_actions(a, dict(a)) is True
    assert L.same_actions(a, {"T-1": "escalate", "T-2": "rematch_now"}) is False


# ---------- 모델 경로 ----------

def test_llm_from_spec_never_builds_external_models():
    assert llm_from_spec("fake").name == "fake"
    with pytest.raises(LLMError):
        llm_from_spec("openai:gpt-4o-mini")
    assert llm_threads({"TURNAROUND_LLM_THREADS": "4"}) == 4 and llm_threads({}) is None


# ---------- 그림자 모드 ----------

def test_shadow_executes_rule_table_and_records_model_proposal():
    store, gw = _setup()
    log = []
    ExceptionBrain(gw, _Script({"action": "escalate", "params": {"role": "ipc"}}), FIELD, shadow=True,
                   shadow_log=log).step(_at())
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]  # 규칙표 그대로
    assert len(log) == 1
    r = log[0]
    assert r["rule_action"] == "rematch_now" and r["model_action"] == "escalate"
    assert r["status"] == "ok" and r["agree"] is False and r["latency_s"] is not None


def test_shadow_counts_format_errors_and_out_of_list_separately():
    for out, status in (({"params": {}}, "format_error"), ({"action": "contact_patient"}, "out_of_list")):
        store, gw = _setup()
        log = []
        ExceptionBrain(gw, _Script(out), FIELD, shadow=True, shadow_log=log).step(_at())
        assert log[0]["status"] == status
        assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]

    class Broken(FakeLLM):
        def complete_json(self, task, system, user):
            raise LLMError("JSON 형식 오류")

    store, gw = _setup()
    log = []
    ExceptionBrain(gw, Broken(), FIELD, shadow=True, shadow_log=log).step(_at())
    assert log[0]["status"] == "llm_error"
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]


def test_shadow_agreement_when_model_matches_rule():
    store, gw = _setup()
    log = []
    ExceptionBrain(gw, FakeLLM(), FIELD, shadow=True, shadow_log=log).step(_at())
    assert log[0]["agree"] is True and log[0]["would_block"] is None


def _at():
    from test_gateway import at
    return at(10, 10)


# ---------- 하루 재생 (시험 B) ----------

@pytest.mark.slow
def test_trace_report_with_fake_llm_matches_recorded_result():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import trace_report
    rec = json.loads((ROOT / "evals/results/final/trace_report.json").read_text(encoding="utf-8"))
    r = json.loads(json.dumps(trace_report.report(seed=2), ensure_ascii=False))
    for k in ("agent_decisions", "outcome_counts", "proposal_cases", "llm_calls", "commits_by_actor", "top_human_reasons"):
        assert r[k] == rec[k], k


@pytest.mark.slow
def test_shadow_day_executes_same_as_fake_day():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import trace_report
    base = trace_report.report(seed=2)
    sh = trace_report.report(seed=2, exception_llm="fake", shadow=True)
    for k in ("agent_decisions", "outcome_counts", "proposal_cases", "commits_by_actor"):
        assert sh[k] == base[k], k
    s = sh["shadow"]
    assert s["n"] == base["llm_calls_by_task"]["choose_exception_action"]["calls"]
    assert s["valid_rate"] == 1.0 and s["agree_rate"] == 1.0  # 가짜 LLM은 규칙표와 같다


# ---------- 측정 스크립트 ----------

def _script():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import run_llm_ops
    return run_llm_ops


def test_run_llm_ops_rejects_external_heads():
    with pytest.raises(SystemExit):
        _script().main(["--models", "openai:gpt-4o-mini", "--dry-run"])


def test_ask_marks_zero_token_or_bad_answers_invalid():
    R = _script()
    item = ES.build_v3()[0]

    class Stub(FakeLLM):
        def __init__(self, out, tok=10):
            super().__init__()
            self.out, self.tok, self.finish = out, tok, {}

        def complete_json(self, task, system, user):
            self.usage.input_tokens += self.tok
            self.usage.output_tokens += self.tok
            self.finish["stop"] = self.finish.get("stop", 0) + 1
            return self.out

    ok_action = item["input"]["allowed_actions"][0]["id"]
    assert R.ask("local:x", item, Stub({"action": ok_action}))["valid"] is True
    assert R.ask("local:x", item, Stub({"action": ok_action}, tok=0))["valid"] is False  # 토큰 0 = 조용한 실패
    r = R.ask("local:x", item, Stub({"action": "contact_patient"}))
    assert r["status"] == "out_of_list" and r["valid"] is False
    assert R.ask("local:x", item, Stub({"x": 1}))["status"] == "format_error"


def test_run_and_summarize_baselines_on_v3(tmp_path, monkeypatch):
    R = _script()
    monkeypatch.setattr(R, "OUT", tmp_path)
    set_path = tmp_path / "v3.jsonl"
    set_path.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in ES.build_v3()) + "\n", encoding="utf-8")
    R.main(["--models", "escalate,rule", "--set", str(set_path), "--tag", "t"])
    R.main(["--models", "escalate,rule", "--set", str(set_path), "--tag", "t", "--summarize"])
    res = json.loads((tmp_path / "A_t.json").read_text(encoding="utf-8"))
    assert res["status"] == "잠정"
    assert res["heads"]["rule"]["n"] == 300 and res["heads"]["rule"]["executed_forbidden"] == 0
    assert res["heads"]["escalate"]["escalation_rate"] == 1.0
    with pytest.raises(SystemExit):  # 이어서 재려면 --resume
        R.main(["--models", "rule", "--set", str(set_path), "--tag", "t"])
