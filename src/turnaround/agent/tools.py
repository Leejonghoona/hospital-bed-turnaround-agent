"""계획 에이전트의 결정적 도구 (AGT-2). 배정 규칙과 숫자 계산은 전부 여기서 한다.

- 후보 병상 필터: 점유, 사용 중지, 배정 기준(SHACL)을 게이트웨이와 같은 검사로 거른다
- 점수 계산: 목표 함수 4~5항(공백 최소, 선호와 운영). 가중치는 정책 파일 `scoring`
- 준비 시각 추정: 단계별 소요시간 분포의 중앙값
- 조회: 사례 상태와 기록 (질의 응답용)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from turnaround.events.models import Event, EventType
from turnaround.gateway import Gateway
from turnaround.saga import fold
from turnaround.sim.distributions import DEFAULT_STAGE_DISTRIBUTIONS
from turnaround.sim.models import IsolationType, Patient, RoomType

_MED = {k: v.median_min for k, v in DEFAULT_STAGE_DISTRIBUTIONS.items()}
DEFAULT_DEPARTURE = time(11, 0)  # 보호자 도착 예정이 없을 때 퇴실 예상 시각 (설계 가정)


@dataclass(frozen=True)
class Candidate:
    bed_id: str
    score: float
    ready_at: datetime
    discharge_id: str | None = None  # 이 병상을 비우는 퇴원 사례 (사전 매칭)
    parts: dict = field(default_factory=dict, hash=False, compare=False)  # 점수 구성 (설명용)


class PlanningTools:
    def __init__(self, gateway: Gateway, wards, score_overrides: dict[str, float] | None = None):
        self.gw = gateway
        self.wards = {w.id: w for w in wards}
        self.w = gateway.policy.scoring
        self.score_overrides = dict(score_overrides or {})  # 평가용 점수 고정 주입

    # ---------- 상태 ----------

    def events(self) -> list[Event]:
        return self.gw.store.read()

    def patient(self, case: str, events: list[Event]) -> tuple[Patient | None, dict]:
        return self.gw._admission(case, events)

    def live_proposals(self, events: list[Event]) -> dict[str, dict]:
        """결론이 나지 않은 게이트웨이 제안: 제안 ID → 내용."""
        props = {e.payload["proposal_id"]: e.payload for e in events if e.type is EventType.PROPOSAL_RECEIVED}
        dead = {e.payload.get("proposal_id") for e in events if e.type in (
            EventType.REJECTED_AT_PROPOSAL, EventType.REJECTED_AT_COMMIT, EventType.WARD_REJECTED,
            EventType.MATCH_COMMITTED, EventType.NO_BED_REPORTED)}
        cancelled = {e.case_id for e in events if e.type is EventType.ADMISSION_CANCELLED}
        return {pid: p for pid, p in props.items() if pid not in dead and p["case"] not in cancelled}

    def held_beds(self, events: list[Event], exclude_case: str | None = None) -> set[str]:
        """다른 사례가 잡고 있는 병상: 확정, 살아 있는 사전 매칭, 결론 전 제안."""
        held = {b for c, b in self.gw._assignments(events).items() if c != exclude_case}
        s = fold(events)
        held |= {m.bed_id for m in s.matches.values() if m.active and m.admission_id != exclude_case}
        held |= {p["bed_id"] for p in self.live_proposals(events).values()
                 if p["case"] != exclude_case and p.get("bed_id")}
        return held

    def pending_admissions(self, events: list[Event]) -> list[str]:
        """에이전트가 병상을 찾아야 하는 입원 대기: 취소·확정·고정·사전 매칭·진행 중 제안이 없는 건."""
        s = fold(events)
        cancelled = {e.case_id for e in events if e.type is EventType.ADMISSION_CANCELLED}
        assigned = set(self.gw._assignments(events))
        matched = {m.admission_id for m in s.matches.values() if m.active}
        live = {p["case"] for p in self.live_proposals(events).values()}
        # 병상 없음 보고 뒤 새 사실(원천 시스템, 사람, 사가 이벤트)이 생기면 다시 찾는다. 그 전에는 담당 검토에 맡긴다
        last_no_bed = {e.case_id: e.seq for e in events if e.type is EventType.NO_BED_REPORTED}
        last_fact = max((e.seq for e in events if e.source in ("his", "human", "saga", "equipment")), default=0)
        review_open = {c for c, seq in last_no_bed.items() if seq > last_fact}
        out = []
        for e in events:
            if e.type is EventType.ADMISSION_REQUESTED and e.case_id not in out:
                c = e.case_id
                if c in cancelled or c in assigned or c in matched or c in live or c in review_open:
                    continue
                if self.gw._pinned(c, events):
                    continue
                out.append(c)
        return out

    # ---------- 준비 시각 ----------

    def ready_at(self, bed_id: str, events: list[Event], now: datetime, state=None) -> tuple[datetime, str | None]:
        """병상 준비 예상 시각과 그 병상을 비우는 퇴원 사례. 분포 중앙값으로 계산 (설계 가정)."""
        s = state or fold(events)
        cases = [d for d in s.discharges.values() if d.bed_id == bed_id and EventType.DISCHARGE_CANCELLED not in d.facts]
        if not cases:
            return now, None
        d = cases[-1]
        f = d.facts
        if EventType.BED_READY in f or EventType.BED_MARKED_VACANT in f:
            return now, None
        m = lambda k: timedelta(minutes=k)  # noqa: E731
        clean = _MED["cleaning_terminal"] if d.isolation else _MED["cleaning_general"]
        wait = 0
        if d.isolation and {"droplet", "airborne"} & set(d.precautions):
            wait = self.gw.aerosol_wait(bed_id)["aerosol_wait_min"]
        if EventType.CLEANING_DONE in f:
            t = f[EventType.CLEANING_DONE] + m(_MED["bed_make"])
        elif EventType.CLEANING_STARTED in f:
            t = f[EventType.CLEANING_STARTED] + m(clean + _MED["bed_make"])
        else:
            if EventType.PATIENT_DEPARTED in f:
                dep = f[EventType.PATIENT_DEPARTED]
            else:
                eta = next((datetime.fromisoformat(e.payload["eta"]) for e in reversed(events)
                            if e.case_id == d.id and e.type in (EventType.GUARDIAN_ETA_UPDATED, EventType.GUARDIAN_ETA_SET)
                            and e.payload.get("eta")), None)
                base = d.discharge_date or now.date()
                dep = (eta or datetime.combine(base, DEFAULT_DEPARTURE)) + m(_MED["exit_prep"])
            t = max(dep + m(wait), dep + m(_MED["cleaning_response"])) + m(clean + _MED["bed_make"])
        overdue = any(e.type is EventType.READY_TIME_RECOMPUTED and e.case_id == d.id
                      and e.payload.get("reason") == "cleaning_overdue" for e in events)
        if overdue and EventType.CLEANING_DONE not in f:
            start = 0 if EventType.CLEANING_STARTED in f else _MED["cleaning_response"]
            t = max(t, now + m(start + clean + _MED["bed_make"]))  # 청소 지연: 지금부터 다시 센다
        return max(t, now), d.id

    # ---------- 후보와 점수 ----------

    def candidates(self, case: str, now: datetime, events: list[Event] | None = None) -> list[Candidate]:
        events = events if events is not None else self.events()
        patient, payload = self.patient(case, events)
        if patient is None:
            return []
        occupied = self.gw._occupants(events, exclude_case=case)
        held = self.held_beds(events, exclude_case=case)
        load = self._ward_load(events, case)
        state = fold(events)
        out = []
        room_ok: dict[str, bool] = {}
        for bed in self.gw.beds:
            b = bed.id
            if b in occupied or b in held or self.gw._out_of_service(b, events):
                continue
            # 배정 기준 위반 병상은 후보가 아니다 (안전은 도구가 보장). 기준은 모두 병실 단위라 병실마다 한 번 검사한다
            r = self.gw.room_of[b]
            if r not in room_ok:
                room_ok[r] = not self.gw._shacl(case, patient, b, events)
            if not room_ok[r]:
                continue
            ready, disc = self.ready_at(b, events, now, state)
            sc, parts = self._score(patient, payload, b, ready, now, load)
            out.append(Candidate(b, self.score_overrides.get(b, sc), ready, disc, parts))
        return sorted(out, key=lambda c: (-c.score, c.ready_at, c.bed_id))

    def _ward_load(self, events: list[Event], case: str) -> dict[str, int]:
        load: dict[str, int] = {}
        for p in self.live_proposals(events).values():
            if p["case"] != case and p.get("bed_id"):
                w = self.gw.room_by_id[self.gw.room_of[p["bed_id"]]].ward_id
                load[w] = load.get(w, 0) + 1
        return load

    def _score(self, p: Patient, payload: dict, bed_id: str, ready: datetime, now: datetime,
               load: dict[str, int]) -> tuple[float, dict]:
        w = self.w
        room = self.gw.room_by_id[self.gw.room_of[bed_id]]
        ward = self.wards.get(room.ward_id)
        parts: dict[str, float] = {}
        if ward is not None and p.department in ward.departments:
            parts["department_ward_match"] = w.department_ward_match
        airborne = IsolationType.AIRBORNE in p.precautions
        if room.room_type is RoomType.NEGATIVE_PRESSURE and not airborne:
            parts["negative_pressure_preserve"] = w.negative_pressure_preserve  # LP-18 음압실 보존
        needs_single = bool(p.isolation_order) or payload.get("single_room_reason") not in (None, "preference")
        wants_single = payload.get("single_room_reason") == "preference" or payload.get("wants_single_room")
        if room.room_type is RoomType.SINGLE:
            if wants_single and not needs_single:
                parts["single_room_requested"] = w.single_room_requested
            elif not needs_single:
                parts["single_room_unrequested"] = w.single_room_unrequested
        if (p.high_fall_risk or p.delirium_positive) and room.near_nurse_station:
            parts["near_nurse_station_high_risk"] = w.near_nurse_station_high_risk
        if load.get(room.ward_id):
            parts["ward_load"] = w.ward_load_per_pending * load[room.ward_id]
        delay = max(0.0, (ready - now).total_seconds() / 60)
        if delay:
            parts["ready_delay"] = w.ready_delay_per_10min * delay / 10
        return round(sum(parts.values()), 2), parts
