"""비교 기준선. 지금은 에이전트 없음(기존 방식)만 있다. 규칙 모드는 계획 에이전트 단계에서 추가한다."""

from __future__ import annotations

from typing import Iterable

from turnaround.sim import generate


def _pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p))]


def _summary(xs: list[float]) -> dict:
    return {"p50": round(_pct(xs, 0.5), 1), "p90": round(_pct(xs, 0.9), 1)}


def no_agent_baseline(seeds: Iterable[int]) -> dict:
    """생성기의 기존 방식 시각으로 구간 지표(O1)를 낸다. 입실 구간은 배정이 생겨야 잴 수 있다."""
    pre, prep, req, noon, n = [], [], [], 0, 0
    for seed in seeds:
        for d in generate(seed=seed).discharges:
            t = d.actual
            pre.append((t.departed_at - t.order_confirmed_at).total_seconds() / 60)
            prep.append((t.bed_ready_at - t.departed_at).total_seconds() / 60)
            req.append((t.baseline_cleaning_requested_at - t.departed_at).total_seconds() / 60)
            noon += t.departed_at.hour < 12
            n += 1
    return {
        "name": "no_agent",
        "n_discharges": n,
        "pre_departure_min": _summary(pre),  # 퇴원 오더 확정 → 퇴실
        "bed_prep_min": _summary(prep),  # 퇴실 → 병상 준비 완료
        "cleaning_request_delay_min": _summary(req),  # 퇴실 → 청소 요청
        "noon_departure_ratio": round(noon / n, 4),
        "admission_segment": None,  # 준비 완료 → 입실: 배정 단계 이후
        "assumptions": [],
    }
