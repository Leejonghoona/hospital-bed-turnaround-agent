"""시연용 합성 병원 하루를 추적을 켜고 돌려 판단·게이트웨이·LLM 수치를 낸다 (DECISIONS 2026-11-10 추적).

python scripts/trace_report.py [--until 23:45] [--out evals/results/final/trace_report.json]

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


def run_day(until: time = time(23, 45)):
    from turnaround.api.app import _inject_late_isolation
    from turnaround.api.shift_model import apply_night_shift
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    with tracing.capture() as rec:
        st = build_demo(seed=2, day=3, until=time(11, 15), via_fhir=False, policy=load_policy(),
                        config=GeneratorConfig.demo_hospital(), occupancy="high", backlog_days=1, live_from=time(7, 0))
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


def report(until: time = time(23, 45)) -> dict:
    t0 = _time.perf_counter()
    st, spans = run_day(until)
    out = tracing.summarize(spans)
    commits = [s for s in spans if s["name"] == "gateway.commit"]
    by_actor: dict[str, dict[str, int]] = {}
    for s in commits:
        a = by_actor.setdefault(s["attrs"].get("gateway.actor", "?"), {})
        o = s["attrs"].get("gateway.outcome", "other")
        a[o] = a.get(o, 0) + 1
    return {
        "note": "합성 데이터 기준 추정. 가짜 LLM(결정적)이라 LLM 지연은 실제 모델 지연이 아니다.",
        "setup": {"hospital": "시연용 합성 병원(build_hospital 설정, 시드 2, 3일째)", "replay": f"전날 16:00 ~ {until:%H:%M}",
                  "llm": st.agent.llm.name if st.agent.llm else None, "spans": len(spans),
                  "wall_s": round(_time.perf_counter() - t0, 2)},
        **out,
        "commits_by_actor": dict(sorted(by_actor.items())),
        "case_example": _example(spans),
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="23:45")
    ap.add_argument("--out", default=str(ROOT / "evals/results/final/trace_report.json"))
    a = ap.parse_args(argv)
    h, m = map(int, a.until.split(":"))
    r = report(time(h, m))
    text = json.dumps(r, ensure_ascii=False, indent=1)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
