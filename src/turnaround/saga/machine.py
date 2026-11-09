"""사례 사가 상태 머신 (구현 우선순위 3단계).

상태는 저장하지 않고 이벤트 기록을 접어서 계산한다(fold). 명령은 결정적 ID를 단 이벤트로
저장해 재시작해도 중복되지 않는다. 판단(누구를 어느 병상에)은 하지 않고, 진행 추적과
타이머, 보상 단계만 맡는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, ConfigDict

from turnaround.events.corrections import correction_direction
from turnaround.events.models import Event, EventType
from turnaround.events.store import EventStore
from turnaround.rules import DEFAULT_ACH, aerosol_wait_min, vacancy_floor

from .isolation import decide_contact_trace, decide_isolation_info, decide_late_isolation_admission


class SagaPolicy(BaseModel):
    """타이머 기한. 정책값이며 생성기 분포를 쓰지 않는다."""

    model_config = ConfigDict(frozen=True)
    registration_check_at: time = time(19, 0)  # 전날 19:00 미등록 확인, 청소 예정표 공유
    medication_delay: timedelta = timedelta(hours=2)  # 처방 발행부터 병동 도착까지 (SAG-6)
    rx_expected_by: time = time(11, 0)  # 퇴원 당일 이 시각까지 퇴원처방이 없으면 병동에 알림 (설계 가정)
    cleaning_overdue_general: timedelta = timedelta(minutes=120)
    cleaning_overdue_terminal: timedelta = timedelta(minutes=180)
    # 환기 대기 (SAG-2a, LP-23): 병상별 공기순환 횟수로 지침 부록 3 표에서 찾는다. 모르는 병상은 일반실 4회
    bed_ach: dict[str, int] = {}
    removal_pct: float = 99.9
    aerosol_wait_override_min: int | None = None  # 병원 규칙
    # 격리 정보 (설계 개정 3.7 (가)). 위험 조건이 비어 있으면 알림을 내지 않는다(지금 동작)
    isolation_risk_conditions: tuple[str, ...] = ()
    isolation_alert_to: tuple[str, ...] = ("bed_manager", "ipc")
    isolation_mismatch_after: timedelta = timedelta(minutes=60)  # 설계 가정
    # 병상 → (병실 종류, 코호트 균). 늦은 격리 오더에서 매칭된 병상이 여전히 맞는지 본다 (for_layout이 채움)
    bed_room: dict[str, tuple[str, str | None]] = {}
    exceptions_enabled: bool = False  # 예외 판단 머리가 켜져 있을 때만 EXCEPTION_RAISED를 낸다
    exception_deadline: timedelta = timedelta(minutes=120)
    # 퇴원 병목 (로드맵 07 M2): 환자 준비가 끝났는데 보호자 도착 예정이 이만큼 넘게 남으면 라운지 판단을 올린다
    lounge_enabled: bool = False
    field_sequence: bool = False  # 현장형 퇴원 순서(심사·조제 병행 → 수납 안내 → 수납 …)를 따르는 병원
    cleaning_split: bool = False  # 청소 두 갈래: 병원 직원 침상 정리 요청을 함께 낸다 (현장 프로필)
    call_up_enabled: bool = False  # 입실 구간: 병상·병동 준비와 수속이 갖춰지면 "올려주세요" (현장 프로필)
    guardian_lounge_margin: timedelta = timedelta(minutes=30)  # 설계 가정

    @classmethod
    def for_layout(cls, rooms, beds, **kw) -> "SagaPolicy":
        ach = {r.id: r.air_changes_per_hour for r in rooms}
        room = {r.id: (r.room_type.value, r.cohort_key) for r in rooms}
        return cls(bed_ach={b.id: ach[b.room_id] for b in beds}, bed_room={b.id: room[b.room_id] for b in beds}, **kw)

    @classmethod
    def from_policy(cls, policy, rooms=None, beds=None, **kw) -> "SagaPolicy":
        """정책 파일의 병원 프로필 값으로 만든다. 프로필 기본값(legacy)이면 지금 SagaPolicy()와 같다."""
        hp = policy.hospital_profile
        h, m = (int(x) for x in hp.discharge_sequence.rx_due_at.split(":"))
        vals = dict(rx_expected_by=time(h, m), isolation_risk_conditions=tuple(hp.isolation.risk_conditions),
                    isolation_alert_to=tuple(hp.isolation.alert_to),
                    isolation_mismatch_after=timedelta(minutes=hp.isolation.mismatch_alert_min),
                    exceptions_enabled=policy.brain.enabled, lounge_enabled=hp.guardian.lounge_enabled,
                    cleaning_split=hp.cleaning.split, field_sequence=hp.discharge_sequence.preset == "field", call_up_enabled=hp.ward_confirmation.mode == "notify",
                    exception_deadline=timedelta(minutes=policy.brain.max_wait_min))
        vals.update(kw)
        return cls.for_layout(rooms, beds, **vals) if rooms is not None else cls(**vals)

    def aerosol_wait(self, bed_id: str | None, organism: str | None = None) -> int:
        floor = vacancy_floor(organism)  # 결핵 60분 하한 (지침 하한이라 시험용 덮어쓰기보다도 우선)
        if self.aerosol_wait_override_min is not None:
            return max(self.aerosol_wait_override_min, floor)
        return max(aerosol_wait_min(self.bed_ach.get(bed_id or "", DEFAULT_ACH["multi"]), self.removal_pct), floor)
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
    rx_revisions: list[tuple[str, datetime, str | None]] = field(default_factory=list)
    discharge_date: date | None = None  # 퇴원 예정일
    discharge_type: str = "normal"  # normal | death(임종실 전실·사망) | ama(자의 퇴원) | transfer_out(급변 전동)
    hold: bool = False  # 급변 전동 때 병상을 잡아 둠 (풀릴 때까지 다른 환자 배정 불가)
    cleaning_dones: list[datetime] = field(default_factory=list)  # 청소 완료 신호 시각들 (종말소독 상향 뒤 판정용)
    guardian_eta: datetime | None = None  # 마지막으로 알려진 보호자 도착 예정
    mobility_aid: bool = False  # 이동 보조 필요 (추출·입력 사실, 라운지 제외 판단)
    cleaning_done_kinds: list[tuple[datetime, str | None]] = field(default_factory=list)  # (시각, 청소 종류)
    bed_prep_dones: list[datetime] = field(default_factory=list)  # 병원 직원 침상 정리(재설치) 완료 신호들

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
    # 격리 정보 (설계 개정): 사례 → [(이벤트 ID, 인지 시각, 균, 결과)], 사례 → 첫 격리 오더 인지 시각
    screening_results: dict[str, list[tuple[str, datetime, str, str]]] = field(default_factory=dict)
    screening_pending: dict[str, int] = field(default_factory=dict)
    isolation_ordered: dict[str, datetime] = field(default_factory=dict)
    isolation_order_payload: dict[str, dict] = field(default_factory=dict)
    no_bed_reports: list[Event] = field(default_factory=list)  # 병상 없음 보고 (E5)
    objections: list[Event] = field(default_factory=list)  # 병동 이의 (E8)
    admission_cmds: dict[str, set[str]] = field(default_factory=dict)  # 입원 사례에 낸 사가 명령 키
    contact_promise: dict[str, str] = field(default_factory=dict)  # 연락 때 환자에게 안내한 시각 (E9 약속)

    def admission_commands(self, case: str) -> set[str]:
        return self.admission_cmds.get(case, set())


_MATCH_TYPES = {EventType.PREMATCH_CREATED, EventType.PATIENT_CONTACTED, EventType.MATCH_COMMITTED,
                EventType.PREMATCH_RELEASED, EventType.CASE_RECLASSIFIED_REVIEW, EventType.WARD_ACCEPTED,
                EventType.PATIENT_ARRIVED, EventType.WARD_PREP_DONE, EventType.PATIENT_CALL_UP,
                EventType.WARD_ASSIGNMENT_NOTIFIED}


_PIN_TRIGGERS = {EventType.ISOLATION_ORDER_ADDED, EventType.PATIENT_INFO_CORRECTED, EventType.ISOLATION_RELEASED}


def _bed_discharge(s: SagaState, bed: str | None) -> str | None:
    for d in s.discharges.values():
        if d.bed_id == bed and EventType.DISCHARGE_CANCELLED not in d.facts:
            return d.id
    return None


def fold(events: Iterable[Event]) -> SagaState:
    s = SagaState()
    for ev in sorted(events, key=lambda x: (x.seq or 0, x.observed_at)):
        if ev.source == "saga":
            s.admission_cmds.setdefault(ev.case_id, set()).add(ev.event_id)
        if ev.type is EventType.WARD_OBJECTION_RAISED:
            s.objections.append(ev)
        if ev.type is EventType.PATIENT_CONTACTED and ev.payload.get("promised_at"):
            s.contact_promise[ev.case_id] = ev.payload["promised_at"]
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
        if ev.type is EventType.SCREENING_RESULT_REPORTED:
            s.screening_results.setdefault(ev.case_id, []).append(
                (ev.event_id, ev.observed_at, ev.payload.get("organism", ""), ev.payload.get("result", "")))
            s.screening_pending[ev.case_id] = max(0, s.screening_pending.get(ev.case_id, 0) - 1)
            continue
        if ev.type is EventType.SCREENING_ORDERED:
            s.screening_pending[ev.case_id] = s.screening_pending.get(ev.case_id, 0) + 1
            continue
        if ev.type is EventType.ISOLATION_ORDER_ADDED:
            s.isolation_ordered.setdefault(ev.case_id, ev.observed_at)  # 퇴원 사례 처리는 아래에서 그대로
            s.isolation_order_payload.setdefault(ev.case_id, dict(ev.payload))
        if ev.type is EventType.NO_BED_REPORTED:
            s.no_bed_reports.append(ev)
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
            d.discharge_type = ev.payload.get("discharge_type") or d.discharge_type
            planned = ev.payload.get("discharge_date")
            # 예정일이 없으면 오후 결정은 다음 날, 오전 결정은 당일 퇴원으로 본다 (설계 가정)
            d.discharge_date = (date.fromisoformat(planned) if planned else
                                ev.observed_at.date() + timedelta(days=1 if ev.observed_at.hour >= 12 else 0))
        if ev.type in _WARD_EXIT:
            # 퇴원 결정 없이 병상을 떠나는 경로 (SAG-9 임종실 전실, SAG-10 병동 사망·급변 전동)
            kind, vacates = _WARD_EXIT[ev.type]
            d = s.discharges.get(ev.case_id)
            if d is None:
                d = s.discharges[ev.case_id] = DischargeCase(ev.case_id)
                d.bed_id = ev.payload.get("bed_id")
                d.isolation = bool(ev.payload.get("isolation_order"))
                d.precautions = tuple(ev.payload.get("precautions") or ())
                d.organism = ev.payload.get("organism")
                d.facts.setdefault(EventType.DISCHARGE_DECIDED, ev.observed_at)
            d.discharge_type = kind
            if ev.type is EventType.UNPLANNED_TRANSFER_OUT and ev.payload.get("hold"):
                d.hold, vacates = True, False  # 병상을 잡아 둔다: 풀릴 때 빈다
            if ev.type is EventType.BED_HOLD_RELEASED:
                d.hold = False
            if vacates:
                d.facts.setdefault(EventType.PATIENT_DEPARTED, ev.observed_at)
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
        if ev.type in (EventType.GUARDIAN_ETA_SET, EventType.GUARDIAN_ETA_UPDATED) and ev.payload.get("eta"):
            d.guardian_eta = datetime.fromisoformat(ev.payload["eta"])
        if d is not None and ev.payload.get("mobility_aid") is True:
            d.mobility_aid = True
        if ev.type is EventType.DISCHARGE_RX_REVISED:
            d.rx_revisions.append((ev.event_id.split(":", 1)[1], ev.observed_at, ev.payload.get("reason")))
        if ev.source == "saga":
            d.commands.add(ev.event_id)
        if ev.type is EventType.CLEANING_DONE and ev.source != "saga":
            d.cleaning_dones.append(ev.observed_at)
            d.cleaning_done_kinds.append((ev.observed_at, ev.payload.get("cleaning_type")))
        if ev.type is EventType.BED_PREP_DONE:
            d.bed_prep_dones.append(ev.observed_at)
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
    decide_isolation_info(s, now, out, policy)
    decide_late_isolation_admission(s, out, policy)
    decide_contact_trace(s, out, policy)
    decide_no_bed(s, out, policy)
    decide_call_up(s, out, policy, now)
    from .exceptions_more import decide_more  # 순환 import 피함
    decide_more(s, now, out, policy)
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


# 퇴원 결정 없이 병상을 떠나는 이벤트: (경로, 이 이벤트로 병상이 비는가)
_WARD_EXIT = {
    EventType.END_OF_LIFE_ROOM_TRANSFER: ("death", True),
    EventType.DEATH_CONFIRMED: ("death", False),  # 가족 면회·사후 처치 동안 병상은 아직 사용 중
    EventType.BODY_TRANSFERRED: ("death", True),
    EventType.UNPLANNED_TRANSFER_OUT: ("transfer_out", True),
    EventType.BED_HOLD_RELEASED: ("transfer_out", True),
}


def _release_matches_on_hold(d: DischargeCase, s: SagaState, now: datetime, out: _Out) -> None:
    """잡아 둔 병상에 걸린 사전 매칭은 풀고 다시 찾는다. 연락 후면 담당 검토와 재연락 (퇴원 취소 보상과 같은 원칙)."""
    bed = d.bed_id
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        if m.bed_id != bed or not m.active:
            continue
        if EventType.PATIENT_CONTACTED in m.facts:
            out.cmd(m.admission_id, f"review:hold:{bed}", EventType.CASE_RECLASSIFIED_REVIEW, now, bed_id=bed,
                    reason="bed_on_hold_after_contact")
            out.cmd(m.admission_id, f"task:recontact:hold:{bed}", EventType.HUMAN_TASK_CREATED, now, bed_id=bed,
                    reason="recontact")
        else:
            out.cmd(m.admission_id, f"prematch_released:hold:{bed}", EventType.PREMATCH_RELEASED, now, bed_id=bed,
                    reason="bed_on_hold")
            _notify_ward_if_accepted(m, out, now, "bed_on_hold")
            out.cmd(m.admission_id, f"rematch:hold:{bed}", EventType.REMATCH_REQUESTED, now, target="admission",
                    admission_id=m.admission_id)


def _decide_cleaning_overdue(d, s, now, out, p, issued, limit, by) -> None:
    bed = d.bed_id
    waiting = sorted(m.admission_id for m in s.matches.values() if m.active and m.bed_id == bed)
    for rnd, due in ((1, issued + limit), (2, issued + limit * 1.5)):
        key = f"exception:cleaning_overdue:{rnd}"
        if now >= due and not by(EventType.CLEANING_DONE, due) and not by(EventType.BED_READY, due):
            out.cmd(d.id, key, EventType.EXCEPTION_RAISED, due, exception_id=f"E4:{d.id}:{rnd}",
                    trigger="cleaning_overdue", bed_id=bed, stage="first" if rnd == 1 else "second",
                    deadline=(due + p.exception_deadline).isoformat(),
                    context={"isolation_discharge": d.isolation, "cleaning_type": "terminal" if d.isolation else "general",
                             "cleaning_started": d.seen(EventType.CLEANING_STARTED) is not None,
                             "waiting_admissions": waiting, "next_admission_waiting": bool(waiting)})
            if rnd == 1 and waiting:  # 기다리는 쪽에 늦어짐을 알린다 (결정적, 현장 조사 권고)
                out.cmd(d.id, "share:cleaning_overdue", EventType.SHARE_WAITING_INFO, due, bed_id=bed,
                        to=["bed_manager", "ward"], reason="cleaning_overdue", waiting_admissions=waiting)
        if any(k.endswith(key) for k in d.commands):
            cleared = d.seen(EventType.CLEANING_DONE) or d.seen(EventType.BED_READY)
            if cleared is not None:
                out.cmd(d.id, f"exception_resolved:cleaning_overdue:{rnd}", EventType.EXCEPTION_RESOLVED, cleared,
                        exception_id=f"E4:{d.id}:{rnd}", how="condition_cleared")


def decide_no_bed(s: SagaState, out, p) -> None:
    """E5 병상 없음 (XD-032): 입원 대기 사례마다 하나(첫 보고 시각). 다시 찾을 때마다 새로 올리지 않는다(알림 폭주 방지).
    매칭·확정되면 닫는다."""
    if not p.exceptions_enabled:
        return
    first: dict[str, Event] = {}
    for e in s.no_bed_reports:
        first.setdefault(e.case_id, e)
    for case, e in sorted(first.items()):
        facts = s.admission_facts.get(case, {})
        if EventType.ADMISSION_CANCELLED in facts:
            continue
        payload = s.admission_payload.get(case, {})
        exid = f"E5:{case}"
        pending = sorted((d.bed_id, d.stage.value) for d in s.discharges.values()
                         if d.bed_id and EventType.PATIENT_DEPARTED not in d.facts and d.discharge_date is not None
                         and d.discharge_date <= e.observed_at.date())
        out.cmd(case, "exception:no_bed", EventType.EXCEPTION_RAISED, e.observed_at,
                exception_id=exid, trigger="no_bed", admission_id=case, stage="before_contact",
                deadline=(e.observed_at + p.exception_deadline).isoformat(),
                context={"from_icu": bool(payload.get("from_icu")), "contacted": False, "committed": False,
                         "pending_discharges_today": [{"bed_id": b, "stage": st} for b, st in pending]})
        m = s.matches.get(case)
        if m is not None and m.active and min(m.facts.values()) >= e.observed_at:
            out.cmd(case, "exception_resolved:no_bed", EventType.EXCEPTION_RESOLVED, min(m.facts.values()),
                    exception_id=exid, how="condition_cleared")


def current_discharge(s: SagaState, m: "MatchCase") -> "DischargeCase | None":
    """이 매칭이 기다리는 퇴원 사례: 매칭에 적힌 사례, 없으면 그 병상에서 가장 최근에 결정된 취소 안 된 사례.
    같은 병상은 기간 중 여러 번 퇴원하므로 첫 사례를 보면 안 된다 (병동·원무 검수 H-4)."""
    if m.discharge_id and m.discharge_id in s.discharges:
        return s.discharges[m.discharge_id]
    cands = [x for x in s.discharges.values() if x.bed_id == m.bed_id and EventType.DISCHARGE_CANCELLED not in x.facts]
    if not cands:
        return None
    return max(cands, key=lambda x: x.seen(EventType.DISCHARGE_DECIDED) or datetime.min)


def decide_call_up(s: SagaState, out, p, now: datetime) -> None:
    """입실 구간 (08 계획 1단계): 확정된 매칭이 병상 준비·병동 준비·수속(입원 예정 시각)을 모두 갖추면 원무에
    "올려주세요"를 알린다. 이송 인력 배정은 범위 밖이다(CLAUDE.md 4번): 알림과 입실 신호 추적까지만."""
    if not p.call_up_enabled:
        return
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        if EventType.MATCH_COMMITTED not in m.facts or EventType.PATIENT_ARRIVED in m.facts or not m.active:
            continue
        prep = m.facts.get(EventType.WARD_PREP_DONE)
        req = (s.admission_payload.get(m.admission_id) or {}).get("requested_at")
        if prep is None or not req:
            continue
        due = datetime.fromisoformat(req)
        if EventType.PATIENT_CONTACTED not in m.facts:
            continue  # 원무가 환자에게 연락하지 않은 입원은 부르지 않는다 (원무 검수 H-1)
        iso_at = s.isolation_ordered.get(m.admission_id)
        if iso_at is not None and iso_at >= m.facts[EventType.MATCH_COMMITTED]:
            # 확정 뒤 격리 오더: 병상이 맞는지 다시 확인(재매칭·재확정)하기 전에는 부르지 않는다 (4단계 시연에서 발견)
            out.cmd(m.admission_id, "call_up_held:isolation", EventType.HUMAN_TASK_CREATED, max(iso_at, now),
                    kind="call_up_held", assignee="bed_manager", bed_id=m.bed_id, reason="isolation_after_commit")
            continue
        d = current_discharge(s, m)
        if d is not None and d.isolation:
            bed_ok = d.seen(EventType.BED_MARKED_VACANT)  # 격리 퇴실은 종말소독 뒤 빈 병상 전환만
        else:
            bed_ok = d is None or d.seen(EventType.BED_MARKED_VACANT) or d.seen(EventType.BED_READY)
        if not bed_ok or now < due:
            continue
        at = max(due, prep, bed_ok if isinstance(bed_ok, datetime) else due)
        out.cmd(m.admission_id, "call_up", EventType.PATIENT_CALL_UP, at, bed_id=m.bed_id, to="admission_office")


def _bed_awaited(s: SagaState, bed: str | None) -> bool:
    """이 병상에 매칭·확정된 다음 환자가 있는가."""
    return any(m.active and m.bed_id == bed for m in s.matches.values())


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

    if d.hold:
        _release_matches_on_hold(d, s, now, out)
    # 사망(임종실 전실, 병동 사망)과 급변 전동은 퇴원이 아니다: 퇴실 전 경보를 모두 억제한다 (SAG-9, SAG-10)
    if d.discharge_type not in ("death", "transfer_out"):
        # 전날 19:00: 등록됐으면 청소 예정표 공유, 19:00까지 미등록이면 병동에 확인 요청
        if registered is not None:
            due = max(due19, registered)
            if now >= due:
                out.cmd(d.id, "schedule_item", EventType.SCHEDULE_ITEM_CREATED, due, bed_id=bed)
        if now >= due19 and not by(EventType.DISCHARGE_REGISTERED, due19):
            out.cmd(d.id, "registration_check", EventType.REGISTRATION_CHECK_REQUESTED, due19, bed_id=bed)

        # 퇴원약 지연 (SAG-6): 기준점은 처방 발행 시각. 처방 이벤트가 없는 원천이면 오더 확정 시각으로 대신한다
        base = d.seen(EventType.DISCHARGE_RX_ISSUED) or d.seen(EventType.ORDER_CONFIRMED)
        if base and now >= base + p.medication_delay and not by(EventType.MEDICATION_READY, base + p.medication_delay):
            out.cmd(d.id, "recompute:medication_delay", EventType.READY_TIME_RECOMPUTED, base + p.medication_delay,
                    bed_id=bed, reason="medication_delay")

        # 퇴원 당일 정책 시각까지 처방이 없으면 병동에 알림 (주치의 연락은 사람이)
        if d.discharge_date is not None and d.discharge_type != "ama":  # 자의 퇴원은 처방 없이 나갈 수 있다
            rx_due = datetime.combine(d.discharge_date, p.rx_expected_by)
            if now >= rx_due and not any(by(t, rx_due) for t in (
                    EventType.DISCHARGE_RX_ISSUED, EventType.MEDICATION_READY, EventType.PATIENT_DEPARTED)):
                if p.exceptions_enabled:  # 무엇을 할지(재촉, 올림)는 머리가 고른다 (로드맵 07 M2)
                    out.cmd(d.id, "exception:order_late", EventType.EXCEPTION_RAISED, rx_due,
                            exception_id=f"E2:{d.id}", trigger="sequence_stalled", bed_id=bed,
                            stage="before_departure", deadline=(rx_due + p.exception_deadline).isoformat(),
                            context={"next_admission_waiting": _bed_awaited(s, bed), "step": "discharge_order",
                                     "step_owner": "physician"})
                else:
                    out.cmd(d.id, "rx_missing", EventType.HUMAN_TASK_CREATED, rx_due, bed_id=bed, kind="rx_missing",
                            assignee="ward")

        # 감지 조건이 풀리면 예외를 닫는다 (머리가 더 고르지 않게)
        if any(k.endswith("exception:order_late") for k in d.commands):
            cleared = next((d.seen(t) for t in (EventType.DISCHARGE_RX_ISSUED, EventType.MEDICATION_READY,
                                                EventType.PATIENT_DEPARTED) if d.seen(t)), None)
            if cleared is not None:
                out.cmd(d.id, "exception_resolved:order_late", EventType.EXCEPTION_RESOLVED, cleared,
                        exception_id=f"E2:{d.id}", how="condition_cleared")
        if any(k.endswith("exception:guardian_delay_same_day") for k in d.commands):
            cleared = d.seen(EventType.GUARDIAN_ARRIVED) or d.seen(EventType.PATIENT_DEPARTED)
            if cleared is not None:
                out.cmd(d.id, "exception_resolved:guardian", EventType.EXCEPTION_RESOLVED, cleared,
                        exception_id=f"E3:{d.id}", how="condition_cleared")

        # 준비는 끝났는데 보호자가 한참 뒤에 온다: 라운지로 병상을 먼저 비울지 머리가 고른다 (로드맵 07 M2)
        ready_parts = [d.seen(EventType.BILLING_DONE), d.seen(EventType.EDUCATION_DONE)]
        if p.exceptions_enabled and p.lounge_enabled and all(ready_parts) and d.guardian_eta is not None:
            ready = max(ready_parts)
            if now >= ready and d.guardian_eta > ready + p.guardian_lounge_margin and \
                    not by(EventType.GUARDIAN_ARRIVED, ready) and not by(EventType.PATIENT_DEPARTED, ready):
                out.cmd(d.id, "exception:guardian_delay_same_day", EventType.EXCEPTION_RAISED, ready,
                        exception_id=f"E3:{d.id}", trigger="guardian_delay_same_day", bed_id=bed,
                        stage="before_departure", deadline=d.guardian_eta.isoformat(),
                        context={"next_admission_waiting": _bed_awaited(s, bed),
                                 "guardian_wait_min": int((d.guardian_eta - ready).total_seconds() // 60),
                                 # 라운지 제외 대상 판단에 쓴다 (2단계 안전 검수 H2)
                                 "isolation_discharge": d.isolation, "mobility_aid": bool(d.mobility_aid)})

        # 처방 수정은 지연 사유로 기록
        for key, seen_at, why in d.rx_revisions:
            out.cmd(d.id, f"delay_reason:{key}", EventType.DELAY_REASON_RECORDED, seen_at, bed_id=bed,
                    reason="rx_revised", detail=why)

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
            extra["aerosol_wait_min"] = p.aerosol_wait(bed, d.organism)  # 환기 대기 뒤 청소 (LP-23)
        if d.isolation and d.organism == "CDIFF":
            extra["sporicidal"] = True  # 아포 대응 소독
        # 퇴실 시점에 알던 격리 여부로 첫 요청을 내고, 퇴실 뒤 격리 오더가 오면 종말소독으로 올린다
        # (늦게 도착한 안전 정보. 실행 시각에 상관없이 같은 결과가 나오도록 사실의 인지 시각으로 판정)
        iso_at = d.seen(EventType.ISOLATION_ORDER_ADDED)
        iso_at_departure = d.isolation and (iso_at is None or iso_at <= departed)
        out.cmd(d.id, "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, departed, bed_id=bed,
                cleaning_type="terminal" if iso_at_departure else "general", **({} if not iso_at_departure else extra))
        terminal_from = departed
        if d.isolation and not iso_at_departure:
            terminal_from = iso_at
            out.cmd(d.id, "cleaning_request:terminal_upgrade", EventType.CLEANING_REQUEST_ISSUED, iso_at, bed_id=bed,
                    cleaning_type="terminal", reason="isolation_after_departure", **extra)
        # 퇴실 전 요청(재요청 포함)은 실제 요청이 아니다: 타이머는 퇴실 뒤부터 (2단계 현장 검수 H1)
        issued = max(d.seen(EventType.CLEANING_REQUEST_ISSUED) or departed, departed)
        limit = p.cleaning_overdue_terminal if d.isolation else p.cleaning_overdue_general
        done = d.seen(EventType.CLEANING_DONE)
        ready = d.seen(EventType.BED_READY)
        due = issued + limit
        if now >= due and not by(EventType.CLEANING_DONE, due) and not by(EventType.BED_READY, due):
            out.cmd(d.id, "recompute:cleaning_overdue", EventType.READY_TIME_RECOMPUTED, issued + limit,
                    bed_id=bed, reason="cleaning_overdue")
        if p.exceptions_enabled:  # E4 청소 지연: 무엇을 할지는 머리가 고른다 (XD-019)
            _decide_cleaning_overdue(d, s, now, out, p, departed, limit, by)  # 사가 청소 요청 = 퇴실 시각
        if done is not None:
            out.cmd(d.id, "recompute:cleaning_done", EventType.READY_TIME_RECOMPUTED, done,
                    bed_id=bed, reason="cleaning_done")
        # 빈 병상 전환: 격리 퇴실은 종말소독 요청 뒤의 완료 신호가 있어야 한다
        late = d.isolation and not iso_at_departure
        if d.isolation:  # 격리 퇴실: 종말소독 요청 뒤, 일반 청소로 표시되지 않은 완료만 센다 (감염관리 검수 P4)
            done = next((t for t, k in sorted(d.cleaning_done_kinds, key=lambda x: x[0])
                         if t >= terminal_from and k != "general"), None)
        vacant_key = "bed_vacant"
        if late:
            prev_vacant = d.seen(EventType.BED_MARKED_VACANT)
            if prev_vacant is not None and prev_vacant <= iso_at:
                # 격리 정보를 모를 때 낸 입실 가능을 되돌린다 (설계 개정 3.7 (다))
                out.cmd(d.id, "bed_vacancy_revoked", EventType.BED_VACANCY_REVOKED, iso_at, bed_id=bed,
                        reason="late_isolation")
                vacant_key = "bed_vacant:after_terminal"
            for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
                if m.bed_id == bed and m.active and EventType.MATCH_COMMITTED in m.facts \
                        and EventType.PATIENT_ARRIVED not in m.facts:
                    out.cmd(m.admission_id, f"task:late_iso_next:{bed}", EventType.HUMAN_TASK_CREATED, iso_at,
                            bed_id=bed, reason="late_isolation_next_committed", assignee="bed_manager")
        if p.cleaning_split:
            # 청소 두 갈래 (08 계획 1단계, 감염관리 검수 P1~P3 반영): 새 리넨·침상 재설치는 표면·침상 청소 뒤
            # (권고안 부록 4-3-2: 린넨 제거 → 표면 → 매트리스 → … → 마지막에 침대 재설치). 그래서 침상 정리 요청은
            # 청소 완료(격리는 종말소독 완료) 뒤에 내고, 그 뒤의 정리 완료만 입실 가능 근거로 센다.
            clean_ok = done if d.isolation else next((t for t, _ in sorted(d.cleaning_done_kinds, key=lambda x: x[0])
                                                     if t >= departed), None)
            if clean_ok is not None:
                key = "bed_prep_request:after_terminal" if late else "bed_prep_request"
                out.cmd(d.id, key, EventType.BED_PREP_REQUESTED, clean_ok, bed_id=bed, after_terminal=d.isolation)
            prep_ok = next((t for t in sorted(d.bed_prep_dones) if clean_ok is not None and t >= clean_ok), None)
            if clean_ok is not None and prep_ok is not None:
                out.cmd(d.id, vacant_key, EventType.BED_MARKED_VACANT, max(clean_ok, prep_ok), bed_id=bed)
            elif ready is not None:
                # 사람의 입실 가능 신호만으로는 두 갈래 요구를 넘지 않는다 (P3): 확인 작업
                out.cmd(d.id, "task:missing_bed_prep", EventType.HUMAN_TASK_CREATED, ready, bed_id=bed,
                        reason="missing_bed_prep" if clean_ok is not None else "missing_cleaning_done")
            return
        if ready is not None:
            if done is not None or not d.isolation:
                out.cmd(d.id, vacant_key, EventType.BED_MARKED_VACANT, max(ready, done or ready), bed_id=bed)
            elif late:
                pass  # 종말소독 상향 요청이 나가 있다. 지연은 청소 지연 타이머가 본다
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
    """병동 수용 회신까지 받았거나(회신형) 병동에 배정을 통보한(통보형) 매칭을 해제하면 철회 통지를 보낸다."""
    if EventType.WARD_ACCEPTED in m.facts or EventType.WARD_ASSIGNMENT_NOTIFIED in m.facts:
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
