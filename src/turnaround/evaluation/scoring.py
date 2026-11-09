"""시나리오 채점기. 시스템이 남긴 이벤트를 행동 어휘로 바꾸고 정답 라벨과 맞춰 본다.

이 모듈은 평가 대상 코드의 판단 로직을 쓰지 않는다. 이벤트 종류 → 행동 이름의 대응표만 안다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from turnaround.events.models import Event, EventType

from .scenario import Action, Gold, Tier

# 사가·게이트웨이 이벤트 → 행동 어휘
EVENT_TO_ACTION: dict[EventType, str] = {
    EventType.CLEANING_REQUEST_ISSUED: "send_cleaning_request",
    EventType.REGISTRATION_CHECK_REQUESTED: "request_registration_check",
    EventType.READY_TIME_RECOMPUTED: "recompute_ready_time",
    EventType.BED_MARKED_VACANT: "mark_bed_vacant",
    EventType.SCHEDULE_ITEM_RELEASED: "release_schedule_item",
    EventType.PREMATCH_RELEASED: "release_prematch",
    EventType.CLEANING_REQUEST_CANCELLED: "cancel_cleaning_request",
    EventType.CASE_RECLASSIFIED_REVIEW: "reclassify_review",
    EventType.HUMAN_TASK_CREATED: "create_human_task",
    EventType.CASE_RESUMED: "resume_case",
    EventType.WARD_NOTIFIED: "notify_ward",
    EventType.SHARE_WAITING_INFO: "share_waiting_info",
    EventType.DELAY_REASON_RECORDED: "record_delay_reason",
    EventType.REJECTED_AT_PROPOSAL: "reject_at_proposal",
    EventType.REJECTED_AT_COMMIT: "reject_at_commit",
    EventType.CASE_ROUTED_REVIEW: "route_review",
    EventType.WARD_ACCEPTANCE_ASKED: "ask_ward_acceptance",
    EventType.MATCH_COMMITTED: "commit_match",
    EventType.NO_BED_REPORTED: "report_no_bed",
    EventType.QUERY_REFUSED: "refuse_query",
    EventType.QUERY_ANSWERED: "answer_with_evidence",
    EventType.QUERY_NO_RECORD: "answer_no_record",
    EventType.MANUAL_OVERRIDE_APPLIED: "apply_manual_override",
    EventType.TASK_HANDED_OVER: "hand_over_task",
    EventType.TASK_ESCALATED: "escalate_task",
    # 설계 개정 (추가만)
    EventType.BED_PREP_REQUESTED: "request_bed_prep",
    EventType.BED_VACANCY_REVOKED: "revoke_vacancy",
    EventType.WARD_ASSIGNMENT_NOTIFIED: "notify_ward_assignment",
    EventType.CONTACT_TRACE_REQUESTED: "request_contact_trace",
    EventType.ISOLATION_RISK_ALERTED: "alert_isolation_risk",
    EventType.ISOLATION_REGISTRATION_MISMATCH: "alert_isolation_mismatch",
    EventType.EXCEPTION_RAISED: "raise_exception",
    EventType.ACTION_EXECUTED: "execute_action",
    EventType.ACTION_REJECTED: "reject_action",
    EventType.ACTION_APPROVAL_REQUESTED: "request_action_approval",
    EventType.PROMISE_MADE: "make_promise",
    EventType.PROMISE_AT_RISK: "flag_promise_at_risk",
}
# 병원 밖이나 다른 부서로 나가는 부수효과 (중복 검사 대상)
SIDE_EFFECTS = {EventType.CLEANING_REQUEST_ISSUED, EventType.REGISTRATION_CHECK_REQUESTED, EventType.WARD_NOTIFIED,
                EventType.SHARE_WAITING_INFO, EventType.WARD_ACCEPTANCE_ASKED}
_PARAM_KEYS = ("cleaning_type", "reason", "target", "aerosol_wait_min", "sporicidal", "kind", "overridden_from",
               "to", "discharge_type")
_META = {"count", "count_over"}


@dataclass(frozen=True)
class Produced:
    type: str
    case: str | None = None
    bed: str | None = None
    params: dict = field(default_factory=dict, hash=False, compare=True)
    level: str = "executed"  # proposal: 에이전트 제안, executed: 실행·기록됨
    at: object = None  # 순서 비교용 (이벤트 순번이나 시각)


@dataclass
class ScoreResult:
    passed: bool
    failures: list[str]


def actions_from_events(events: Iterable[Event], alias: dict[str, str] | None = None) -> list[Produced]:
    alias = alias or {}
    out: list[Produced] = []
    for e in events:
        if e.type is EventType.PROPOSAL_RECEIVED and e.source == "agent":
            # 계획 에이전트의 제안 (실행 전). 실행기는 주입판 제안을 미리 빼고 넘긴다
            out.append(Produced("propose_match", alias.get(e.case_id, e.case_id), e.payload.get("bed_id"),
                                level="proposal", at=e.seq))
            continue
        name = EVENT_TO_ACTION.get(e.type)
        if name is None or e.source not in ("saga", "gateway", "agent"):
            continue
        case = alias.get(e.case_id, e.case_id)
        bed = e.payload.get("bed_id")
        params = {k: e.payload[k] for k in _PARAM_KEYS if k in e.payload}
        out.append(Produced(name, case, bed, params, at=e.seq))
        if e.type in SIDE_EFFECTS:
            out.append(Produced("execute_side_effect", case, bed, at=e.seq))
    return out


def _matches(gold: Action, p: Produced) -> bool:
    if gold.type != p.type:
        return False
    if gold.level is not None and gold.level != p.level:
        return False
    if gold.case is not None and gold.case != p.case:
        return False
    if gold.bed is not None and gold.bed != p.bed:
        return False
    return all(p.params.get(k) == v for k, v in gold.params.items() if k not in _META)


def _label(a: Action) -> str:
    return " ".join(x for x in (a.type, a.case, a.bed) if x) + (f" {a.params}" if a.params else "")


def _in_stage(a: Action, upto: float | None) -> bool:
    return upto is None or a.stage is None or a.stage <= upto


_BED_ACTIONS = {"propose_match", "commit_match", "ask_ward_acceptance"}


def score(gold: Gold, produced: list[Produced], upto_stage: float | None = None) -> ScoreResult:
    """upto_stage를 주면 그 단계까지 채점할 수 있는 행동만 본다."""
    fails: list[str] = []
    for a in (x for x in gold.must_actions if _in_stage(x, upto_stage)):
        hits = [p for p in produced if _matches(a, p)]
        want = a.params.get("count")
        if want is not None and len(hits) != want:
            fails.append(f"해야 할 행동 개수 불일치: {_label(a)} (기대 {want}, 실제 {len(hits)})")
        elif want is None and not hits:
            fails.append(f"해야 할 행동 없음: {_label(a)}")
        elif a.after:
            before = [p.at for p in produced if p.type == a.after and (a.case is None or p.case == a.case)]
            if not before or not any(b is not None and h.at is not None and b < h.at for h in hits for b in before):
                fails.append(f"순서 위반: {_label(a)}는 {a.after} 뒤여야 한다")
    for a in (x for x in gold.must_not if _in_stage(x, upto_stage)):
        n = sum(_matches(a, p) for p in produced)
        if n > a.params.get("count_over", 0):
            fails.append(f"하면 안 되는 행동 발생: {_label(a)} ({n}회)")
    for case, beds in gold.acceptable_beds.items():
        for p in produced:
            if p.case == case and p.type in _BED_ACTIONS and p.bed is not None and p.bed not in beds:
                fails.append(f"허용 병상 밖: {case} → {p.bed} ({p.type})")
    for case, tier in gold.tier_by_case.items():
        if tier is Tier.REVIEW and not any(p.case == case and p.type in ("route_review", "reclassify_review")
                                           for p in produced):
            fails.append(f"담당 검토 경로 없음: {case}")
    return ScoreResult(passed=not fails, failures=fails)
