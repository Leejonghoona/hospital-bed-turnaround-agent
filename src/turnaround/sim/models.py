"""합성 병원 데이터 모델. 실제 환자, 실제 병원 데이터는 쓰지 않는다."""

from __future__ import annotations

import math
import random
from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RoomType(str, Enum):
    NEGATIVE_PRESSURE = "negative_pressure"  # 음압격리실
    SINGLE = "single"  # 1인실
    MULTI = "multi"  # 다인실 (최대 4병상)


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"


class CleaningType(str, Enum):
    GENERAL = "general"  # 일반 청소
    TERMINAL = "terminal"  # 종말소독


class IsolationType(str, Enum):
    """전파경로별 주의 (질병관리청 의료관련감염 표준예방지침). 엄격한 순서: 공기 > 비말 > 접촉."""

    AIRBORNE = "airborne"  # 공기주의: 음압격리실
    DROPLET = "droplet"  # 비말주의: 1인실 또는 같은 병원체 코호트
    CONTACT = "contact"  # 접촉주의: 1인실 또는 같은 병원체 코호트


_STRICTNESS = {IsolationType.AIRBORNE: 3, IsolationType.DROPLET: 2, IsolationType.CONTACT: 1}


class PrecautionStatus(str, Enum):
    CONFIRMED = "confirmed"  # 확진
    SUSPECTED = "suspected"  # 의심
    PREEMPTIVE = "preemptive"  # 선제 격리 (선별검사 결과 대기)


# 병원체(코호트 키) → 필요한 주의. 지침 부록1 기준. 합성 데이터용 코드다.
ORGANISM_PRECAUTIONS: dict[str, tuple[IsolationType, ...]] = {
    "MTB": (IsolationType.AIRBORNE,),  # 활동성 폐결핵: 방 공유 금지
    "MEASLES": (IsolationType.AIRBORNE,),
    "VARICELLA": (IsolationType.AIRBORNE, IsolationType.CONTACT),
    "INFLUENZA": (IsolationType.DROPLET,),
    "PERTUSSIS": (IsolationType.DROPLET,),
    "ADENOVIRUS": (IsolationType.CONTACT, IsolationType.DROPLET),
    "MRSA": (IsolationType.CONTACT,),
    "VRE": (IsolationType.CONTACT,),
    "CRE": (IsolationType.CONTACT,),
    "CDIFF": (IsolationType.CONTACT,),  # 퇴실 시 아포 대응 소독
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StageDistribution(_Frozen):
    """단계 소요시간 분포(분). 로그정규분포를 중앙값과 P90으로 정의한다."""

    median_min: float = Field(gt=0)
    p90_min: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "StageDistribution":
        if self.p90_min <= self.median_min:
            raise ValueError("p90_min은 median_min보다 커야 한다")
        return self

    @property
    def mu(self) -> float:
        return math.log(self.median_min)

    @property
    def sigma(self) -> float:
        # P90 = exp(mu + 1.2816 * sigma)
        return math.log(self.p90_min / self.median_min) / 1.2815515655446004

    def sample(self, rng: random.Random) -> float:
        return rng.lognormvariate(self.mu, self.sigma)


class Ward(_Frozen):
    id: str
    name: str
    specialty: str
    departments: tuple[str, ...]


class Room(_Frozen):
    id: str
    ward_id: str
    room_type: RoomType
    capacity: int = Field(ge=1, le=4)  # 의료법 시행규칙: 병실 최대 4병상
    gender: Gender | None = None  # 다인실만 성별 지정
    cohort_key: str | None = None  # 같은 병원체 환자 코호트실로 지정된 다인실
    near_nurse_station: bool = False  # 낙상·섬망 고위험 환자 배치 점수용
    air_changes_per_hour: int | None = Field(default=None, ge=1)  # 시간당 공기순환 횟수 (DAT-9). 비우면 유형별 기본값

    @model_validator(mode="after")
    def _default_ach(self):
        if self.air_changes_per_hour is None:
            from turnaround.rules import DEFAULT_ACH
            object.__setattr__(self, "air_changes_per_hour", DEFAULT_ACH[self.room_type.value])
        return self


class Bed(_Frozen):
    id: str
    room_id: str
    ward_id: str


class Patient(_Frozen):
    id: str
    name: str  # 가명
    gender: Gender | None  # None은 입력 누락
    age: int = Field(ge=0, le=110)
    department: str
    isolation_order: bool = False  # 격리 오더 값 (주치의 또는 감염관리실)
    isolation_type: IsolationType | None = None  # 가장 엄격한 주의 하나 (호환 필드)
    precautions: tuple[IsolationType, ...] = ()  # 전파경로 주의 집합 (LP-16)
    organism: str | None = None  # 병원체 = 코호트 키
    precaution_status: PrecautionStatus | None = None
    isolation_ordered_by: Literal["physician", "ipc"] | None = None  # ipc = 감염관리실
    immunocompromised: bool = False  # 코호트실 배치 금지
    open_wound: bool = False  # 코호트실 배치 금지
    high_fall_risk: bool = False
    delirium_positive: bool = False
    synthetic: Literal[True] = True

    @model_validator(mode="before")
    @classmethod
    def _normalize_precautions(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        prec = tuple(data.get("precautions") or ())
        if not prec and data.get("isolation_type") is not None:
            prec = (IsolationType(data["isolation_type"]),)
        prec = tuple(sorted({IsolationType(p) for p in prec}, key=lambda p: -_STRICTNESS[p]))
        data["precautions"] = prec
        if prec:
            data["isolation_order"] = True
            data["isolation_type"] = prec[0]
        return data


RX_REVISION_REASONS = ("discharge_hold", "dose_change", "add_medication")  # 수정 사유 1위는 퇴원 보류 (LP-27)


class DischargeTimeline(_Frozen):
    """기존 방식(에이전트 없음) 기준선의 실제 시각."""

    order_confirmed_at: datetime  # 퇴원 오더 확정 (회진 후)
    medication_ready_at: datetime  # 퇴원약 준비
    education_done_at: datetime  # 퇴원 교육 완료
    billing_done_at: datetime  # 진료비 심사와 수납 완료
    guardian_arrived_at: datetime  # 보호자 도착
    departed_at: datetime  # 퇴실
    baseline_cleaning_requested_at: datetime  # 병동이 청소를 요청한 시각
    cleaning_started_at: datetime
    cleaning_done_at: datetime  # 청소 완료 신호
    bed_ready_at: datetime  # 침상 정리 후 준비 완료
    rx_issued_at: datetime | None = None  # 퇴원처방 발행 (DAT-7)
    rx_revised_at: datetime | None = None  # 퇴원처방 수정 (수정 사례만)
    rx_dispensed_at: datetime | None = None  # 약제부 조제 완료. 병동 도착은 medication_ready_at


class Discharge(_Frozen):
    id: str
    day: int = Field(ge=0)
    bed_id: str
    patient: Patient
    decided_at: datetime  # 전날 주치의 결정
    registered_at: datetime | None  # 퇴원 예정 등록. None이면 미등록
    guardian_expected_at: datetime  # 병동이 입력한 보호자 도착 예정
    guardian_delayed: bool
    cleaning_type: CleaningType
    cleaning_delayed: bool
    cleaning_extra_delay_min: int = Field(ge=0)
    aerosol_wait_min: int = Field(default=0, ge=0)  # 비말·공기주의 퇴실 병실 환기 대기 (LP-20)
    sporicidal: bool = False  # C. difficile 퇴실: 아포 대응 소독
    rx_revision_reason: str | None = None  # 퇴원처방 수정 사유 (수정 사례만)
    discharge_type: Literal["normal", "death", "ama"] = "normal"  # 사망(임종실 전실), 자의 퇴원 (DAT-10)
    durations_min: dict[str, int]  # 단계별로 뽑은 소요시간
    actual: DischargeTimeline


class AdmissionRequest(_Frozen):
    id: str
    day: int = Field(ge=0)
    patient: Patient
    requested_at: datetime  # 입원 예정 시각
    preferred_ward_id: str
    from_icu: bool = False  # 중환자실 하향 전동
    wants_single_room: bool = False  # 1인실 희망 (병실료 차액 동의 필요)
    single_room_reason: Literal["isolation", "preference"] | None = None  # 차액 동의는 preference만 (LP-22)
    integrated_care_requested: bool = False  # 간호·간병통합서비스 병동 희망
    integrated_care_consent: bool | None = None  # 환자(또는 보호자) 동의서. None은 미확인
    cancelled_at: datetime | None = None


class GeneratorConfig(_Frozen):
    base_date: date = date(2026, 3, 2)  # 월요일
    days: int = Field(default=5, ge=1)
    discharges_per_day: int = Field(default=6, ge=1)
    admission_count: int = Field(default=15, ge=8)
    guardian_delay_count: int = Field(default=6, ge=0)
    cleaning_delay_count: int = Field(default=4, ge=0)
    unregistered_count: int = Field(default=2, ge=0)
    admission_cancel_count: int = Field(default=2, ge=0)
    isolation_discharge_count: int = Field(default=3, ge=0)
    # 환기 대기: 병실 공기순환 횟수로 지침 부록 3 표에서 찾는다 (LP-23). 병원 규칙으로 덮어쓰려면 값을 넣는다
    aerosol_wait_override_min: int | None = Field(default=None, ge=0)
    rx_revision_rate: float = Field(default=0.15, ge=0, le=1)  # 퇴원처방 수정 비율 (LP-27, 합성 가정)
    death_count: int = Field(default=1, ge=0)  # 임종실 전실 사례 (DAT-10, 합성 가정)
    ama_count: int = Field(default=1, ge=0)  # 자의 퇴원 사례

    @property
    def discharge_count(self) -> int:
        return self.days * self.discharges_per_day

    @model_validator(mode="after")
    def _check_counts(self) -> "GeneratorConfig":
        total = self.discharge_count
        for name in ("guardian_delay_count", "cleaning_delay_count", "unregistered_count"):
            if getattr(self, name) > total:
                raise ValueError(f"{name}가 퇴원 건수({total})보다 많다")
        if self.isolation_discharge_count > self.days:
            raise ValueError("격리 퇴실은 하루 1건까지만 둔다")
        if self.admission_cancel_count > self.admission_count - N_SPECIAL_ADMISSIONS:
            raise ValueError("입원 취소는 검토 사례가 아닌 일반 입원 대기에서만 고른다")
        return self


N_SPECIAL_ADMISSIONS = 10  # 담당 검토 사례로 쓰는 입원 대기 건수 (generator.SPECIAL_KINDS)


class SyntheticHospital(_Frozen):
    synthetic: Literal[True] = True
    note: str = "합성(가상) 데이터. 실제 환자나 병원 정보가 아니다."
    seed: int
    config: GeneratorConfig
    distributions: dict[str, StageDistribution]
    wards: tuple[Ward, ...]
    rooms: tuple[Room, ...]
    beds: tuple[Bed, ...]
    discharges: tuple[Discharge, ...]
    admissions: tuple[AdmissionRequest, ...]
