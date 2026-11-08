"""회귀 시나리오 정답 라벨 스키마.

라벨은 생성기와 독립적으로 설계 문서 규칙에서 도출한다. 지금은 결과 수준만 고정하고
이벤트 순서와 시각(occurred_at, observed_at)은 2단계 이벤트 형식이 정해진 뒤 채운다.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Category(str, Enum):
    SAFETY = "safety"  # A 안전·배정
    GATEWAY = "gateway"  # B 게이트웨이·동시성
    SAGA = "saga"  # C 사가·보상
    TIER = "tier"  # D 확인 등급
    DELAY = "delay"  # E 지연 추적
    SECURITY = "security"  # F 보안


class Tier(str, Enum):
    AUTO = "auto"  # 자동
    WARD_CONFIRM = "ward_confirm"  # 병동 확인
    REVIEW = "review"  # 담당 검토


# 행동 어휘. 계획 에이전트, 게이트웨이, 사가가 내는 행동을 같은 이름으로 채점한다.
ACTION_TYPES = frozenset({
    "propose_match",  # 사전 매칭 또는 재매칭 제안
    "ask_ward_acceptance",  # 병동에 수용 가능 여부 문의
    "commit_match",  # 원무 HIS 확정
    "reject_at_proposal",  # 게이트웨이 제안 시점 재확인에서 거부
    "reject_at_commit",  # 게이트웨이 확정 직전 재확인에서 거부
    "reclassify_review",  # 담당 검토로 재분류
    "route_review",  # 처음부터 담당 검토 큐로
    "send_cleaning_request",  # 청소 요청 (params.cleaning_type)
    "mark_bed_vacant",  # 빈 병상 전환
    "share_waiting_info",  # 부서 화면에 대기 정보 공유
    "request_registration_check",  # 퇴원 예정 미등록 확인 요청
    "recompute_ready_time",  # 병상 준비 시각 재추정
    "release_schedule_item",  # 보상: 청소 예정표 항목 해제
    "release_prematch",  # 보상: 사전 매칭 해제
    "cancel_cleaning_request",  # 보상: 확정된 청소 요청 되돌림
    "create_human_task",  # 사람 작업 생성 (재연락 등)
    "resume_case",  # 재시작 후 사례 재개
    "ignore_duplicate",  # 중복 이벤트·요청 무시
    "answer_with_evidence",  # 근거 레코드와 함께 답변
    "refuse_query",  # 권한 없음, 근거 없음으로 답변 거부
    "report_no_bed",  # 조건에 맞는 병상 없음 보고
    "execute_side_effect",  # 외부 부수효과 실행 (중복 검사용)
    "answer_no_record",  # 근거 레코드가 없음을 조회 근거와 함께 보고 (단정 금지)
    "record_delay_reason",  # 퇴원 지연 사유 코드 기록 (params.reason)
    "notify_ward",  # 병동에 수용 회신 철회 통지 (사용자 결정으로 채택)
})


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Action(_M):
    type: str
    case: str | None = None  # 시나리오 안의 기호 사례 ID (예: P1, D1)
    bed: str | None = None
    params: dict[str, str | int | bool] = Field(default_factory=dict)
    note: str | None = None
    stage: float | None = None  # 이 행동을 채점할 수 있는 구현 단계. None이면 시나리오 단계
    level: Literal["proposal", "executed"] | None = None  # 제안 수준(에이전트)인지 실행 수준인지
    after: str | None = None  # 같은 사례에서 이 행동보다 먼저 있어야 할 행동

    @field_validator("type")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in ACTION_TYPES:
            raise ValueError(f"알 수 없는 행동: {v}")
        return v


class Gold(_M):
    tier_by_case: dict[str, Tier] = Field(default_factory=dict)
    must_actions: list[Action] = Field(default_factory=list)
    must_not: list[Action] = Field(default_factory=list)
    acceptable_beds: dict[str, list[str]] = Field(default_factory=dict)  # 허용 병상 집합
    final_state: dict[str, str] = Field(default_factory=dict)  # 사례별 사가 최종 상태
    human_touches: int | None = None


class Provenance(_M):
    author: Literal["claude-draft", "human"]
    rule_source: str  # 설계 문서의 어느 규칙에서 도출했는지
    reviewed_by: str | None = None


class Scenario(_M):
    id: str
    category: Category
    title: str
    tags: list[str] = Field(default_factory=list)
    design_ref: str
    situation: str  # 사람이 읽는 상황 설명
    fixture: dict = Field(default_factory=dict)  # 2단계 이후 이벤트 세부를 채움
    gold: Gold
    metrics: list[str] = Field(default_factory=list)  # 이 시나리오가 기여하는 평가표 지표 ID
    measured_from_stage: float
    autonomy_stage: int = Field(default=1, ge=1, le=3)  # 자율화 단계 (1 HITL, 2 예외 관리, 3 HOTL)
    status: Literal["draft", "approved"] = "draft"
    provenance: Provenance


def load_scenario(path: Path) -> Scenario:
    with open(path, encoding="utf-8") as f:
        return Scenario.model_validate(yaml.safe_load(f))


def load_scenarios(directory: Path) -> list[Scenario]:
    return [load_scenario(p) for p in sorted(Path(directory).glob("*.yaml"))]
