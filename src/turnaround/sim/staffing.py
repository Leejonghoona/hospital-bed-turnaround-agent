"""근무조별 근무 간호사 수 (DAT-8). 합성 값이며 실제 병원 인원이 아니다.

StaffingSource는 교체 가능한 인터페이스다. 실제 병원은 근무표 시스템을 이 인터페이스로 붙인다.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Protocol

from .models import SyntheticHospital

# 병상 12개 병동 기준 근무조별 기본 인원 (설계 가정: 주간·초번 3명, 야간 2명)
BASE_NURSES = {"day": 3, "evening": 3, "night": 2}


class StaffingSource(Protocol):
    def nurses_on(self, ward_id: str, day: date, shift: str) -> int: ...


class StaffingTable:
    def __init__(self, table: dict[tuple[str, date, str], int]):
        self.table = dict(table)

    def nurses_on(self, ward_id: str, day: date, shift: str) -> int:
        return self.table[(ward_id, day, shift)]


def synthetic_staffing(h: SyntheticHospital, seed: int = 0, shortage_rate: float = 0.1) -> StaffingTable:
    """근무조마다 기본 인원, 그중 일부(기본 10%)는 결원 1명. 야간은 주간보다 많지 않게."""
    rng = random.Random(f"staffing-{seed}")
    table = {}
    for w in h.wards:
        for i in range(-1, h.config.days + 1):
            d = h.config.base_date + timedelta(days=i)
            day_n = BASE_NURSES["day"] - (rng.random() < shortage_rate)
            for shift, base in BASE_NURSES.items():
                n = day_n if shift == "day" else base - (rng.random() < shortage_rate)
                table[(w.id, d, shift)] = max(1, min(n, day_n) if shift == "night" else n)
    return StaffingTable(table)
