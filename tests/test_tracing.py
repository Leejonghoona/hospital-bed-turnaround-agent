"""에이전트 추적 (DECISIONS 2026-11-10 MCP 서버와 에이전트 추적). 합성 데이터만, 네트워크 없음."""

from __future__ import annotations

import json
from datetime import time

import pytest

pytest.importorskip("opentelemetry.sdk")

from turnaround import tracing  # noqa: E402
from turnaround.agent.llm import FakeLLM, GuardedLLM, LLMError, Usage  # noqa: E402

CTX = json.dumps({"candidates": [{"bed_id": "B1", "score": 3.0}, {"bed_id": "B2", "score": 5.0}]})


def test_off_by_default():
    assert not tracing.enabled()
    s = tracing.span("x", a=1)
    assert s is tracing.NOOP
    with s as h:
        h.set(b=2)  # 꺼져 있으면 아무것도 하지 않는다
    FakeLLM().complete_json("choose_bed", "", CTX)  # 꺼진 상태 호출은 기록되지 않는다
    with tracing.capture() as rec:
        pass
    assert rec.spans == []


def test_env_switch(monkeypatch):
    assert not tracing.env_enabled({})
    assert tracing.env_enabled({"TURNAROUND_TRACE": "1"})
    assert not tracing.env_enabled({"TURNAROUND_TRACE": "0"})


def test_llm_span_attributes():
    with tracing.capture() as rec:
        out = FakeLLM().complete_json("choose_bed", "", CTX)
    assert out["bed_id"] == "B2"
    assert not tracing.enabled()  # 문맥이 끝나면 다시 꺼진다
    [s] = rec.spans
    a = s["attrs"]
    assert s["name"] == "llm.call"
    assert a["llm.task"] == "choose_bed" and a["llm.role"] == "planner"
    assert a["llm.model"] == "fake" and a["llm.external"] is False
    assert a["llm.external_blocked"] is False
    assert a["llm.latency_ms"] >= 0
    assert a["llm.input_tokens"] == 0 and a["llm.output_tokens"] == 0
    assert s["duration_ms"] >= 0


def test_llm_error_recorded():
    with tracing.capture() as rec:
        with pytest.raises(LLMError):
            FakeLLM(fail=True).complete_json("extract", "", "{}")
    [s] = rec.spans
    assert s["attrs"]["llm.error"] == "LLMError" and s["attrs"]["llm.role"] == "extractor"
    assert s["status"] == "ERROR"


class _TokenLLM:
    """토큰 수를 돌려주는 가짜 외부 모델 (네트워크 없음)."""

    name, model, external = "ext:test", "test-model", True

    def __init__(self):
        self.usage = Usage()

    @tracing.traced_llm
    def complete_json(self, task, system, user):
        self.usage.calls += 1
        self.usage.input_tokens += 120
        self.usage.output_tokens += 30
        return {"rationale": "ok"}


def test_guarded_external_blocked_single_span():
    llm = GuardedLLM(_TokenLLM(), data_class="hospital")  # 정책 external_llm=deny → 병원 데이터는 막힌다
    with tracing.capture() as rec:
        with pytest.raises(PermissionError):
            llm.complete_json("explain_choice", "", "{}")
    [s] = rec.spans
    assert s["attrs"]["llm.external_blocked"] is True and s["attrs"]["llm.external"] is True


def test_guarded_external_tokens_single_span():
    llm = GuardedLLM(_TokenLLM(), data_class="synthetic")
    with tracing.capture() as rec:
        llm.complete_json("explain_choice", "", "{}")
    [s] = rec.spans  # 관문과 안쪽 모델이 span을 두 번 만들지 않는다
    a = s["attrs"]
    assert a["llm.input_tokens"] == 120 and a["llm.output_tokens"] == 30
    assert a["llm.model"] == "test-model" and a["llm.role"] == "planner"


def test_nested_spans_and_case_attr():
    with tracing.capture() as rec:
        with tracing.span("agent.plan", case_id="SYN-A001") as h:
            FakeLLM().complete_json("choose_bed", "", CTX)
            h.set(bed_id="B2", candidates=["B1", "B2"], meta={"k": 1}, none=None)
    plan = next(s for s in rec.spans if s["name"] == "agent.plan")
    llm = next(s for s in rec.spans if s["name"] == "llm.call")
    assert llm["parent_id"] == plan["span_id"] and llm["trace_id"] == plan["trace_id"]
    assert plan["attrs"]["case_id"] == "SYN-A001" and plan["attrs"]["candidates"] == ["B1", "B2"]
    assert "none" not in plan["attrs"] and json.loads(plan["attrs"]["meta"]) == {"k": 1}
    assert tracing.case_trace(rec.spans, "SYN-A001")[0]["name"] == "agent.plan"
    assert [s["name"] for s in tracing.case_trace(rec.spans, "SYN-A001")] == ["agent.plan", "llm.call"]


def test_gateway_outcome_classes():
    from turnaround.gateway import Decision
    assert tracing.gateway_outcome(Decision("committed", "proposal", "auto")) == "auto"
    assert tracing.gateway_outcome(Decision("awaiting_review", "proposal", "review", ["x"])) == "human"
    assert tracing.gateway_outcome(Decision("awaiting_manager", "proposal", "ward_confirm")) == "human"
    assert tracing.gateway_outcome(Decision("rejected", "proposal", None, ["gender_room"])) == "blocked"
    assert tracing.gateway_outcome(Decision("executed", "action", "auto"), action="escalate") == "human"
    assert tracing.gateway_outcome(Decision("executed", "action", "post_report"), action="notify_ward") == "auto"
    assert tracing.gateway_outcome(Decision("awaiting_approval", "action", "human")) == "human"
    assert tracing.gateway_outcome(Decision("duplicate", "proposal")) == "duplicate"
    assert tracing.gateway_outcome(Decision("committed", "commit"), actor="human") == "human"
    assert tracing.gateway_outcome(Decision("committed", "commit"), actor="agent") == "auto"


def test_demo_day_emits_agent_and_gateway_spans():
    from turnaround.ui.demo import build_demo
    with tracing.capture() as rec:
        st = build_demo(seed=0, day=2, until=time(10, 0), via_fhir=False)
    names = {s["name"] for s in rec.spans}
    assert {"agent.plan", "gateway.submit", "llm.call"} <= names
    subs = [s for s in rec.spans if s["name"] == "gateway.submit"]
    assert all(s["attrs"]["gateway.outcome"] in {"auto", "human", "blocked", "duplicate"} for s in subs)
    assert all("case_id" in s["attrs"] and "gateway.status" in s["attrs"] for s in subs)
    plans = [s for s in rec.spans if s["name"] == "agent.plan"]
    assert all(s["attrs"]["agent.mode"] in {"explain", "select", "rule", "fallback"} for s in plans)
    # 추적은 결과를 바꾸지 않는다: 같은 재생을 끈 채로 돌린 이벤트와 같다
    st2 = build_demo(seed=0, day=2, until=time(10, 0), via_fhir=False)
    assert [e.event_id for e in st.store.read()] == [e.event_id for e in st2.store.read()]
    summary = tracing.summarize(rec.spans)
    assert summary["agent_decisions"] == len(subs) + summary["actions"]["n"]
    assert abs(sum(summary["outcome_share"].values()) - 1.0) < 1e-3
    pc = summary["proposal_cases"]
    assert pc["n"] == len({s["attrs"]["case_id"] for s in subs}) == pc["auto"] + pc["human"] + pc["blocked"]


def test_jsonl_file_export(tmp_path):
    path = tmp_path / "t.jsonl"
    tracing.enable(path=str(path))
    try:
        FakeLLM().complete_json("parse_question", "", json.dumps({"question": "빈 병상"}))
    finally:
        tracing.disable()
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert rows and rows[0]["name"] == "llm.call" and rows[0]["attrs"]["llm.role"] == "qa"
    assert not tracing.enabled()


def test_langfuse_config_no_network():
    assert tracing.langfuse_config({}) is None
    cfg = tracing.langfuse_config({"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
                                   "LANGFUSE_HOST": "https://lf.example"})
    assert cfg["endpoint"] == "https://lf.example/api/public/otel/v1/traces"
    assert cfg["headers"]["Authorization"].startswith("Basic ")
