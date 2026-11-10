"""입실 대기 쪼개 보기 (2026-11-10 사용자 질문 "수속부터 입실까지 왜 이렇게 안 줄어드나"). 결과는 합성 데이터 기준 추정.

python scripts/measure_entry_breakdown.py --seeds 0 50 --day 3 > evals/results/final/entry_breakdown.json
python scripts/measure_entry_breakdown.py --ops --seeds 0 50 --day 3 > evals/results/final/entry_breakdown_realistic.json

--ops: 운영 현실 옵션(침상 정리 일괄 회차, 청소 인력 시간대, 에이전트 수단 두 가지)을 켠 병원 (DECISIONS 2026-11-10).

같은 병원을 에이전트 없이(기준선)와 있게(반응형 효과) 돌려, 입원마다 다음을 잰다.
- 예정 수속부터 입실: 기존 지표(입원 예정 시각부터). 원무가 병상 사정으로 늦게 오라고 안내한 시간(집에서 기다린 시간)까지 들어간다.
- 안내 시각부터 입실: 환자에게 안내한 수속 시각부터 입실까지. 병원에 와서 기다린 시간에 가깝다.
- 수속 때 병상 준비: 안내 시각에 병상이 이미 준비돼 있던 비율.
- 구간: 앞 환자 퇴실, 청소 요청, 청소 시작, 청소 끝, 병상 준비(예정 수속 기준 분).
"""
from __future__ import annotations

import argparse
import json
import statistics as S
from collections import Counter
from datetime import datetime, time

from turnaround.evaluation.safety_exec import executed_safety
from turnaround.events import EventType as E
from turnaround.gateway import load_policy
from turnaround.saga import fold
from turnaround.sim import GeneratorConfig
from turnaround.sim.reactive import ReactiveEffects
from turnaround.ui.demo import build_demo


def rows_for(seed: int, day: int, reactive: bool, ops: bool = False, safety: Counter | None = None,
             fx: ReactiveEffects | None = None) -> list[dict]:
    cfg = GeneratorConfig.realistic(ops_realism=True) if ops else GeneratorConfig.realistic()
    st = build_demo(seed=seed, day=day, until=time(23, 45), via_fhir=False, policy=load_policy(),
                    config=cfg, reactive=(fx or ReactiveEffects()) if reactive else None,
                    past_admissions=False, occupancy="full")
    ev = st.store.read()
    if safety is not None:  # 실행 상태 안전 S1~S4 (measure_cycle.py와 같은 계산)
        sx = executed_safety(st.gw, ev)
        for k in ("S1", "S2", "S3", "S4"):
            safety[k] += sx[k]["violations"]
            safety[k + "_n"] += sx[k]["denominator"]
    s = fold(ev)
    first: dict = {}
    for e in ev:
        first.setdefault((e.case_id, e.type), e)
    out = []
    for a in st.h.admissions:
        if a.day != day or a.cancelled_at is not None:
            continue
        arr, m = first.get((a.id, E.PATIENT_ARRIVED)), s.matches.get(a.id)
        if arr is None or m is None:
            continue
        mn = lambda t: (t - a.requested_at).total_seconds() / 60 if t else None  # noqa: E731
        con = first.get((a.id, E.PATIENT_CONTACTED))
        prom = datetime.fromisoformat(con.payload["promised_at"]) if con and con.payload.get("promised_at") else a.requested_at
        dis = [d for d in s.discharges.values() if d.bed_id == m.bed_id and E.PATIENT_DEPARTED in d.facts
               and d.facts[E.PATIENT_DEPARTED].date() == a.requested_at.date()]
        r = {"seed": seed, "case": a.id, "from_due": mn(arr.observed_at),
             "from_promised": (arr.observed_at - max(prom, a.requested_at)).total_seconds() / 60, "promised": mn(prom)}
        if dis:
            d = dis[0]
            rdy = d.seen(E.BED_MARKED_VACANT) if d.isolation else (d.seen(E.BED_READY) or d.seen(E.BED_MARKED_VACANT))
            r.update(departed=mn(d.facts[E.PATIENT_DEPARTED]), clean_req=mn(d.seen(E.CLEANING_REQUEST_ISSUED) or d.seen(E.CLEANING_REQUESTED)),
                     clean_start=mn(d.seen(E.CLEANING_STARTED)), clean_done=mn(d.seen(E.CLEANING_DONE)), ready=mn(rdy),
                     ready_at_checkin=bool(rdy and rdy <= max(prom, a.requested_at)))
        else:
            r.update(ready_at_checkin=True)
        out.append(r)
    return out


def summary(rows: list[dict]) -> dict:
    mean = lambda k: round(S.mean([r[k] for r in rows if r.get(k) is not None]), 1)  # noqa: E731
    med = lambda k: round(S.median([r[k] for r in rows if r.get(k) is not None]), 1)  # noqa: E731
    gap = lambda a, b: round(S.mean([r[b] - r[a] for r in rows if r.get(a) is not None and r.get(b) is not None]), 1)  # noqa: E731
    return {"n": len(rows), "from_due_mean": mean("from_due"), "from_due_median": med("from_due"),
            "from_promised_mean": mean("from_promised"), "from_promised_median": med("from_promised"),
            "ready_at_checkin": round(sum(r["ready_at_checkin"] for r in rows) / len(rows), 3),
            "departed_to_request": gap("departed", "clean_req"), "request_to_start": gap("clean_req", "clean_start"),
            "cleaning": gap("clean_start", "clean_done"), "done_to_ready": gap("clean_done", "ready")}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--seeds", nargs=2, type=int, default=[0, 50])
    p.add_argument("--day", type=int, default=3)
    p.add_argument("--ops", action="store_true", help="운영 현실 옵션을 켠 병원")
    a = p.parse_args()
    from turnaround.evaluation.provenance import stamp
    res = {"provenance": stamp(load_policy()), "seeds": a.seeds, "day": a.day, "note": "합성 데이터 기준 추정"}
    if a.ops:
        res["ops_realism"] = True
    for name, reactive in (("off", False), ("on", True)):
        safety: Counter = Counter()
        rows = [r for seed in range(*a.seeds) for r in rows_for(seed, a.day, reactive, a.ops, safety)]
        res[name] = summary(rows)
        if a.ops:  # 기존 결과 파일 형식은 그대로 둔다
            res[name]["safety"] = dict(safety)
            # 안내 시각보다 먼저 불려 와서 음수가 된 건을 0으로 본 평균(병원에 와서 기다린 시간은 음수가 될 수 없다)
            res[name]["from_promised_clip0_mean"] = round(S.mean(max(0.0, r["from_promised"]) for r in rows), 1)
            res[name]["from_promised_p90"] = round(S.quantiles([r["from_promised"] for r in rows], n=10)[-1], 1)
    if a.ops:  # 수단별 쪼개 보기와 미리 알린 이득 없는 민감도 (에이전트 켬만)
        old = frozenset({"order_nudge", "lounge", "cleaning_request"})
        variants = {"old_levers_only": ReactiveEffects(enabled=old),
                    "plus_bed_prep_request": ReactiveEffects(enabled=old | {"bed_prep_request"}),
                    "plus_schedule_share": ReactiveEffects(enabled=old | {"schedule_share"}),
                    "all_no_advance_notice": ReactiveEffects(bed_prep_min_response_min=None)}
        res["variants"] = {}
        for name, fx in variants.items():
            rows = [r for seed in range(*a.seeds) for r in rows_for(seed, a.day, True, True, fx=fx)]
            sm = summary(rows)
            res["variants"][name] = {k: sm[k] for k in ("n", "from_promised_mean", "from_promised_median",
                                                        "ready_at_checkin", "request_to_start", "done_to_ready")}
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
