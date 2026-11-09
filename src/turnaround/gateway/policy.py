"""정책 파일(YAML). 설계의 OPA 정책 엔진 대신 쓴다."""

from __future__ import annotations

from pathlib import Path

import yaml
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "policy.yaml"


class ManualOverridePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    roles: list[str]
    reason_codes: list[str]
    safety_exception_roles: list[str]
    # GW-10 안전 예외 3단계 (LP-24). 위반 코드 -> forbidden | preapproval | reason_entry
    safety_tiers: dict[str, str] = Field(default_factory=dict)
    preapproval_organisms: dict[str, list[str]] = Field(default_factory=dict)  # 병원체에 따라 단계를 올리는 코드


class Ventilation(BaseModel):
    model_config = ConfigDict(frozen=True)
    removal_pct: float = 99.9
    hospital_wait_min: int | None = Field(default=None, ge=0)  # 병원 규칙 덮어쓰기 (POL-2)


class Scoring(BaseModel):
    """목표 함수 4~5항 점수 가중치 (SPEC 3절). 값은 설계 가정이며 재생 평가로 조정한다."""
    model_config = ConfigDict(frozen=True)
    department_ward_match: float = 30.0
    negative_pressure_preserve: float = -40.0
    single_room_requested: float = 20.0
    single_room_unrequested: float = -15.0
    near_nurse_station_high_risk: float = 10.0
    ward_load_per_pending: float = -3.0
    ready_delay_per_10min: float = -1.0
    rematch_gain_min: float = 3.0  # 6항 변동 최소: 재매칭은 이득이 이 값을 넘을 때만 (준비 30분 단축)


class ExtractionSchema(BaseModel):
    """추출 필드와 지연 사유 코드 (설정으로 둔다: 병원마다 다르고 2단계 온보딩이 제안한다).

    U3(상대 날짜)·U5(메모 주체)는 결정됨. 지연 사유 코드(U2)와 필드 집합(U1)은 현직자 설문 뒤 확정한다.
    """
    model_config = ConfigDict(frozen=True)
    delay_codes: list[str] = Field(default_factory=lambda: ["guardian", "billing", "medication", "transport"])
    fields_by_case: dict[str, list[str]] = Field(default_factory=lambda: {
        "discharge": ["guardian_eta", "guardian_eta_day", "delay_reason", "mobility_aid"],
        "admission": ["single_room_wish"],
    })
    max_day_offset: int = 7


class Provenance(BaseModel):
    model_config = ConfigDict(frozen=True)
    tier: Literal["law", "guideline", "hospital_rule", "field_practice", "design_assumption"]
    cite: str


# 지침의 금지 조항: 병원 규칙으로 바꿀 수 없다 (POL-2). 위반 코드 -> 고정 단계
LOCKED_SAFETY_TIERS = {"tb_shared_room": "forbidden"}  # 표준예방지침 5.5.2.4


class Policy(BaseModel):
    model_config = ConfigDict(frozen=True)
    version: str
    autonomy_stage: int = Field(ge=1, le=3)
    promoted_departments: list[str] = Field(default_factory=list)
    pediatric_age: int
    score_gap_min: float
    review_reasons: list[str]
    auto_actions: list[str]
    query_fields: dict[str, list[str]]
    manual_override: ManualOverridePolicy
    ventilation: Ventilation = Field(default_factory=Ventilation)
    scoring: Scoring = Field(default_factory=Scoring)
    extraction: ExtractionSchema = Field(default_factory=ExtractionSchema)
    external_llm: str = "deny"
    provenance: dict[str, Provenance] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _locked(self):
        for code, tier in LOCKED_SAFETY_TIERS.items():
            got = self.manual_override.safety_tiers.get(code, tier)
            if got != tier:
                raise ValueError(f"{code}는 지침 금지 조항이라 병원 규칙으로 바꿀 수 없다 ({got} → {tier})")
        return self


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    return Policy.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
