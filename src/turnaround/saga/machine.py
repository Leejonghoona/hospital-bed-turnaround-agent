"""사례 사가 상태 머신 (구현 우선순위 3단계).

상태는 저장하지 않고 이벤트 기록을 접어서 계산한다(fold). 명령은 결정적 ID를 단 이벤트로
저장해 재시작해도 중복되지 않는다. 판단(누구를 어느 병상에)은 하지 않고, 진행 추적과
타이머, 보상 단계만 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from turnaround.events.corrections import correction_direction
from turnaround.events.models import Event, EventType
from turnaround.events.store import EventStore


class SagaPolicy(BaseModel):
    """타이머 기한. 정책값이며 생성기 분포를 쓰지 않는다."""

    model_config = ConfigDict(frozen=True)
    registration_check_at: time = time(19, 0)  # 전날 19:00 미등록 확인, 청소 예정표 공유
    medication_delay: timedelta = timedelta(hours=2)
    cleaning_overdue_general: timedelta = timedelta(minutes=120)
    cleaning_overdue_terminal: timedelta = timedelta(minutes=180)
    aerosol_wait_min: int = 30  # 비말·공기주의 퇴실 환기 대기 (LP-20, 설계 가정)
    billing_wait: timedelta = timedelta(minutes=60)  # 퇴원약 준비 뒤 수납 대기 기준 (설계 가정)


class DischargeStage(str, Enum):
    DECIDED = "decided"
    REGISTERED = "registered"
    ORDER_CONFIRMED = "order_confirmed"
    DEPARTED = "departed"
    CLEANING_REQUESTED = "cleaning_requested"
    CLEANING = "cleaning"
    CLEANED = "cleaned"
    BED_READY = "bed_ready"
    VACANT = "vacant"
    CANCELLED = "cancelled"


class MatchStage(str, Enum):
    PREMATCHED = "prematched"
    CONTACTED = "contacted"
    COMMITTED = "committed"
    REVIEW = "review"
    RELEASED = "released"


@dataclass
class DischargeCase:
    id: str
    bed_id: str | None = None
    isolation: bool = False
    round: int = 1  # 회차: 퇴원 취소 뒤 재퇴원 결정이면 2 (EVT-5)
    precautions: tuple[str, ...] = ()
    organism: str | None = None
    facts: dict[EventType, datetime] = field(default_factory=dict)  # 사실 → 처음 인지한 시각
    commands: set[str] = field(default_factory=set)  # 이미 낸 명령 키
    eta_updates: list[tuple[str, datetime]] = field(default_factory=list)

    def seen(self, t: EventType) -> datetime | None:
        return self.facts.get(t)

    @property
    def stage(self) -> DischargeStage:
        f = self.facts
        for etype, stage in (
            (EventType.DISCHARGE_CANCELLED, DischargeStage.CANCELLED),
            (EventType.BED_MARKED_VACANT, DischargeStage.VACANT),
            (EventType.BED_READY, DischargeStage.BED_READY),
            (EventType.CLEANING_DONE, DischargeStage.CLEANED),
            (EventType.CLEANING_STARTED, DischargeStage.CLEANING),
            (EventType.CLEANING_REQUEST_ISSUED, DischargeStage.CLEANING_REQUESTED),
            (EventType.CLEANING_REQUESTED, DischargeStage.CLEANING_REQUESTED),
            (EventType.PATIENT_DEPARTED, DischargeStage.DEPARTED),
            (EventType.ORDER_CONFIRMED, DischargeStage.ORDER_CONFIRMED),
            (EventType.DISCHARGE_REGISTERED, DischargeStage.REGISTERED),
        ):
            if etype in f:
                return stage
        return DischargeStage.DECIDED


@dataclass
class MatchCase:
    admission_id: str
    bed_id: str
    discharge_id: str | None
    facts: dict[EventType, datetime] = field(default_factory=dict)

    @property
    def stage(self) -> MatchStage:
        f = self.facts
        if EventType.PREMATCH_RELEASED in f:
            return MatchStage.RELEASED
        if EventType.CASE_RECLASSIFIED_REVIEW in f:
            return MatchStage.REVIEW
        if EventType.MATCH_COMMITTED in f:
            return MatchStage.COMMITTED
        if EventType.PATIENT_CONTACTED in f:
            return MatchStage.CONTACTED
        return MatchStage.PREMATCHED

    @property
    def active(self) -> bool:
        return self.stage in (MatchStage.PREMATCHED, MatchStage.CONTACTED, MatchStage.COMMITTED)


@dataclass
class SagaState:
    discharges: dict[str, DischargeCase] = field(default_factory=dict)
    matches: dict[str, MatchCase] = field(default_factory=dict)  # 입원 요청 ID → 현재 매칭
    admission_facts: dict[str, dict[EventType, datetime]] = field(default_factory=dict)
    admission_payload: dict[str, dict] = field(default_factory=dict)
    corrections: list[Event] = field(default_factory=list)  # 입력 정정 (EVT-4)
    pins: dict[str, str] = field(default_factory=dict)  # 고정된 입원 사례 → 직접 수정한 사람 (GW-11)
    pin_triggers: list[Event] = field(default_factory=list)  # 고정 뒤 생긴 안전 관련 변화
    confirmed_corrections: set[str] = field(default_factory=set)


_MATCH_TYPES = {EventType.PREMATCH_CREATED, EventType.PATIENT_CONTACTED, EventType.MATCH_COMMITTED,
                EventType.PREMATCH_RELEASED, EventType.CASE_RECLASSIFIED_REVIEW, EventType.WARD_ACCEPTED}


_PIN_TRIGGERS = {EventType.ISOLATION_ORDER_ADDED, EventType.PATIENT_INFO_CORRECTED, EventType.ISOLATION_RELEASED}


def _bed_discharge(s: SagaState, bed: str | None) -> str | None:
    for d in s.discharges.values():
        if d.bed_id == bed and EventType.DISCHARGE_CANCELLED not in d.facts:
            return d.id
    return None


def fold(events: Iterable[Event]) -> SagaState:
    s = SagaState()
    for ev in sorted(events, key=lambda x: (x.seq or 0, x.observed_at)):
        if ev.type is EventType.MANUAL_OVERRIDE_APPLIED:
            s.pins[ev.case_id] = ev.payload.get("actor", "")
            continue
        if ev.type is EventType.MANUAL_PIN_RELEASED:
            s.pins.pop(ev.case_id, None)
            continue
        if ev.type in _PIN_TRIGGERS and ev.case_id in s.pins:
            s.pin_triggers.append(ev)
        if ev.type is EventType.MATCH_COMMITTED and ev.source == "gateway":
            # 게이트웨이 확정과 직접 수정 배정도 매칭으로 본다 (SAG-7)
            bed = ev.payload.get("bed_id")
            cur = s.matches.get(ev.case_id)
            if cur is None or cur.bed_id != bed or not cur.active:
                s.matches[ev.case_id] = MatchCase(ev.case_id, bed, ev.payload.get("discharge_id") or _bed_discharge(s, bed),
                                                  {ev.type: ev.observed_at})
            else:
                cur.facts.setdefault(ev.type, ev.observed_at)
            continue
        if ev.type is EventType.PREMATCH_CREATED:
            s.matches[ev.case_id] = MatchCase(ev.case_id, ev.payload["bed_id"], ev.payload.get("discharge_id"),
                                              {ev.type: ev.observed_at})
            continue
        if ev.case_id in s.matches and ev.type in _MATCH_TYPES:
            s.matches[ev.case_id].facts.setdefault(ev.type, ev.observed_at)
            continue
        if ev.type in (EventType.ADMISSION_REQUESTED, EventType.ADMISSION_CANCELLED):
            s.admission_facts.setdefault(ev.case_id, {}).setdefault(ev.type, ev.observed_at)
            if ev.type is EventType.ADMISSION_REQUESTED:
                s.admission_payload[ev.case_id] = dict(ev.payload)
            continue
        if ev.type is EventType.PATIENT_INFO_CORRECTED:
            s.corrections.append(ev)
            continue
        if ev.type is EventType.CORRECTION_CONFIRMED:
            s.confirmed_corrections.add(ev.payload.get("correction_id"))
            continue
        if ev.type is EventType.DISCHARGE_DECIDED:
            r = int(ev.payload.get("round", 1))
            old = s.discharges.get(ev.case_id)
            if old is not None and r > old.round:
                s.discharges[ev.case_id] = DischargeCase(ev.case_id, round=r)  # 새 회차: 이전 사실을 비운다
            d = s.discharges.setdefault(ev.case_id, DischargeCase(ev.case_id, round=r))
            d.bed_id = ev.payload.get("bed_id")
            d.isolation = bool(ev.payload.get("isolation_order"))
            d.precautions = tuple(ev.payload.get("precautions") or ())
            d.organism = ev.payload.get("organism")
        d = s.discharges.get(ev.case_id)
        if d is None:
            continue
        if d.round > 1:  # 이전 회차의 이벤트는 무시
            if ev.source == "saga" and not ev.event_id.startswith(f"{d.id}:r{d.round}:"):
                continue
            if ev.source != "saga" and int(ev.payload.get("round", 1)) < d.round:
                continue
        if ev.type is EventType.ISOLATION_ORDER_ADDED:
            d.isolation = True
        if ev.type is EventType.GUARDIAN_ETA_UPDATED:
            d.eta_updates.append((ev.event_id.split(":", 1)[1], ev.observed_at))
        if ev.source == "saga":
            d.commands.add(ev.event_id)
        d.facts.setdefault(ev.type, ev.observed_at)
    return s


class _Out:
    def __init__(self, now: datetime, known: set[str]):
        self.now, self.known, self.events = now, known, []
        self.scopes: dict[str, str] = {}  # 사례 → 회차 접두사 (2회차부터)

    def cmd(self, case: str, key: str, etype: EventType, occurred: datetime, **payload) -> None:
        scope = self.scopes.get(case)
        eid = f"{case}:{scope}:saga:{key}" if scope else f"{case}:saga:{key}"
        if eid in self.known:
            return
        self.known.add(eid)
        self.events.append(Event(event_id=eid, case_id=case, type=etype, occurred_at=min(occurred, self.now),
                                 observed_at=self.now, source="saga", payload=payload))


def decide(s: SagaState, now: datetime, known: set[str], policy: SagaPolicy = SagaPolicy()) -> list[Event]:
    out = _Out(now, known)
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        if d.round > 1:
            out.scopes[d.id] = f"r{d.round}"
        _decide_discharge(d, s, now, out, policy)
    for t in s.pin_triggers:  # 고정된 배치도 계속 감시: 안전 관련 변화는 직접 수정한 사람에게 (GW-11)
        if t.case_id in s.pins:
            out.cmd(t.case_id, f"task:pinned_review:{t.event_id}", EventType.HUMAN_TASK_CREATED, t.observed_at,
                    reason="pinned_placement_review", assignee=s.pins[t.case_id], trigger=t.type.value)
    for c in s.corrections:  # 제약을 푸는 정정은 사람 확인 작업
        current = s.admission_payload.get(c.case_id) or {}
        if c.event_id not in s.confirmed_corrections and \
                correction_direction(current, c.payload["field"], c.payload.get("value")) == "loosen":
            out.cmd(c.case_id, f"task:confirm_correction:{c.event_id}", EventType.HUMAN_TASK_CREATED, c.observed_at,
                    reason="confirm_correction", correction_id=c.event_id)
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        if EventType.ADMISSION_CANCELLED in s.admission_facts.get(m.admission_id, {}) and m.active:
            out.cmd(m.admission_id, f"prematch_released:{m.bed_id}", EventType.PREMATCH_RELEASED, now,
                    bed_id=m.bed_id, reason="admission_cancelled")
            _notify_ward_if_accepted(m, out, now, "admission_cancelled")
            if m.discharge_id:
                out.cmd(m.discharge_id, f"rematch:{m.admission_id}", EventType.REMATCH_REQUESTED, now,
                        target="bed", bed_id=m.bed_id, discharge_id=m.discharge_id)
    return out.events


def _decide_discharge(d: DischargeCase, s: SagaState, now: datetime, out: _Out, p: SagaPolicy) -> None:
    decided = d.seen(EventType.DISCHARGE_DECIDED)
    if decided is None:
        return
    bed = d.bed_id
    cancelled = d.seen(EventType.DISCHARGE_CANCELLED)
    departed = d.seen(EventType.PATIENT_DEPARTED)
    registered = d.seen(EventType.DISCHARGE_REGISTERED)
    due19 = datetime.combine(decided.date(), p.registration_check_at)

    if cancelled is not None:
        _compensate(d, s, now, out, departed)
        return

    # 타이머는 "기한 시각까지 인지한 사실"로 판정한다. 중단 중 기한이 지나도 재개 뒤 똑같이 발화한다.
    def by(t: EventType, due: datetime) -> bool:
        seen_at = d.seen(t)
        return seen_at is not None and seen_at <= due

    # 전날 19:00: 등록됐으면 청소 예정표 공유, 19:00까지 미등록이면 병동에 확인 요청
    if registered is not None:
        due = max(due19, registered)
        if now >= due:
            out.cmd(d.id, "schedule_item", EventType.SCHEDULE_ITEM_CREATED, due, bed_id=bed)
    if now >= due19 and not by(EventType.DISCHARGE_REGISTERED, due19):
        out.cmd(d.id, "registration_check", EventType.REGISTRATION_CHECK_REQUESTED, due19, bed_id=bed)

    # 퇴원약 2시간 지연
    order = d.seen(EventType.ORDER_CONFIRMED)
    if order and now >= order + p.medication_delay and not by(EventType.MEDICATION_READY, order + p.medication_delay):
        out.cmd(d.id, "recompute:medication_delay", EventType.READY_TIME_RECOMPUTED, order + p.medication_delay,
                bed_id=bed, reason="medication_delay")

    # 보호자 도착 예정 변경: 준비 시각 재계산과 지연 사유 코드 기록
    for key, seen_at in d.eta_updates:
        out.cmd(d.id, f"recompute:guardian_eta:{key}", EventType.READY_TIME_RECOMPUTED, seen_at,
                bed_id=bed, reason="guardian_eta")
        out.cmd(d.id, "delay_reason:guardian", EventType.DELAY_REASON_RECORDED, seen_at, bed_id=bed, reason="guardian")

    # 수납 대기 기준 초과: 다음 환자가 기다리면 원무 화면에 대기 정보 공유 (자동)
    med = d.seen(EventType.MEDICATION_READY)
    if med and now >= med + p.billing_wait and not by(EventType.BILLING_DONE, med + p.billing_wait):
        waiting = any(m.discharge_id == d.id and m.active for m in s.matches.values())
        if waiting:
            out.cmd(d.id, "share:billing", EventType.SHARE_WAITING_INFO, med + p.billing_wait, bed_id=bed,
                    target="원무", reason="billing_wait")
            out.cmd(d.id, "delay_reason:billing", EventType.DELAY_REASON_RECORDED, med + p.billing_wait,
                    bed_id=bed, reason="billing")

    # 퇴실 즉시 청소 요청 (AS-01). 격리면 종말소독 강제
    if departed is not None:
        extra = {}
        if d.isolation and {"droplet", "airborne"} & set(d.precautions):
            extra["aerosol_wait_min"] = p.aerosol_wait_min  # 환기 대기 뒤 청소 (LP-20)
        if d.isolation and d.organism == "CDIFF":
            extra["sporicidal"] = True  # 아포 대응 소독
        out.cmd(d.id, "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, departed, bed_id=bed,
                cleaning_type="terminal" if d.isolation else "general", **extra)
        issued = d.seen(EventType.CLEANING_REQUEST_ISSUED) or departed
        limit = p.cleaning_overdue_terminal if d.isolation else p.cleaning_overdue_general
        done = d.seen(EventType.CLEANING_DONE)
        ready = d.seen(EventType.BED_READY)
        due = issued + limit
        if now >= due and not by(EventType.CLEANING_DONE, due) and not by(EventType.BED_READY, due):
            out.cmd(d.id, "recompute:cleaning_overdue", EventType.READY_TIME_RECOMPUTED, issued + limit,
                    bed_id=bed, reason="cleaning_overdue")
        if done is not None:
            out.cmd(d.id, "recompute:cleaning_done", EventType.READY_TIME_RECOMPUTED, done,
                    bed_id=bed, reason="cleaning_done")
        # 빈 병상 전환: 격리 퇴실은 종말소독 완료 신호가 있어야 한다
        if ready is not None:
            if done is not None or not d.isolation:
                out.cmd(d.id, "bed_vacant", EventType.BED_MARKED_VACANT, max(ready, done or ready), bed_id=bed)
            else:
                out.cmd(d.id, "task:missing_cleaning_done", EventType.HUMAN_TASK_CREATED, ready,
                        bed_id=bed, reason="missing_cleaning_done")


def _compensate(d: DischargeCase, s: SagaState, now: datetime, out: _Out, departed: datetime | None) -> None:
    """퇴원 취소 보상. 연락 전이면 자동 해제, 연락 후면 담당 검토와 재연락 사람 작업."""
    bed = d.bed_id
    if departed is None:
        if d.seen(EventType.SCHEDULE_ITEM_CREATED) and not d.seen(EventType.SCHEDULE_ITEM_RELEASED):
            out.cmd(d.id, "schedule_item_released", EventType.SCHEDULE_ITEM_RELEASED, now, bed_id=bed)
    else:
        if d.seen(EventType.CLEANING_REQUEST_ISSUED) and not d.seen(EventType.CLEANING_STARTED):
            out.cmd(d.id, "cleaning_request_cancelled", EventType.CLEANING_REQUEST_CANCELLED, now, bed_id=bed)
        out.cmd(d.id, "task:cancel_after_departure", EventType.HUMAN_TASK_CREATED, now, bed_id=bed,
                reason="cancel_after_departure")
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        if m.discharge_id != d.id or not m.active:
            continue
        if EventType.PATIENT_CONTACTED in m.facts:
            out.cmd(m.admission_id, f"review:{bed}", EventType.CASE_RECLASSIFIED_REVIEW, now,
                    bed_id=bed, reason="discharge_cancelled_after_contact")
            out.cmd(m.admission_id, f"task:recontact:{bed}", EventType.HUMAN_TASK_CREATED, now,
                    bed_id=bed, reason="recontact")
        else:
            if m.admission_id in s.pins:
                out.cmd(m.admission_id, f"task:pinned_review:cancel:{bed}", EventType.HUMAN_TASK_CREATED, now,
                        reason="pinned_placement_review", assignee=s.pins[m.admission_id], trigger="discharge_cancelled")
            out.cmd(m.admission_id, f"prematch_released:{bed}", EventType.PREMATCH_RELEASED, now,
                    bed_id=bed, reason="discharge_cancelled")
            _notify_ward_if_accepted(m, out, now, "discharge_cancelled")
            out.cmd(m.admission_id, f"rematch:{bed}", EventType.REMATCH_REQUESTED, now,
                    target="admission", admission_id=m.admission_id)


def _notify_ward_if_accepted(m: MatchCase, out: _Out, now: datetime, reason: str) -> None:
    """병동 수용 회신까지 받은 매칭을 해제하면 철회 통지를 보낸다(사용자 결정)."""
    if EventType.WARD_ACCEPTED in m.facts:
        out.cmd(m.admission_id, f"ward_notified:{m.bed_id}", EventType.WARD_NOTIFIED, now,
                bed_id=m.bed_id, reason=reason)


class SagaRunner:
    """저장소를 읽어 상태를 계산하고, 새 명령을 저장한다. 인스턴스에 상태를 두지 않는다."""

    def __init__(self, store: EventStore, policy: SagaPolicy = SagaPolicy(), max_rounds: int = 5):
        self.store, self.policy, self.max_rounds = store, policy, max_rounds

    def step(self, now: datetime) -> list[Event]:
        emitted: list[Event] = []
        for _ in range(self.max_rounds):
            events = self.store.read(as_of=now)
            known = {e.event_id for e in self.store.read()}  # 게이트웨이가 같은 멱등 키로 먼저 낸 명령도 포함
            new = decide(fold(events), now, known, self.policy)
            if not new:
                break
            self.store.extend(new)
            emitted += new
        return emitted

    def resume(self, now: datetime) -> list[str]:
        """재시작 직후 진행 중이던 사례를 기록하고 이어서 실행한다."""
        s = fold(self.store.read(as_of=now))
        marker = self.store.last_seq()
        cases = [d.id for d in sorted(s.discharges.values(), key=lambda x: x.id)
                 if d.stage not in (DischargeStage.CANCELLED, DischargeStage.VACANT)]
        cases += [m.admission_id for m in sorted(s.matches.values(), key=lambda x: x.admission_id) if m.active]
        self.store.extend(Event(event_id=f"{c}:saga:resumed:{marker}", case_id=c, type=EventType.CASE_RESUMED,
                                occurred_at=now, observed_at=now, source="saga") for c in cases)
        self.step(now)
        return cases
