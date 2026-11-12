"""정정 방향 판정 (EVT-4). 제약을 푸는 정정은 사람 확인 뒤 반영, 조이는 정정은 즉시 반영."""

from __future__ import annotations

from typing import Literal, Mapping

Direction = Literal["loosen", "tighten", "neutral"]


def correction_direction(current: Mapping, field: str, value) -> Direction:
    if field == "precautions":
        old, new = set(current.get("precautions") or ()), set(value or ())
        if old - new:
            return "loosen"  # 하나라도 빠지면 완화다(교체 contact→droplet 포함, 2단계 안전 검수 M5)
        if new > old:
            return "tighten"
    if field == "precaution_status":
        rank = {"confirmed": 3, "suspected": 2, "preemptive": 1, None: 0}
        o, n = rank.get(current.get("precaution_status"), 0), rank.get(value, 0)
        return "loosen" if n < o else "tighten" if n > o else "neutral"
    if field == "organism":
        old = current.get("organism")
        if old and value != old:
            # 지우는 것도, 다른 균으로 바꾸는 것도 완화로 본다. 균마다 걸린 규칙(결핵 방 공유 금지·1시간 비움,
            # C. difficile 아포 소독)이 달라, MTB → MRSA 같은 교체가 규칙 하나를 조용히 푸는 경로가 된다 (2026-11-12)
            return "loosen"
        if not old and value:
            return "tighten"
    return "neutral"


def apply_correction(payload: dict, field: str, value, extra: Mapping) -> None:
    payload[field] = value
    for k in ("organism", "precaution_status", "isolation_ordered_by"):
        if k in extra:
            payload[k] = extra[k]
    if field == "precautions":
        payload["isolation_order"] = bool(value)
        if not value:
            payload.update(organism=None, precaution_status=None, isolation_ordered_by=None)


def apply_release(payload: dict) -> None:
    payload.update(isolation_order=False, precautions=[], organism=None, precaution_status=None,
                   isolation_ordered_by=None)
