"""사이클 재생 측정 (재현용, 08 계획 1단계 검수 P5). 결과 수치는 합성 데이터 기준 추정.

python scripts/measure_cycle.py --seeds 0 30 --day 3 [--occupancy full|sparse] [--reactive on|off]
설정을 모두 인자로 받고 결과를 JSON으로 출력한다(RESULTS에 이 명령과 함께 적는다).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import time

from turnaround.evaluation.flow import day_flow
from turnaround.evaluation.safety_exec import executed_safety
from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.sim import GeneratorConfig
from turnaround.sim.reactive import ReactiveEffects
from turnaround.ui.demo import build_demo


def _config(stress: int) -> GeneratorConfig:
    """stress>1이면 격리 비중을 높인 병원(안전 불변식 분모를 키우는 스트레스 재생). 효과 수치에는 쓰지 않는다."""
    if stress <= 1:
        return GeneratorConfig.realistic()
    base = GeneratorConfig.realistic()
    rates = {k: (min(0.3, v * stress) if k in ("airborne", "contact", "droplet", "preemptive") else v)
             for k, v in base.realistic_rates.items()}
    # 격리 퇴실은 생성기 제약상 하루 1건까지: 날마다 1건으로 채운다
    return GeneratorConfig.realistic(isolation_discharge_count=base.days, realistic_rates=rates)


def run(seeds, day, occupancy, reactive, profile, scale=1.0, stress=1):
    pol = load_policy(profile=profile, allow_legacy=profile == "legacy") if profile else load_policy()
    gen_cfg = _config(stress)
    agg, raised, safety, entry, by_seed = Counter(), Counter(), Counter(), [], {}
    for seed in seeds:
        st = build_demo(seed=seed, day=day, until=time(23, 45), via_fhir=False, policy=pol,
                        config=gen_cfg, reactive=ReactiveEffects().scaled(scale) if reactive else None,
                        past_admissions=False, occupancy=occupancy)
        ev = st.store.read()
        f = day_flow(st.h, ev, day)
        dbeds = {d.bed_id for d in st.h.discharges if d.day == day}
        commits = {e.case_id: e.payload.get("bed_id") for e in ev if e.type is EventType.MATCH_COMMITTED}
        day_adm = [a.id for a in st.h.admissions if a.day == day and a.cancelled_at is None]
        agg.update(discharges=f["discharges"], before_noon=f["departed_before_noon"], admissions=len(day_adm),
                   committed=sum(1 for a in day_adm if a in commits),
                   into_discharge_bed=sum(1 for a in day_adm if commits.get(a) in dbeds), arrived=f["arrived"])
        if f["entry_wait_mean_min"] is not None:
            entry.append(f["entry_wait_mean_min"])
        by_seed[str(seed)] = f["entry_wait_mean_min"]  # 시드 키로도 남긴다(짝 비교, 2회차 방법론 검토)
        s = executed_safety(st.gw, ev)
        for k in ("S1", "S2", "S3", "S4"):
            safety[k] += s[k]["violations"]
            safety[k + "_n"] += s[k]["denominator"]
        raised.update(e.payload["trigger"] for e in ev if e.type is EventType.EXCEPTION_RAISED)
    from turnaround.evaluation.provenance import stamp
    return {"provenance": stamp(pol), "seeds": [seeds[0], seeds[-1]], "day": day, "occupancy": occupancy, "reactive": reactive,
            "profile": profile, "effect_scale": scale, "isolation_stress": stress, **agg, "entry_wait_mean_by_seed": entry, "entry_wait_by_seed": by_seed, "safety": dict(safety),
            "exceptions": dict(raised)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs=2, type=int, default=[0, 8])
    ap.add_argument("--day", type=int, default=3)
    ap.add_argument("--occupancy", default="full")
    ap.add_argument("--reactive", default="on")
    ap.add_argument("--profile", default="field_default")
    ap.add_argument("--scale", type=float, default=1.0, help="행동 효과 크기 배수 (민감도: 0.5, 1.5)")
    ap.add_argument("--isolation-stress", type=int, default=1, help="격리 비중 배수(안전 분모 확대용 스트레스 재생)")
    ap.add_argument("--out", default=None, help="결과를 이 파일에도 쓴다 (예: evals/results/final/cycle_50_on.json)")
    a = ap.parse_args()
    text = json.dumps(run(list(range(*a.seeds)), a.day, a.occupancy, a.reactive == "on", a.profile, a.scale, a.isolation_stress), ensure_ascii=False)
    if a.out:
        from pathlib import Path
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
