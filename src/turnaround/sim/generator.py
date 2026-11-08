"""합성 병원 데이터 생성기. 같은 시드면 항상 같은 결과를 낸다."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from turnaround.rules import aerosol_wait_min

from .distributions import DEFAULT_STAGE_DISTRIBUTIONS
from .models import (
    RX_REVISION_REASONS,
    AdmissionRequest,
    Bed,
    CleaningType,
    Discharge,
    DischargeTimeline,
    Gender,
    GeneratorConfig,
    IsolationType,
    N_SPECIAL_ADMISSIONS,
    ORGANISM_PRECAUTIONS,
    PrecautionStatus,
    Patient,
    Room,
    RoomType,
    StageDistribution,
    SyntheticHospital,
    Ward,
)

_SURNAMES = ("김", "이", "박", "최", "정", "강", "조", "윤", "장", "임", "한", "오")
_SYLLABLES = ("가", "나", "다", "라", "온", "윤", "하", "서", "도", "린", "솔", "결", "새", "봄", "별", "누", "리", "진")

_WARDS = (
    Ward(id="W71", name="71병동", specialty="내과", departments=("호흡기내과", "소화기내과", "감염내과")),
    Ward(id="W72", name="72병동", specialty="외과", departments=("일반외과", "정형외과")),
)

# (방 번호, 유형, 병상 수, 성별)
_ROOM_LAYOUT = {
    "W71": (("01", RoomType.NEGATIVE_PRESSURE, 1, None), ("02", RoomType.SINGLE, 1, None),
            ("03", RoomType.MULTI, 4, Gender.MALE), ("04", RoomType.MULTI, 4, Gender.FEMALE),
            ("05", RoomType.MULTI, 2, Gender.FEMALE)),
    "W72": (("01", RoomType.SINGLE, 1, None), ("02", RoomType.SINGLE, 1, None),
            ("03", RoomType.MULTI, 4, Gender.MALE), ("04", RoomType.MULTI, 4, Gender.FEMALE),
            ("05", RoomType.MULTI, 2, Gender.MALE)),
}

_GUARDIAN_SLOTS = tuple(time(h, m) for h in range(10, 14) for m in (0, 30))
_AIRBORNE_ORGANISMS = ("MTB", "MEASLES", "VARICELLA")
_SINGLE_ROOM_ORGANISMS = ("MRSA", "VRE", "CRE", "CDIFF", "INFLUENZA", "PERTUSSIS")
_MDRO = {"MRSA", "VRE", "CRE"}  # 다제내성균: 격리·해제는 감염관리실 (LP-19)
# 담당 검토로 가야 할 입원 대기 사례 종류. 서로 다른 대기 건에 하나씩 둔다.
SPECIAL_KINDS = ("airborne", "contact", "droplet", "preemptive", "pediatric", "icu", "single_room",
                 "same_name", "missing_gender", "integrated_care")
assert len(SPECIAL_KINDS) == N_SPECIAL_ADMISSIONS
_ISOLATION_ROOMS = {RoomType.NEGATIVE_PRESSURE, RoomType.SINGLE}


def _layout() -> tuple[tuple[Room, ...], tuple[Bed, ...]]:
    rooms, beds = [], []
    for ward in _WARDS:
        for no, rtype, cap, gender in _ROOM_LAYOUT[ward.id]:
            room = Room(id=f"{ward.id}-R{no}", ward_id=ward.id, room_type=rtype, capacity=cap, gender=gender)
            rooms.append(room)
            beds += [Bed(id=f"{room.id}-B{i}", room_id=room.id, ward_id=ward.id) for i in range(1, cap + 1)]
    return tuple(rooms), tuple(beds)


class _Gen:
    def __init__(self, seed: int, config: GeneratorConfig, dists: dict[str, StageDistribution]):
        self.rng = random.Random(seed)
        self.rx_rng = random.Random(f"rx-{seed}")  # 퇴원약 단계 전용: 기존 난수열을 바꾸지 않는다
        self.cfg = config
        self.dists = dists
        self.rooms, self.beds = _layout()
        self.room_by_id = {r.id: r for r in self.rooms}
        self.ward_by_id = {w.id: w for w in _WARDS}
        self.used_names: set[str] = set()
        self._pid = 0

    # ---------- 공통 ----------

    def minutes(self, stage: str) -> int:
        return max(1, round(self.dists[stage].sample(self.rng)))

    def day_at(self, day: int, t: time) -> datetime:
        return datetime.combine(self.cfg.base_date + timedelta(days=day), t)

    def pick_indices(self, n: int, k: int, exclude: set[int] = frozenset()) -> set[int]:
        pool = [i for i in range(n) if i not in exclude]
        return set(self.rng.sample(pool, k))

    def new_name(self) -> str:
        while True:
            name = self.rng.choice(_SURNAMES) + self.rng.choice(_SYLLABLES) + self.rng.choice(_SYLLABLES)
            if name not in self.used_names:
                self.used_names.add(name)
                return name

    def new_patient(self, *, gender: Gender | None, department: str, age: int | None = None,
                    organism: str | None = None, status: PrecautionStatus | None = None,
                    ordered_by: str | None = None, name: str | None = None) -> Patient:
        """organism을 주면 지침 부록1 대응표(ORGANISM_PRECAUTIONS)대로 주의 집합을 붙인다."""
        self._pid += 1
        age = self.rng.randint(25, 90) if age is None else age
        prec = ORGANISM_PRECAUTIONS[organism] if organism else ()
        return Patient(
            id=f"SYN-P{self._pid:04d}", name=name or self.new_name(), gender=gender, age=age,
            department=department, precautions=prec, organism=organism,
            precaution_status=(status or PrecautionStatus.CONFIRMED) if organism else None,
            isolation_ordered_by=(ordered_by or ("ipc" if organism in _MDRO else "physician")) if organism else None,
            immunocompromised=self.rng.random() < 0.05, open_wound=self.rng.random() < 0.05,
            high_fall_risk=age >= 75 and self.rng.random() < 0.5,
            delirium_positive=age >= 75 and self.rng.random() < 0.2,
        )

    # ---------- 퇴원 ----------

    def organism_for(self, room: Room) -> str:
        """공기주의 병원체는 음압격리실에만, 접촉·비말주의는 1인실에 둔다 (LP-17)."""
        if room.room_type is RoomType.NEGATIVE_PRESSURE:
            return self.rng.choice(_AIRBORNE_ORGANISMS)
        return self.rng.choice(_SINGLE_ROOM_ORGANISMS)

    def discharge_beds(self) -> list[tuple[int, Bed, bool]]:
        """(day, bed, isolation) 목록. 같은 날 같은 병상은 한 번만."""
        cfg = self.cfg
        iso_beds = [b for b in self.beds if self.room_by_id[b.room_id].room_type in _ISOLATION_ROOMS]
        iso_days = set(self.rng.sample(range(cfg.days), cfg.isolation_discharge_count))
        out = []
        for day in range(cfg.days):
            chosen: list[tuple[Bed, bool]] = []
            if day in iso_days:
                chosen.append((self.rng.choice(iso_beds), True))
            taken = {b.id for b, _ in chosen}
            rest = [b for b in self.beds if b.id not in taken]
            chosen += [(b, False) for b in self.rng.sample(rest, cfg.discharges_per_day - len(chosen))]
            out += [(day, b, iso) for b, iso in chosen]
        return out

    def discharges(self) -> tuple[Discharge, ...]:
        cfg = self.cfg
        plan = self.discharge_beds()
        n = len(plan)
        guardian_delayed = self.pick_indices(n, cfg.guardian_delay_count)
        cleaning_delayed = self.pick_indices(n, cfg.cleaning_delay_count)
        unregistered = self.pick_indices(n, cfg.unregistered_count)
        rx_revised = set(self.rx_rng.sample(range(n), round(n * cfg.rx_revision_rate)))

        result = []
        for i, (day, bed, iso) in enumerate(plan):
            room = self.room_by_id[bed.room_id]
            ward = self.ward_by_id[bed.ward_id]
            gender = room.gender or self.rng.choice(list(Gender))
            patient = self.new_patient(
                gender=gender, department=self.rng.choice(ward.departments),
                organism=self.organism_for(room) if iso else None,
            )
            d = {s: self.minutes(s) for s in (
                "order_after_round", "medication", "education", "billing", "exit_prep",
                "baseline_cleaning_request_delay", "cleaning_response", "bed_make")}
            cleaning_type = CleaningType.TERMINAL if iso else CleaningType.GENERAL
            d["cleaning"] = self.minutes("cleaning_terminal" if iso else "cleaning_general")
            extra = self.minutes("cleaning_extra_delay") if i in cleaning_delayed else 0
            if i in guardian_delayed:
                d["guardian_delay"] = self.minutes("guardian_delay")

            decided_at = self.day_at(day - 1, time(17, 0)) + timedelta(minutes=self.rng.randint(0, 60))
            registered_at = None if i in unregistered else decided_at + timedelta(minutes=self.rng.randint(5, 90))
            guardian_expected = self.day_at(day, self.rng.choice(_GUARDIAN_SLOTS))
            if i in guardian_delayed:
                guardian_arrived = guardian_expected + timedelta(minutes=d["guardian_delay"])
            else:
                guardian_arrived = guardian_expected - timedelta(minutes=self.rng.randint(0, 30))

            m = lambda k: timedelta(minutes=k)  # noqa: E731
            order = self.day_at(day, time(8, 30)) + m(d["order_after_round"])
            # 퇴원약 (DAT-7): 처방 발행 → (수정) → 조제 → 병동 도착. 단계 시각은 설계 가정
            rx_issued = order + m(self.rx_rng.randint(0, 30))
            med = max(order + m(d["medication"]), rx_issued + m(20))
            rx_revised_at = rx_reason = None
            if i in rx_revised:
                rx_revised_at = rx_issued + m(self.rx_rng.randint(15, 60))
                rx_reason = self.rx_rng.choice(RX_REVISION_REASONS)
                med = max(med, rx_revised_at + m(self.rx_rng.randint(30, 60)))  # 재조제
            rx_dispensed = max(med - m(self.rx_rng.randint(10, 25)), rx_revised_at or rx_issued)
            edu = order + m(d["education"])
            billing = med + m(d["billing"])
            departed = max(billing, edu, guardian_arrived) + m(d["exit_prep"])
            requested = departed + m(d["baseline_cleaning_request_delay"])
            aerosol = bool({IsolationType.AIRBORNE, IsolationType.DROPLET} & set(patient.precautions))
            room = self.room_by_id[bed.room_id]
            wait = (cfg.aerosol_wait_override_min if cfg.aerosol_wait_override_min is not None
                    else aerosol_wait_min(room.air_changes_per_hour)) if aerosol else 0
            clean_start = max(requested + m(d["cleaning_response"] + extra), departed + m(wait))  # 환기 대기 (LP-20)
            clean_done = clean_start + m(d["cleaning"])
            ready = clean_done + m(d["bed_make"])

            result.append(Discharge(
                id=f"SYN-D{i + 1:03d}", day=day, bed_id=bed.id, patient=patient,
                decided_at=decided_at, registered_at=registered_at,
                guardian_expected_at=guardian_expected, guardian_delayed=i in guardian_delayed,
                cleaning_type=cleaning_type, cleaning_delayed=i in cleaning_delayed,
                cleaning_extra_delay_min=extra, durations_min=d,
                aerosol_wait_min=wait, sporicidal=patient.organism == "CDIFF", rx_revision_reason=rx_reason,
                actual=DischargeTimeline(
                    order_confirmed_at=order, medication_ready_at=med, education_done_at=edu,
                    billing_done_at=billing, guardian_arrived_at=guardian_arrived, departed_at=departed,
                    baseline_cleaning_requested_at=requested, cleaning_started_at=clean_start,
                    cleaning_done_at=clean_done, bed_ready_at=ready,
                    rx_issued_at=rx_issued, rx_revised_at=rx_revised_at, rx_dispensed_at=rx_dispensed,
                ),
            ))
        return tuple(result)

    # ---------- 입원 대기 ----------

    def admissions(self, discharges: tuple[Discharge, ...]) -> tuple[AdmissionRequest, ...]:
        cfg = self.cfg
        n = cfg.admission_count
        days = list(range(cfg.days)) + [self.rng.randrange(cfg.days) for _ in range(n - cfg.days)]
        self.rng.shuffle(days)
        # 담당 검토로 가야 할 사례를 서로 다른 대기 건에 하나씩 둔다.
        special = list(SPECIAL_KINDS)
        order = self.rng.sample(range(n), n)
        kind = {idx: special[j] for j, idx in enumerate(order[: len(special)])}
        normal = order[len(special):]
        cancelled = set(self.rng.sample(normal, cfg.admission_cancel_count))
        same_name = self.rng.choice(discharges).patient.name

        result = []
        for i in range(n):
            k = kind.get(i)
            ward = self.rng.choice(_WARDS)
            dept = self.rng.choice(ward.departments)
            organism = {"airborne": "MTB", "contact": self.rng.choice(["MRSA", "VRE", "CDIFF"]),
                        "droplet": "INFLUENZA", "preemptive": "CRE"}.get(k)
            if organism:
                ward, dept = _WARDS[0], "감염내과"
            patient = self.new_patient(
                gender=None if k == "missing_gender" else self.rng.choice(list(Gender)),
                department="소아청소년과" if k == "pediatric" else dept,
                age=self.rng.randint(3, 12) if k == "pediatric" else None,
                organism=organism,
                status=PrecautionStatus.PREEMPTIVE if k == "preemptive" else None,
                ordered_by="ipc" if k == "preemptive" else None,
                name=same_name if k == "same_name" else None,
            )
            day = days[i]
            requested = self.day_at(day, time(10, 0)) + timedelta(minutes=30 * self.rng.randint(0, 10))
            cancelled_at = None
            if i in cancelled:
                cancelled_at = self.day_at(day, time(7, 30)) + timedelta(minutes=self.rng.randint(0, 180))
            result.append(AdmissionRequest(
                id=f"SYN-A{i + 1:03d}", day=day, patient=patient, requested_at=requested,
                preferred_ward_id=ward.id, from_icu=k == "icu", wants_single_room=k == "single_room",
                single_room_reason="isolation" if organism else ("preference" if k == "single_room" else None),
                integrated_care_requested=k == "integrated_care",
                integrated_care_consent=None,  # 동의 미확인 (LP-21 검토 사유)
                cancelled_at=cancelled_at,
            ))
        return tuple(result)


def generate(seed: int = 42, config: GeneratorConfig | None = None,
             distributions: dict[str, StageDistribution] | None = None) -> SyntheticHospital:
    """합성 병원 하나를 만든다. 전역 random 상태는 건드리지 않는다."""
    cfg = config or GeneratorConfig()
    dists = {**DEFAULT_STAGE_DISTRIBUTIONS, **(distributions or {})}
    g = _Gen(seed, cfg, dists)
    discharges = g.discharges()
    admissions = g.admissions(discharges)
    return SyntheticHospital(
        seed=seed, config=cfg, distributions=dists, wards=_WARDS, rooms=g.rooms, beds=g.beds,
        discharges=discharges, admissions=admissions,
    )
