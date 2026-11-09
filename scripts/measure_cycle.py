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


def run(seeds, day, occupancy, reactive, profile):
    pol = load_policy(profile=profile) if profile else load_policy()
    agg, raised, safety, entry = Counter(), Counter(), Counter(), []
    for seed in seeds:
        st = build_demo(seed=seed, day=day, until=time(23, 45), via_fhir=False, policy=pol,
                        config=GeneratorConfig.realistic(), reactive=ReactiveEffects() if reactive else None,
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
        s = executed_safety(st.gw, ev)
        for k in ("S1", "S2", "S3", "S4"):
            safety[k] += s[k]["violations"]
            safety[k + "_n"] += s[k]["denominator"]
        raised.update(e.payload["trigger"] for e in ev if e.type is EventType.EXCEPTION_RAISED)
    return {"seeds": [seeds[0], seeds[-1]], "day": day, "occupancy": occupancy, "reactive": reactive,
            "profile": profile, **agg, "entry_wait_mean_by_seed": entry, "safety": dict(safety),
            "exceptions": dict(raised)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs=2, type=int, default=[0, 8])
    ap.add_argument("--day", type=int, default=3)
    ap.add_argument("--occupancy", default="full")
    ap.add_argument("--reactive", default="on")
    ap.add_argument("--profile", default="field_default")
    a = ap.parse_args()
    print(json.dumps(run(list(range(*a.seeds)), a.day, a.occupancy, a.reactive == "on", a.profile), ensure_ascii=False))
