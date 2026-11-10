"""사람 응답 모형 (로드맵 07 M3). 하루 재생에서 담당자·병동이 에이전트 제안을 얼마나 고치는지 흉내 낸다.

비율은 근거가 없는 설계 가정이다. 자율권 승격(M3)이 "사람이 늘 그대로 승인하는 유형"을 찾아내는지 보이는 데 쓴다.
같은 제안에는 늘 같은 응답을 낸다(결정적).
"""

from __future__ import annotations

import random

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_OVERRIDE = {  # 담당자가 제안을 그대로 승인하지 않고 다른 병상으로 직접 수정하는 비율 (설계 가정)
    "official_commit": 0.01, "close_scores": 0.03, "icu_stepdown": 0.05, "isolation": 0.15,
    "preemptive_isolation": 0.15, "single_room_preference": 0.10, "integrated_care_consent_missing": 0.05,
}


class ReviewerModel(BaseModel):
    model_config = ConfigDict(frozen=True)
    override_rate: dict[str, float] = Field(default_factory=lambda: dict(_DEFAULT_OVERRIDE))
    default_override: float = 0.10
    ward_objection_rate: float = Field(default=0.02, ge=0, le=1)  # 에이전트 확정 뒤 병동 이의 (설계 가정)
    salt: str = ""

    def overrides(self, proposal_id: str, unit: str | None) -> bool:
        rate = self.override_rate.get(unit or "", self.default_override)
        return random.Random(f"ov-{self.salt}-{proposal_id}").random() < rate

    def objects(self, proposal_id: str) -> bool:
        return random.Random(f"ob-{self.salt}-{proposal_id}").random() < self.ward_objection_rate


__all__ = ["ReviewerModel"]
