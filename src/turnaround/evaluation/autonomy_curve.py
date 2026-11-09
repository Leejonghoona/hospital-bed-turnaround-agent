"""자율권 곡선 (로드맵 07 M3): 보수적으로 시작한 병원에서 에이전트가 자율권을 증명해 넓혀 가는 과정을 날마다 재생한다.

하루씩 재생하며 그날 입원의 사람 결정 기록을 모은다. 승격 권고가 나오면 재생 증명을 붙인다:
지난 며칠을 새 정책으로 다시 돌려 실행 안전(S1·S2) 위반 0이고 사람 손 없이 확정한 비율이 낮아지지 않아야 한다.
증명을 통과한 제안은 사람(병상관리 책임자)이 승인한다고 보고 다음 날부터 적용한다. 강등은 바로 적용한다.
사람 응답(직접 수정·이의 비율)은 설계 가정이다(`sim.humans.ReviewerModel`). 합성 데이터 기준 추정.
"""

from __future__ import annotations

from datetime import time

from turnaround.agent.autonomy import apply, evaluate, unit_outcomes
from turnaround.evaluation.human_load import human_load
from turnaround.evaluation.safety_exec import executed_safety


def _run(seed, day, policy, cfg, reviewer):
    from turnaround.ui.demo import build_demo
    st = build_demo(seed=seed, day=day, until=time(23, 45), via_fhir=False, policy=policy, config=cfg,
                    reviewer=reviewer, past_admissions=False)
    ev = st.store.read()
    cases = {a.id for a in st.h.admissions if a.day == day}
    mine = [e for e in ev if e.case_id in cases]
    hl = human_load(mine)
    s = executed_safety(st.gw, ev)
    return ev, cases, hl, s


def autonomy_curve(seed: int, days: int, start_policy, reviewer, cfg, proof_days: int = 2) -> dict:
    policy = start_policy
    outcomes = []
    since: dict[str, int] = {}  # 단위별 마지막 강등 시점
    curve, log = [], []
    for day in range(1, days):
        ev, cases, hl, s = _run(seed, day, policy, cfg, reviewer)
        outcomes += unit_outcomes(ev, cases)
        auto_units = sorted(r["unit"] for r in evaluate(outcomes, policy, since) if r["mode"] == "auto")
        curve.append({"day": day, "admissions": hl["admission_cases"], "committed": hl["committed"],
                      "no_touch": hl["no_touch_committed"], "S1": s["S1"]["violations"], "S2": s["S2"]["violations"],
                      "auto_units": auto_units})
        for row in evaluate(outcomes, policy, since):
            if row["recommendation"] == "demote":
                policy = apply(policy, row["unit"], "human")
                since[row["unit"]] = len(outcomes)
                log.append({"day": day, "unit": row["unit"], "action": "demoted", "evidence": row})
            elif row["recommendation"] == "promote":
                cand = apply(policy, row["unit"], "auto")
                proof = []
                for d in range(max(1, day - proof_days + 1), day + 1):
                    _, _, hl_new, s_new = _run(seed, d, cand, cfg, reviewer)
                    _, _, hl_old, _ = _run(seed, d, policy, cfg, reviewer)
                    proof.append({"day": d, "S1": s_new["S1"]["violations"], "S2": s_new["S2"]["violations"],
                                  "no_touch_old": hl_old["no_touch_committed"], "no_touch_new": hl_new["no_touch_committed"]})
                ok = all(p["S1"] == 0 and p["S2"] == 0 and p["no_touch_new"] >= p["no_touch_old"] for p in proof)
                log.append({"day": day, "unit": row["unit"], "action": "promoted" if ok else "proof_failed",
                            "evidence": row, "replay_proof": proof})
                if ok:
                    policy = cand
    return {"seed": seed, "days": days, "curve": curve, "log": log}


__all__ = ["autonomy_curve"]
