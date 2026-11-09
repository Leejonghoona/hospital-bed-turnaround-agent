"""병원 프로필: 병원마다 다른 절차 스위치 (설계 개정 아키텍처 6절, DECISIONS 2026-11-09).

정책 파일의 기본값은 지금 동작(legacy)이다. 현장 기본값은 `config/profiles/field_default.yaml`로 덮어쓴다.
스위치를 기본값으로 바꾸는 일은 단계마다 한 커밋으로 한다(인터뷰 Q2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 사람만 하는 행동: 정책으로 자동화할 수 없다 (책임 순간, DECISIONS "U8 확장").
# 공식 확정(official_commit)은 정책 자율 단계로 둔다 (DECISIONS 2026-11-09 "공식 확정을 책임 순간에서 뺀다")
LOCKED_HUMAN_ACTIONS = frozenset({"contact_patient", "approve_safety_exception"})
# 단계 잠금: 환자 연락·확정·입실 뒤에는 매칭을 스스로 바꾸지 않는다(목표 함수 2항, 안전 검토 1.3).
# 허용 목록에서 빼고(머리에게 보이지 않음) 게이트웨이도 거부한다. 병원 설정으로 풀 수 없다.
# 근거: Haiku 5.5 예외 판단 측정(2026-11-09)에서 금지 20건 중 14건이 이 단계의 자동 재매칭이었다
STAGE_LOCKED_ACTIONS: dict[str, frozenset[str]] = {
    "after_contact": frozenset({"rematch_now"}), "after_commit": frozenset({"rematch_now"}),
    "after_arrival": frozenset({"rematch_now"})}
Autonomy = Literal["auto", "post_report", "human"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Assignment(_Frozen):
    timing: Literal["same_day_morning", "prematch_evening"] = "prematch_evening"
    window: tuple[str, str] = ("08:00", "11:30")  # 정규 입원 배정 창 (설계 가정)
    contact_by: str = "11:30"  # 환자 연락 목표
    window_applies_to: list[str] = Field(default_factory=lambda: ["scheduled"])
    prematch_evening_at: str = "17:00"


class WardConfirmation(_Frozen):
    mode: Literal["notify", "reply"] = "reply"
    objection_window_min: int = 30  # 설계 가정
    merge_manager_confirm_into_commit: bool = False  # 인터뷰 Q1


class DischargeSequence(_Frozen):
    preset: Literal["field", "legacy"] = "legacy"
    rx_due_at: str = "11:00"
    departure_due_at: str = "11:00"


class Guardian(_Frozen):
    same_day_tolerance_min: int = 60  # 설계 가정
    lounge_enabled: bool = False


class Cleaning(_Frozen):
    split: bool = False  # True: 병원 직원 침상 정리 + 미화 표면 청소 두 갈래
    housekeeping_enabled: bool = True
    bed_prep_mode: Literal["on_departure", "batch"] = "on_departure"
    bed_prep_batch_times: list[str] = Field(default_factory=lambda: ["13:30"])
    readiness_scope: dict[str, str] = Field(default_factory=lambda: {
        "multi": "room", "single": "room", "negative_pressure": "room", "isolation": "room_terminal"})


class Isolation(_Frozen):
    risk_conditions: list[str] = Field(default_factory=list)  # 병원 감염관리 규정이 정함
    alert_to: list[str] = Field(default_factory=lambda: ["bed_manager", "ipc"])
    mismatch_alert_min: int = 60  # 설계 가정


class RoomChange(_Frozen):
    display_chain: bool = False  # 표시만 (GW-15 실행은 설계 항목)
    max_depth: int = 3


class HospitalProfile(_Frozen):
    id: str = "legacy"
    # 예외 판단 머리가 사람에게 올릴 수 있는 역할. 외주 미화·그 책임자는 기본에서 뺀다(DECISIONS 2026-11-09):
    # 청소 지연은 책임간호사·원무에게 올리고 그들이 지금처럼 미화에 연락한다. 직영 환경관리 담당은 병원 설정으로 추가
    escalation_roles: list[str] = Field(default_factory=lambda: [
        "bed_manager", "ipc", "ward", "admission_office", "billing", "pharmacy", "admission_lead",
        "nursing_supervisor"])
    assignment: Assignment = Field(default_factory=Assignment)
    ward_confirmation: WardConfirmation = Field(default_factory=WardConfirmation)
    discharge_sequence: DischargeSequence = Field(default_factory=DischargeSequence)
    guardian: Guardian = Field(default_factory=Guardian)
    cleaning: Cleaning = Field(default_factory=Cleaning)
    isolation: Isolation = Field(default_factory=Isolation)
    room_change: RoomChange = Field(default_factory=RoomChange)
    admission_window: tuple[str, str] = ("10:00", "15:00")
    # 진료과 → 병동 (온보딩이 과거 배정 기록에서 찾음, 로드맵 07 M4). 비어 있으면 병동 정의의 진료과를 쓴다
    ward_departments: dict[str, list[str]] = Field(default_factory=dict)


class ExceptionRule(_Frozen):
    allowed: list[str]
    fallback: str = "escalate"
    max_attempts: dict[str, int] = Field(default_factory=dict)
    # 기다림 행동마다 함께 할 행동: [종류, 받는 역할]. 기다림만 하는 대응은 없다 (사용자 원칙, 현장 조사 예외3건)
    companions: dict[str, list[tuple[str, str]]] = Field(default_factory=dict)
    # 단계별로 더 좁힌 허용 목록 (없으면 allowed 그대로). 단계 잠금(STAGE_LOCKED_ACTIONS)은 언제나 뺀다
    stage_allowed: dict[str, list[str]] = Field(default_factory=dict)

    def allowed_for(self, stage: str | None) -> list[str]:
        base = self.stage_allowed.get(stage or "", self.allowed)
        locked = STAGE_LOCKED_ACTIONS.get(stage or "", frozenset())
        return [a for a in base if a in self.allowed and a not in locked]


class PromotionRule(_Frozen):
    """자율권 승격 기준 (로드맵 07 M3). 측정 전에 고정한다. 값은 설계 가정."""
    min_cases: int = 25  # 그 유형만으로 검토된 사례 수
    min_agreement_lower95: float = 0.85  # 사람이 제안을 그대로 승인한 비율의 윌슨 95% 하한
    demote_window: int = 20  # 승격 뒤 최근 이 수의 에이전트 결정 중
    demote_disagreements: int = 3  # 이의·직접 수정이 이만큼이면 자동으로 내린다 (안전을 조이는 쪽은 자동)
    # 승격 대상이 아닌 유형: 안전(격리·동명이인·입력 누락), 환자 동의(차액·통합서비스), 병상 없음, 소아
    never: list[str] = Field(default_factory=lambda: [
        "isolation", "preemptive_isolation", "isolation_discharge_bed", "same_name_same_ward", "missing_input",
        "single_room_preference", "integrated_care_consent_missing", "no_bed", "pediatric"])


class Brain(_Frozen):
    enabled: bool = False  # False면 모든 예외가 결정적 대체(지금 동작)
    max_wait_min: int = 120


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
