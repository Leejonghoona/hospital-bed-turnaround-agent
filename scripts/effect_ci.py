"""효과의 짝 비교 (2회차 방법론 검토): 같은 시드의 켬/끔 결과로 차이, 개선·같음·악화 시드 수, 부트스트랩 95% 구간.

python scripts/effect_ci.py <켬.json> <끔.json> [--n 10000]
결과 수치는 합성 데이터 기준 추정이고, 효과 크기는 설계 가정(가정한 효과 크기의 결과)이다.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics


def paired(on: dict, off: dict, n: int = 10000, seed: int = 0) -> dict:
    a, b = on["entry_wait_by_seed"], off["entry_wait_by_seed"]
    keys = sorted((k for k in a if a[k] is not None and b.get(k) is not None), key=int)
    d = [a[k] - b[k] for k in keys]
    rng = random.Random(seed)
    boots = sorted(statistics.mean(rng.choice(d) for _ in d) for _ in range(n))
    mean_off = statistics.mean(b[k] for k in keys)
    return {"pairs": len(keys), "missing_seeds": sorted(set(a) - set(keys), key=int),
            "mean_on": round(statistics.mean(a[k] for k in keys), 1), "mean_off": round(mean_off, 1),
            "mean_diff_min": round(statistics.mean(d), 1), "ci95_min": [round(boots[int(.025 * n)], 1), round(boots[int(.975 * n)], 1)],
            "rel_pct": round(100 * statistics.mean(d) / mean_off, 1),
            "improved": sum(x < 0 for x in d), "same": sum(x == 0 for x in d), "worse": sum(x > 0 for x in d)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("on")
    ap.add_argument("off")
    ap.add_argument("--n", type=int, default=10000)
    a = ap.parse_args()
    print(json.dumps(paired(json.load(open(a.on)), json.load(open(a.off)), a.n), ensure_ascii=False))
