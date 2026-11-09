"""5단계 최종 재측정: M1(일반 경로 처리율)과 M3(자율권 곡선)를 최종 코드로 다시 잰다. 합성 데이터 기준 추정.

실행: python scripts/measure_final.py --part m1|m3 [--seeds 0 20] [--days 60]
결과: evals/results/final/m1.json, m3_seed<N>.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.evaluation.human_load import human_load  # noqa: E402
from turnaround.evaluation.safety_exec import executed_safety  # noqa: E402
from turnaround.gateway import load_policy  # noqa: E402
from turnaround.sim import GeneratorConfig  # noqa: E402
from turnaround.ui.demo import build_demo  # noqa: E402

OUT = ROOT / "evals/results/final"


def m1(seeds) -> dict:
    """현장 프로필(기본)과 legacy를 같은 시드로. 데모 하루 재생(4일차 끝까지), 현실 구성 생성기."""
    out = {}
    for name, pol in (("field_default", load_policy()), ("legacy", load_policy(profile="legacy", allow_legacy=True))):
        tot = {"admission_cases": 0, "committed": 0, "no_touch_committed": 0, "S1": 0, "S1_n": 0, "S2": 0, "S2_n": 0}
        per = []
        for seed in seeds:
            st = build_demo(seed=seed, day=3, until=time(23, 45), via_fhir=False, policy=pol,
                            config=GeneratorConfig.realistic())
            ev = st.store.read()
            hl, s = human_load(ev), executed_safety(st.gw, ev)
            for k in ("admission_cases", "committed", "no_touch_committed"):
                tot[k] += hl[k]
            for k in ("S1", "S2"):
                tot[k] += s[k]["violations"]
                tot[k + "_n"] += s[k]["denominator"]
            per.append(round(hl["no_touch_committed"] / hl["committed"], 3) if hl["committed"] else None)
        tot["no_touch_ratio"] = round(tot["no_touch_committed"] / max(1, tot["committed"]), 3)
        tot["per_seed_min"] = min(x for x in per if x is not None) if any(x is not None for x in per) else None
        out[name] = tot
    from turnaround.evaluation.provenance import stamp
    return {"provenance": stamp(load_policy()), "seeds": [seeds[0], seeds[-1]], **out}


def m3(seed: int, days: int) -> dict:
    """보수적으로 시작한 병원(공식 확정 = 사람, 점수 근접 = 담당 검토)에서 자율권 곡선."""
    from turnaround.evaluation.autonomy_curve import autonomy_curve
    from turnaround.sim.humans import ReviewerModel
    base = load_policy()
    start = base.model_copy(update={"autonomy": {**base.autonomy, "official_commit": "human"},
                                    "review_reasons": [*base.review_reasons, "close_scores"]})
    cfg = GeneratorConfig.realistic().model_copy(update={"days": days, "admission_count": 3 * days})
    from turnaround.evaluation.provenance import stamp
    return {"provenance": stamp(start), **autonomy_curve(seed, days, start, ReviewerModel(salt=str(seed)), cfg)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["m1", "m3"], required=True)
    ap.add_argument("--seeds", nargs=2, type=int, default=[0, 20])
    ap.add_argument("--days", type=int, default=60)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seeds = list(range(*a.seeds))
    if a.part == "m1":
        r = m1(seeds)
        (OUT / "m1.json").write_text(json.dumps(r, ensure_ascii=False, indent=1))
    else:
        for sd in seeds:
            r = m3(sd, a.days)
            (OUT / f"m3_seed{sd}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str))
    print("done")
