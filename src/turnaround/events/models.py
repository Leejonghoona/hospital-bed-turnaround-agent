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
    DISCHARGE_RX_ISSUED = "discharge_rx_issued"  # 퇴원처방 발행 (DAT-7)
    DISCHARGE_RX_REVISED = "discharge_rx_revised"  # 퇴원처방 수정 (사유: discharge_hold 등)
    DISCHARGE_RX_DISPENSED = "discharge_rx_dispensed"  # 약제부 조제 완료
    MEDICATION_READY = "medication_ready"  # 퇴원약 병동 도착
    EDUCATION_DONE = "education_done"
    BILLING_DONE = "billing_done"
    GUARDIAN_ARRIVED = "guardian_arrived"
    PATIENT_DEPARTED = "patient_departed"
    END_OF_LIFE_ROOM_TRANSFER = "end_of_life_room_transfer"  # 임종실 전실: 원래 병상이 빈다 (SAG-9, LP-28)
    DEATH_CONFIRMED = "death_confirmed"  # 병동 내 사망 확인: 가족 면회·사후 처치 동안 병상은 아직 사용 중 (SAG-10)
    BODY_TRANSFERRED = "body_transferred"  # 안치실 이송: 이때 병상이 빈다 (SAG-10)
    UNPLANNED_TRANSFER_OUT = "unplanned_transfer_out"  # 중증 악화 등 급변 전동(중환자실 등): payload destination, hold
    BED_HOLD_RELEASED = "bed_hold_released"  # 급변 전동 때 잡아 둔 병상을 풀었다
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
    WARD_ACCEPTED = "ward_accepted"  # 책임간호사 수용 가능 회신
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
    HUMAN_TASK_DONE = "human_task_done"  # 사람이 작업을 마침 (payload task_id)
    TASK_HANDED_OVER = "task_handed_over"  # 교대 인계 (SAG-8)
    TASK_ESCALATED = "task_escalated"  # 마감 초과, 상위 담당에게 (SAG-8)
    REMATCH_REQUESTED = "rematch_requested"
    CASE_RESUMED = "case_resumed"
    WARD_NOTIFIED = "ward_notified"  # 수용 회신 철회 통지
    SHARE_WAITING_INFO = "share_waiting_info"  # 부서 화면에 다음 환자 대기 정보 공유
    DELAY_REASON_RECORDED = "delay_reason_recorded"  # 퇴원 지연 사유 코드
    FREE_TEXT_NOTE = "free_text_note"  # 병동 전달사항, 보호자 통화 메모 (자유 서술. 추출 필드만 쓴다, SEC-3)
    FIELDS_EXTRACTED = "fields_extracted"  # 추출 LLM 결과 (AGT-5)
    QUERY_NO_RECORD = "query_no_record"  # 근거 레코드 없음 (AGT-6)
    # 실행 게이트웨이
    PROPOSAL_RECEIVED = "proposal_received"
    GATEWAY_DECISION = "gateway_decision"  # 감사 로그
    REJECTED_AT_PROPOSAL = "rejected_at_proposal"
    REJECTED_AT_COMMIT = "rejected_at_commit"
    CASE_ROUTED_REVIEW = "case_routed_review"  # 담당 검토 큐로
    MANAGER_CONFIRM_REQUESTED = "manager_confirm_requested"  # 1단계 병동 확인 등급의 담당 확인
    MANAGER_APPROVED = "manager_approved"
    SAFETY_EXCEPTION_APPROVED = "safety_exception_approved"  # 감염관리실 등 권한자가 낸 안전 예외 사전 승인 (병상 지정)
    WARD_ACCEPTANCE_ASKED = "ward_acceptance_asked"
    WARD_REJECTED = "ward_rejected"  # 책임간호사 수용 불가 회신
    NO_BED_REPORTED = "no_bed_reported"
    QUERY_ANSWERED = "query_answered"
    QUERY_REFUSED = "query_refused"
    MANUAL_OVERRIDE_APPLIED = "manual_override_applied"  # 관리자 직접 수정, 사례 고정
    MANUAL_PIN_RELEASED = "manual_pin_released"  # 고정 해제, 에이전트 관리로 복귀 (GW-11)
    LOCATION_UPDATED = "location_updated"  # HIS 병상 정보 변경 (가상 FHIR 버전 증가)
    PATIENT_INFO_UPDATED = "patient_info_updated"
    PATIENT_INFO_CORRECTED = "patient_info_corrected"  # 입력 정정 (EVT-4)
    CORRECTION_CONFIRMED = "correction_confirmed"  # 제약을 푸는 정정의 사람 확인
    ISOLATION_RELEASED = "isolation_released"  # 격리 해제 오더 (주치의 또는 감염관리실)
    # 병실 사용 중지 (GW-12). case_id = 병실 ID. 사유: closed | equipment_failure | surveillance_hold
    ROOM_OUT_OF_SERVICE = "room_out_of_service"
    ROOM_RETURNED_TO_SERVICE = "room_returned_to_service"
    # 다인실 성별 전환 (2026-11-12, 현장 검수). case_id = 병실 ID, payload room_id·gender·by. 빈 방에서만, 사람이 승인
    ROOM_GENDER_CHANGED = "room_gender_changed"
    # ---- 설계 개정 (2026-11-09, docs/설계개정/아키텍처_개정안.md 2절). 추가만 하고 옛 타입·의미는 바꾸지 않는다 ----
    # 퇴원 줄
    BILLING_REVIEW_REQUESTED = "billing_review_requested"
    BILLING_REVIEW_DONE = "billing_review_done"  # 진료비 심사 완료: 연락·수납의 기준점
    BILLING_REVIEW_REOPENED = "billing_review_reopened"  # "심사 풀고 다시 심사"
    DISCHARGE_NOTICE_GIVEN = "discharge_notice_given"  # 간호사가 환자에게 수납 안내 (입원 측 PATIENT_CONTACTED와 다름)
    RECEIPT_CHECKED = "receipt_checked"
    MEDICATION_HANDED_OVER = "medication_handed_over"  # 퇴원약 전달 (MEDICATION_READY = 병동 도착)
    GUARDIAN_DELAY_REPORTED = "guardian_delay_reported"  # kind: same_day | date_change
    LOUNGE_OFFERED = "lounge_offered"
    LOUNGE_ACCEPTED = "lounge_accepted"
    # 병상 준비 (두 갈래)
    BED_PREP_REQUESTED = "bed_prep_requested"  # 병원 직원 침상 정리 요청 (사람 지정 안 함, CLAUDE.md 4번)
    BED_PREP_DONE = "bed_prep_done"
    CLEANING_INSPECTED = "cleaning_inspected"  # 종말소독 청결도 확인 (옵션)
    BED_VACANCY_REVOKED = "bed_vacancy_revoked"  # 낸 입실 가능을 되돌림 (늦은 격리 정보)
    # 입원 줄
    WARD_ASSIGNMENT_NOTIFIED = "ward_assignment_notified"  # 통보형: 확정 뒤 병동 통보
    WARD_OBJECTION_RAISED = "ward_objection_raised"
    WARD_PREP_DONE = "ward_prep_done"  # 병동 준비 완료 (투약 티켓, 네임카드, 침상)
    PATIENT_CALL_UP = "patient_call_up"  # "올려주세요" (배정 확정과 다른 사건, 이송 추적 아님)
    PATIENT_ARRIVED = "patient_arrived"  # 입실
    # 격리 정보
    SCREENING_ORDERED = "screening_ordered"
    SCREENING_RESULT_REPORTED = "screening_result_reported"  # 양성만으로 격리 규칙 적용 안 함 (오더로만)
    ISOLATION_RISK_ALERTED = "isolation_risk_alerted"  # 고위험·과거력 알림. 격리 여부는 사람이 정함
    ISOLATION_REGISTRATION_MISMATCH = "isolation_registration_mismatch"  # 양성 결과는 있는데 격리 오더 없음
    CONTACT_TRACE_REQUESTED = "contact_trace_requested"  # 같은 병실 접촉자 관리 요청 (이름 없음)
    # 병실 변경 (표시만, 이동은 사람)
    ROOM_CHANGE_REQUESTED = "room_change_requested"
    ROOM_CHANGE_WITHDRAWN = "room_change_withdrawn"
    ROOM_CHANGE_COMPLETED = "room_change_completed"
    # 예외 판단 머리 (U8 확장)
    EXCEPTION_RAISED = "exception_raised"
    EXCEPTION_RESOLVED = "exception_resolved"
    ACTION_PROPOSED = "action_proposed"
    ACTION_EXECUTED = "action_executed"
    ACTION_REJECTED = "action_rejected"
    ACTION_APPROVAL_REQUESTED = "action_approval_requested"
    ACTION_APPROVED = "action_approved"
    ACTION_REPORTED = "action_reported"
    RECHECK_SCHEDULED = "recheck_scheduled"
    # 약속 (D3)
    PROMISE_MADE = "promise_made"
    PROMISE_KEPT = "promise_kept"
    PROMISE_AT_RISK = "promise_at_risk"


CLOCK_SKEW_TOLERANCE_S = 300  # 시계 차이 허용 범위 (정책값, 설계 가정)

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

    @model_validator(mode="before")
    @classmethod
    def _clock_skew(cls, data):
        """원천 시스템 시계가 조금 빠르면(허용 범위 안) 받아들이고 표시한다 (EVT-6)."""
        if not isinstance(data, dict):
            return data
        occ, obs = data.get("occurred_at"), data.get("observed_at")
        if isinstance(occ, str):
            occ = datetime.fromisoformat(occ)
        if isinstance(obs, str):
            obs = datetime.fromisoformat(obs)
        if isinstance(occ, datetime) and isinstance(obs, datetime) and obs < occ:
            skew = (occ - obs).total_seconds()
            if skew > CLOCK_SKEW_TOLERANCE_S:
                raise ValueError(f"observed_at이 occurred_at보다 {skew:.0f}초 앞선다(허용 {CLOCK_SKEW_TOLERANCE_S}초)")
            data = {**data, "observed_at": occ, "payload": {**(data.get("payload") or {}), "_clock_skew_s": int(skew)}}
        return data

    @model_validator(mode="after")
    def _check_times(self) -> "Event":
        if self.observed_at < self.occurred_at:
            raise ValueError("observed_at은 occurred_at보다 앞설 수 없다")
        return self
