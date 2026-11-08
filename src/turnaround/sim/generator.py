"""합성 병원 데이터 생성기. 같은 시드면 항상 같은 결과를 낸다."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from .distributions import DEFAULT_STAGE_DISTRIBUTIONS
from .models import (
    AdmissionRequest,
    Bed,
    CleaningType,
    Discharge,
    DischargeTimeline,
    Gender,
    GeneratorConfig,
    IsolationType,
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
                    isolation: IsolationType | None = None, name: str | None = None) -> Patient:
        self._pid += 1
        age = self.rng.randint(25, 90) if age is None else age
        return Patient(
            id=f"SYN-P{self._pid:04d}", name=name or self.new_name(), gender=gender, age=age,
            department=department, isolation_order=isolation is not None, isolation_type=isolation,
            high_fall_risk=age >= 75 and self.rng.random() < 0.5,
        )

    # ---------- 퇴원 ----------

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

        result = []
        for i, (day, bed, iso) in enumerate(plan):
            room = self.room_by_id[bed.room_id]
            ward = self.ward_by_id[bed.ward_id]
            gender = room.gender or self.rng.choice(list(Gender))
            patient = self.new_patient(
                gender=gender, department=self.rng.choice(ward.departments),
                isolation=self.rng.choice(list(IsolationType)) if iso else None,
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
            med = order + m(d["medication"])
            edu = order + m(d["education"])
            billing = med + m(d["billing"])
            departed = max(billing, edu, guardian_arrived) + m(d["exit_prep"])
            requested = departed + m(d["baseline_cleaning_request_delay"])
            clean_start = requested + m(d["cleaning_response"] + extra)
            clean_done = clean_start + m(d["cleaning"])
            ready = clean_done + m(d["bed_make"])

            result.append(Discharge(
                id=f"SYN-D{i + 1:03d}", day=day, bed_id=bed.id, patient=patient,
                decided_at=decided_at, registered_at=registered_at,
                guardian_expected_at=guardian_expected, guardian_delayed=i in guardian_delayed,
                cleaning_type=cleaning_type, cleaning_delayed=i in cleaning_delayed,
                cleaning_extra_delay_min=extra, durations_min=d,
                actual=DischargeTimeline(
                    order_confirmed_at=order, medication_ready_at=med, education_done_at=edu,
                    billing_done_at=billing, guardian_arrived_at=guardian_arrived, departed_at=departed,
                    baseline_cleaning_requested_at=requested, cleaning_started_at=clean_start,
                    cleaning_done_at=clean_done, bed_ready_at=ready,
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
        special = ["airborne", "contact", "pediatric", "icu", "single_room", "same_name", "missing_gender"]
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
            if k in ("airborne", "contact"):
                ward, dept = _WARDS[0], "감염내과"
            patient = self.new_patient(
                gender=None if k == "missing_gender" else self.rng.choice(list(Gender)),
                department="소아청소년과" if k == "pediatric" else dept,
                age=self.rng.randint(3, 12) if k == "pediatric" else None,
                isolation={"airborne": IsolationType.AIRBORNE, "contact": IsolationType.CONTACT}.get(k),
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
