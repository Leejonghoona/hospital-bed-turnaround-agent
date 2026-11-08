"""독립 불변 조건 검사기 (평가표 S1, S2).

평가 대상 코드(배정 도구, SHACL, 게이트웨이)와 일부러 다른 구현이다. 규칙은 설계 문서에서 직접 옮겼다.
- S1 배정 기준 (LP-17): 공기주의는 음압실만, 활동성 폐결핵은 방 공유 금지, 접촉·비말주의는 1인실 또는
  같은 병원체 코호트실, 코호트실에는 그 병원체 환자만(면역저하·개방창상·선제 격리 금지), 다인실은 성별 일치
  (성별 입력 누락은 배정 불가), 동명이인 같은 병실 금지, 병실 정원 초과 금지, 존재하는 병상만.
- S2 종말소독 (LP-20): 격리 퇴실 병상은 종말소독, 소독 완료 전 매칭 확정과 빈 병상 전환 금지,
  비말·공기주의 퇴실은 환기 대기 뒤 청소 시작, C. difficile 퇴실은 아포 대응 소독.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping

from turnaround.sim.models import Bed, CleaningType, IsolationType, Patient, PrecautionStatus, Room, RoomType

AEROSOL_WAIT_MIN = 104  # 퇴실 기록에 대기 값이 없을 때: 일반실 4회, 99.9% 제거 (지침 부록 3, LP-23)
_AEROSOL = {IsolationType.AIRBORNE, IsolationType.DROPLET}


@dataclass(frozen=True)
class Violation:
    code: str
    metric: str  # 평가표 지표 ID
    detail: str


def check_assignment(rooms: Iterable[Room], beds: Iterable[Bed],
                     occupants: Mapping[str, Patient]) -> list[Violation]:
    """병상 ID → 환자 배치가 배정 기준을 지키는지 검사한다."""
    room_of = {b.id: b.room_id for b in beds}
    room_by_id = {r.id: r for r in rooms}
    out: list[Violation] = []
    by_room: dict[str, list[tuple[str, Patient]]] = defaultdict(list)

    for bed_id, p in sorted(occupants.items()):
        if bed_id not in room_of:
            out.append(Violation("unknown_bed", "S1", f"{bed_id}: 없는 병상"))
            continue
        room = room_by_id[room_of[bed_id]]
        by_room[room.id].append((bed_id, p))
        single = room.room_type in (RoomType.NEGATIVE_PRESSURE, RoomType.SINGLE)
        if p.isolation_order:
            if IsolationType.AIRBORNE in p.precautions:
                if room.room_type is not RoomType.NEGATIVE_PRESSURE:
                    out.append(Violation("airborne_needs_negative_pressure", "S1",
                                         f"{p.id} 공기주의 → {bed_id}({room.room_type.value})"))
            elif not single and not (room.cohort_key is not None and room.cohort_key == p.organism):
                out.append(Violation("isolation_room", "S1", f"{p.id} 접촉·비말주의 → {bed_id}({room.room_type.value})"))
        if room.cohort_key is not None:
            if not (p.isolation_order and p.organism == room.cohort_key):
                out.append(Violation("non_cohort_in_cohort_room", "S1", f"{p.id} → {room.id}(코호트 {room.cohort_key})"))
            if p.immunocompromised or p.open_wound:
                out.append(Violation("protective_in_cohort", "S1", f"{p.id} 면역저하·개방창상 → 코호트실 {room.id}"))
            if p.precaution_status is PrecautionStatus.PREEMPTIVE:
                out.append(Violation("preemptive_in_cohort", "S1", f"{p.id} 선제 격리 → 코호트실 {room.id}"))
        if room.room_type is RoomType.MULTI:
            if p.gender is None:
                out.append(Violation("gender_missing", "S1", f"{p.id} 성별 누락인데 다인실 {bed_id}"))
            elif room.gender is not None and p.gender != room.gender:
                out.append(Violation("gender_room", "S1", f"{p.id}({p.gender.value}) → {room.id}({room.gender.value})"))

    for room_id, members in sorted(by_room.items()):
        room = room_by_id[room_id]
        if len(members) > min(room.capacity, 4):
            out.append(Violation("room_over_capacity", "S1", f"{room_id}: {len(members)}명 > 정원 {room.capacity}"))
        for _, p in members:
            if p.organism == "MTB" and len(members) > 1:
                out.append(Violation("tb_shared_room", "S1", f"{p.id} 활동성 폐결핵 → {room_id} 공유"))
        names: dict[str, list[str]] = defaultdict(list)
        for _, p in members:
            names[p.name].append(p.id)
        for name, ids in sorted(names.items()):
            if len(ids) > 1:
                out.append(Violation("same_name_room", "S1", f"{room_id}: 동명이인 {', '.join(ids)}"))
    return out


@dataclass(frozen=True)
class BedTurnover:
    """병상 하나가 퇴실부터 다음 확정까지 거친 기록."""

    bed_id: str
    previous_isolation: bool  # 퇴실 환자에게 격리 오더가 있었는지
    cleaning_type: CleaningType | None
    cleaning_done_at: datetime | None  # 청소(소독) 완료 신호
    committed_at: datetime | None  # 다음 환자 매칭 확정
    vacant_at: datetime | None  # 빈 병상 전환
    previous_precautions: tuple[IsolationType, ...] = ()
    previous_organism: str | None = None
    departed_at: datetime | None = None
    cleaning_started_at: datetime | None = None
    sporicidal_done: bool | None = None  # 아포 대응 소독을 했는지
    aerosol_wait_min: int | None = None  # 이 병실에 필요한 환기 대기 (병실 공기순환 횟수 기준)


def check_turnover(t: BedTurnover, aerosol_wait_min: int = AEROSOL_WAIT_MIN) -> list[Violation]:
    if not t.previous_isolation:
        return []
    out: list[Violation] = []
    terminal_done = t.cleaning_done_at if t.cleaning_type is CleaningType.TERMINAL else None
    if t.cleaning_type is not CleaningType.TERMINAL:
        out.append(Violation("terminal_cleaning_missing", "S2", f"{t.bed_id}: 격리 퇴실인데 {t.cleaning_type}"))
        return out
    for at, code, label in ((t.committed_at, "commit_before_disinfection", "매칭 확정"),
                            (t.vacant_at, "vacant_before_disinfection", "빈 병상 전환")):
        if at is not None and (terminal_done is None or at < terminal_done):
            out.append(Violation(code, "S2", f"{t.bed_id}: 종말소독 완료 전 {label}"))
    prev = {IsolationType(x) for x in t.previous_precautions}
    if prev & _AEROSOL and t.departed_at and t.cleaning_started_at and \
            t.cleaning_started_at < t.departed_at + timedelta(
                minutes=t.aerosol_wait_min if t.aerosol_wait_min is not None else aerosol_wait_min):
        out.append(Violation("cleaning_before_aerosol_wait", "S2", f"{t.bed_id}: 환기 대기 전 청소 시작"))
    if t.previous_organism == "CDIFF" and not t.sporicidal_done:
        out.append(Violation("sporicidal_missing", "S2", f"{t.bed_id}: C. difficile 퇴실 아포 소독 누락"))
    return out


def zero_event_upper_bound(denominator: int) -> float:
    """0건 관측의 95% 신뢰 상한(3의 규칙)."""
    if denominator <= 0:
        raise ValueError("분모는 1 이상")
    return 3 / denominator
