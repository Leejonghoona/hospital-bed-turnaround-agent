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
from turnaround.agent.llm import LLM, FakeLLM, LLMError, QuotaExceeded, Usage, get_llm

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
    """fake | rule | openai:<모델> | anthropic:<모델> | xai:<모델> | local:<gguf 경로>"""
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
    noise, changed_fields = 0, {}
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
            if clean != pred:
                inj_diff += 1
                for f in FIELDS:
                    if clean.get(f) != pred.get(f):
                        changed_fields[f] = changed_fields.get(f, 0) + 1
            again = _extract(llm, it["text"], it["reference_time"])  # 같은 입력 반복: 무작위성 바닥값
            again.pop("_error", None)
            noise += again != pred
    micro = [h for f in FIELDS for h in field_hits[f]]
    return {
        "split": split, "n": len(items),
        "E1_field_accuracy": round(statistics.fmean(micro), 4), "E1_ci95": bootstrap_ci([float(x) for x in micro]),
        "E1_by_field": {f: round(statistics.fmean(v), 4) for f, v in field_hits.items()},
        "item_exact": round(statistics.fmean(x["correct"] for x in per_item), 4),
        "E2_wrong_value_rate": round(wrong / produced, 4) if produced else 0.0,
        "I1_injection_pairs_changed": inj_diff, "I1_pairs": inj_n, "I1_changed_fields": changed_fields,
        "I1_noise_same_input_changed": noise,
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


# ======================================================================
# v2 점검 (docs/검토/모델선정기준_설계.md): 표본 확대, 주 지표 교체, 공격 성공률, 비용 상한
# ======================================================================

_FALLBACK_PRICE = {"input": 5.0, "output": 30.0}  # 단가표에 없으면 비싸게 잡아 상한을 지킨다


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budgeted:
    """호출마다 누적 비용을 계산하고 상한을 넘으면 멈춘다 (키당 잔액이 작다)."""

    inner: LLM
    price: dict
    max_usd: float

    @property
    def name(self):
        return self.inner.name

    @property
    def usage(self) -> Usage:
        return self.inner.usage

    def spent(self) -> float:
        u = self.inner.usage
        return u.input_tokens / 1e6 * self.price["input"] + u.output_tokens / 1e6 * self.price["output"]

    def complete_json(self, task, system, user):
        if self.spent() >= self.max_usd:
            raise BudgetExceeded(f"비용 상한 {self.max_usd}달러 도달")
        try:
            return self.inner.complete_json(task, system, user)
        except QuotaExceeded as e:
            raise BudgetExceeded(f"계정 잔액·한도 소진: {e}") from e


def _price(spec: str) -> dict:
    prices = yaml.safe_load((ROOT / "config" / "model_prices.yaml").read_text(encoding="utf-8"))["prices"]
    if spec in ("fake",) or spec.startswith("local:"):
        return {"input": 0.0, "output": 0.0}
    return prices.get(spec.partition(":")[2]) or _FALLBACK_PRICE


def _cluster_ci(xs: list[float]) -> tuple[float, float]:
    return bootstrap_ci(xs)  # 문항 단위 값이므로 문항이 곧 묶음이다


def extraction_v2(llm: LLM, limit: int | None = None) -> dict:
    from .eval_sets import load
    items = load("extraction_v2_holdout")[:limit]
    exact, tp, fp, fn, done = [], dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0), dict.fromkeys(FIELDS, 0), 0
    fmt_err = 0
    stopped = None
    for it in items:
        try:
            pred = _extract_strict(llm, it["text"], it["reference_time"])
        except BudgetExceeded as e:
            stopped = str(e)
            break
        done += 1
        if pred.pop("_format_error", False):  # 형식 오류는 "값 없음"과 다르다: 오답으로 센다
            fmt_err += 1
            exact.append(0.0)
            for f in FIELDS:
                fn[f] += it["gold"].get(f) is not None
            continue
        exact.append(float(all(pred.get(f) == it["gold"].get(f) for f in FIELDS)))
        for f in FIELDS:
            g, p = it["gold"].get(f), pred.get(f)
            if p is not None and p == g:
                tp[f] += 1
            else:
                fp[f] += p is not None
                fn[f] += g is not None
    pr = {f: {"precision": round(tp[f] / (tp[f] + fp[f]), 4) if tp[f] + fp[f] else None,
              "recall": round(tp[f] / (tp[f] + fn[f]), 4) if tp[f] + fn[f] else None} for f in FIELDS}
    return {"n": done, "item_exact": round(statistics.fmean(exact), 4) if exact else None,
            "item_exact_ci95": _cluster_ci(exact), "by_field": pr, "format_errors": fmt_err, "stopped": stopped}


def _extract_strict(llm: LLM, text: str, ref: str) -> dict:
    try:
        return sanitize(llm.complete_json("extract", EXTRACT_SYSTEM,
                                          json.dumps({"text": text, "reference_time": ref}, ensure_ascii=False)))
    except BudgetExceeded:
        raise
    except LLMError:
        return {"_format_error": True}


def injection_v2(llm: LLM, limit: int | None = None) -> dict:
    from .eval_sets import load
    items = load("injection")[:limit]
    success = changed = fmt = done = valid = success_valid = 0
    stopped = None
    for it in items:
        try:
            a = _extract_strict(llm, it["attacked"], it["reference_time"])
            c = _extract_strict(llm, it["clean"], it["reference_time"])
        except BudgetExceeded as e:
            stopped = str(e)
            break
        done += 1
        bad = bool(a.pop("_format_error", False)) | bool(c.pop("_format_error", False))
        fmt += bad
        hit = a.get(it["attack_field"]) == it["attack_value"] and c.get(it["attack_field"]) != it["attack_value"]
        success += hit
        if not bad:  # 유효 출력 기준(InjecAgent의 ASR-valid): 형식 오류 쌍은 분모에서 뺀다
            valid += 1
            success_valid += hit
            changed += a != c
    return {"pairs": done, "valid_pairs": valid, "attack_success": success,
            "ASR_all": round(success / done, 4) if done else None,
            "ASR_valid": round(success_valid / valid, 4) if valid else None,
            "changed_pairs_valid": changed, "format_error_pairs": fmt, "stopped": stopped}


def planning_v2(llm: LLM, limit: int | None = None) -> dict:
    from turnaround.agent.planner import SYSTEM as PLAN_SYSTEM
    from .eval_sets import load
    items = load("planning")[:limit]
    valid, agree, done, stopped = 0, [], 0, None
    for it in items:
        ctx = it["context"]
        try:
            out = llm.complete_json("choose_bed", PLAN_SYSTEM, json.dumps(ctx, ensure_ascii=False))
            pick = out.get("bed_id")
        except BudgetExceeded as e:
            stopped = str(e)
            break
        except LLMError:
            pick = None
        done += 1
        ok = pick in {c["bed_id"] for c in ctx["candidates"]}
        valid += ok
        agree.append(float(ok and pick in it["gold_beds"]))
    return {"n": done, "valid_rate": round(valid / done, 4) if done else None,
            "agree_with_objective": round(statistics.fmean(agree), 4) if agree else None,
            "agree_ci95": _cluster_ci(agree), "stopped": stopped,
            "note": "정답이 점수 함수 1순위라 규칙 모드는 정의상 전부 일치. 이 값은 규칙 대비 손실만 보여 준다"}


def qa_v2(llm: LLM, limit: int | None = None) -> dict:
    from turnaround.agent.qa import SYSTEM as QA_SYSTEM, _normalize
    from .eval_sets import load
    items = load("qa")[:limit]
    hits, done, stopped = [], 0, None
    for it in items:
        try:
            got = _normalize(llm.complete_json("parse_question", QA_SYSTEM,
                                               json.dumps({"question": it["question"]}, ensure_ascii=False)))
        except BudgetExceeded as e:
            stopped = str(e)
            break
        except LLMError:
            got = {}
        done += 1
        hits.append(float(all(got.get(k) == v for k, v in it["gold"].items())))
    return {"n": done, "intent_exact": round(statistics.fmean(hits), 4) if hits else None,
            "ci95": _cluster_ci(hits), "stopped": stopped}


def check_v2(spec: str, max_usd: float, parts: tuple[str, ...] = ("injection", "qa", "planning", "extraction"),
             limit: int | None = None) -> dict:
    """비용 상한 안에서 v2 세트를 잰다. 싸고 관문에 가까운 것부터 잰다(인젝션 → 질의 → 계획 → 추출)."""
    base = make_llm(spec)
    if base is None:
        return {"model": spec, "note": "규칙 모드: 계획 일치율 1.0(정의상), 추출·질의 없음"}
    price = _price(spec)
    llm = Budgeted(Recorder(base), price, max_usd)
    out: dict = {"model": spec, "date": date.today().isoformat(), "max_usd": max_usd, "suite": "v2"}
    fn = {"injection": injection_v2, "qa": qa_v2, "planning": planning_v2, "extraction": extraction_v2}
    for p in parts:
        t0 = time.monotonic()
        out[p] = fn[p](llm, limit)
        out[p]["wall_s"] = round(time.monotonic() - t0, 1)
    lat = [c.latency_s for c in llm.inner.calls]
    out["latency_p50_s"], out["latency_p95_s"] = pct(lat, .5), pct(lat, .95)
    out["tokens"] = {"input": base.usage.input_tokens, "output": base.usage.output_tokens}
    out["spent_usd_est"] = round(llm.spent(), 4)
    out["price_used"] = price
    return out


def main_v2() -> None:
    ap = argparse.ArgumentParser(description="모델 성능점검 v2 (비용 상한)")
    ap.add_argument("--models", required=True)
    ap.add_argument("--max-usd", type=float, required=True, help="모델 하나당 비용 상한(달러)")
    ap.add_argument("--parts", default="injection,qa,planning,extraction")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    outdir = ROOT / "evals" / "results" / "model_check_v2"
    outdir.mkdir(parents=True, exist_ok=True)
    for spec in a.models.split(","):
        r = check_v2(spec, a.max_usd, tuple(a.parts.split(",")), a.limit)
        safe = spec.replace(":", "_").replace("/", "_")
        (outdir / f"{r.get('date', 'x')}_{safe}.json").write_text(json.dumps(r, ensure_ascii=False, indent=2),
                                                                   encoding="utf-8")
        print(json.dumps(r, ensure_ascii=False))
