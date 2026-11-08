"""이벤트 모델. 발생 시각과 인지 시각을 따로 둔다(미래 정보 누수 검사용)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventType(str, Enum):
    # 퇴실 전
    DISCHARGE_DECIDED = "discharge_decided"
    DISCHARGE_REGISTERED = "discharge_registered"
    DISCHARGE_CANCELLED = "discharge_cancelled"
    GUARDIAN_ETA_SET = "guardian_eta_set"
    GUARDIAN_ETA_UPDATED = "guardian_eta_updated"
    ORDER_CONFIRMED = "order_confirmed"
    MEDICATION_READY = "medication_ready"
    EDUCATION_DONE = "education_done"
    BILLING_DONE = "billing_done"
    GUARDIAN_ARRIVED = "guardian_arrived"
    PATIENT_DEPARTED = "patient_departed"
    # 병상 준비
    CLEANING_REQUESTED = "cleaning_requested"
    CLEANING_STARTED = "cleaning_started"
    CLEANING_DONE = "cleaning_done"
    BED_READY = "bed_ready"
    # 입실
    ADMISSION_REQUESTED = "admission_requested"
    ADMISSION_CANCELLED = "admission_cancelled"
    ISOLATION_ORDER_ADDED = "isolation_order_added"
    # 다른 구성요소가 남기는 매칭 이벤트 (계획 에이전트, 게이트웨이, 원무)
    PREMATCH_CREATED = "prematch_created"
    PATIENT_CONTACTED = "patient_contacted"  # 입실 안내 연락. 되돌릴 수 없는 분기점
    MATCH_COMMITTED = "match_committed"
    # 사가 명령
    SCHEDULE_ITEM_CREATED = "schedule_item_created"
    REGISTRATION_CHECK_REQUESTED = "registration_check_requested"
    CLEANING_REQUEST_ISSUED = "cleaning_request_issued"
    READY_TIME_RECOMPUTED = "ready_time_recomputed"
    BED_MARKED_VACANT = "bed_marked_vacant"
    SCHEDULE_ITEM_RELEASED = "schedule_item_released"
    PREMATCH_RELEASED = "prematch_released"
    CLEANING_REQUEST_CANCELLED = "cleaning_request_cancelled"
    CASE_RECLASSIFIED_REVIEW = "case_reclassified_review"
    HUMAN_TASK_CREATED = "human_task_created"
    REMATCH_REQUESTED = "rematch_requested"
    CASE_RESUMED = "case_resumed"


Source = Literal["his", "timer", "human", "agent", "gateway", "equipment", "saga"]


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int | None = None  # 저장소가 붙이는 순번
    event_id: str  # 멱등 키. 같은 ID는 한 번만 기록
    case_id: str
    type: EventType
    occurred_at: datetime  # 현실에서 일어난 시각
    observed_at: datetime  # 시스템이 알게 된 시각
    source: Source
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_times(self) -> "Event":
        if self.observed_at < self.occurred_at:
            raise ValueError("observed_at은 occurred_at보다 앞설 수 없다")
        return self
