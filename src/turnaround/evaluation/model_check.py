"""모델 성능점검 하네스 (EVL-4, M1). 점검표: docs/모델_성능점검표.md.

같은 하네스로 모델마다 잰다.
- 추출: E1 필드별 정확 일치율, E2 틀린 값 비율(값을 낸 것 중), 인젝션 짝 I1(지시문을 뺀 문장과 결과가 다른 쌍 수)
- 계획: 회귀 시나리오 통과(P2 대용), 도구 호출 유효율 P1(후보 안에서 고른 비율), 반복 일관성 C1(pass^k)
- 비용: 호출 지연 P50·P95, 토큰, 단가표(config/model_prices.yaml)로 환산한 비용
- 통계: 붓스트랩 95% 신뢰구간, 모델 쌍의 정확 McNemar 검정(짝지은 문항)

사용: python -m turnaround.evaluation.model_check --models fake,rule,openai:gpt-4.1-mini --reps 5
외부 API에는 합성 데이터만 간다(TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only가 있어야 실행).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from turnaround.agent.extract import SYSTEM as EXTRACT_SYSTEM
from turnaround.agent.extract import sanitize
from turnaround.agent.llm import LLM, FakeLLM, LLMError, Usage, get_llm

from . import extraction_set
from .scenario import load_scenarios

ROOT = Path(__file__).resolve().parents[3]
FIELDS = ("guardian_eta", "delay_reason", "mobility_aid", "single_room_wish")


# ---------- 호출 기록기 ----------

@dataclass
class Call:
    task: str
    ok: bool  # JSON을 받았는지
    valid: bool  # 계획: 후보 안에서 골랐는지 / 추출: 허용 필드만 냈는지
    latency_s: float


@dataclass
class Recorder:
    """LLM을 감싸 호출마다 지연과 유효성을 기록한다."""

    inner: LLM
    calls: list[Call] = field(default_factory=list)

    @property
    def name(self):
        return self.inner.name

    @property
    def usage(self) -> Usage:
        return self.inner.usage

    def complete_json(self, task, system, user):
        t0 = time.monotonic()
        try:
            out = self.inner.complete_json(task, system, user)
        except LLMError:
            self.calls.append(Call(task, False, False, time.monotonic() - t0))
            raise
        valid = True
        if task == "choose_bed":
            ids = {c["bed_id"] for c in json.loads(user).get("candidates", [])}
            valid = out.get("bed_id") in ids or (not ids and out.get("bed_id") is None)
        elif task == "extract":
            valid = set(out) <= set(FIELDS)
        self.calls.append(Call(task, True, valid, time.monotonic() - t0))
        return out


def make_llm(spec: str) -> LLM | None:
    """fake | rule | openai:<모델> | anthropic:<모델> | local:<gguf 경로>"""
    if spec == "rule":
        return None
    if spec == "fake":
        return FakeLLM()
    kind, _, model = spec.partition(":")
    env = dict(os.environ, TURNAROUND_LLM=kind, TURNAROUND_LLM_MODEL=model)
    if kind == "openai":
        env.setdefault("OPENAI_API_KEY", "proxy-injected")  # 키는 환경 프록시가 넣는다
    return get_llm(env)


# ---------- 통계 ----------

def bootstrap_ci(xs: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    if not xs:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(xs, k=len(xs))) for _ in range(n))
    return (round(means[int(0.025 * n)], 4), round(means[int(0.975 * n) - 1], 4))


def mcnemar_exact(a: list[bool], b: list[bool]) -> dict:
    """짝지은 정오(같은 문항). 불일치 쌍으로 양측 정확 이항 검정."""
    b01 = sum(1 for x, y in zip(a, b) if x and not y)
    b10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = b01 + b10
    if n == 0:
        return {"a_only": 0, "b_only": 0, "p": 1.0}
    k = min(b01, b10)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {"a_only": b01, "b_only": b10, "p": round(p, 4)}


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return math.nan
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 3)


# ---------- 추출 ----------

def _extract(llm: LLM, text: str, ref: str) -> dict:
    try:
        return sanitize(llm.complete_json("extract", EXTRACT_SYSTEM,
                                          json.dumps({"text": text, "reference_time": ref}, ensure_ascii=False)))
    except LLMError:
        return {"_error": True}


def _strip_injection(text: str) -> str:
    for s in extraction_set.INJECTIONS:
        text = text.replace(s, "")
    return " ".join(text.split())


def extraction_eval(llm: LLM, split: str = "dev", limit: int | None = None) -> dict:
    items = extraction_set.load(split)[:limit]
    per_item, field_hits, wrong, produced, inj_diff, inj_n = [], {f: [] for f in FIELDS}, 0, 0, 0, 0
    for it in items:
        pred = _extract(llm, it["text"], it["reference_time"])
        err = pred.pop("_error", False)
        ok_all = not err
        for f in FIELDS:
            hit = pred.get(f) == it["gold"].get(f)
            field_hits[f].append(hit)
            ok_all &= hit
            if f in pred:
                produced += 1
                wrong += pred[f] != it["gold"].get(f)
        per_item.append({"id": it["id"], "correct": ok_all, "pred": pred, "error": err})
        if it["injected"]:
            inj_n += 1
            clean = _extract(llm, _strip_injection(it["text"]), it["reference_time"])
            clean.pop("_error", None)
            inj_diff += clean != pred
    micro = [h for f in FIELDS for h in field_hits[f]]
    return {
        "split": split, "n": len(items),
        "E1_field_accuracy": round(statistics.fmean(micro), 4), "E1_ci95": bootstrap_ci([float(x) for x in micro]),
        "E1_by_field": {f: round(statistics.fmean(v), 4) for f, v in field_hits.items()},
        "item_exact": round(statistics.fmean(x["correct"] for x in per_item), 4),
        "E2_wrong_value_rate": round(wrong / produced, 4) if produced else 0.0,
        "I1_injection_pairs_changed": inj_diff, "I1_pairs": inj_n,
        "items": per_item,
    }


# ---------- 계획 ----------

def planning_eval(spec: str, reps: int = 1) -> dict:
    from . import runners_gateway as rg
    from .runners import RUNNERS

    scenarios = load_scenarios(ROOT / "evals" / "scenarios" / "dev")
    shared = make_llm(spec)
    rec = Recorder(shared) if shared is not None else None
    old = dict(rg.AGENT_CONFIG)
    rg.AGENT_CONFIG.update(llm_factory=(lambda: rec) if rec else FakeLLM, mode="rule" if rec is None else "agent")
    try:
        runs: dict[str, list[bool]] = {s.id: [] for s in scenarios}
        fails: dict[str, list[str]] = {}
        for _ in range(reps):
            for s in scenarios:
                r = RUNNERS[s.id](s)
                runs[s.id].append(r.passed)
                if not r.passed:
                    fails.setdefault(s.id, r.failures)
    finally:
        rg.AGENT_CONFIG.clear()
        rg.AGENT_CONFIG.update(old)
    choose = [c for c in rec.calls if c.task == "choose_bed"] if rec else []
    first = [v[0] for v in runs.values()]
    return {
        "reps": reps, "scenarios": len(scenarios),
        "pass_rate_first": round(statistics.fmean(first), 4), "pass_ci95": bootstrap_ci([float(x) for x in first]),
        "C1_pass_all_reps": sum(all(v) for v in runs.values()),
        "P1_valid_tool_choice": round(statistics.fmean(c.valid for c in choose), 4) if choose else None,
        "choose_calls": len(choose), "failed": fails,
        "per_scenario": {k: v for k, v in runs.items()},
    }


# ---------- 비용 ----------

def cost(spec: str, usage: Usage, prices: dict) -> float | None:
    p = prices.get(spec.partition(":")[2]) or prices.get(spec)
    if not p:
        return None
    return round(usage.input_tokens / 1e6 * p["input"] + usage.output_tokens / 1e6 * p["output"], 4)


def check_model(spec: str, reps: int = 1, split: str = "dev", limit: int | None = None) -> dict:
    prices = yaml.safe_load((ROOT / "config" / "model_prices.yaml").read_text(encoding="utf-8"))["prices"]
    out: dict = {"model": spec, "date": date.today().isoformat()}
    llm = make_llm(spec)
    if llm is not None:
        rec = Recorder(llm)
        t0 = time.monotonic()
        out["extraction"] = extraction_eval(rec, split, limit)
        out["extraction"]["wall_s"] = round(time.monotonic() - t0, 1)
        lat = [c.latency_s for c in rec.calls]
        out["extraction"]["latency_p50_s"], out["extraction"]["latency_p95_s"] = pct(lat, .5), pct(lat, .95)
        out["extraction"]["tokens"] = {"input": llm.usage.input_tokens, "output": llm.usage.output_tokens}
        out["extraction"]["cost_usd"] = cost(spec, llm.usage, prices)
    if reps > 0:
        t0 = time.monotonic()
        out["planning"] = planning_eval(spec, reps)
        out["planning"]["wall_s"] = round(time.monotonic() - t0, 1)
    return out


def compare(results: list[dict]) -> dict:
    """모델 쌍마다 추출 문항 정오로 McNemar."""
    pairs = {}
    ex = [r for r in results if "extraction" in r]
    for i, a in enumerate(ex):
        for b in ex[i + 1:]:
            ca = [x["correct"] for x in a["extraction"]["items"]]
            cb = [x["correct"] for x in b["extraction"]["items"]]
            pairs[f"{a['model']} vs {b['model']}"] = mcnemar_exact(ca, cb)
    return pairs


def main() -> None:
    ap = argparse.ArgumentParser(description="모델 성능점검")
    ap.add_argument("--models", default="fake,rule")
    ap.add_argument("--reps", type=int, default=1, help="계획 반복 횟수. 0이면 계획 점검을 건너뛴다")
    ap.add_argument("--split", default="dev", choices=["dev", "holdout"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", default=str(ROOT / "evals" / "results" / "model_check"))
    a = ap.parse_args()
    results = []
    for spec in a.models.split(","):
        r = check_model(spec, a.reps, a.split, a.limit)
        results.append(r)
        Path(a.out).mkdir(parents=True, exist_ok=True)
        safe = spec.replace(":", "_").replace("/", "_")
        (Path(a.out) / f"{r['date']}_{safe}_{a.split}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                                                     encoding="utf-8")
        brief = {k: v for k, v in r.get("extraction", {}).items() if k not in ("items",)}
        print(json.dumps({"model": spec, "extraction": brief,
                          "planning": {k: v for k, v in r.get("planning", {}).items() if k != "per_scenario"}},
                         ensure_ascii=False))
    if len(results) > 1:
        print(json.dumps({"mcnemar": compare(results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
