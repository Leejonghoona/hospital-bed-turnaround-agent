"""정정 방향 판정 (EVT-4). 제약을 푸는 정정은 사람 확인 뒤 반영, 조이는 정정은 즉시 반영."""

from __future__ import annotations

from typing import Literal, Mapping

Direction = Literal["loosen", "tighten", "neutral"]


def correction_direction(current: Mapping, field: str, value) -> Direction:
    if field == "precautions":
        old, new = set(current.get("precautions") or ()), set(value or ())
        if new < old:
            return "loosen"
        if new > old:
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
