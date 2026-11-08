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
}
# 병원 밖이나 다른 부서로 나가는 부수효과 (중복 검사 대상)
SIDE_EFFECTS = {EventType.CLEANING_REQUEST_ISSUED, EventType.REGISTRATION_CHECK_REQUESTED}
_PARAM_KEYS = ("cleaning_type", "reason", "target")
_META = {"count", "count_over"}


@dataclass(frozen=True)
class Produced:
    type: str
    case: str | None = None
    bed: str | None = None
    params: dict = field(default_factory=dict, hash=False, compare=True)


@dataclass
class ScoreResult:
    passed: bool
    failures: list[str]


def actions_from_events(events: Iterable[Event], alias: dict[str, str] | None = None) -> list[Produced]:
    alias = alias or {}
    out: list[Produced] = []
    for e in events:
        name = EVENT_TO_ACTION.get(e.type)
        if name is None or e.source not in ("saga", "gateway", "agent"):
            continue
        case = alias.get(e.case_id, e.case_id)
        bed = e.payload.get("bed_id")
        params = {k: e.payload[k] for k in _PARAM_KEYS if k in e.payload}
        out.append(Produced(name, case, bed, params))
        if e.type in SIDE_EFFECTS:
            out.append(Produced("execute_side_effect", case, bed))
    return out


def _matches(gold: Action, p: Produced) -> bool:
    if gold.type != p.type:
        return False
    if gold.case is not None and gold.case != p.case:
        return False
    if gold.bed is not None and gold.bed != p.bed:
        return False
    return all(p.params.get(k) == v for k, v in gold.params.items() if k not in _META)


def _label(a: Action) -> str:
    return " ".join(x for x in (a.type, a.case, a.bed) if x) + (f" {a.params}" if a.params else "")


def score(gold: Gold, produced: list[Produced]) -> ScoreResult:
    fails: list[str] = []
    for a in gold.must_actions:
        n = sum(_matches(a, p) for p in produced)
        want = a.params.get("count")
        if want is not None and n != want:
            fails.append(f"해야 할 행동 개수 불일치: {_label(a)} (기대 {want}, 실제 {n})")
        elif want is None and n == 0:
            fails.append(f"해야 할 행동 없음: {_label(a)}")
    for a in gold.must_not:
        n = sum(_matches(a, p) for p in produced)
        limit = a.params.get("count_over", 0)
        if n > limit:
            fails.append(f"하면 안 되는 행동 발생: {_label(a)} ({n}회)")
    for case, tier in gold.tier_by_case.items():
        if tier is Tier.REVIEW and not any(p.case == case and p.type in ("route_review", "reclassify_review")
                                           for p in produced):
            fails.append(f"담당 검토 경로 없음: {case}")
    return ScoreResult(passed=not fails, failures=fails)
