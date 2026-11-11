"""시연용 합성 병원 하루를 추적을 켜고 돌려 판단·게이트웨이·LLM 수치를 낸다 (DECISIONS 2026-11-10 추적).

python scripts/trace_report.py [--until 23:45] [--out evals/results/final/trace_report.json]
python scripts/trace_report.py --seed 2 --llm local:models/x.gguf --exception-llm local:models/x.gguf --shadow --out ...
  (시험 B, docs/검토/LLM운영_시험설계.md 3절: 예외 판단 그림자 모드. 실행은 규칙표, 모델 제안은 기록만)

시연 병원(`build_hospital`과 같은 설정: 시드 2, 3일째, 병동 4개, 가동률 약 94%)을 전날 16:00부터 --until까지 재생한다.
가짜 LLM(결정적)이 기본이라 LLM 지연은 실제 모델 지연이 아니다. 모든 수치는 합성 데이터 기준 추정이다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time as _time
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround import tracing  # noqa: E402

KEEP = ("case_id", "agent.mode", "agent.bed_id", "agent.candidates", "agent.trigger", "agent.action",
        "gateway.status", "gateway.tier", "gateway.outcome", "gateway.reasons", "gateway.action", "gateway.actor",
        "llm.task", "llm.role", "llm.model", "llm.external_blocked", "llm.input_tokens", "llm.output_tokens")


def run_day(until: time = time(23, 45), seed: int = 2, llm=None, exception_llm=None, shadow: bool = False):
    from turnaround.api.app import _inject_late_isolation
    from turnaround.api.shift_model import apply_night_shift
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    with tracing.capture() as rec:
        from turnaround.api.app import demo_config, demo_policy
        st = build_demo(seed=seed, day=3, until=time(11, 15), via_fhir=False, policy=demo_policy(),
                        config=demo_config(), occupancy="high", backlog_days=1, live_from=time(7, 0),
                        llm=llm, exception_llm=exception_llm, shadow=shadow)
        _inject_late_isolation(st)
        st.advance(st.now.replace(hour=11, minute=45))
        apply_night_shift(st)
        if until > time(11, 45):
            st.advance(st.now.replace(hour=until.hour, minute=until.minute))
        spans = rec.spans
    return st, spans


def _example(spans: list[dict]) -> dict:
    """사례 하나의 span을 순서대로. 계획→게이트웨이→(LLM)이 고루 있는 사례를 고른다."""
    cases: dict[str, set] = {}
    for s in spans:
        c = s["attrs"].get("case_id")
        if c:
            cases.setdefault(c, set()).add(s["name"])
    if not cases:
        return {}
    case = max(sorted(cases), key=lambda c: (len(cases[c]), "gateway.commit" in cases[c]))
    rows = tracing.case_trace(spans, case)
    t0 = rows[0]["start_ns"]
    ids = {s["span_id"]: i for i, s in enumerate(rows)}
    return {"case": case, "spans": [{"i": i, "parent": ids.get(s["parent_id"]), "name": s["name"],
                                     "at_ms": round((s["start_ns"] - t0) / 1e6, 3), "duration_ms": s["duration_ms"],
                                     "attrs": {k: s["attrs"][k] for k in KEEP if k in s["attrs"]}}
                                    for i, s in enumerate(rows)][:40]}


def _pct(xs: list[float], q: float):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 3)


def shadow_summary(log: list[dict]) -> dict:
    """그림자 모드: 형식 오류(JSON 깨짐·행동 없음, 목록 밖, 모델 오류)와 판단 불일치를 따로 센다."""
    n = len(log)
    by_status: dict[str, int] = {}
    by_trig: dict[str, dict] = {}
    for r in log:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        t = by_trig.setdefault(r["trigger"], {"n": 0, "ok": 0, "format_error": 0, "out_of_list": 0, "llm_error": 0,
                                               "agree": 0, "disagree": 0})
        t["n"] += 1
        t[r["status"]] += 1
        if r["status"] == "ok":
            t["agree" if r["agree"] else "disagree"] += 1
    ok = by_status.get("ok", 0)
    blocks: dict[str, int] = {}
    for r in log:
        if r["status"] == "ok" and r["would_block"]:
            blocks[r["would_block"]] = blocks.get(r["would_block"], 0) + 1
    lat = [r["latency_s"] for r in log if r["latency_s"] is not None]
    return {"n": n, "by_status": dict(sorted(by_status.items())), "valid_rate": round(ok / n, 4) if n else None,
            "agree_rate": round(sum(r["agree"] for r in log) / ok, 4) if ok else None,
            "gateway_would_block": dict(sorted(blocks.items())), "by_trigger": dict(sorted(by_trig.items())),
            "latency_s": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "mean": round(sum(lat) / len(lat), 3) if lat else None}}


def _usage(llm) -> dict | None:
    if llm is None:
        return None
    inner = getattr(llm, "inner", llm)
    u = getattr(inner, "usage", None)
    return {"name": getattr(inner, "name", type(inner).__name__), "calls": getattr(u, "calls", None),
            "input_tokens": getattr(u, "input_tokens", None), "output_tokens": getattr(u, "output_tokens", None),
            "latency_s": round(getattr(u, "latency_s", 0.0) or 0.0, 3), "finish": getattr(inner, "finish", None),
            "n_threads": getattr(inner, "n_threads", None)}


def report(until: time = time(23, 45), seed: int = 2, llm: str = "fake", exception_llm: str | None = None,
           shadow: bool = False) -> dict:
    from turnaround.agent.llm import llm_from_spec
    from turnaround.evaluation.safety_exec import executed_safety
    t0 = _time.perf_counter()
    m_llm = llm_from_spec(llm)
    m_exc = llm_from_spec(exception_llm) if exception_llm and exception_llm != llm else (m_llm if exception_llm else None)
    st, spans = run_day(until, seed=seed, llm=m_llm, exception_llm=m_exc, shadow=shadow)
    out = tracing.summarize(spans)
    commits = [s for s in spans if s["name"] == "gateway.commit"]
    by_actor: dict[str, dict[str, int]] = {}
    for s in commits:
        a = by_actor.setdefault(s["attrs"].get("gateway.actor", "?"), {})
        o = s["attrs"].get("gateway.outcome", "other")
        a[o] = a.get(o, 0) + 1
    res = {
        "note": "합성 데이터 기준 추정. 가짜 LLM(결정적)이라 LLM 지연은 실제 모델 지연이 아니다.",
        "setup": {"hospital": f"시연용 합성 병원(build_hospital 설정, 시드 {seed}, 3일째)", "replay": f"전날 16:00 ~ {until:%H:%M}",
                  "llm": st.agent.llm.name if st.agent.llm else None, "spans": len(spans),
                  "wall_s": round(_time.perf_counter() - t0, 2)},
        **out,
        "commits_by_actor": dict(sorted(by_actor.items())),
        "case_example": _example(spans),
    }
    if shadow or exception_llm or llm != "fake":  # 시험 B 항목 (기본 실행의 출력 모양은 그대로 둔다)
        res["setup"].update(seed=seed, exception_llm=st.agent.exception_llm.name if st.agent.exception_llm else None,
                            shadow=shadow)
        res["safety"] = executed_safety(st.gw, st.store.read())
        res["llm_usage"] = {"planner": _usage(st.agent.llm),
                            "exception": _usage(st.agent.exception_llm) if st.agent.exception_llm is not st.agent.llm else None}
        if shadow:
            res["shadow"] = shadow_summary(st.agent.shadow_log)
            res["shadow_log"] = st.agent.shadow_log
    return res


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="23:45")
    ap.add_argument("--out", default=str(ROOT / "evals/results/final/trace_report.json"))
    ap.add_argument("--seed", type=int, default=2)
    ap.add_argument("--llm", default="fake", help="설명 모델: fake 또는 local:<경로>")
    ap.add_argument("--exception-llm", default=None, help="예외 판단 모델(없으면 설명 모델과 같음)")
    ap.add_argument("--shadow", action="store_true", help="예외 판단 그림자 모드: 실행은 규칙표, 모델 제안은 기록만")
    a = ap.parse_args(argv)
    h, m = map(int, a.until.split(":"))
    r = report(time(h, m), seed=a.seed, llm=a.llm, exception_llm=a.exception_llm, shadow=a.shadow)
    text = json.dumps(r, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
