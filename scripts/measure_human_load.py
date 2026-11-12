"""사람에게 넘어간 일 측정 (엔드픽처 E2, 2026-11-12). 결과 수치는 합성 데이터 기준 추정.

엔드픽처: 사람은 책임질 순간(환자 연락·동의, 안전 예외, 시스템이 "확인 필요"로 올린 판단)에만 개입한다.
그래서 사람에게 생긴 작업을 네 갈래로 나눠 센다.
  책임   환자 연락·재연락, 차액 동의, 격리·안전 예외 결정
  판단   시스템이 "확인 필요"로 올린 것(예외 판단 넘김, 검토로 간 입원)
  조율   사람이 "완료"를 눌러야 하는 재촉·감시 작업(엔드픽처에서는 에이전트가 할 일. 줄여야 할 일)
  보완   입력이 빠져 사람이 데이터를 고쳐야 하는 일
  알림   에이전트가 보낸 통지(사람이 처리할 일이 아님, 조건이 풀리면 사라짐). 작업 수에서 따로 센다

python scripts/measure_human_load.py --seeds 0 30 [--out evals/results/human_load/<이름>.json]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import time

from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.sim import GeneratorConfig
from turnaround.sim.reactive import ReactiveEffects
from turnaround.saga.worklist import is_notice
from turnaround.ui.demo import build_demo

RESPONSIBLE = {"recontact", "recontact_prepare", "postpone_contact", "fee_consent", "isolation_notice",
               "isolation_mismatch", "isolation_order_after_commit", "approval_rejected"}
DATA = {"data_fix"}


def category(kind: str) -> str:
    if kind in DATA:
        return "보완"
    if kind in RESPONSIBLE or kind.startswith("isolation") or kind.startswith("safety"):
        return "책임"
    if kind == "brain_escalate":
        return "판단"
    return "조율"


def run(seeds, day=3, policy=None):
    pol = policy or load_policy()
    by_kind, by_cat, uniq_cat = Counter(), Counter(), Counter()
    admissions = discharges = routed = 0
    for seed in seeds:
        st = build_demo(seed=seed, day=day, until=time(23, 45), via_fhir=False, policy=pol,
                        config=GeneratorConfig.realistic(), reactive=ReactiveEffects(), past_admissions=False,
                        occupancy="full")
        ev = st.store.read()
        day_adm = {a.id for a in st.h.admissions if a.day == day and a.cancelled_at is None}
        admissions += len(day_adm)
        discharges += sum(1 for d in st.h.discharges if d.day == day)
        routed += len({e.case_id for e in ev if e.type is EventType.CASE_ROUTED_REVIEW and e.case_id in day_adm})
        seen = set()
        for e in ev:
            if e.type is not EventType.HUMAN_TASK_CREATED:
                continue
            kind = str(e.payload.get("kind") or e.payload.get("reason") or "unknown")
            cat = "알림" if is_notice(e) else category(kind)
            by_kind[kind] += 1
            by_cat[cat] += 1
            if (e.case_id, kind) not in seen:  # 같은 사례·같은 종류는 한 번만 (중복 알림을 걸러낸 값)
                seen.add((e.case_id, kind))
                uniq_cat[cat] += 1
    work = {k: v for k, v in by_cat.items() if k != "알림"}
    work_u = {k: v for k, v in uniq_cat.items() if k != "알림"}
    total, uniq = sum(work.values()), sum(work_u.values())
    from turnaround.evaluation.provenance import stamp
    return {"provenance": stamp(pol), "seeds": [seeds[0], seeds[-1]], "day": day, "admissions": admissions,
            "discharges": discharges, "routed_admissions": routed,
            "notices": by_cat.get("알림", 0), "tasks": total, "tasks_per_admission": round(total / max(admissions, 1), 2),
            "tasks_unique": uniq, "tasks_unique_per_admission": round(uniq / max(admissions, 1), 2),
            "by_category": dict(by_cat), "by_category_unique": dict(uniq_cat),
            "responsible_share_unique": round(uniq_cat["책임"] / max(uniq, 1), 3),
            "by_kind": dict(by_kind.most_common())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", nargs=2, type=int, default=[0, 30])
    ap.add_argument("--day", type=int, default=3)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    text = json.dumps(run(list(range(*a.seeds)), a.day), ensure_ascii=False, indent=1)
    if a.out:
        from pathlib import Path
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
