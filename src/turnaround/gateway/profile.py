"""병원 프로필: 병원마다 다른 절차 스위치 (설계 개정 아키텍처 6절, DECISIONS 2026-11-09).

정책 파일의 기본값은 지금 동작(legacy)이다. 현장 기본값은 `config/profiles/field_default.yaml`로 덮어쓴다.
스위치를 기본값으로 바꾸는 일은 단계마다 한 커밋으로 한다(인터뷰 Q2).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# 사람만 하는 행동: 정책으로 자동화할 수 없다 (책임 순간, DECISIONS "U8 확장")
LOCKED_HUMAN_ACTIONS = frozenset({"contact_patient", "official_commit", "approve_safety_exception"})
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
    assignment: Assignment = Field(default_factory=Assignment)
    ward_confirmation: WardConfirmation = Field(default_factory=WardConfirmation)
    discharge_sequence: DischargeSequence = Field(default_factory=DischargeSequence)
    guardian: Guardian = Field(default_factory=Guardian)
    cleaning: Cleaning = Field(default_factory=Cleaning)
    isolation: Isolation = Field(default_factory=Isolation)
    room_change: RoomChange = Field(default_factory=RoomChange)
    admission_window: tuple[str, str] = ("10:00", "15:00")


class ExceptionRule(_Frozen):
    allowed: list[str]
    fallback: str = "escalate"
    max_attempts: dict[str, int] = Field(default_factory=dict)


class Brain(_Frozen):
    enabled: bool = False  # False면 모든 예외가 결정적 대체(지금 동작)
    max_wait_min: int = 120


def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out
