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
    AIRBORNE = "airborne"  # 공기감염 격리
    CONTACT = "contact"  # 접촉 격리


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
    isolation_order: bool = False  # 주치의 오더 값
    isolation_type: IsolationType | None = None
    high_fall_risk: bool = False
    synthetic: Literal[True] = True


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
        if self.admission_cancel_count > self.admission_count - 7:
            raise ValueError("입원 취소는 검토 사례가 아닌 일반 입원 대기에서만 고른다")
        return self


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
