"""측정 결과(evals/results/)에서 화면에 보일 요약을 뽑아 config/results_summary.yaml에 쓴다.

배포 이미지에는 evals/results/가 없어서 화면은 이 요약을 읽는다(DECISIONS 2026-11-10).
시험(tests/test_results_summary.py)이 요약과 원본이 같은지 확인한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "evals" / "results" / "final"
OUT = ROOT / "config" / "results_summary.yaml"


def _load(name: str) -> dict:
    return json.loads((FINAL / name).read_text(encoding="utf-8"))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def _safety(d: dict) -> dict:
    s = d["safety"]
    return {"violations": sum(s[k] for k in ("S1", "S2", "S3", "S4")), "checks": sum(s[k + "_n"] for k in ("S1", "S2", "S3", "S4"))}


def _entry_breakdown() -> dict:
    """안내한 수속 시각부터 입실까지(병원에 와서 기다린 시간). scripts/measure_entry_breakdown.py"""
    d = _load("entry_breakdown.json")
    off, on = d["off"], d["on"]
    a, b = d["seeds"]
    return {"seeds": f"{a}~{b - 1}", "n": on["n"], "off_min": off["from_promised_mean"], "on_min": on["from_promised_mean"],
            "change": round((on["from_promised_mean"] - off["from_promised_mean"]) / off["from_promised_mean"], 3),
            "ready_off": off["ready_at_checkin"], "ready_on": on["ready_at_checkin"],
            "done_to_ready_min": on["done_to_ready"], "request_to_start_off": off["request_to_start"], "request_to_start_on": on["request_to_start"]}


def _entry_breakdown_realistic() -> dict:
    """운영 현실 옵션(침상 정리 일괄 회차, 청소 인력 시간대)을 켠 병원에서 같은 쪼개 보기 (DECISIONS 2026-11-10 ops_realism).
    scripts/measure_entry_breakdown.py --ops. 기존 entry_breakdown과 따로 둔다."""
    d = _load("entry_breakdown_realistic.json")
    off, on = d["off"], d["on"]
    a, b = d["seeds"]
    sv = lambda x: sum(x["safety"][k] for k in ("S1", "S2", "S3", "S4"))  # noqa: E731
    v = d["variants"]
    return {"seeds": f"{a}~{b - 1}", "day": d["day"], "n_off": off["n"], "n_on": on["n"],
            "off_min": off["from_promised_mean"], "on_min": on["from_promised_mean"],
            "change": round((on["from_promised_mean"] - off["from_promised_mean"]) / off["from_promised_mean"], 3),
            "median_off_min": off["from_promised_median"], "median_on_min": on["from_promised_median"],
            "ready_off": off["ready_at_checkin"], "ready_on": on["ready_at_checkin"],
            "request_to_start_off": off["request_to_start"], "request_to_start_on": on["request_to_start"],
            "done_to_ready_off": off["done_to_ready"], "done_to_ready_on": on["done_to_ready"],
            "safety_violations_off": sv(off), "safety_violations_on": sv(on),
            "old_levers_only_min": v["old_levers_only"]["from_promised_mean"],
            "plus_bed_prep_request_min": v["plus_bed_prep_request"]["from_promised_mean"],
            "plus_schedule_share_min": v["plus_schedule_share"]["from_promised_mean"],
            "no_advance_notice_min": v["all_no_advance_notice"]["from_promised_mean"]}


def summarize() -> dict:
    m1_all = _load("m1.json")
    m1 = m1_all["field_default"]
    a, b = m1_all["seeds"]
    off, on, hold = _load("cycle_50_off.json"), _load("cycle_50_on.json"), _load("cycle_holdout_50_99_on.json")
    w_off, w_on = _mean(off["entry_wait_mean_by_seed"]), _mean(on["entry_wait_mean_by_seed"])
    curves = []
    for seed in (0, 1, 2):
        d = _load(f"m3_seed{seed}.json")
        weeks = []
        for i in range(0, len(d["curve"]), 10):
            chunk = d["curve"][i:i + 10]
            c = sum(x["committed"] for x in chunk)
            weeks.append({"days": f"{chunk[0]['day']}~{chunk[-1]['day']}일", "ratio": round(sum(x["no_touch"] for x in chunk) / c, 3) if c else 0.0})
        promoted = {}
        for x in d["curve"]:
            for u in x["auto_units"]:
                promoted.setdefault(u, x["day"])
        curves.append({"seed": seed, "periods": weeks, "promoted_day": promoted,
                       "safety_violations": sum(x["S1"] + x["S2"] for x in d["curve"])})
    sel = json.loads(sorted((ROOT / "evals" / "results" / "model_select").glob("*.json"))[-1].read_text(encoding="utf-8"))
    scen = {k: len(list((ROOT / "evals" / "scenarios" / k).glob("*.yaml"))) for k in ("dev", "holdout")}
    return {
        "note": "합성 데이터 기준 추정",
        "no_touch": {"ratio": m1["no_touch_ratio"], "committed": m1["committed"], "no_touch": m1["no_touch_committed"],
                     "seed_min": m1["per_seed_min"], "seeds": f"{a}~{b}"},
        "entry_wait": {"seeds": len(on["entry_wait_mean_by_seed"]), "off_min": round(w_off, 1), "on_min": round(w_on, 1),
                       "change": round((w_on - w_off) / w_off, 3)},
        "safety": {"seeds": "0~49", **_safety(on), "holdout_seeds": "50~99", "holdout": _safety(hold)},
        "autonomy": {"days": 60, "curves": curves},
        "models": {"candidates": len(sel["exception"]["rows"]),
                   "exception_selected": sel["exception"]["selected"] is not None,
                   "explain_selected": sel["explain"]["selected"] is not None, "date": sel["date"]},
        "regression": scen,
        "entry_breakdown": _entry_breakdown(),
        "entry_breakdown_realistic": _entry_breakdown_realistic(),
    }


def main() -> int:
    OUT.write_text("# scripts/summarize_results.py가 만든 파일. 손으로 고치지 않는다.\n"
                   + yaml.safe_dump(summarize(), allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
