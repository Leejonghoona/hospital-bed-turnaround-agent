"""정책 파일(YAML). 설계의 OPA 정책 엔진 대신 쓴다."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_POLICY = Path(__file__).resolve().parents[3] / "config" / "policy.yaml"


class ManualOverridePolicy(BaseModel):
    model_config = ConfigDict(frozen=True)
    roles: list[str]
    reason_codes: list[str]
    safety_exception_roles: list[str]
    # GW-10 안전 예외 3단계 (LP-24). 위반 코드 -> forbidden | preapproval | reason_entry
    safety_tiers: dict[str, str] = Field(default_factory=dict)
    preapproval_organisms: dict[str, list[str]] = Field(default_factory=dict)  # 병원체에 따라 단계를 올리는 코드


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
    aerosol_wait_min: int = 30
    external_llm: str = "deny"


def load_policy(path: Path = DEFAULT_POLICY) -> Policy:
    return Policy.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))
