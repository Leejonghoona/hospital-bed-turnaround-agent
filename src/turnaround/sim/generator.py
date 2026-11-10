"""합성 병원 데이터 생성기. 같은 시드면 항상 같은 결과를 낸다."""

from __future__ import annotations

import random
from datetime import datetime, time, timedelta

from turnaround.rules import aerosol_wait_min

from . import ops

from .distributions import DEFAULT_STAGE_DISTRIBUTIONS, REALISTIC_STAGE_OVERRIDES, SPLIT_CLEANING
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
# 이름 두 글자: 위 음절 뽑기(난수 순서를 바꾸지 않으려고 그대로 둔다)를 실제 이름처럼 들리는 조합으로 1:1로 바꾼다.
# 둘째 글자 목록은 성별마다 따로 두고 서로 겹치지 않게 해서, 새 동명이인이 생기지 않는다(동명이인 판정과 측정 결과 그대로).
# (2026-11-10 사용자 지적: 이름이 사람 이름 같지 않음)
_GIVEN_FIRST = ("지", "서", "민", "현", "수", "유", "하", "윤", "은", "주", "예", "도", "시", "정", "승", "재", "다", "채")
_GIVEN_SECOND = {
    "M": ("준", "호", "혁", "훈", "석", "수", "성", "규", "찬", "범", "환", "철", "태", "욱", "섭", "용", "근", "기"),
    "F": ("연", "아", "희", "은", "영", "경", "정", "미", "지", "숙", "란", "화", "선", "림", "나", "소", "유", "혜"),
    None: ("원", "윤", "현", "민", "진", "빈", "율", "서", "우", "솔", "하", "주", "인", "결", "담", "새", "람", "온"),
}
_GIVEN_ALT = {"M": "건", "F": "슬", None: "겸"}  # 같은 글자 반복(수수, 은은 등)을 피할 때 쓰는 글자. 어느 목록에도 없다


def _given(a: str, b: str, gender: str | None = None) -> str:
    second = _GIVEN_SECOND.get(gender, _GIVEN_SECOND[None])
    f, g = _GIVEN_FIRST[_SYLLABLES.index(a)], second[_SYLLABLES.index(b)]
    return f + (_GIVEN_ALT.get(gender, _GIVEN_ALT[None]) if f == g else g)


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

# 시연용 병원 (hospital="demo"): 병동 4개, 병동마다 44병상(1인실 2, 2인실 1, 4인실 10). 다인실은 남녀를 나눠 운영한다.
# 구성 근거: 일반 병동 하나 약 40~43병상, 4인실 위주에 1·2인실 일부(국민건강보험 일산병원 연보 병실 현황, 원문 확인).
# 성인 다인실 남녀 구분(의료법 시행규칙 제35조의2, 2026-05-31 복지부 보도설명 "현행대로 유지"). 병동 이름·진료과 묶음은 설계 가정.
_DEMO_WARDS = (
    Ward(id="W61", name="61병동", specialty="내과", departments=("소화기내과", "내분비내과")),
    Ward(id="W62", name="62병동", specialty="내과", departments=("호흡기내과", "감염내과")),
    Ward(id="W71", name="71병동", specialty="내과", departments=("순환기내과", "신장내과", "혈액종양내과")),
    Ward(id="W72", name="72병동", specialty="외과", departments=("일반외과", "정형외과", "신경외과")),
)
_M, _F = Gender.MALE, Gender.FEMALE
_DEMO_FOURS = tuple((f"{n:02d}", RoomType.MULTI, 4, _M if n % 2 == 0 else _F) for n in range(4, 14))
_DEMO_LAYOUT = {w.id: ((("01", RoomType.NEGATIVE_PRESSURE, 1, None), ("02", RoomType.NEGATIVE_PRESSURE, 1, None))
                       if w.id == "W62" else (("01", RoomType.SINGLE, 1, None), ("02", RoomType.SINGLE, 1, None)))
                + (("03", RoomType.MULTI, 2, _F if w.id in ("W61", "W72") else _M),) + _DEMO_FOURS for w in _DEMO_WARDS}


def _wards(cfg: GeneratorConfig) -> tuple[Ward, ...]:
    return _DEMO_WARDS if cfg.hospital == "demo" else _WARDS


_GUARDIAN_SLOTS = tuple(time(h, m) for h in range(10, 14) for m in (0, 30))
_GUARDIAN_SLOTS_REALISTIC = tuple(time(h, m) for h in range(9, 14) for m in (0, 30))[:-1]  # 09:00~13:00 (등급 C: 서울성모 "오전 내원" 안내, 점심 뒤 도착 사례 해운대백)
# 입원 수속 예정 (등급 B, 해운대백·동아대·서울아산·분당서울대): (시작, 끝, 가중치)
_ADMISSION_WINDOWS_REALISTIC = ((time(10, 0), time(12, 0), 15), (time(12, 0), time(14, 0), 25),
                                (time(14, 0), time(16, 0), 35), (time(16, 0), time(17, 30), 25))
_AIRBORNE_ORGANISMS = ("MTB", "MEASLES", "VARICELLA")
_SINGLE_ROOM_ORGANISMS = ("MRSA", "VRE", "CRE", "CDIFF", "INFLUENZA", "PERTUSSIS")
_MDRO = {"MRSA", "VRE", "CRE"}  # 다제내성균: 격리·해제는 감염관리실 (LP-19)
# 담당 검토로 가야 할 입원 대기 사례 종류. 서로 다른 대기 건에 하나씩 둔다.
SPECIAL_KINDS = ("airborne", "contact", "droplet", "preemptive", "pediatric", "icu", "single_room",
                 "same_name", "missing_gender", "integrated_care")
assert len(SPECIAL_KINDS) == N_SPECIAL_ADMISSIONS
_ISOLATION_ROOMS = {RoomType.NEGATIVE_PRESSURE, RoomType.SINGLE}


def _layout(cfg: GeneratorConfig | None = None) -> tuple[tuple[Room, ...], tuple[Bed, ...]]:
    rooms, beds = [], []
    demo = cfg is not None and cfg.hospital == "demo"
    for ward in (_DEMO_WARDS if demo else _WARDS):
        for no, rtype, cap, gender in (_DEMO_LAYOUT if demo else _ROOM_LAYOUT)[ward.id]:
            room = Room(id=f"{ward.id}-R{no}", ward_id=ward.id, room_type=rtype, capacity=cap, gender=gender)
            rooms.append(room)
            beds += [Bed(id=f"{room.id}-B{i}", room_id=room.id, ward_id=ward.id) for i in range(1, cap + 1)]
    return tuple(rooms), tuple(beds)


class _Gen:
    def __init__(self, seed: int, config: GeneratorConfig, dists: dict[str, StageDistribution]):
        self.rng = random.Random(seed)
        self.seed = seed
        self.rx_rng = random.Random(f"rx-{seed}")  # 퇴원약 단계 전용: 기존 난수열을 바꾸지 않는다
        self.cfg = config
        self.dists = dists
        self.wards = _wards(config)
        self.rooms, self.beds = _layout(config)
        self.room_by_id = {r.id: r for r in self.rooms}
        self.ward_by_id = {w.id: w for w in self.wards}
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

    def new_name(self, gender: Gender | None = None) -> str:
        while True:
            sur, a, b = self.rng.choice(_SURNAMES), self.rng.choice(_SYLLABLES), self.rng.choice(_SYLLABLES)
            if sur + a + b not in self.used_names:
                self.used_names.add(sur + a + b)
                return sur + _given(a, b, gender.value if gender is not None else None)

    def new_patient(self, *, gender: Gender | None, department: str, age: int | None = None,
                    organism: str | None = None, status: PrecautionStatus | None = None,
                    ordered_by: str | None = None, name: str | None = None) -> Patient:
        """organism을 주면 지침 부록1 대응표(ORGANISM_PRECAUTIONS)대로 주의 집합을 붙인다."""
        self._pid += 1
        age = self.rng.randint(25, 90) if age is None else age
        prec = ORGANISM_PRECAUTIONS[organism] if organism else ()
        return Patient(
            id=f"SYN-P{self._pid:04d}", name=name or self.new_name(gender), gender=gender, age=age,
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
        # 사망·자의 퇴원 (DAT-10): 격리·미등록·보호자 지연이 아닌 건에서 고른다. 별도 난수열
        iso_idx = {i for i, (_, _, iso) in enumerate(plan) if iso}
        pool = sorted(set(range(n)) - iso_idx - unregistered - guardian_delayed)
        special = self.rx_rng.sample(pool, cfg.death_count + cfg.ama_count)
        dtype = {i: "death" for i in special[:cfg.death_count]} | {i: "ama" for i in special[cfg.death_count:]}
        # 급변 전동과 사망 장소는 또 다른 난수열: 기존 합성 데이터의 다른 값은 바뀌지 않는다 (SAG-10)
        ex_rng = random.Random(f"exit-{self.cfg.days}-{n}-{sorted(special)}")
        rest = [i for i in pool if i not in dtype]
        dtype |= {i: "transfer_out" for i in ex_rng.sample(rest, min(cfg.transfer_out_count, len(rest)))}
        place = {i: ex_rng.choice(["eol_room", "ward"]) for i, k in dtype.items() if k == "death"}

        real = cfg.discharge_mix == "realistic"
        mix_rng = random.Random(f"mix-{self.seed}-{self.cfg.days}-{n}")  # 현실 구성 전용 난수열 (시험 구성 값은 그대로)
        regular = ({i for i in range(n) if i not in unregistered and dtype.get(i) is None
                    and mix_rng.random() < cfg.regular_discharge_rate} if real else set())
        slots = _GUARDIAN_SLOTS_REALISTIC if real else _GUARDIAN_SLOTS
        seq_rng = random.Random(f"seq-{self.seed}-{self.cfg.days}-{n}")  # 현장형 순서·청소 두 갈래 전용 (기존 값 불변)
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
            guardian_expected = self.day_at(day, self.rng.choice(slots))
            if i in guardian_delayed:
                guardian_arrived = guardian_expected + timedelta(minutes=d["guardian_delay"])
            else:
                guardian_arrived = guardian_expected - timedelta(minutes=self.rng.randint(0, 30))

            m = lambda k: timedelta(minutes=k)  # noqa: E731
            order = self.day_at(day, time(8, 30)) + m(d["order_after_round"])
            # 퇴원약 (DAT-7): 처방 발행 → (수정) → 조제 → 병동 도착. 단계 시각은 설계 가정
            rx_issued = order + m(self.rx_rng.randint(0, 30))
            med = max(order + m(d["medication"]), rx_issued + m(20))
            if i in regular:  # 정규 퇴원: 08시 전에 처방 완료, 퇴원약 09:30 일괄 불출 (인천성모·경북대·보라매, 등급 B)
                order = self.day_at(day, time(7, 0)) + m(mix_rng.randint(0, 60))
                rx_issued = order
                med = self.day_at(day, time(9, 30)) + m(mix_rng.randint(0, 60))
            rx_revised_at = rx_reason = None
            if i in rx_revised:
                rx_revised_at = rx_issued + m(self.rx_rng.randint(15, 60))
                rx_reason = self.rx_rng.choice(RX_REVISION_REASONS)
                med = max(med, rx_revised_at + m(self.rx_rng.randint(30, 60)))  # 재조제
            rx_dispensed = max(med - m(self.rx_rng.randint(10, 25)), rx_revised_at or rx_issued)
            edu = order + m(d["education"])
            billing = med + m(d["billing"])
            departed = max(billing, edu, guardian_arrived) + m(d["exit_prep"])
            seq = {}
            if real:
                # 현장형 퇴원 순서 (병동·원무 검수 반영, 2026-11-10): 심사는 조제와 나란히 진행되고 수납을 연다.
                # 퇴원약 병동 도착은 수납이 아니라 퇴원약 전달의 조건이다. 처방이 심사 뒤 바뀌면 다시 심사한다.
                # 교육은 퇴원약 전달 때 함께 한다(교육 사건 하나). 간격은 설계 가정(인천성모 연락 6 + 수납 9분 참고)
                review = order + m(seq_rng.randint(20, 60))
                reopened = None
                if rx_revised_at is not None and rx_revised_at > review:
                    reopened = rx_revised_at
                    review = rx_revised_at + m(seq_rng.randint(15, 30))
                notice = review + m(seq_rng.randint(3, 8))
                billing = notice + m(seq_rng.randint(6, 20))
                receipt = billing + m(seq_rng.randint(2, 5))
                handed = max(med, receipt) + m(seq_rng.randint(10, 30))  # 퇴원약 전달·복약 교육 (설계 가정)
                edu = handed
                departed = max(handed, guardian_arrived) + m(d["exit_prep"])
                seq = dict(billing_review_done_at=review, billing_review_reopened_at=reopened, notice_given_at=notice,
                           receipt_checked_at=receipt, medication_handed_at=handed)
            requested = departed + m(d["baseline_cleaning_request_delay"])
            aerosol = bool({IsolationType.AIRBORNE, IsolationType.DROPLET} & set(patient.precautions))
            room = self.room_by_id[bed.room_id]
            wait = (cfg.aerosol_wait_override_min if cfg.aerosol_wait_override_min is not None
                    else aerosol_wait_min(room.air_changes_per_hour)) if aerosol else 0
            clean_start = max(requested + m(d["cleaning_response"] + extra), departed + m(wait))  # 환기 대기 (LP-20)
            clean_done = clean_start + m(d["cleaning"])
            ready = clean_done + m(d["bed_make"])
            if real:  # 청소 두 갈래. 별도 난수열(seq_rng)
                mm = lambda k: max(1, round(SPLIT_CLEANING[k].sample(seq_rng)))  # noqa: E731
                if cleaning_type is CleaningType.GENERAL and room.room_type is RoomType.MULTI:
                    clean_done = clean_start + m(mm("cleaning_bed_unit"))  # 다인실은 침상 단위
                # 침상 재설치(새 리넨)는 표면·침상 청소 뒤 (권고안 부록 4-3-2, 격리는 종말소독 뒤 H34). 감염관리 검수 P1
                prep_start = clean_done + m(mm("bed_prep_response"))
                prep_done = prep_start + m(mm("bed_prep"))
                ready = max(clean_done, prep_done)
                seq.update(bed_prep_started_at=prep_start, bed_prep_done_at=prep_done)
                if cfg.ops_realism:  # 이미 뽑은 정리 응답 시간을 "회차 없이 바로 올 때"의 값으로 남긴다 (새 난수 없음)
                    d["bed_prep_direct_response"] = round((prep_start - clean_done).total_seconds() / 60)

            result.append(Discharge(
                id=f"SYN-D{i + 1:03d}", day=day, bed_id=bed.id, patient=patient,
                decided_at=decided_at, registered_at=registered_at,
                guardian_expected_at=guardian_expected, guardian_delayed=i in guardian_delayed,
                cleaning_type=cleaning_type, cleaning_delayed=i in cleaning_delayed,
                cleaning_extra_delay_min=extra, durations_min=d,
                aerosol_wait_min=wait, sporicidal=patient.organism == "CDIFF", rx_revision_reason=rx_reason,
                discharge_type=dtype.get(i, "normal"), death_place=place.get(i),
                actual=DischargeTimeline(
                    order_confirmed_at=order, medication_ready_at=med, education_done_at=edu,
                    billing_done_at=billing, guardian_arrived_at=guardian_arrived, departed_at=departed,
                    baseline_cleaning_requested_at=requested, cleaning_started_at=clean_start,
                    cleaning_done_at=clean_done, bed_ready_at=ready,
                    rx_issued_at=rx_issued, rx_revised_at=rx_revised_at, rx_dispensed_at=rx_dispensed, **seq,
                ),
            ))
        return _apply_ops(result, self.beds) if cfg.ops_realism else tuple(result)

    # ---------- 입원 대기 ----------

    def admissions(self, discharges: tuple[Discharge, ...]) -> tuple[AdmissionRequest, ...]:
        cfg = self.cfg
        n = cfg.admission_count
        days = list(range(cfg.days)) + [self.rng.randrange(cfg.days) for _ in range(n - cfg.days)]
        self.rng.shuffle(days)
        # 담당 검토로 가야 할 사례를 서로 다른 대기 건에 하나씩 둔다.
        if cfg.admission_mix == "realistic":
            kind = {}
            for idx in range(n):
                r, acc = self.rng.random(), 0.0
                for k in SPECIAL_KINDS:
                    acc += cfg.realistic_rates.get(k, 0.0)
                    if r < acc:
                        kind[idx] = k
                        break
            normal = [i for i in range(n) if i not in kind]
        else:
            special = list(SPECIAL_KINDS)
            order = self.rng.sample(range(n), n)
            kind = {idx: special[j] for j, idx in enumerate(order[: len(special)])}
            normal = order[len(special):]
        cancelled = set(self.rng.sample(normal, min(cfg.admission_cancel_count, len(normal))))
        same_name = self.rng.choice(discharges).patient.name

        result = []
        for i in range(n):
            k = kind.get(i)
            ward = self.rng.choice(self.wards)
            dept = self.rng.choice(ward.departments)
            organism = {"airborne": "MTB", "contact": self.rng.choice(["MRSA", "VRE", "CDIFF"]),
                        "droplet": "INFLUENZA", "preemptive": "CRE"}.get(k)
            if organism:
                ward, dept = next(w for w in self.wards if "감염내과" in w.departments), "감염내과"
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
            if cfg.discharge_mix == "realistic":
                a_rng = random.Random(f"adm-{self.seed}-{i}-{day}-{cfg.days}")
                lo, hi, _ = a_rng.choices(_ADMISSION_WINDOWS_REALISTIC, weights=[w for *_, w in _ADMISSION_WINDOWS_REALISTIC])[0]
                span = (hi.hour * 60 + hi.minute) - (lo.hour * 60 + lo.minute)
                requested = self.day_at(day, lo) + timedelta(minutes=30 * a_rng.randint(0, span // 30 - 1))
            cancelled_at = None
            if i in cancelled:
                cancelled_at = self.day_at(day, time(7, 30)) + timedelta(minutes=self.rng.randint(0, 180))
            result.append(AdmissionRequest(
                id=f"SYN-A{i + 1:03d}", day=day, patient=patient, requested_at=requested,
                preferred_ward_id=ward.id, from_icu=k == "icu", wants_single_room=k == "single_room",
                single_room_reason="isolation" if organism else ("preference" if k == "single_room" else None),
                integrated_care_requested=k == "integrated_care",
                # 동의 미확인 (LP-21 검토 사유). 현실 구성에서는 일부만 미확인
                integrated_care_consent=(None if cfg.admission_mix == "test"
                                         or self.rng.random() < cfg.integrated_care_consent_missing_rate else True)
                if k == "integrated_care" else None,
                cancelled_at=cancelled_at,
            ))
        return tuple(result)


def ops_cleaning_arrival(d: Discharge, requested: datetime, departed: datetime) -> datetime:
    """운영 현실 옵션: 청소 인력이 올 수 있는 가장 이른 시각(인력 시간대 반영, 환기 대기 뒤). sim/ops.py"""
    arrive = ops.cleaning_arrival(requested, d.durations_min["cleaning_response"], d.cleaning_extra_delay_min)
    return max(arrive, departed + timedelta(minutes=d.aerosol_wait_min))


def ops_bed_side(d: Discharge, clean_start: datetime, prep_request_at: datetime | None = None,
                 min_response_min: int | None = 10) -> dict:
    """운영 현실 옵션: 청소 시작부터 입실 가능까지의 시각. 청소·정리 길이는 생성기가 뽑은 값 그대로."""
    t = d.actual
    clean_len = round((t.cleaning_done_at - t.cleaning_started_at).total_seconds() / 60)
    prep_len = round((t.bed_prep_done_at - t.bed_prep_started_at).total_seconds() / 60)
    cs, cd, ps, pd, rd = ops.bed_timeline(clean_start, clean_len, prep_len, d.durations_min["bed_prep_direct_response"],
                                          prep_request_at, min_response_min)
    return dict(cleaning_started_at=cs, cleaning_done_at=cd, bed_prep_started_at=ps, bed_prep_done_at=pd, bed_ready_at=rd)


def _apply_ops(result: list[Discharge], beds) -> tuple[Discharge, ...]:
    """운영 현실 옵션 (DECISIONS 2026-11-10): 병동별 청소 대기열(들어온 순서)과 침상 정리 회차로 병상 쪽 시각을 다시 잡는다."""
    ward = {b.id: b.ward_id for b in beds}
    jobs = []
    for x in result:
        t = x.actual
        jobs.append(ops.CleanJob(x.id, ward[x.bed_id], ops_cleaning_arrival(x, t.baseline_cleaning_requested_at, t.departed_at),
                                 round((t.cleaning_done_at - t.cleaning_started_at).total_seconds() / 60)))
    starts = ops.schedule_cleaning(jobs)
    return tuple(x.model_copy(update={"actual": x.actual.model_copy(update=ops_bed_side(x, starts[x.id]))}) for x in result)


def generate(seed: int = 42, config: GeneratorConfig | None = None,
             distributions: dict[str, StageDistribution] | None = None) -> SyntheticHospital:
    """합성 병원 하나를 만든다. 전역 random 상태는 건드리지 않는다."""
    cfg = config or GeneratorConfig()
    real = REALISTIC_STAGE_OVERRIDES if cfg.discharge_mix == "realistic" else {}
    dists = {**DEFAULT_STAGE_DISTRIBUTIONS, **real, **(distributions or {})}
    g = _Gen(seed, cfg, dists)
    discharges = g.discharges()
    admissions = g.admissions(discharges)
    return SyntheticHospital(
        seed=seed, config=cfg, distributions=dists, wards=g.wards, rooms=g.rooms, beds=g.beds,
        discharges=discharges, admissions=admissions,
    )
