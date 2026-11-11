"""예외 판단 머리의 제한된 도구 호출 루프 (DECISIONS 2026-11-12).

루프는 ExceptionBrain(tool_loop=True)일 때만 돈다. 최대 3단계, 허용 밖 도구·형식 오류·단계 초과는 사람에게 올림.
최종 행동은 지금처럼 게이트웨이가 다시 검사한다. 가짜 LLM(규칙표)은 도구를 부르지 않는 1단계라 결과가 같다.
"""

import json

from test_brain import FIELD, _setup, _types
from test_gateway import at
from turnaround.agent import FakeLLM
from turnaround.agent.brain import MAX_TOOL_STEPS, ExceptionBrain
from turnaround.events import EventType


class _Steps(FakeLLM):
    """단계마다 정해 둔 답을 차례로 낸다. 받은 입력을 남긴다."""

    def __init__(self, *outs):
        super().__init__()
        self.outs, self.seen = list(outs), []

    def complete_json(self, task, system, user):
        if task in ("choose_exception_step", "choose_exception_action"):
            self.seen.append((task, json.loads(user)))
            return self.outs.pop(0)
        return super().complete_json(task, system, user)


def _act(action, **params):
    return {"action": action, "params": params, "rationale": "후보 병상이 있음"}


def test_one_tool_call_then_action_is_executed_by_gateway():
    store, gw = _setup()
    llm = _Steps({"tool": "rematch_candidates", "args": {}}, _act("rematch_now"))
    ExceptionBrain(gw, llm, FIELD, tool_loop=True).step(at(10, 10))
    assert [t for t, _ in llm.seen] == ["choose_exception_step"] * 2
    first, second = llm.seen[0][1], llm.seen[1][1]
    assert "tool_outputs" not in first and first["tool_results"] == [] and first["step"] == 1
    assert {t["name"] for t in first["tools"]} == {"rematch_candidates", "ready_eta", "case_history"}
    res = second["tool_results"][0]
    assert res["tool"] == "rematch_candidates" and "rematch_candidates" in res["result"]
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]
    assert [e.payload["mode"] for e in _types(store, EventType.ACTION_PROPOSED)] == ["llm"]


def test_tool_results_reuse_precomputed_tool_outputs():
    store, gw = _setup()
    brain = ExceptionBrain(gw, _Steps(), FIELD, tool_loop=True)
    ex = brain.open_exceptions(store.read())[0]
    ctx = brain._input(ex, store.read(), at(10, 10))
    res = brain._tool_results(ctx, ex, store.read(), at(10, 10))
    merged = {**res["rematch_candidates"], **res["ready_eta"]}
    assert merged == ctx["tool_outputs"]  # 새 계산 없이 같은 값을 나눠 보여 준다
    hist = res["case_history"]["recent_events"]
    assert hist and all(set(h) == {"type", "min_ago"} for h in hist)
    assert "환자A1" not in json.dumps(res, ensure_ascii=False)


def test_exceeding_step_limit_falls_back_to_escalate():
    store, gw = _setup()
    llm = _Steps(*[{"tool": "case_history", "args": {}}] * (MAX_TOOL_STEPS + 1))
    brain = ExceptionBrain(gw, llm, FIELD, tool_loop=True)
    ex = brain.open_exceptions(store.read())[0]
    choice = brain.choose(ex, store.read(), at(10, 10))
    assert len(llm.seen) == MAX_TOOL_STEPS  # 상한을 넘겨 부르지 않는다
    assert choice.mode == "fallback" and choice.action == "escalate" and choice.loop_status == "step_limit"
    assert choice.params["role"] == "ipc" and len(choice.tool_calls) == MAX_TOOL_STEPS
    gw.submit_action(choice, at(10, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]


def test_unknown_tool_or_bad_format_falls_back():
    for bad, status in (({"tool": "official_commit", "args": {}}, "unknown_tool"),
                        ({"tool": "ready_eta", "args": "x"}, "format_error"),
                        ({"tool": "ready_eta", "action": "escalate"}, "format_error"),
                        ({"rationale": "?"}, "format_error")):
        store, gw = _setup()
        brain = ExceptionBrain(gw, _Steps(bad), FIELD, tool_loop=True)
        brain.step(at(10, 10))
        assert [e.payload["mode"] for e in _types(store, EventType.ACTION_PROPOSED)] == ["fallback"], bad
        assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"], bad
    store, gw = _setup()
    brain = ExceptionBrain(gw, _Steps({"tool": "nope"}), FIELD, tool_loop=True)
    c = brain.choose(brain.open_exceptions(store.read())[0], store.read(), at(10, 10))
    assert c.mode == "fallback" and c.loop_status == "unknown_tool" and c.tool_calls == []


def test_llm_failure_in_loop_falls_back():
    store, gw = _setup()
    ExceptionBrain(gw, FakeLLM(fail=True), FIELD, tool_loop=True).step(at(10, 10))
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]


def test_forbidden_action_chosen_by_loop_is_blocked_by_gateway():
    """연락 뒤 자동 재매칭은 루프가 도구를 보고 골라도 게이트웨이 단계 잠금이 막고, 대체 경로(올림)가 돈다."""
    store, gw = _setup(contacted=True)
    llm = _Steps({"tool": "rematch_candidates", "args": {}}, _act("rematch_now"))
    ExceptionBrain(gw, llm, FIELD, tool_loop=True).step(at(10, 10))
    rej = _types(store, EventType.ACTION_REJECTED)
    assert rej and rej[0].payload["reasons"] == ["stage_locked"]
    assert not _types(store, EventType.REMATCH_REQUESTED)
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["escalate"]
    assert [e.payload["mode"] for e in _types(store, EventType.ACTION_PROPOSED)] == ["llm", "fallback"]


def test_tool_calls_are_recorded_on_choice_and_records():
    store, gw = _setup()
    llm = _Steps({"tool": "rematch_candidates", "args": {}}, {"tool": "case_history", "args": {"limit": 3}},
                 _act("rematch_now"))
    brain = ExceptionBrain(gw, llm, FIELD, tool_loop=True)
    ex = brain.open_exceptions(store.read())[0]
    choice = brain.choose(ex, store.read(), at(10, 10))
    assert choice.loop_status == "ok" and choice.mode == "llm"
    assert [(c["step"], c["tool"]) for c in choice.tool_calls] == [(1, "rematch_candidates"), (2, "case_history")]
    assert choice.tool_calls[1]["args"] == {"limit": 3} and "recent_events" in choice.tool_calls[1]["result"]
    assert len(brain.records) == 1 + 3  # 전체 입력 1개 + 단계별 입력 3개 (감사·시험용)


def test_tool_calls_are_recorded_on_tracing_span():
    import pytest
    pytest.importorskip("opentelemetry.sdk")
    from turnaround import tracing
    store, gw = _setup()
    llm = _Steps({"tool": "rematch_candidates", "args": {}}, _act("rematch_now"))
    with tracing.capture() as rec:
        ExceptionBrain(gw, llm, FIELD, tool_loop=True).step(at(10, 10))
    spans = rec.spans
    tool = [s for s in spans if s["name"] == "agent.brain.tool"]
    assert [s["attrs"]["agent.tool"] for s in tool] == ["rematch_candidates"]
    choose = next(s for s in spans if s["name"] == "agent.brain.choose")
    assert choose["attrs"]["agent.loop.status"] == "ok" and choose["attrs"]["agent.loop.tools"] == ["rematch_candidates"]
    assert tool[0]["parent_id"] == choose["span_id"]


def test_tool_calls_are_recorded_in_shadow_log_and_execution_stays_rule_table():
    store, gw = _setup()
    log: list = []
    llm = _Steps({"tool": "ready_eta", "args": {}}, _act("escalate", role="ipc"))
    ExceptionBrain(gw, llm, FIELD, shadow=True, shadow_log=log, tool_loop=True).step(at(10, 10))
    r = log[-1]
    assert r["loop_status"] == "ok" and [c["tool"] for c in r["tool_calls"]] == ["ready_eta"]
    assert r["model_action"] == "escalate" and r["rule_action"] == "rematch_now" and not r["agree"]
    assert "features" in r and r["shown_to_human"] is False
    # 실행은 규칙표 (모델 답은 기록만)
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]
    assert [e.payload["mode"] for e in _types(store, EventType.ACTION_PROPOSED)] == ["fake"]


def test_shadow_loop_failure_is_recorded_and_rule_table_still_runs():
    store, gw = _setup()
    log: list = []
    ExceptionBrain(gw, _Steps({"tool": "nope"}), FIELD, shadow=True, shadow_log=log, tool_loop=True).step(at(10, 10))
    assert log[-1]["status"] == "unknown_tool" and log[-1]["model_action"] is None
    assert [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)] == ["rematch_now"]


def _run(contacted, **kw):
    store, gw = _setup(contacted=contacted)
    log: list = []
    brain = ExceptionBrain(gw, FakeLLM(), FIELD, shadow_log=log, **kw)
    brain.step(at(10, 10))
    evs = [(e.type.value, e.case_id, json.dumps(e.payload, ensure_ascii=False, sort_keys=True, default=str))
           for e in store.read()]
    for r in log:
        r.pop("latency_s", None)
    return evs, brain.records, log


def test_fake_llm_results_unchanged_with_tool_loop_on():
    for contacted in (False, True):
        for shadow in (False, True):
            assert _run(contacted, shadow=shadow) == _run(contacted, shadow=shadow, tool_loop=True), (contacted, shadow)


def test_default_is_single_call():
    store, gw = _setup()
    llm = _Steps(_act("rematch_now"))
    brain = ExceptionBrain(gw, llm, FIELD)
    assert brain.tool_loop is False
    brain.step(at(10, 10))
    assert [t for t, _ in llm.seen] == ["choose_exception_action"] and "tool_outputs" in llm.seen[0][1]
