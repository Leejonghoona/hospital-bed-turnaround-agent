"""병상 흐름 지표 (로드맵 07 M2): 에이전트가 퇴원 병목을 밀었을 때 병상이 얼마나 일찍 생기는가.

하루 재생 하나에서 그날 정상 퇴원과 그날 입원만 본다. 합성 데이터 기준 추정이다.
- 12시 이전 퇴실 비율, 퇴실 시각 중앙값 (퇴실 기준. 국내 QI 지표 정의와 같다)
- 병상 준비 완료 시각 중앙값, 14시까지 준비된 병상 비율
- 입원 대기 해소: 입원 예정 시각에 배정 병상이 이미 준비된 비율, 병상을 기다린 시간(분) 평균
"""

from __future__ import annotations

import statistics
from datetime import date, datetime, time

from turnaround.events import Event, EventType


def _hm(ts: list[datetime]) -> str | None:
    if not ts:
        return None
    m = statistics.median(t.hour * 60 + t.minute for t in ts)
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


def day_flow(h, events: list[Event], day: int) -> dict:
    the_date: date = h.config.base_date.fromordinal(h.config.base_date.toordinal() + day)
    normal = {d.id: d for d in h.discharges if d.day == day and d.discharge_type == "normal"}
    first: dict[tuple[str, EventType], datetime] = {}
    for e in events:
        first.setdefault((e.case_id, e.type), e.observed_at)
    dep = [first[(c, EventType.PATIENT_DEPARTED)] for c in normal if (c, EventType.PATIENT_DEPARTED) in first]
    ready = {normal[c].bed_id: first[(c, EventType.BED_READY)] for c in normal if (c, EventType.BED_READY) in first}
    commits = {e.case_id: e.payload.get("bed_id") for e in events if e.type is EventType.MATCH_COMMITTED}
    waits, on_time = [], 0
    adm = [a for a in h.admissions if a.day == day and a.cancelled_at is None and a.id in commits]
    for a in adm:
        r = ready.get(commits[a.id])
        w = max(0.0, (r - a.requested_at).total_seconds() / 60) if r else 0.0
        waits.append(w)
        on_time += w == 0
    noon = datetime.combine(the_date, time(12, 0))
    two = datetime.combine(the_date, time(14, 0))
    return {
        "discharges": len(dep), "departed_before_noon": sum(t < noon for t in dep), "departure_median": _hm(dep),
        "beds_ready": len(ready), "ready_by_14": sum(t <= two for t in ready.values()),
        "ready_median": _hm(list(ready.values())),
        "admissions": len(adm), "bed_ready_on_arrival": on_time,
        "bed_wait_mean_min": round(statistics.mean(waits), 1) if waits else None,
        "bed_wait_total_min": round(sum(waits), 1),
    }


__all__ = ["day_flow"]
