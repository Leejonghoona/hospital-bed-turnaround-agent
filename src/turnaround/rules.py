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


# 결핵 진료지침 5판: "환자 퇴실 후 병실은 … 일반적으로 1시간 이상 비워두어야 한다" (DECISIONS 2026-11-09)
VACANCY_FLOOR_MIN = {"MTB": 60}


# 정규 원인균 코드 (sim/models.py ORGANISM_PRECAUTIONS와 같다. 시험이 일치를 확인한다)
KNOWN_ORGANISMS = frozenset({"MTB", "MEASLES", "VARICELLA", "INFLUENZA", "PERTUSSIS", "ADENOVIRUS", "MRSA", "VRE", "CRE",
                             "CDIFF"})


def unknown_organism(organism: str | None) -> bool:
    """정규 코드가 아닌 원인균 값인가. 입구(매핑·HL7)에서 걸러야 하지만, 걸러지지 않고 들어온 값은
    쓰는 쪽에서 가장 엄격하게 다룬다(2026-11-12 2차 검수: 글자 비교 규칙이 조용히 꺼지는 문제를 종류째 닫음)."""
    return organism is not None and organism not in KNOWN_ORGANISMS


def vacancy_floor(organism: str | None) -> int:
    """병원체별 퇴실 후 최소 비움 시간(분). 없으면 0. 모르는 원인균은 가장 긴 하한을 쓴다."""
    if unknown_organism(organism):
        return max(VACANCY_FLOOR_MIN.values())
    return VACANCY_FLOOR_MIN.get(organism or "", 0)


def needs_sporicidal(organism: str | None) -> bool:
    """퇴실 뒤 아포 대응 소독이 필요한가. C. difficile, 그리고 무엇인지 모르는 원인균(엄격한 쪽)."""
    return organism == "CDIFF" or unknown_organism(organism)


def aerosol_wait_applies(precautions, droplet_wait: bool = False) -> bool:
    """퇴실 뒤 환기 대기를 거는가. 공기주의는 늘 건다. 비말주의는 병원 설정(ventilation.droplet_wait)을 켠 병원만.
    근거: 국내 표준예방지침 5.4.5.1은 비말주의에도 대기를 권고하지만 등급 (II) 약한 권고이고, 그 근거 문헌(PHAC)과
    CDC 2007은 공기주의에만 대기를 둔다 (DECISIONS 2026-11-12 비말 환기 대기)."""
    p = {getattr(x, "value", x) for x in (precautions or ())}
    return "airborne" in p or (droplet_wait and "droplet" in p)


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
