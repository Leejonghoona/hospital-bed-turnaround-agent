"""사람 개입 지표 (EVL-5, 평가표 H1).

- 책임 개입(공식 확정, 병동 수용 회신, 환자 연락)과 판단 개입(담당 확인·검토 승인, 직접 수정, 작업 처리)을 나눈다.
  엔드픽처의 "사람은 책임질 순간에만"은 판단 개입을 줄이는 것이다.
- 완전 자동 처리 비율: 판단 개입 없이 확정된 입원 사례 / 확정된 입원 사례.
- 승인에 걸린 시간: 확인 요청부터 승인까지. 기준(기본 30초)보다 짧으면 "형식적 승인 의심"으로 센다.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime

from turnaround.events.models import Event, EventType

JUDGEMENT = {EventType.MANAGER_APPROVED: "approve", EventType.MANUAL_OVERRIDE_APPLIED: "manual_override",
             EventType.HUMAN_TASK_DONE: "task_done", EventType.CORRECTION_CONFIRMED: "confirm_correction"}
ACCOUNTABLE = {EventType.WARD_ACCEPTED: "ward_reply", EventType.WARD_REJECTED: "ward_reply",
               EventType.PATIENT_CONTACTED: "contact", EventType.WARD_OBJECTION_RAISED: "ward_objection"}


def human_load(events: list[Event], rubber_stamp_s: float = 30.0) -> dict:
    admissions = {e.case_id for e in events if e.type is EventType.ADMISSION_REQUESTED}
    commits = [e for e in events if e.type is EventType.MATCH_COMMITTED and e.case_id in admissions]
    committed = {e.case_id for e in commits}
    # 공식 확정은 사람이 한 것만 책임 개입이다. 에이전트 확정은 통보만 받는다 (DECISIONS 2026-11-09)
    human_committed = {e.case_id for e in commits if e.payload.get("committed_by", "human") != "agent"}
    judge: Counter = Counter()
    acct: Counter = Counter()
    kinds: Counter = Counter()
    for e in events:
        if e.type in JUDGEMENT and e.case_id in admissions:
            judge[e.case_id] += 1
            kinds[JUDGEMENT[e.type]] += 1
        elif e.type in ACCOUNTABLE and e.case_id in admissions:
            acct[e.case_id] += 1
            kinds[ACCOUNTABLE[e.type]] += 1
    for c in human_committed:
        acct[c] += 1  # 사람이 한 공식 확정(원무가 HIS에 기록)은 책임 개입
    kinds["official_commit"] = len(human_committed)
    kinds["agent_commit"] = len(committed - human_committed)
    asked: dict[str, datetime] = {}
    waits = []
    for e in events:
        pid = e.payload.get("proposal_id")
        if e.type in (EventType.MANAGER_CONFIRM_REQUESTED, EventType.CASE_ROUTED_REVIEW) and pid:
            asked.setdefault(pid, e.observed_at)
        elif e.type is EventType.MANAGER_APPROVED and pid in asked:
            waits.append((e.observed_at - asked[pid]).total_seconds())
    n = len(admissions) or 1
    return {
        "admission_cases": len(admissions), "committed": len(committed),
        "judgement_per_case": round(sum(judge.values()) / n, 3),
        "accountable_per_case": round(sum(acct.values()) / n, 3),
        "fully_automatic_ratio": round(sum(1 for c in committed if judge[c] == 0) / len(committed), 3) if committed else None,
        "approval_wait_median_s": statistics.median(waits) if waits else None,
        "rubber_stamp_suspects": sum(1 for w in waits if w < rubber_stamp_s),
        "by_kind": dict(kinds),
    }
