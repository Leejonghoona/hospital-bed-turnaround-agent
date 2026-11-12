"""사람 작업 목록: 교대 인계와 마감 초과 에스컬레이션 (SAG-8, LP-25).

사가처럼 상태를 저장하지 않는다. 이벤트를 접어 열린 작업을 계산하고, 결정적 이벤트 ID로 명령을 남긴다.
"""

from __future__ import annotations

from bisect import bisect_left

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

import yaml

from turnaround.events.models import Event, EventType
from turnaround.events.store import EventStore

DEFAULT_OPERATIONS = Path(__file__).resolve().parents[3] / "config" / "operations.yaml"
_SHIFTS = ("day", "evening", "night")


def _t(s: str) -> time:
    return time.fromisoformat(s)


@dataclass(frozen=True)
class OperatingCalendar:
    holidays: frozenset[date] | set[date] = frozenset()
    shift_starts: tuple[time, time, time] = (time(7), time(15), time(23))
    office_hours: tuple[time, time] = (time(9), time(18))

    def is_workday(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def in_office(self, dt: datetime) -> bool:
        return self.is_workday(dt.date()) and self.office_hours[0] <= dt.time() < self.office_hours[1]

    def next_workday_noon(self, dt: datetime) -> datetime:
        d = dt.date() + timedelta(days=1)
        while not self.is_workday(d):
            d += timedelta(days=1)
        return datetime.combine(d, time(12), tzinfo=dt.tzinfo)

    def shift(self, dt: datetime) -> str:
        day, evening, night = self.shift_starts
        t = dt.time()
        if day <= t < evening:
            return "day"
        if evening <= t < night:
            return "evening"
        return "night"

    def shift_changes(self, start: datetime, end: datetime) -> list[datetime]:
        """(start, end] 사이 교대 시각."""
        out, d = [], start.date()
        while d <= end.date():
            for s in self.shift_starts:
                t = datetime.combine(d, s, tzinfo=start.tzinfo)
                if start < t <= end:
                    out.append(t)
            d += timedelta(days=1)
        return sorted(out)


@dataclass(frozen=True)
class Operations:
    calendar: OperatingCalendar
    roles: dict[str, dict[str, str]]
    escalation: dict[str, str]
    task_sla_min: dict[str, int] = field(default_factory=dict)
    staff: dict[str, str] = field(default_factory=dict)  # 승인 권한자 ID → 역할 (설계 가정)

    def holder(self, role: str, dt: datetime) -> str:
        """논리 역할의 그 시각 실제 담당."""
        m = self.roles.get(role)
        if m is None:
            return role
        if "shift" in m:
            return f"{m['shift']}:{self.calendar.shift(dt)}"
        return m["office"] if self.calendar.in_office(dt) else m["off_hours"]

    def due(self, task: Event) -> datetime:
        if task.payload.get("due"):
            return datetime.fromisoformat(task.payload["due"])
        kind = "review" if task.type is EventType.CASE_ROUTED_REVIEW else (task.payload.get("kind") or
                                                                          task.payload.get("reason") or "")
        sla = self.task_sla_min.get(kind,
                                    self.task_sla_min.get("default", 120))
        return task.observed_at + timedelta(minutes=sla)


def load_operations(path: Path = DEFAULT_OPERATIONS) -> Operations:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    c = raw["calendar"]
    cal = OperatingCalendar(
        holidays=frozenset(date.fromisoformat(str(x)) for x in c.get("holidays", [])),
        shift_starts=tuple(_t(c["nursing_shifts"][s]) for s in _SHIFTS),
        office_hours=tuple(_t(x) for x in c["office_hours"]))
    return Operations(cal, raw["roles"], raw["escalation"], raw.get("task_sla_min", {}), raw.get("staff") or {})


# 정책에서 쓰는 다른 이름 → 운영 역할 (상위 담당 이름으로 올린 작업은 그 줄로 간다)
_ALIASES = {"admission_office": "bed_manager", "admission_lead": "bed_manager", "nursing_supervisor": "ward",
            "billing": "cashier"}


def _role(task: Event, ops: "Operations | None" = None) -> str:
    """작업의 논리 역할. 모르는 역할은 원무로 몰래 바꾸지 않고 실패한다 (2단계 현장 검수 H3)."""
    if task.type is EventType.CASE_ROUTED_REVIEW:
        return "bed_manager"
    a = task.payload.get("assignee") or "bed_manager"
    a = _ALIASES.get(a, a)
    known = set(ops.roles) if ops is not None else {"ward", "ipc", "bed_manager", "environment_vendor"}
    if a not in known:
        if ops is None:
            return "bed_manager"
        raise ValueError(f"운영 설정에 없는 역할: {a} (작업 {task.event_id})")
    return a


_TASK_TYPES = (EventType.HUMAN_TASK_CREATED, EventType.CASE_ROUTED_REVIEW)
# 담당 검토는 사례에 대해 사람이 결정하면 닫힌다
_REVIEW_CLOSERS = (EventType.MANAGER_APPROVED, EventType.MATCH_COMMITTED, EventType.MANUAL_OVERRIDE_APPLIED,
                   EventType.ADMISSION_CANCELLED)


# 참고 알림: 사람이 "완료"를 누르는 일이 아니다. 마감·올림·인계 대상이 아니고 조건이 풀리면 사라진다 (현장 검수 H2)
NOTICE_KINDS = ("order_nudge", "step_nudge", "call_up_reminder", "next_ready_share", "cleaning_reminder_notice",
                # 에이전트가 함께 보내는 통지·공유·지켜보기 (2026-11-12, 사람 작업 측정). 사람이 "완료"를 누를 일이 아니라
                # 정보다. 사람이 직접 해야 하는 동행 작업(격리 물품 확인, 보호자 재확인 전화, 재연락 준비)은 작업으로 남긴다
                "nudge_late_discharges", "cleaning_rerequest_notice", "no_bed_eta_notice", "cleaning_eta_share",
                "promise_watch", "cleaning_track", "ipc_reminder")


def is_notice(task: Event) -> bool:
    k = str(task.payload.get("kind") or "")
    return task.payload.get("notice") is True or any(k == n or k.startswith(n + ":") for n in NOTICE_KINDS)


def _closed_at(events: list[Event]) -> dict[str, datetime]:
    out = {e.payload.get("task_id"): e.observed_at for e in events if e.type is EventType.HUMAN_TASK_DONE}
    # 예외에 딸린 작업은 그 예외가 조건이 풀려 닫히면 함께 닫힌다
    cleared = {e.payload.get("exception_id"): e.observed_at for e in events if e.type is EventType.EXCEPTION_RESOLVED
               and e.payload.get("how") == "condition_cleared"}
    for t in events:
        if t.type is EventType.HUMAN_TASK_CREATED and t.payload.get("exception_id") in cleared:
            out.setdefault(t.event_id, max(cleared[t.payload["exception_id"]], t.observed_at))
    reviews = [e for e in events if e.type is EventType.CASE_ROUTED_REVIEW]
    # 사례별 색인 (시연용 큰 병원에서 사건 수가 늘면 전체를 매번 훑는 계산이 제곱으로 느려진다. 판정은 그대로)
    closers: dict[str, list[datetime]] = {}
    for e in events:
        if e.type in _REVIEW_CLOSERS:
            closers.setdefault(e.case_id, []).append(e.observed_at)
    for v in closers.values():
        v.sort()
    by_case: dict[str, list[Event]] = {}
    for rv in reviews:
        by_case.setdefault(rv.case_id, []).append(rv)

    def closer_between(case: str, lo: datetime, hi: datetime) -> bool:
        c = closers.get(case, [])
        i = bisect_left(c, lo)
        return i < len(c) and c[i] <= hi

    for rv in reviews:
        c = closers.get(rv.case_id, [])
        i = bisect_left(c, rv.observed_at)
        t = c[i] if i < len(c) else None
        same = by_case[rv.case_id]
        # 사례마다 열린 검토는 하나다 (2단계 현장 검수 M2·H5). 같은 사유로 다시 올라온 검토는 앞 검토의 연속이라
        # 바로 닫고(앞 검토가 마감·올림을 맡는다), 사유가 바뀐 새 검토가 열리면 앞 검토는 대체되어 닫힌다
        prev = [r for r in same if (r.seq or 0) < (rv.seq or 0)
                and not closer_between(rv.case_id, r.observed_at, rv.observed_at)]
        if prev and sorted(prev[-1].payload.get("reasons") or []) == sorted(rv.payload.get("reasons") or []):
            t = rv.observed_at
        later = min((r.observed_at for r in same if (r.seq or 0) > (rv.seq or 0)
                     and sorted(r.payload.get("reasons") or []) != sorted(rv.payload.get("reasons") or [])),
                    default=None)
        if later is not None and (t is None or later < t):
            t = later
        if t is not None:
            out.setdefault(rv.event_id, t)
    return out


def open_tasks(events: list[Event], now: datetime | None = None) -> dict[str, Event]:
    closed = _closed_at(events)
    return {e.event_id: e for e in events if e.type in _TASK_TYPES
            and not (e.event_id in closed and (now is None or closed[e.event_id] <= now))}


class WorklistRunner:
    """열린 작업을 교대 시각에 인계하고, 마감을 넘기면 상위 담당에게 올린다. 재시작해도 멱등."""

    def __init__(self, store: EventStore, ops: Operations):
        self.store, self.ops = store, ops

    def _cmd(self, known: set[str], task: Event, key: str, etype: EventType, when: datetime, **payload) -> None:
        eid = f"{task.event_id}:wl:{key}"
        if eid in known:
            return
        self.store.append(Event(event_id=eid, case_id=task.case_id, type=etype, occurred_at=when, observed_at=when,
                                source="saga", payload={"task_id": task.event_id, "kind": task.payload.get("kind"),
                                                        **payload}))
        known.add(eid)

    def step(self, now: datetime) -> None:
        events = self.store.read()
        known = {e.event_id for e in events}
        # 완료 판정은 "그 시각까지 인지한 완료"로 한다 (사가 타이머와 같은 원칙)
        done_at = _closed_at(events)
        for t in (e for e in events if e.type in _TASK_TYPES):
            closed = done_at.get(t.event_id)
            if is_notice(t):
                continue  # 참고 알림은 올리지도 인계하지도 않는다
            role = _role(t, self.ops)
            due = self.ops.due(t)
            if now >= due and not (closed and closed <= due):
                self._cmd(known, t, "escalated", EventType.TASK_ESCALATED, due, role=role,
                          **{"from": self.ops.holder(role, due), "to": self.ops.escalation.get(role, "admission_lead")})
            for change in self.ops.calendar.shift_changes(t.observed_at, now):
                if closed and closed <= change:
                    break
                before, after = self.ops.holder(role, change - timedelta(seconds=1)), self.ops.holder(role, change)
                if before != after:
                    self._cmd(known, t, f"handover:{change:%Y%m%d%H%M}", EventType.TASK_HANDED_OVER, change,
                              role=role, **{"from": before, "to": after})
