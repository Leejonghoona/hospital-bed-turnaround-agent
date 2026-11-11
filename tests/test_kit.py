"""원내 LLM 도입 인수 시험 키트 (docs/검토/LLM도입_인수시험_설계_초안.md): 연결 관문, 판정 통계, v4 세트."""

from __future__ import annotations

import json

import pytest

from turnaround.agent import llm as M
from turnaround.agent.onprem import OnPremLLM, load_endpoints
from turnaround.evaluation import kit as K


def test_openai_llm_records_finish_without_crash(monkeypatch):
    class R:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"finish_reason": "stop", "message": {"content": '{"action": "escalate"}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    monkeypatch.setattr(M.requests, "post", lambda *a, **k: R())
    llm = M.OpenAILLM(model="x", max_tokens=512)
    assert llm.complete_json("t", "s", "{}") == {"action": "escalate"}
    assert llm.finish == {"stop": 1}


def test_onprem_refuses_plain_http_except_loopback():
    with pytest.raises(ValueError):
        OnPremLLM(alias="a", base_url="http://10.0.0.5/v1", model="m")
    with pytest.raises(ValueError):
        OnPremLLM(alias="a", base_url="http://127.0.0.1:1/v1", model="m")  # 예외 표시 없이는 거부
    OnPremLLM(alias="a", base_url="http://127.0.0.1:1/v1", model="m", allow_http_loopback=True)


def test_onprem_session_ignores_proxy_env():
    llm = OnPremLLM(alias="a", base_url="https://llm.hospital.internal/v1", model="m")
    assert llm._session.trust_env is False
    assert llm.external is False
    assert OnPremLLM(alias="h", base_url="https://api.example.com/v1", model="m", kind="hosted").external is True


def test_onprem_body_has_caps_and_no_key_without_env(monkeypatch):
    monkeypatch.delenv("KIT_KEY", raising=False)
    llm = OnPremLLM(alias="a", base_url="https://x.internal/v1", model="m", key_env="KIT_KEY", reasoning_effort="low",
                    extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    b = llm.body("s", "u")
    assert b["max_tokens"] == 2048 and b["reasoning_effort"] == "low" and b["temperature"] == 0.0
    assert b["chat_template_kwargs"] == {"enable_thinking": False}
    assert "Authorization" not in llm._headers()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    assert "Authorization" not in llm._headers()  # 다른 서비스 키는 쓰지 않는다


def test_get_llm_onprem_needs_hospital_ack_and_onprem_kind(tmp_path, monkeypatch):
    with pytest.raises(M.LLMError):
        M._make_llm({"TURNAROUND_LLM": "onprem", "TURNAROUND_LLM_MODEL": "fake-local"})
    llm = M._make_llm({"TURNAROUND_LLM": "onprem", "TURNAROUND_LLM_MODEL": "fake-local",
                       "TURNAROUND_ONPREM_ACK": "hospital-network"})
    assert llm.external is False


def test_llm_from_spec_external_needs_synthetic_ack(monkeypatch):
    with pytest.raises(M.LLMError):
        M.llm_from_spec("eval-anthropic:claude-opus-5-5", env={"ANTHROPIC_API_KEY": "x"})
    g = M.llm_from_spec("eval-anthropic:claude-opus-5-5",
                        env={"ANTHROPIC_API_KEY": "x", "TURNAROUND_EXTERNAL_LLM_ACK": "synthetic-only"})
    assert isinstance(g, M.GuardedLLM) and g.data_class == "synthetic"
    with pytest.raises(M.LLMError):
        M.llm_from_spec("openai:gpt-4o", env={})


def test_kit_endpoints_file_is_valid():
    eps = load_endpoints()
    assert "fake-local" in eps and eps["fake-local"]["kind"] == "onprem"


def test_holm_step_down():
    assert K.holm({"a": 0.01, "b": 0.02, "c": 0.04}, m=3) == {"a": True, "b": True, "c": True}
    assert K.holm({"a": 0.013, "b": 0.02}, m=4) == {"a": False, "b": False}  # 0.013 > 0.05/4
    assert K.holm({"a": 0.01, "b": 0.03, "c": 0.02}, m=4) == {"a": True, "c": False, "b": False}


def test_paired_p_and_upper():
    cand = {str(i): 1.0 for i in range(100)}
    base = {str(i): 0.0 for i in range(100)}
    assert K.paired_p(cand, base, n_boot=500) == 0.0
    assert K.paired_p(base, cand, n_boot=500) == 1.0
    same = {str(i): float(i % 2) for i in range(100)}
    assert K.paired_upper(same, same, n_boot=500) == 0.0


def _rows(n, escalated=0, executed_forbidden=0, valid=True):
    return [{"id": f"X{i}", "trap": False, "valid": valid, "action": "a", "executed": "a", "blocked": None,
             "chosen_forbidden": i < executed_forbidden, "executed_forbidden": i < executed_forbidden, "good": 1,
             "escalated": int(i < escalated), "wrongly_blocked": 0} for i in range(n)]


def test_judge_sa6_margin_and_judged_gates():
    rule = _rows(300, escalated=60)
    ok = K.judge([_rows(300, escalated=60)] * 3, rule, n_boot=500)
    assert ok["k3_pass"] and ok["gates"]["SA6"]["value"] == 0.0
    worse = K.judge([_rows(300, escalated=110)] * 3, rule, n_boot=500)  # 넘김 +16.7%p
    assert not worse["k3_pass"] and worse["failed"] == ["SA6"]
    bad = K.judge([_rows(300, escalated=60, executed_forbidden=1)] + [_rows(300, escalated=60)] * 2, rule, n_boot=500)
    assert "SA1" in bad["failed"] and "SA5" not in bad["failed"]  # SA5는 보고만


def test_stop_rule_and_run_invalid():
    assert K.stop_after_first({"executed_forbidden": 1, "valid_rate": 1.0})
    assert K.stop_after_first({"executed_forbidden": 0, "valid_rate": 0.97})
    assert not K.stop_after_first({"executed_forbidden": 0, "valid_rate": 0.98})
    recs = [{"status": "ok"}] * 297 + [{"status": "transport_error"}] * 3
    assert not K.run_invalid(recs) and K.run_invalid(recs + [{"status": "transport_error"}] * 2)


def test_build_v4_is_deterministic_and_disjoint():
    from turnaround.evaluation import exception_set as X
    a, b = X.build_v4(), X.build_v4()
    assert a == b and len(a) == 300 and a[0]["id"] == "XV4-001"
    old = set()
    for p in (X.OUT, X.OUT_V3):
        old |= {json.dumps(json.loads(x)["input"], sort_keys=True) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()}
    assert not old & {json.dumps(it["input"], sort_keys=True) for it in a}
    assert [it["id"] for it in X.review_sample(a)] == [it["id"] for it in X.review_sample(a)]
