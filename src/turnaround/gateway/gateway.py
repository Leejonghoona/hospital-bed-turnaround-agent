"""실행 게이트웨이 (구현 우선순위 4단계).

에이전트(또는 규칙 모드)의 제안을 실제로 실행하기 전에 검사하는 결정적 코드다. LLM을 쓰지 않는다.
- 판단 정보 두 번 재확인: 제안 시점, 확정 직전 (근거 순번 basis_seq 이후 바뀐 정보가 있으면 거부)
- SHACL 재검사, 정책 파일의 확인 등급, 멱등 키, 감사 로그, 종말소독 강제, 조회 권한
- 관리자 직접 수정 (안전 규칙은 감염관리실 예외 승인 없이는 못 넘음, 적용하면 사례 고정)

상태는 저장하지 않는다. 매번 이벤트 저장소를 읽어 계산하므로 재시작해도 안전하다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from turnaround.events.corrections import apply_correction, apply_release, correction_direction
from turnaround.events.models import Event, EventType
from turnaround.saga.worklist import OperatingCalendar, load_operations
from turnaround.rules import DEFAULT_ACH, aerosol_wait_min, guideline_delta, vacancy_floor
from turnaround.events.store import EventStore
from turnaround.ontology import validate as shacl_validate
from turnaround.saga import fold
from turnaround.sim.models import Bed, Patient, IsolationType, Room, RoomType

from .policy import Policy

# 판단 정보가 바뀌었다고 보는 이벤트
# 알림·재요청 행동: 실행해도 예외를 닫지 않고 다시 본다. 같은 행동은 한 번만(정책 max_attempts로 바꿀 수 있음).
# 청소 지연(E4)은 1차·2차가 따로 오르므로 재요청으로 닫는다 (2단계 안전 검수 H6)
NOTIFY_ACTIONS = frozenset({"remind_owner", "remind_ward", "re_request_cleaning", "request_bed_prep_first", "share_eta",
                            "offer_lounge"})
NOTIFY_RESOLVES = frozenset({"cleaning_overdue"})
NOTIFY_RECHECK = timedelta(minutes=30)  # 알림 뒤 다시 볼 때까지 (설계 가정)
MAX_WAITS = 3  # 같은 예외에서 연속 기다림 상한 (설계 가정, 정답 세트 W-01 "연속 기다림은 침묵 실패")
# 매칭을 푸는 효과가 있는 행동: 입실 뒤에는 무엇이든 거부
REMATCH_EFFECT_ACTIONS = frozenset({"rematch_now", "replan_now", "propose_rematch"})
_MATERIAL = {EventType.ISOLATION_ORDER_ADDED, EventType.DISCHARGE_CANCELLED, EventType.ADMISSION_CANCELLED,
             EventType.PATIENT_INFO_UPDATED, EventType.PATIENT_INFO_CORRECTED, EventType.CORRECTION_CONFIRMED,
             EventType.ISOLATION_RELEASED}
_INPUT_VIOLATIONS = {"gender_missing"}


class Proposal(BaseModel):
    """계획 에이전트(또는 규칙 모드)의 매칭 제안."""

    model_config = ConfigDict(frozen=True)
    proposal_id: str
    kind: Literal["match"] = "match"
    case: str  # 입원 대기 사례 ID
    bed_id: str | None  # None이면 조건에 맞는 병상 없음
    basis_seq: int  # 판단에 쓴 마지막 이벤트 순번
    discharge_id: str | None = None  # 이 병상을 비우는 퇴원 사례 (사전 매칭이면)
    scores: dict[str, float] = Field(default_factory=dict)  # 후보 병상 점수 (1·2순위 차 판정)
    alternatives: list[str] = Field(default_factory=list)  # 검토 카드 대안
    rationale: str = ""  # 에이전트 설명 (검토 카드). 판단 근거로 쓰지 않는다


@dataclass
class Decision:
    status: str  # rejected | awaiting_manager | awaiting_review | awaiting_ward | committed | executed | ...
    stage: str = ""  # proposal | commit | override | cleaning | query
    tier: str | None = None
    reasons: list[str] = field(default_factory=list)


def _patient_from_payload(pid: str, p: Mapping) -> Patient:
    return Patient(id=p.get("patient_id") or pid, name=p.get("name") or pid, gender=p.get("gender"),
                   age=p.get("age", 50), department=p.get("department") or "미상",
                   precautions=tuple(p.get("precautions") or ()), organism=p.get("organism"),
                   precaution_status=p.get("precaution_status"), isolation_ordered_by=p.get("isolation_ordered_by"),
                   immunocompromised=bool(p.get("immunocompromised")), open_wound=bool(p.get("open_wound")))


class Gateway:
    def __init__(self, store: EventStore, rooms: Iterable[Room], beds: Iterable[Bed], policy: Policy,
                 census: Mapping[str, Patient] | None = None, calendar: OperatingCalendar | None = None):
        self.store, self.policy = store, policy
        self.rooms, self.beds = tuple(rooms), tuple(beds)
        self.room_of = {b.id: b.room_id for b in self.beds}
        self.room_by_id = {r.id: r for r in self.rooms}
        self.census = dict(census or {})  # 현재 재원 환자 (병상 ID → 환자)
        self.calendar = calendar or load_operations().calendar  # 사후 검토 기한 (운영 달력, SAG-8)
        self._waived: dict[str, list[str]] = {}  # 제안별로 정책이 검토에서 뺀 사유 (통보 근거)

    # ---------- 이벤트에서 계산하는 상태 ----------

    def _events(self) -> list[Event]:
        return self.store.read()

    def _admission(self, case: str, events: list[Event]) -> tuple[Patient | None, dict]:
        payload = self._apply_patient_events(case, {}, events)
        return (_patient_from_payload(case, payload) if payload else None), payload

    def _apply_patient_events(self, case: str, payload: dict, events: list[Event]) -> dict:
        """입원 요청, 격리 오더, 정보 변경·정정, 격리 해제를 순서대로 반영한다. 재원 환자에게도 쓴다."""
        payload = dict(payload)
        confirmed = {e.payload.get("correction_id") for e in events if e.type is EventType.CORRECTION_CONFIRMED}
        for e in events:
            if e.case_id != case:
                continue
            if e.type is EventType.ADMISSION_REQUESTED:
                payload.update(e.payload)
            elif e.type in (EventType.ISOLATION_ORDER_ADDED, EventType.PATIENT_INFO_UPDATED):
                payload.update(e.payload)
                if e.type is EventType.ISOLATION_ORDER_ADDED:
                    payload["isolation_order"] = True
            elif e.type is EventType.ISOLATION_RELEASED:
                apply_release(payload)
            elif e.type is EventType.PATIENT_INFO_CORRECTED:
                field_, value = e.payload["field"], e.payload.get("value")
                if correction_direction(payload, field_, value) == "loosen" and e.event_id not in confirmed:
                    continue  # 제약을 푸는 정정은 사람 확인 전까지 반영하지 않는다
                apply_correction(payload, field_, value, e.payload)
        return payload

    def _census_patient(self, p: Patient, events: list[Event]) -> Patient:
        base = {"patient_id": p.id, "name": p.name, "gender": p.gender.value if p.gender else None, "age": p.age,
                "department": p.department, "precautions": [x.value for x in p.precautions], "organism": p.organism,
                "precaution_status": p.precaution_status.value if p.precaution_status else None,
                "isolation_ordered_by": p.isolation_ordered_by, "immunocompromised": p.immunocompromised,
                "open_wound": p.open_wound, "isolation_order": p.isolation_order}
        return _patient_from_payload(p.id, self._apply_patient_events(p.id, base, events))

    def _proposal(self, pid: str, events: list[Event]) -> Proposal | None:
        for e in events:
            if e.type is EventType.PROPOSAL_RECEIVED and e.payload.get("proposal_id") == pid:
                return Proposal.model_validate(e.payload)
        return None

    def _assignments(self, events: list[Event]) -> dict[str, str]:
        """입원 사례 → 현재 확정 병상 (확정, 직접 수정, 해제를 순서대로 반영)."""
        cur: dict[str, str] = {}
        for e in events:
            if e.type is EventType.MATCH_COMMITTED and e.source == "gateway":
                cur[e.case_id] = e.payload["bed_id"]
            elif e.type is EventType.PREMATCH_RELEASED and e.source == "gateway" and cur.get(e.case_id) == e.payload.get("bed_id"):
                cur.pop(e.case_id, None)
        return cur

    def _occupants(self, events: list[Event], exclude_case: str | None = None) -> dict[str, Patient]:
        saga = fold(events)
        leaving = {d.bed_id for d in saga.discharges.values()
                   if EventType.DISCHARGE_CANCELLED not in d.facts}  # 퇴원 예정·퇴실 병상의 재원 환자는 뺀다
        occ = {b: self._census_patient(p, events) for b, p in self.census.items() if b not in leaving}
        for case, bed in self._assignments(events).items():
            if case != exclude_case:
                patient, _ = self._admission(case, events)
                if patient is not None:
                    occ[bed] = patient
        return occ

    def _room_status(self, events: list[Event]) -> dict[str, str]:
        """사용 중지된 병실 → 사유 (GW-12). 마지막 이벤트가 이긴다."""
        status: dict[str, str] = {}
        for e in events:
            if e.type is EventType.ROOM_OUT_OF_SERVICE:
                status[e.payload["room_id"]] = e.payload.get("reason", "closed")
            elif e.type is EventType.ROOM_RETURNED_TO_SERVICE:
                status.pop(e.payload["room_id"], None)
        return status

    def room_status(self) -> dict[str, str]:
        return self._room_status(self._events())

    def _out_of_service(self, bed_id: str, events: list[Event]) -> bool:
        return self.room_of.get(bed_id) in self._room_status(events)

    def _on_hold(self, bed_id: str, events: list[Event]) -> str | None:
        """배정을 막는 병상 사유 (SAG-10). 풀릴 때까지 다른 환자를 배정하지 않는다.
        - bed_on_hold: 급변 전동 때 잡아 둔 병상
        - after_death_care: 병동 사망 뒤 가족 면회·사후 처치 중 (안치실 이송 때 빈다)
        """
        for d in fold(events).discharges.values():
            if d.bed_id != bed_id:
                continue
            if d.hold:
                return "bed_on_hold"
            if d.discharge_type == "death" and d.seen(EventType.PATIENT_DEPARTED) is None:
                return "after_death_care"
        return None

    def check_room_alerts(self, now: datetime) -> list[str]:
        """고장 난 음압실에 공기주의 환자가 있으면 감염관리실 경보 (GW-12). 고장 이벤트당 한 번."""
        events = self._events()
        raised = []
        last_off = {e.payload["room_id"]: e for e in events if e.type is EventType.ROOM_OUT_OF_SERVICE}
        status = self._room_status(events)
        occ = self._occupants(events)
        for room_id, reason in status.items():
            room = self.room_by_id.get(room_id)
            if reason != "equipment_failure" or room is None or room.room_type is not RoomType.NEGATIVE_PRESSURE:
                continue
            for bed_id, p in occ.items():
                if self.room_of.get(bed_id) == room_id and IsolationType.AIRBORNE in p.precautions:
                    case = next((c for c, b in self._assignments(events).items() if b == bed_id), room_id)
                    key = f"gw:alert:{last_off[room_id].event_id}:{bed_id}"
                    if self._emit(case, key, EventType.HUMAN_TASK_CREATED, now, kind="negative_pressure_failure",
                                  assignee="ipc", bed_id=bed_id, room_id=room_id):
                        raised.append(bed_id)
        return raised

    def aerosol_wait(self, bed_id: str, organism: str | None = None) -> dict:
        """환기 대기 분 (SAG-2a). 병원 규칙이 있으면 그 값, 지침과 다르면 차이 표시 (POL-2).
        결핵은 지침 하한 60분 아래로 내리지 않는다 (DECISIONS 2026-11-09)."""
        room = self.room_by_id.get(self.room_of.get(bed_id, ""))
        ach = room.air_changes_per_hour if room else DEFAULT_ACH["multi"]
        v = self.policy.ventilation
        guide = aerosol_wait_min(ach, v.removal_pct)
        chosen = v.hospital_wait_min if v.hospital_wait_min is not None else guide
        floor = vacancy_floor(organism)
        out = {"aerosol_wait_min": max(chosen, floor)}
        if floor > chosen:
            out["vacancy_floor"] = {"organism": organism, "min": floor}
        delta = guideline_delta(guide, v.hospital_wait_min)
        if delta:
            out["guideline_delta"] = delta
        return out

    def location_version(self, bed_id: str) -> int:
        """가상 FHIR Location 버전. 병상 정보 변경과 확정마다 1씩 오른다."""
        n = 1
        for e in self._events():
            if e.type is EventType.LOCATION_UPDATED and e.case_id == bed_id:
                n += 1
            elif e.type is EventType.MATCH_COMMITTED and e.source == "gateway" and e.payload.get("bed_id") == bed_id:
                n += 1
        return n

    def _pins(self, events: list[Event]) -> dict[str, Event]:
        """현재 고정된 사례 → 마지막 직접 수정 이벤트. 해제되면 빠진다."""
        pins: dict[str, Event] = {}
        for e in events:
            if e.type is EventType.MANUAL_OVERRIDE_APPLIED:
                pins[e.case_id] = e
            elif e.type is EventType.MANUAL_PIN_RELEASED:
                pins.pop(e.case_id, None)
        return pins

    def _pinned(self, case: str, events: list[Event]) -> bool:
        return case in self._pins(events)

    def placement_tags(self, role: str) -> dict[str, dict]:
        """직접 수정 배치 태그 (GW-11). 관리 역할만 누가·사유를 보고, 다른 역할은 분류만 본다."""
        full = role in self.policy.manual_override.roles
        tags = {}
        for case, e in self._pins(self._events()).items():
            reason = e.payload.get("reason_code")
            if full:
                tags[e.payload["bed_id"]] = {"manual": True, "by": e.payload.get("actor"),
                                             "at": e.occurred_at.isoformat(), "reason": reason}
            else:
                tags[e.payload["bed_id"]] = {"manual": True, "reason": "요청 배치" if reason in ("vip", "patient_request")
                                             else "직접 배치"}
        return tags

    def release_pin(self, case: str, actor: str, role: str, now: datetime) -> Decision:
        rid = f"rp{self.store.last_seq() + 1}"
        if role not in self.policy.manual_override.roles:
            d = Decision("refused", "release_pin", None, ["role_not_allowed"])
        elif not self._pinned(case, self._events()):
            d = Decision("refused", "release_pin", None, ["not_pinned"])
        else:
            self._emit(case, f"gw:{rid}:released", EventType.MANUAL_PIN_RELEASED, now, actor=actor)
            d = Decision("released", "release_pin")
        self._audit(f"gw:{rid}:release_pin", case, now, "release_pin", rid, d, actor=actor, role=role)
        return d

    def _ward_accepted(self, pid: str, events: list[Event]) -> bool:
        return any(e.type is EventType.WARD_ACCEPTED and e.payload.get("proposal_id") == pid for e in events)

    def _changed_since(self, prop: Proposal, events: list[Event]) -> list[Event]:
        cases = {prop.case}
        if prop.discharge_id:
            cases.add(prop.discharge_id)
        for d in fold(events).discharges.values():
            if d.bed_id == prop.bed_id:
                cases.add(d.id)
        room = self.room_of.get(prop.bed_id)
        if room:  # 같은 병실 재원 환자와 확정 환자 (GW-9)
            cases |= {p.id for b, p in self.census.items() if self.room_of.get(b) == room}
            cases |= {c for c, b in self._assignments(events).items() if self.room_of.get(b) == room}
        return [e for e in events if (e.seq or 0) > prop.basis_seq and e.case_id in cases and e.type in _MATERIAL]

    def _isolation_discharge_pending(self, bed_id: str, events: list[Event]) -> bool | None:
        """이 병상의 격리 퇴실이 있으면 종말소독 완료 신호 전인지. 격리 퇴실이 없으면 None."""
        found = None
        for d in fold(events).discharges.values():
            if d.bed_id == bed_id and d.isolation and EventType.DISCHARGE_CANCELLED not in d.facts:
                done = any(e.type is EventType.CLEANING_DONE and e.case_id == d.id
                           and e.payload.get("cleaning_type") == "terminal" for e in events)
                found = (found or False) or not done
        return found

    # ---------- 예외 행동 (U8 확장, 설계 개정 4.6) ----------

    _ACTION_MATERIAL = _MATERIAL | {EventType.SCREENING_RESULT_REPORTED}

    def submit_action(self, choice, now: datetime) -> Decision:
        """머리가 고른 예외 행동을 검사하고 실행한다. 병상과 숫자는 다루지 않는다(병상은 규칙 모드가 고른다)."""
        events = self._events()
        exid = choice.exception_id
        raised = next((e for e in events if e.type is EventType.EXCEPTION_RAISED
                       and e.payload.get("exception_id") == exid), None)
        resolved = any(e.type is EventType.EXCEPTION_RESOLVED and e.payload.get("exception_id") == exid for e in events)
        attempt = sum(1 for e in events if e.type is EventType.ACTION_PROPOSED and e.payload.get("exception_id") == exid)
        base = dict(exception_id=exid, action=choice.action)
        self._emit(choice.case, f"{exid}:proposed:{attempt + 1}", EventType.ACTION_PROPOSED, now, "agent", **base,
                   params=choice.params, rationale=choice.rationale, mode=choice.mode, basis_seq=choice.basis_seq)

        def reject(reason: str) -> Decision:
            self._emit(choice.case, f"{exid}:rejected:{attempt + 1}", EventType.ACTION_REJECTED, now, **base,
                       reasons=[reason])
            return Decision("rejected", "action", None, [reason])

        if raised is None or resolved:
            return reject("superseded")
        rule = self.policy.exception_actions.get(choice.trigger)
        fallback_ok = choice.mode == "fallback" and choice.action == "escalate"
        if not fallback_ok and (rule is None or choice.action not in rule.allowed):
            return reject("not_allowed")
        contacted = bool((raised.payload.get("context") or {}).get("contacted")) or any(
            e.type is EventType.PATIENT_CONTACTED and e.case_id == (raised.payload.get("admission_id") or choice.case)
            for e in events)
        if (why := self._stage_lock(choice.action, raised, events)) is not None:
            return reject(why)  # 연락·확정·입실 뒤 자동 재매칭 금지 (머리가 골라도 막는다)
        if not fallback_ok and choice.action not in rule.allowed_for(raised.payload.get("stage"), contacted):
            return reject("not_allowed")
        if not fallback_ok and choice.basis_input:
            from .guards import situational_block
            if (why := situational_block(choice.basis_input, choice.action)) is not None:
                return reject(why)  # 상황별 금지: 정답 세트 측정과 같은 함수 (08 계획 2단계)
        if any((e.seq or 0) > choice.basis_seq and e.case_id == choice.case and e.type in self._ACTION_MATERIAL
               for e in events):
            return reject("stale_basis")  # 재확인 1: 고른 뒤 판단 정보가 바뀜 → 예외를 열어 두고 다시 고른다
        if not fallback_ok and (why := self._repeat_limit(choice, raised, events, now)) is not None:
            return reject(why)  # 마감 뒤 기다림·같은 알림 반복은 받지 않는다 → 머리가 대체 경로로 사람에게 올린다
        if choice.action == "escalate" and \
                choice.params.get("role", "bed_manager") not in self.policy.hospital_profile.escalation_roles:
            return reject("bad_params")
        if (why := self._action_precheck(choice, raised, events)) is not None:
            return reject(why)  # 안전·상태 검사 (예: 종말소독 전 침상 정리, 퇴실 전 청소 재요청)
        autonomy = "auto" if fallback_ok else self.policy.autonomy.get(choice.action, "human")
        if autonomy == "human":
            self._emit(choice.case, f"{exid}:approval:{choice.action}", EventType.ACTION_APPROVAL_REQUESTED, now,
                       **base, autonomy=autonomy)
            return Decision("awaiting_approval", "action", autonomy)
        self._execute_action(choice, raised, now)
        self._emit(choice.case, f"{exid}:executed:{attempt + 1}", EventType.ACTION_EXECUTED, now, **base,
                   autonomy=autonomy)
        if autonomy == "post_report":
            self._emit(choice.case, f"{exid}:reported", EventType.ACTION_REPORTED, now, **base, to_role="bed_manager")
        if choice.action in NOTIFY_ACTIONS and choice.trigger not in NOTIFY_RESOLVES:
            # 알림·재요청은 예외를 닫지 않는다. 다시 볼 시각을 잡고, 조건이 풀리면 사가가 닫는다 (2단계 안전 검수 H6)
            self._emit(choice.case, f"{exid}:recheck:n{attempt + 1}", EventType.RECHECK_SCHEDULED, now,
                       exception_id=exid, at=(now + NOTIFY_RECHECK).isoformat(), after=choice.action)
        elif not choice.action.startswith("wait"):
            self._emit(choice.case, f"{exid}:resolved", EventType.EXCEPTION_RESOLVED, now, exception_id=exid,
                       how=choice.action)
        return Decision("executed", "action", autonomy)

    def _targets(self, raised: Event) -> set[str]:
        """이 예외의 행동이 건드리는 입원 사례."""
        ctx = raised.payload.get("context") or {}
        out = set(ctx.get("waiting_admissions") or [])
        if raised.payload.get("admission_id"):
            out.add(raised.payload["admission_id"])
        return out

    def _stage_lock(self, action: str, raised: Event, events: list[Event]) -> str | None:
        """단계 잠금을 예외의 단계 이름이 아니라 대상 입원의 현재 사실(연락·확정·입실)로 판정한다 (2단계 안전 검수 H1)."""
        from .profile import AUTO_REMATCH_ACTIONS, STAGE_LOCKED_ACTIONS
        if action in STAGE_LOCKED_ACTIONS.get(raised.payload.get("stage") or "", frozenset()):
            return "stage_locked"
        matches = fold(events).matches
        facts = set()
        for c in self._targets(raised):
            m = matches.get(c)
            if m is not None:
                facts |= set(m.facts)
            facts |= {e.type for e in events if e.case_id == c and e.type in (
                EventType.PATIENT_CONTACTED, EventType.MATCH_COMMITTED, EventType.PATIENT_ARRIVED)}
        if action in AUTO_REMATCH_ACTIONS and facts & {EventType.PATIENT_CONTACTED, EventType.MATCH_COMMITTED,
                                                       EventType.PATIENT_ARRIVED}:
            return "stage_locked"
        if action in REMATCH_EFFECT_ACTIONS and EventType.PATIENT_ARRIVED in facts:
            return "stage_locked"  # 입실한 환자의 매칭은 어떤 행동으로도 풀지 않는다
        return None

    def _repeat_limit(self, choice, raised: Event, events: list[Event], now: datetime) -> str | None:
        exid = choice.exception_id
        deadline = datetime.fromisoformat(raised.payload["deadline"])
        if choice.action.startswith("wait") and now >= deadline:
            return "deadline_passed"
        done = [e.payload.get("action") for e in events if e.type is EventType.ACTION_EXECUTED
                and e.payload.get("exception_id") == exid]
        rule = self.policy.exception_actions.get(choice.trigger)
        limits = rule.max_attempts if rule else {}
        if choice.action.startswith("wait"):
            if sum(1 for a in done if str(a).startswith("wait")) >= limits.get(choice.action, MAX_WAITS):
                return "max_attempts"
        elif choice.action in NOTIFY_ACTIONS and done.count(choice.action) >= limits.get(choice.action, 1):
            return "max_attempts"  # 같은 알림을 되풀이하지 않는다(정답 세트 XD-042·045·200·203)
        return None

    def approve_action(self, exception_id: str, actor: str, now: datetime) -> Decision:
        """사람 승인이 필요한 행동(propose_rematch 등)을 승인하고 실행한다."""
        events = self._events()
        req = next((e for e in events if e.type is EventType.ACTION_APPROVAL_REQUESTED
                    and e.payload.get("exception_id") == exception_id), None)
        raised = next((e for e in events if e.type is EventType.EXCEPTION_RAISED
                       and e.payload.get("exception_id") == exception_id), None)
        if req is None or raised is None:
            return Decision("unknown", "action")
        if any(e.type is EventType.EXCEPTION_RESOLVED and e.payload.get("exception_id") == exception_id for e in events):
            return Decision("superseded", "action")
        action = req.payload["action"]
        roles = set(self.policy.hospital_profile.escalation_roles) | set(self.policy.manual_override.roles)
        if actor not in roles:
            return Decision("rejected", "action", None, ["approver_role"])
        prop = next((e for e in events if e.type is EventType.ACTION_PROPOSED
                     and e.payload.get("exception_id") == exception_id and e.payload.get("action") == action), None)
        from turnaround.agent.brain import ActionChoice
        choice = ActionChoice(exception_id=exception_id, trigger=raised.payload["trigger"], case=raised.case_id,
                              action=action, params=(prop.payload.get("params") if prop else {}) or {},
                              rationale=(prop.payload.get("rationale") if prop else "") or "",
                              basis_seq=max((e.seq or 0) for e in events), mode="approved")
        base = dict(exception_id=exception_id, action=action)
        # 승인 직전 재확인 (CLAUDE.md 4번, 2단계 안전 검수 H4): 단계 잠금·선행 검사를 다시 돌린다
        why = self._stage_lock(action, raised, events) or self._action_precheck(choice, raised, events)
        if why is not None:
            self._emit(raised.case_id, f"{exception_id}:approval_rejected:{action}", EventType.ACTION_REJECTED, now,
                       **base, reasons=[why], actor=actor)
            self._emit(raised.case_id, f"{exception_id}:task:approval_rejected", EventType.HUMAN_TASK_CREATED, now,
                       kind="approval_rejected", assignee="bed_manager", exception_id=exception_id, action=action,
                       reason=why, bed_id=raised.payload.get("bed_id"))
            self._emit(raised.case_id, f"{exception_id}:resolved", EventType.EXCEPTION_RESOLVED, now,
                       exception_id=exception_id, how="approval_rejected")
            return Decision("rejected", "action", None, [why])
        self._emit(raised.case_id, f"{exception_id}:approved:{action}", EventType.ACTION_APPROVED, now, "human",
                   **base, actor=actor)
        self._execute_action(choice, raised, now)
        self._emit(raised.case_id, f"{exception_id}:executed:approved", EventType.ACTION_EXECUTED, now, **base,
                   autonomy="human")
        self._emit(raised.case_id, f"{exception_id}:resolved", EventType.EXCEPTION_RESOLVED, now,
                   exception_id=exception_id, how=action)
        return Decision("executed", "action", "human")

    def _exception_discharge(self, raised: Event, events: list[Event]):
        ds = fold(events).discharges
        return ds.get(raised.payload.get("discharge_id") or "") or ds.get(raised.case_id)

    def _room_terminal_pending(self, bed_id: str | None, events: list[Event]) -> bool:
        """같은 병실(다인실 옆 침상 포함)에 종말소독 전 격리 퇴실이 있는가 (2단계 안전 검수 M4, 병실 단위 판정)."""
        room = self.room_of.get(bed_id or "")
        beds = [b for b, r in self.room_of.items() if r == room] if room else [bed_id]
        return any(self._isolation_discharge_pending(b, events) for b in beds if b)

    def _action_precheck(self, choice, raised: Event, events: list[Event]) -> str | None:
        ctx = raised.payload.get("context") or {}
        d = self._exception_discharge(raised, events)
        if choice.action == "offer_lounge" and d is not None and (d.isolation or any(
                e.case_id == d.id and e.payload.get("mobility_aid") is True for e in events)):
            return "lounge_unsafe"  # 격리·이동 보조 퇴실 환자는 공용 라운지 제외 (설계 개정 3.2·Q9, 2단계 안전 검수 H2)
        if choice.action in ("re_request_cleaning", "request_bed_prep_first") and d is not None \
                and EventType.PATIENT_DEPARTED not in d.facts:
            return "not_departed"  # 환자가 있는 병실에 청소·침상 정리를 보내지 않는다 (2단계 검수 H3·현장 H1)
        if choice.action == "re_request_cleaning" and not any(
                e.type is EventType.CLEANING_REQUEST_ISSUED and e.case_id == (d.id if d else raised.case_id)
                and not e.payload.get("reminder") for e in events):
            return "no_request_to_remind"  # 재요청은 이미 나간 요청이 있을 때만
        if choice.action == "request_bed_prep_first" and self._room_terminal_pending(raised.payload.get("bed_id"),
                                                                                    events):
            return "terminal_cleaning_pending"  # 격리 퇴실 병실은 종말소독 완료 전 침상 정리 금지 (H34)
        if choice.action == "replan_now" and ctx.get("correction_direction") == "loosen" \
                and not ctx.get("correction_confirmed"):
            return "loosen_unconfirmed"  # 안전을 푸는 정정은 사람이 확인하기 전에 다시 계획하지 않는다
        if choice.action == "request_bed_prep_first" and raised.payload.get("trigger") == "promise_at_risk" \
                and ctx.get("isolation_discharge"):
            return "terminal_cleaning_pending"
        if choice.action == "rematch_now" and raised.payload.get("trigger") == "cleaning_overdue":
            contacted = {e.case_id for e in events if e.type is EventType.PATIENT_CONTACTED}
            if set(ctx.get("waiting_admissions") or []) & contacted:
                return "contacted"  # 연락한 매칭은 추천안 + 사람 승인으로 (propose_rematch)
        return None

    def _release_waiting(self, exid: str, raised: Event, now: datetime, reason: str) -> None:
        """이 병상을 기다리던 사전 매칭을 풀고 다시 찾는다. 원래 병동에 철회 통지 먼저 (DECISIONS 재매칭 꼬임 방지)."""
        bed = raised.payload.get("bed_id")
        for adm_case in (raised.payload.get("context") or {}).get("waiting_admissions") or []:
            self._emit(adm_case, f"{exid}:withdraw:{adm_case}", EventType.WARD_NOTIFIED, now, bed_id=bed,
                       reason="rematch_withdrawn", detail=reason)
            self._emit(adm_case, f"{exid}:release:{adm_case}", EventType.PREMATCH_RELEASED, now, bed_id=bed,
                       reason=reason, discharge_id=raised.case_id)
            self._emit(adm_case, f"{exid}:rematch:{adm_case}", EventType.REMATCH_REQUESTED, now, target="admission",
                       admission_id=adm_case)

    def _execute_action(self, choice, raised: Event, now: datetime) -> None:
        exid, case = choice.exception_id, choice.case
        rule = self.policy.exception_actions.get(choice.trigger)
        for kind, role in (rule.companions.get(choice.action, []) if rule else []):
            # 함께 할 행동: 근거(예외·사유)를 붙인 작업 (가만히 기다리지 않는다, 자동 행동은 사유를 알린다)
            self._emit(case, f"{exid}:companion:{kind}", EventType.HUMAN_TASK_CREATED, now, kind=kind,
                       assignee=role, reason=f"{choice.trigger}:{choice.action}", bed_id=raised.payload.get("bed_id"),
                       exception_id=exid,
                       rationale=choice.rationale, context=raised.payload.get("context"))
        if choice.action == "escalate":
            card = dict(raised.payload.get("context") or {})
            card.update({k: choice.params[k] for k in ("rejected_action", "rejected_reason") if choice.params.get(k)})
            card["deadline"] = raised.payload.get("deadline")
            also = []
            p, _ = self._admission(raised.payload.get("admission_id") or case, self._events())
            if raised.payload.get("trigger") == "late_isolation" or (p is not None and p.isolation_order and
                                                                      IsolationType.AIRBORNE in p.precautions):
                also.append("ipc")  # 격리 사안은 감염관리실도 받는다 (2단계 안전 검수 M7)
            self._emit(case, f"{exid}:task:escalate", EventType.HUMAN_TASK_CREATED, now, reason="brain_escalate",
                       kind="brain_escalate", assignee=choice.params.get("role", "bed_manager"),
                       trigger=choice.trigger, bed_id=raised.payload.get("bed_id"), exception_id=exid,
                       card=card, also_notify=also)  # 근거 카드 (거부된 행동·사유·마감 포함)
        elif choice.action in ("rematch_now", "propose_rematch") and choice.trigger == "cleaning_overdue":
            self._release_waiting(exid, raised, now, "cleaning_overdue")
        elif choice.action in ("rematch_now", "propose_rematch") and choice.trigger in ("ward_objection",
                                                                                       "info_changed_pending"):
            self._release_waiting(exid, raised, now, choice.trigger)
        elif choice.action == "replan_now":
            self._release_waiting(exid, raised, now, "info_changed")
            if not (raised.payload.get("context") or {}).get("waiting_admissions"):
                self._emit(case, f"{exid}:replan", EventType.REMATCH_REQUESTED, now, target="admission", admission_id=case)
        elif choice.action == "request_recontact":
            self._emit(case, f"{exid}:task:recontact", EventType.HUMAN_TASK_CREATED, now, kind="recontact",
                       assignee="bed_manager", reason=choice.trigger, bed_id=raised.payload.get("bed_id"),
                       exception_id=exid, context=raised.payload.get("context"))
        elif choice.action == "remind_ward":
            self._emit(case, f"{exid}:task:call_up", EventType.HUMAN_TASK_CREATED, now, kind="call_up_reminder",
                       assignee="ward", reason=choice.trigger, bed_id=raised.payload.get("bed_id"), exception_id=exid,
                       notice=True)
        elif choice.action in ("rematch_now", "propose_rematch"):
            # 병상은 규칙 모드가 고른다(U8). 여기서는 다시 찾기만 요청한다
            self._emit(case, f"{exid}:rematch", EventType.REMATCH_REQUESTED, now, target="admission", admission_id=case)
        elif choice.action == "remind_owner":
            # 그 단계 담당에게 근거(이 병상을 기다리는 입원)를 붙여 알린다. 처방은 주치의라 병동을 거친다 (사유·근거 통지)
            ctx = raised.payload.get("context") or {}
            step = ctx.get("step", "discharge_order")
            owner = {"physician": "ward"}.get(ctx.get("step_owner", "physician"), ctx.get("step_owner", "ward"))
            kind = "order_nudge" if step == "discharge_order" else f"step_nudge:{step}"
            self._emit(case, f"{exid}:task:{kind}", EventType.HUMAN_TASK_CREATED, now, reason="sequence_stalled",
                       kind=kind, assignee=owner, bed_id=raised.payload.get("bed_id"), step=step, exception_id=exid,
                       notice=True, next_admission_waiting=bool(ctx.get("next_admission_waiting")),
                       rationale=choice.rationale)
        elif choice.action == "offer_lounge":
            ctx = raised.payload.get("context") or {}
            self._emit(case, f"{exid}:lounge_offered", EventType.LOUNGE_OFFERED, now, bed_id=raised.payload.get("bed_id"),
                       to="ward", guardian_wait_min=ctx.get("guardian_wait_min"),
                       next_admission_waiting=bool(ctx.get("next_admission_waiting")), rationale=choice.rationale)
        elif choice.action == "re_request_cleaning":
            target = raised.payload.get("discharge_id") or case  # 약속 위험(E9)은 입원 사례에서 오른다
            evs = self._events()
            if any(e.type is EventType.CLEANING_REQUEST_ISSUED and e.case_id == target and e.payload.get("reminder")
                   and e.observed_at > now - timedelta(minutes=30) for e in evs):
                return  # 30분 안에 같은 청소를 이미 재요청했다 (설계 가정, 다른 예외와 겹침 방지)
            case = target
            last = [e for e in evs if e.type is EventType.CLEANING_REQUEST_ISSUED and e.case_id == case]
            keep = {k: v for k, v in (last[-1].payload if last else {}).items()
                    if k in ("bed_id", "cleaning_type", "aerosol_wait_min", "sporicidal")}
            keep.setdefault("bed_id", raised.payload.get("bed_id"))
            d = fold(evs).discharges.get(case)
            # 등급은 격리 여부로 다시 계산한다(앞 요청이 일반이어도 격리 퇴실이면 종말소독, 하향 없음. 안전 검수 H3)
            if (d is not None and d.isolation) or any(e.payload.get("cleaning_type") == "terminal" for e in last):
                keep["cleaning_type"] = "terminal"
            keep.setdefault("cleaning_type", "general")
            self._emit(case, f"{exid}:re_request", EventType.CLEANING_REQUEST_ISSUED, now, **keep, reminder=True,
                       reason=choice.trigger)
        elif choice.action == "request_bed_prep_first":
            self._emit(case, f"{exid}:bed_prep", EventType.BED_PREP_REQUESTED, now, bed_id=raised.payload.get("bed_id"),
                       reason="cleaning_overdue")
        elif choice.action == "share_eta":
            self._emit(case, f"{exid}:share_eta", EventType.SHARE_WAITING_INFO, now, to=["bed_manager", "ward"],
                       reason=choice.trigger, context=raised.payload.get("context"))
        elif choice.action == "request_postpone_contact":
            self._emit(case, f"{exid}:task:postpone", EventType.HUMAN_TASK_CREATED, now, kind="postpone_contact",
                       assignee="bed_manager", reason=choice.trigger, exception_id=exid)
        elif choice.action.startswith("wait"):
            until = min(datetime.fromisoformat(raised.payload["deadline"]),
                        now + timedelta(minutes=self.policy.brain.max_wait_min))
            n = sum(1 for e in self._events() if e.type is EventType.RECHECK_SCHEDULED
                    and e.payload.get("exception_id") == exid)
            self._emit(case, f"{exid}:recheck:{n + 1}", EventType.RECHECK_SCHEDULED, now, exception_id=exid,
                       at=max(until, now + timedelta(minutes=15)).isoformat())

    # ---------- 기록 ----------

    def _emit(self, case: str, key: str, etype: EventType, now: datetime, source: str = "gateway", /, **payload) -> bool:
        e = Event(event_id=key, case_id=case, type=etype, occurred_at=now, observed_at=now, source=source,
                  payload=payload)
        return self.store.append(e) is not None

    def _audit(self, key: str, case: str, now: datetime, step: str, pid: str, decision: Decision, **extra) -> bool:
        return self._emit(case, key, EventType.GATEWAY_DECISION, now, step=step, proposal_id=pid,
                          decision=decision.status, tier=decision.tier, reasons=decision.reasons,
                          policy_version=self.policy.version, latest_seq=self.store.last_seq(), **extra)

    # ---------- 확인 등급 ----------

    def _review_reasons(self, prop: Proposal, patient: Patient, payload: dict, events: list[Event]) -> list[str]:
        """정책이 담당 검토로 정한 사유만. 정책에서 뺀 사유(자율권 승격 등)는 통보 근거에 남긴다 (로드맵 07 M3)."""
        r = self._all_review_reasons(prop, patient, payload, events)
        self._waived[prop.proposal_id] = [x for x in r if x not in self.policy.review_reasons]
        return [x for x in r if x in self.policy.review_reasons]

    def _all_review_reasons(self, prop: Proposal, patient: Patient, payload: dict, events: list[Event]) -> list[str]:
        r: list[str] = []
        if patient.isolation_order:
            r.append("preemptive_isolation" if patient.precaution_status and patient.precaution_status.value == "preemptive"
                     else "isolation")
        if patient.age < self.policy.pediatric_age:
            r.append("pediatric")
        if payload.get("from_icu"):
            r.append("icu_stepdown")
        if payload.get("single_room_reason") == "preference" or (payload.get("wants_single_room") and not patient.isolation_order):
            r.append("single_room_preference")
        if prop.bed_id is not None and prop.bed_id in self.room_of and not patient.isolation_order \
                and not payload.get("wants_single_room") and payload.get("single_room_reason") != "preference" \
                and self.room_by_id[self.room_of[prop.bed_id]].room_type is not RoomType.MULTI:
            r.append("grade_upgrade")  # 다인실(기본 입원료) 희망인데 상급병실 배정: 차액 동의 필요 (DECISIONS F, 원무 검수 H-5)
        if prop.bed_id is None:
            r.append("no_bed")
        if len(prop.scores) >= 2:
            top = sorted(prop.scores.values(), reverse=True)
            if top[0] - top[1] < self.policy.score_gap_min:
                r.append("close_scores")
        if payload.get("integrated_care_requested") and payload.get("integrated_care_consent") is not True:
            r.append("integrated_care_consent_missing")
        if patient.gender is None or patient.department in ("", "미상"):
            r.append("missing_input")
        if prop.bed_id is not None:
            ward = self.room_by_id[self.room_of[prop.bed_id]].ward_id if prop.bed_id in self.room_of else None
            room = self.room_of.get(prop.bed_id)
            for bed, other in self._occupants(events, exclude_case=prop.case).items():
                r_other = self.room_of.get(bed)
                if r_other and r_other != room and self.room_by_id[r_other].ward_id == ward and other.name == patient.name:
                    r.append("same_name_same_ward")
                    break
            if self._isolation_discharge_pending(prop.bed_id, events) is not None:
                r.append("isolation_discharge_bed")
        return r

    def _shacl(self, case: str, patient: Patient, bed_id: str, events: list[Event]) -> list[str]:
        occ = self._occupants(events, exclude_case=case)
        occ[bed_id] = patient
        mine = {f"urn:turnaround:patient/{patient.id}", f"urn:turnaround:room/{self.room_of.get(bed_id)}"}
        out = []
        for v in shacl_validate(self.rooms, self.beds, occ):
            focus = v.detail.split(" ", 1)[0]
            if focus in mine or v.code == "unknown_bed":
                out.append(v.code)
        return sorted(set(out))

    # ---------- 제안 시점 ----------

    def submit(self, prop: Proposal, now: datetime) -> Decision:
        key = f"gw:{prop.proposal_id}:submit"
        events = self._events()
        if any(e.event_id == key for e in events):
            return Decision("duplicate", "proposal")
        self._emit(prop.case, f"gw:{prop.proposal_id}:received", EventType.PROPOSAL_RECEIVED, now, "agent",
                   **prop.model_dump())
        events = self._events()
        patient, payload = self._admission(prop.case, events)
        d = self._check_proposal(prop, patient, payload, events, now)
        self._audit(key, prop.case, now, "submit", prop.proposal_id, d, basis_seq=prop.basis_seq)
        return d

    def _reject(self, prop: Proposal, stage: str, reasons: list[str], now: datetime, tier: str | None = None) -> Decision:
        etype = EventType.REJECTED_AT_PROPOSAL if stage == "proposal" else EventType.REJECTED_AT_COMMIT
        self._emit(prop.case, f"gw:{prop.proposal_id}:{stage}:rejected", etype, now, bed_id=prop.bed_id,
                   proposal_id=prop.proposal_id, reason=reasons[0], reasons=reasons)
        return Decision("rejected", stage, tier, reasons)

    def _route_review(self, prop: Proposal, reasons: list[str], now: datetime, suffix: str = "") -> None:
        self._emit(prop.case, f"gw:{prop.proposal_id}:review{suffix}", EventType.CASE_ROUTED_REVIEW, now,
                   bed_id=prop.bed_id, proposal_id=prop.proposal_id, reasons=reasons, alternatives=prop.alternatives)

    def _task(self, case: str, pid: str, kind: str, now: datetime, bed: str | None = None,
              assignee: str = "bed_manager") -> None:
        self._emit(case, f"gw:{pid}:task:{kind}", EventType.HUMAN_TASK_CREATED, now, bed_id=bed, kind=kind,
                   proposal_id=pid, assignee=assignee)

    def _check_proposal(self, prop: Proposal, patient: Patient | None, payload: dict, events, now) -> Decision:
        if patient is None:
            return self._reject(prop, "proposal", ["unknown_case"], now)
        if self._pinned(prop.case, events):
            return self._reject(prop, "proposal", ["pinned_by_human"], now)
        if self._changed_since(prop, events):
            return self._reject(prop, "proposal", ["info_changed"], now)
        reasons = self._review_reasons(prop, patient, payload, events)
        if prop.bed_id is None:
            self._emit(prop.case, f"gw:{prop.proposal_id}:no_bed", EventType.NO_BED_REPORTED, now,
                       proposal_id=prop.proposal_id)
            self._route_review(prop, reasons or ["no_bed"], now)
            if "missing_input" in reasons:
                self._task(prop.case, prop.proposal_id, "data_fix", now)
            return Decision("awaiting_review", "proposal", "review", reasons or ["no_bed"])
        if prop.bed_id not in self.room_of:
            return self._reject(prop, "proposal", ["unknown_bed"], now)
        if prop.bed_id in self._occupants(events, exclude_case=prop.case):
            return self._reject(prop, "proposal", ["bed_unavailable"], now)
        if self._out_of_service(prop.bed_id, events):
            return self._reject(prop, "proposal", ["room_out_of_service"], now)
        if blocked := self._on_hold(prop.bed_id, events):
            return self._reject(prop, "proposal", [blocked], now)
        violations = self._shacl(prop.case, patient, prop.bed_id, events)
        if violations:
            d = self._reject(prop, "proposal", violations, now, "review" if reasons else None)
            if set(violations) <= _INPUT_VIOLATIONS or "missing_input" in reasons:
                # 입력 누락은 사람이 데이터를 고쳐야 하므로 담당 검토로 보낸다
                self._route_review(prop, reasons or ["missing_input"], now)
                self._task(prop.case, prop.proposal_id, "data_fix", now)
            return d
        if reasons:
            self._route_review(prop, reasons, now)
            if "missing_input" in reasons:
                self._task(prop.case, prop.proposal_id, "data_fix", now)
            room = self.room_by_id[self.room_of[prop.bed_id]]
            if ("single_room_preference" in reasons or "grade_upgrade" in reasons) and room.room_type is not RoomType.MULTI:
                # 차액 동의는 환자·병실 등급마다 한 번 받는 서명이다 (LP-22, 2단계 현장 검수 H5)
                self._emit(prop.case, f"gw:{prop.case}:task:fee_consent:{room.room_type.value}",
                           EventType.HUMAN_TASK_CREATED, now, bed_id=prop.bed_id, kind="fee_consent",
                           proposal_id=prop.proposal_id, room_grade=room.room_type.value, assignee="bed_manager")
            return Decision("awaiting_review", "proposal", "review", reasons)
        if self._agent_commits():
            # 검토 사유 없는 평범한 사례: 확정 직전 재확인을 거쳐 에이전트가 확정하고 통보한다 (DECISIONS 2026-11-09)
            d = self.commit(prop.proposal_id, self.location_version(prop.bed_id), now, actor="agent")
            return Decision(d.status, d.stage, "auto" if d.status == "committed" else d.tier, d.reasons)
        promoted = self.policy.autonomy_stage >= 2 and patient.department in self.policy.promoted_departments
        if promoted:
            self._ask_ward(prop, now)
            return Decision("awaiting_ward", "proposal", "ward_confirm", [])
        self._emit(prop.case, f"gw:{prop.proposal_id}:manager_confirm", EventType.MANAGER_CONFIRM_REQUESTED, now,
                   bed_id=prop.bed_id, proposal_id=prop.proposal_id)
        return Decision("awaiting_manager", "proposal", "ward_confirm", [])

    def _notify_mode(self) -> bool:
        """통보형 병동 확인 (DECISIONS 2026-11-09 C): 병동에 묻지 않고 확정 뒤 통보한다."""
        return self.policy.hospital_profile.ward_confirmation.mode == "notify"

    def _runner_up(self, prop: Proposal) -> dict:
        """2순위 병상과 점수 (점수 근접이면 표시). 통보를 받은 사람이 바로 되돌릴 수 있게."""
        others = sorted(((s, b) for b, s in prop.scores.items() if b != prop.bed_id), reverse=True)
        if not others:
            alt = next((b for b in prop.alternatives if b != prop.bed_id), None)
            return {"runner_up": {"bed_id": alt, "score": None}, "close_call": False} if alt else {}
        score, bed = others[0]
        mine = prop.scores.get(prop.bed_id or "")
        return {"runner_up": {"bed_id": bed, "score": score},
                "close_call": mine is not None and mine - score < self.policy.score_gap_min}

    def _agent_commits(self) -> bool:
        """공식 확정 자율 단계가 auto이고 통보형이면 검토 사유 없는 사례를 에이전트가 확정한다."""
        return self._notify_mode() and self.policy.autonomy.get("official_commit", "human") != "human"

    def _ask_ward(self, prop: Proposal, now: datetime) -> None:
        if self._notify_mode():
            return
        self._emit(prop.case, f"gw:{prop.proposal_id}:ask_ward", EventType.WARD_ACCEPTANCE_ASKED, now,
                   bed_id=prop.bed_id, proposal_id=prop.proposal_id)

    # ---------- 사람 응답 ----------

    def manager_approve(self, pid: str, now: datetime, actor: str = "bed_manager") -> Decision:
        events = self._events()
        prop = self._proposal(pid, events)
        if prop is None:
            return Decision("unknown", "approve")
        reviewed = any(e.type is EventType.CASE_ROUTED_REVIEW and e.payload.get("proposal_id") == pid for e in events)
        wc = self.policy.hospital_profile.ward_confirmation
        if self._notify_mode() and wc.merge_manager_confirm_into_commit and not reviewed:
            # 인터뷰 Q1: 통보형에서는 원무 배정 담당의 한 동작이 확정이다 (판단 개입이 아니라 책임 개입 하나)
            return self.commit(pid, self.location_version(prop.bed_id), now, actor=actor)
        self._emit(prop.case, f"gw:{pid}:manager_approved", EventType.MANAGER_APPROVED, now, "human",
                   proposal_id=pid, bed_id=prop.bed_id, actor=actor)
        if self._notify_mode():
            return self.commit(pid, self.location_version(prop.bed_id), now, actor=actor)  # 검토 승인 뒤 확정
        self._ask_ward(prop, now)
        return Decision("awaiting_ward", "approve")

    def ward_objection(self, pid: str, reason: str, now: datetime) -> Decision:
        """통보형에서 병동의 이의 제기. 연락 전이면 자동 해제·재매칭, 연락 뒤면 담당 검토·재연락 (보상 원칙)."""
        events = self._events()
        prop = self._proposal(pid, events)
        if prop is None:
            return Decision("unknown", "objection")
        self._emit(prop.case, f"gw:{pid}:ward_objection", EventType.WARD_OBJECTION_RAISED, now, "human",
                   proposal_id=pid, bed_id=prop.bed_id, reason=reason)
        contacted = any(e.type is EventType.PATIENT_CONTACTED and e.case_id == prop.case for e in events)
        if contacted:
            self._route_review(prop, ["ward_objection"], now, ":objection")
            self._task(prop.case, pid, "recontact", now, prop.bed_id)
            return Decision("awaiting_review", "objection", "review", ["ward_objection"])
        self._emit(prop.case, f"gw:{pid}:objection_release", EventType.PREMATCH_RELEASED, now, bed_id=prop.bed_id,
                   proposal_id=pid, reason="ward_objection")
        self._emit(prop.case, f"gw:{pid}:objection_rematch", EventType.REMATCH_REQUESTED, now, target="admission",
                   admission_id=prop.case)
        return Decision("released", "objection")

    def ward_reply(self, pid: str, accepted: bool, now: datetime, reason: str | None = None) -> Decision:
        events = self._events()
        prop = self._proposal(pid, events)
        if prop is None:
            return Decision("unknown", "ward")
        if accepted:
            self._emit(prop.case, f"gw:{pid}:ward_accepted", EventType.WARD_ACCEPTED, now, "human",
                       proposal_id=pid, bed_id=prop.bed_id)
            return Decision("awaiting_commit", "ward")
        self._emit(prop.case, f"gw:{pid}:ward_rejected", EventType.WARD_REJECTED, now, "human",
                   proposal_id=pid, bed_id=prop.bed_id, reason=reason)
        # 설계: 수용 불가 회신이면 사유와 재매칭 후보 2개를 병상관리 담당에게
        self._route_review(prop, ["ward_rejected"], now, ":ward_rejected")
        return Decision("awaiting_review", "ward", "review", ["ward_rejected"])

    # ---------- 확정 직전 ----------

    def commit(self, pid: str, approved_version: int, now: datetime, attempt: int = 1, actor: str = "원무") -> Decision:
        key = f"gw:{pid}:commit:{attempt}"
        events = self._events()
        if any(e.event_id == key for e in events):
            return Decision("duplicate", "commit")
        prop = self._proposal(pid, events)
        if prop is None:
            return Decision("unknown", "commit")
        d = self._check_commit(prop, approved_version, events, now, attempt, actor)
        self._audit(key, prop.case, now, "commit", pid, d, approved_version=approved_version, actor=actor)
        return d

    def _notify_if_accepted(self, prop: Proposal, events, now, reason: str) -> None:
        if self._ward_accepted(prop.proposal_id, events):
            self._emit(prop.case, f"gw:{prop.proposal_id}:ward_notified", EventType.WARD_NOTIFIED, now,
                       bed_id=prop.bed_id, reason=reason, proposal_id=prop.proposal_id)

    def _check_commit(self, prop: Proposal, version: int, events, now, attempt: int, actor: str = "원무") -> Decision:
        stage = "commit"
        tag = f"{stage}" if attempt == 1 else f"{stage}{attempt}"
        if self._pinned(prop.case, events):
            return self._reject_commit(prop, ["pinned_by_human"], now, tag)
        changed = self._changed_since(prop, events)
        if changed:
            d = self._reject_commit(prop, ["info_changed"], now, tag)
            if any(e.type is EventType.ISOLATION_ORDER_ADDED for e in changed):
                self._emit(prop.case, f"gw:{prop.proposal_id}:reclassify", EventType.CASE_RECLASSIFIED_REVIEW, now,
                           bed_id=prop.bed_id, reason="isolation_order_added", proposal_id=prop.proposal_id)
            self._notify_if_accepted(prop, events, now, "info_changed")
            return d
        holder = next((c for c, b in self._assignments(events).items() if b == prop.bed_id and c != prop.case), None)
        if holder is not None:
            d = self._reject_commit(prop, ["bed_taken"], now, tag)
            self._emit(prop.case, f"gw:{prop.proposal_id}:no_bed", EventType.NO_BED_REPORTED, now,
                       proposal_id=prop.proposal_id)
            self._route_review(prop, ["no_bed"], now, ":bed_taken")
            self._notify_if_accepted(prop, events, now, "bed_taken")
            return d
        if not self._notify_mode() and not self._ward_accepted(prop.proposal_id, events):
            return self._reject_commit(prop, ["ward_acceptance_missing"], now, tag)
        if blocked := self._on_hold(prop.bed_id, events):
            d = self._reject_commit(prop, [blocked], now, tag)
            self._notify_if_accepted(prop, events, now, blocked)
            return d
        if self._out_of_service(prop.bed_id, events):
            d = self._reject_commit(prop, ["room_out_of_service"], now, tag)
            self._notify_if_accepted(prop, events, now, "room_out_of_service")
            return d
        if self._isolation_discharge_pending(prop.bed_id, events):
            return self._reject_commit(prop, ["precondition_unmet"], now, tag)  # LP-04: 보류, 재분류 아님
        if version != self.location_version(prop.bed_id):
            d = self._reject_commit(prop, ["version_conflict"], now, tag)
            self._task(prop.case, prop.proposal_id, "reapproval", now, prop.bed_id)
            return d
        patient, _ = self._admission(prop.case, events)
        violations = self._shacl(prop.case, patient, prop.bed_id, events)
        if violations:
            return self._reject_commit(prop, violations, now, tag)
        by = "agent" if actor == "agent" else "human"
        self._emit(prop.case, f"gw:{prop.proposal_id}:committed", EventType.MATCH_COMMITTED, now,
                   bed_id=prop.bed_id, proposal_id=prop.proposal_id, version=version,
                   discharge_id=prop.discharge_id, committed_by=by)
        if self._notify_mode():
            wc = self.policy.hospital_profile.ward_confirmation
            # 에이전트 확정은 원무 배정 담당에게도 근거와 함께 사후 보고한다 (되돌릴 수 있게)
            to = ["ward", "bed_manager"] if by == "agent" else ["ward"]
            basis = {"score": prop.scores.get(prop.bed_id or ""), "rationale": prop.rationale, "review_reasons": [],
                     "waived_reasons": self._waived.get(prop.proposal_id, []),
                     **self._runner_up(prop),
                     "alternatives": prop.alternatives[:2], "basis_seq": prop.basis_seq}
            self._emit(prop.case, f"gw:{prop.proposal_id}:ward_assignment_notified", EventType.WARD_ASSIGNMENT_NOTIFIED,
                       now, bed_id=prop.bed_id, proposal_id=prop.proposal_id, committed_by=by, notify_to=to,
                       basis=basis, objection_until=(now + timedelta(minutes=wc.objection_window_min)).isoformat())
        # 재매칭으로 다른 병상을 확정했으면 이전 사전 매칭을 푼다
        for m in fold(events).matches.values():
            if m.admission_id == prop.case and m.active and m.bed_id != prop.bed_id:
                self._emit(prop.case, f"gw:{prop.proposal_id}:release_prev:{m.bed_id}", EventType.PREMATCH_RELEASED,
                           now, bed_id=m.bed_id, reason="rematched", discharge_id=m.discharge_id)
        return Decision("committed", stage)

    def _reject_commit(self, prop: Proposal, reasons: list[str], now: datetime, tag: str) -> Decision:
        self._emit(prop.case, f"gw:{prop.proposal_id}:{tag}:rejected", EventType.REJECTED_AT_COMMIT, now,
                   bed_id=prop.bed_id, proposal_id=prop.proposal_id, reason=reasons[0], reasons=reasons)
        return Decision("rejected", "commit", None, reasons)

    # ---------- 청소 요청 (LP-03, LP-20) ----------

    def request_cleaning(self, discharge_case: str, cleaning_type: str, now: datetime) -> Decision:
        events = self._events()
        d = fold(events).discharges.get(discharge_case)
        if d is None:
            return Decision("unknown", "cleaning")
        key = f"{discharge_case}:saga:cleaning_request"  # 사가와 같은 멱등 키
        if any(e.event_id == key for e in events):
            return Decision("duplicate", "cleaning")
        payload: dict = {"bed_id": d.bed_id, "cleaning_type": "terminal" if d.isolation else cleaning_type}
        if d.isolation and {"droplet", "airborne"} & set(d.precautions):
            payload.update(self.aerosol_wait(d.bed_id, d.organism))
        if d.isolation and d.organism == "CDIFF":
            payload["sporicidal"] = True
        reasons = []
        if payload["cleaning_type"] != cleaning_type:
            payload["overridden_from"] = cleaning_type  # 거부하지 않고 고쳐서 실행, 감사 기록
            reasons.append("cleaning_type_forced")
        self._emit(discharge_case, key, EventType.CLEANING_REQUEST_ISSUED, now, **payload)
        dec = Decision("executed", "cleaning", "auto", reasons)
        self._audit(f"gw:cleaning:{discharge_case}", discharge_case, now, "cleaning", discharge_case, dec)
        return dec

    # ---------- 조회 권한 ----------

    def query(self, role: str, fields: list[str], case: str, now: datetime) -> Decision:
        allowed = set(self.policy.query_fields.get(role, []))
        qid = f"q{self.store.last_seq() + 1}"
        if not set(fields) <= allowed:
            self._emit(case, f"gw:{qid}:refused", EventType.QUERY_REFUSED, now, role=role, fields=fields,
                       reason="unauthorized_fields")
            d = Decision("refused", "query", None, ["unauthorized_fields"])
        else:
            self._emit(case, f"gw:{qid}:answered", EventType.QUERY_ANSWERED, now, role=role, fields=fields)
            d = Decision("answered", "query")
        self._audit(f"gw:{qid}:query", case, now, "query", qid, d, role=role)
        return d

    # ---------- 관리자 직접 수정 ----------

    def manual_override(self, case: str, bed_id: str, actor: str, role: str, reason: str, now: datetime,
                        safety_exception_by: tuple[str, str] | None = None,
                        reason_text: str | None = None) -> Decision:
        oid = f"mo{self.store.last_seq() + 1}"
        mo = self.policy.manual_override
        events = self._events()
        patient, _ = self._admission(case, events)
        if role not in mo.roles:
            d = Decision("refused", "override", None, ["role_not_allowed"])
        elif reason not in mo.reason_codes:
            d = Decision("refused", "override", None, ["reason_required"])
        elif patient is None or bed_id not in self.room_of:
            d = Decision("refused", "override", None, ["unknown_case_or_bed"])
        elif bed_id in self._occupants(events, exclude_case=case):
            # 물리 사실(병상 점유)은 누구도, 어떤 예외로도 넘지 못한다
            d = Decision("refused", "override", None, ["bed_occupied"])
        elif self._isolation_discharge_pending(bed_id, events):
            d = Decision("refused", "override", None, ["precondition_unmet"])  # 종말소독 완료 전
        elif self._out_of_service(bed_id, events):
            d = Decision("refused", "override", None, ["room_out_of_service"])  # 폐쇄·고장·감시 대기
        elif blocked := self._on_hold(bed_id, events):
            d = Decision("refused", "override", None, [blocked])  # 잡아 둔 병상, 사후 처치 중 병상
        elif (self._room_status(events).get(self.room_of.get(self._assignments(events).get(case, ""), ""))
              == "surveillance_hold"):
            d = Decision("refused", "override", None, ["surveillance_hold"])  # 지침 8.3.4 동실자 이동 제한
        else:
            violations = self._shacl(case, patient, bed_id, events)
            exception = None
            tier = self._safety_tier(violations, patient) if violations else None
            if violations:
                approved = bool(safety_exception_by and safety_exception_by[1] in mo.safety_exception_roles)
                ok = (tier == "preapproval" and approved) or (tier == "reason_entry" and (reason_text or approved))
                if not ok:
                    d = Decision("refused", "override", tier, violations)
                    self._audit(f"gw:{oid}:override", case, now, "override", oid, d, actor=actor, role=role,
                                reason_code=reason, bed_id=bed_id, reason_text=reason_text)
                    return d
                exception = {"tier": tier, "violations": violations, "reason_text": reason_text}
                if approved:
                    exception.update(by=safety_exception_by[0], role=safety_exception_by[1])
            old = self._assignments(events).get(case)
            if old and old != bed_id:
                self._emit(case, f"gw:{oid}:released", EventType.PREMATCH_RELEASED, now, bed_id=old,
                           reason="manual_override")
                if any(e.type is EventType.WARD_ACCEPTED and e.case_id == case and e.payload.get("bed_id") == old
                       for e in events):
                    self._emit(case, f"gw:{oid}:ward_notified", EventType.WARD_NOTIFIED, now, bed_id=old,
                               reason="manual_override")
            self._emit(case, f"gw:{oid}:committed", EventType.MATCH_COMMITTED, now, bed_id=bed_id,
                       proposal_id=oid, manual=True, version=self.location_version(bed_id))
            self._emit(case, f"gw:{oid}:applied", EventType.MANUAL_OVERRIDE_APPLIED, now, bed_id=bed_id,
                       actor=actor, reason_code=reason)
            if self._notify_mode():  # 통보형 뒷처리 (원무 검수 H-3): 옛 병동 철회, 새 병동 통보, 연락했으면 재연락
                if old and old != bed_id and any(e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.case_id == case
                                                 and e.payload.get("bed_id") == old for e in events):
                    self._emit(case, f"gw:{oid}:withdraw", EventType.WARD_NOTIFIED, now, bed_id=old,
                               reason="manual_override")
                wc = self.policy.hospital_profile.ward_confirmation
                self._emit(case, f"gw:{oid}:ward_assignment_notified", EventType.WARD_ASSIGNMENT_NOTIFIED, now,
                           bed_id=bed_id, proposal_id=oid, committed_by="human", notify_to=["ward"],
                           objection_until=(now + timedelta(minutes=wc.objection_window_min)).isoformat())
                if any(e.type is EventType.PATIENT_CONTACTED and e.case_id == case for e in events):
                    self._emit(case, f"gw:{oid}:task:recontact", EventType.HUMAN_TASK_CREATED, now, kind="recontact",
                               assignee="bed_manager", bed_id=bed_id, reason="manual_override_after_contact")
            if tier == "reason_entry" and not (exception or {}).get("by"):
                self._emit(case, f"gw:{oid}:post_review", EventType.HUMAN_TASK_CREATED, now, kind="post_review",
                           assignee="ipc", bed_id=bed_id, violations=violations, reason_text=reason_text,
                           due=self.calendar.next_workday_noon(now).isoformat())
            d = Decision("applied", "override", tier, [])
            self._audit(f"gw:{oid}:override", case, now, "override", oid, d, actor=actor, role=role,
                        reason_code=reason, bed_id=bed_id, safety_exception=exception, reason_text=reason_text)
            return d
        self._audit(f"gw:{oid}:override", case, now, "override", oid, d, actor=actor, role=role,
                    reason_code=reason, bed_id=bed_id)
        return d

    _TIER_RANK = {"reason_entry": 1, "preapproval": 2, "forbidden": 3}

    def _safety_tier(self, violations: list[str], patient: Patient) -> str:
        mo = self.policy.manual_override
        tiers = []
        for v in violations:
            t = mo.safety_tiers.get(v, "forbidden")  # 분류 안 된 위반은 가장 엄격하게
            if patient.organism and patient.organism in mo.preapproval_organisms.get(v, []):
                t = max(t, "preapproval", key=self._TIER_RANK.__getitem__)
            tiers.append(t)
        return max(tiers, key=self._TIER_RANK.__getitem__)
