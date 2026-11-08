"""여러 구성요소가 함께 쓰는 지침 값 (POL-1). 의존성 없는 순수 함수만 둔다.

출처: 질병관리청 의료관련감염 표준예방지침(2017) 제5장 부록 3,
"시간당 공기순환 횟수에 따른 공기 중 오염물질 제거 시간" (PHAC 2012 인용).
"""

from __future__ import annotations

import math

# 시간당 공기순환 횟수(ACH) → (99% 제거 분, 99.9% 제거 분). 지침 부록 3 표.
AEROSOL_REMOVAL_TABLE: dict[int, tuple[int, int]] = {
    2: (138, 207),
    4: (69, 104),
    6: (46, 69),
    8: (35, 52),
    10: (28, 41),
    12: (23, 35),
    15: (18, 28),
    20: (14, 21),
    50: (6, 8),
}
AEROSOL_SOURCE = "표준예방지침(2017) 제5장 부록 3"

# 병실 유형별 공기순환 횟수 기본값 (DAT-9). 설계 가정: 음압실 12회, 일반실 4회.
DEFAULT_ACH = {"negative_pressure": 12, "single": 4, "multi": 4}


def aerosol_wait_min(ach: int, removal_pct: float = 99.9) -> int:
    """환기 대기 분. 표에 있으면 표 값, 없으면 같은 식(t = ln(1/(1-제거율)) / ACH × 60)을 올림한다."""
    if ach <= 0:
        raise ValueError("공기순환 횟수는 양수여야 한다")
    if ach in AEROSOL_REMOVAL_TABLE and removal_pct in (99.0, 99.9):
        return AEROSOL_REMOVAL_TABLE[ach][0 if removal_pct == 99.0 else 1]
    return math.ceil(math.log(1 / (1 - removal_pct / 100)) / ach * 60)


def guideline_delta(guideline_min: int, hospital_min: int | None) -> dict | None:
    """병원 규칙 값과 지침 값의 차이 (POL-2). 같거나 병원 값이 없으면 None."""
    if hospital_min is None or hospital_min == guideline_min:
        return None
    diff = hospital_min - guideline_min
    label = f"지침 대비 {abs(diff)}분 {'단축' if diff < 0 else '연장'}"
    return {"guideline_min": guideline_min, "hospital_min": hospital_min, "delta_min": diff, "label": label}
