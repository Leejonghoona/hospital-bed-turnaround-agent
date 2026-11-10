"""운영 현실 옵션 ops_realism (DECISIONS 2026-11-10): 침상 정리 일괄 회차와 청소 인력 시간대.

근거는 `docs/검토/현장조사/감염관리_청소_실무.md`.
- 침상 정리 일괄 처리: 3-1 해운대백병원 병동업무보조 과업지시서 "13:30 퇴원 침상정리 및 신환 침상 준비",
  근무 평일·토요일 08:00~17:00 (원문). 10:30, 16:00 회차는 설계 가정.
- 청소 인력: 3-3 06~15시, 15~22시 2교대, 주말 혼자, 병동 담당 (보도 인터뷰). 3-13 저녁 인력 감소 (해외 컨설팅).
  인원 수(평일 주간 2, 저녁 1, 주말 1, 밤 0)는 설계 가정.

모두 결정적이다(난수를 쓰지 않는다). 사람 배정은 하지 않는다(CLAUDE.md 4번): 대기열은 "언제 시작될 수 있나"만 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Iterable

_M = timedelta(minutes=1)

PREP_BATCH_TIMES = (time(10, 30), time(13, 30), time(16, 0))  # 13:30 출처(3-1), 나머지 설계 가정
AIDE_WEEKDAYS = frozenset({0, 1, 2, 3, 4, 5})  # 월~토 (3-1 "평일·토요일")
DAY_CREW = 2  # 평일 주간 병동당 청소 인원 (설계 가정, 3-3 "2명이 하던 청소일을 주말에는 혼자서")
_SHIFT_CHANGES = (time(6), time(15), time(22))


def next_prep_batch(t: datetime) -> datetime | None:
    """t 이후(같은 시각 포함) 같은 날의 침상 정리 회차. 보조 인력이 없는 날이나 마지막 회차 뒤면 None."""
    if t.weekday() not in AIDE_WEEKDAYS:
        return None
    for b in PREP_BATCH_TIMES:
        c = datetime.combine(t.date(), b)
        if c >= t:
            return c
    return None


def bed_timeline(clean_start: datetime, clean_len_min: int, prep_len_min: int, direct_response_min: int,
                 prep_request_at: datetime | None = None, min_response_min: int | None = 10):
    """청소 시작부터 입실 가능까지. (청소 시작, 청소 끝, 정리 시작, 정리 끝, 입실 가능)

    침상 정리는 청소 뒤(감염관리 검수 P1). 기본은 다음 회차, 회차가 없으면 기존 응답 시간(간호사 직접, 설계 가정).
    prep_request_at(에이전트의 퇴실 즉시 요청)이 있으면 max(청소 끝 + 최소 응답, 요청 + 기존 응답)과 회차 중 이른 쪽.
    """
    clean_done = clean_start + clean_len_min * _M
    batch = next_prep_batch(clean_done)
    prep_start = batch if batch is not None else clean_done + direct_response_min * _M
    if prep_request_at is not None:
        if min_response_min is None:  # 민감도: 미리 알린 이득 없음 (청소 끝 뒤 기존 응답 시간)
            direct = clean_done + direct_response_min * _M
        else:
            direct = max(clean_done + min_response_min * _M, prep_request_at + direct_response_min * _M)
        prep_start = min(prep_start, direct)
    prep_done = prep_start + prep_len_min * _M
    return clean_start, clean_done, prep_start, prep_done, max(clean_done, prep_done)


def cleaners_on_duty(t: datetime) -> int:
    """병동 하나에서 t에 근무 중인 청소 인원 (형태 3-3·3-13, 인원 수 설계 가정)."""
    h = t.time()
    if not time(6) <= h < time(22):
        return 0
    if t.weekday() >= 5:
        return 1
    return DAY_CREW if h < time(15) else 1


def _next_duty_start(t: datetime) -> datetime:
    day = t.date() + (timedelta(days=1) if t.time() >= time(22) else timedelta())
    return datetime.combine(day, time(6))


def cleaning_arrival(requested: datetime, response_min: int, extra_min: int = 0) -> datetime:
    """청소 요청 뒤 청소 인력이 올 수 있는 가장 이른 시각. 밤 요청은 06시부터 세고, 인원이 주간보다 적으면
    응답 시간을 인원 비율만큼 늘린다(같은 구역을 적은 인원이 맡음, 설계 가정). 청소 지연 추가분은 그대로 더한다."""
    r = requested if cleaners_on_duty(requested) else _next_duty_start(requested)
    scale = DAY_CREW / cleaners_on_duty(r)
    return r + round(response_min * scale) * _M + extra_min * _M


def _next_shift_change(t: datetime) -> datetime:
    for c in _SHIFT_CHANGES:
        x = datetime.combine(t.date(), c)
        if x > t:
            return x
    return datetime.combine(t.date() + timedelta(days=1), _SHIFT_CHANGES[0])


@dataclass(frozen=True)
class CleanJob:
    id: str
    ward: str
    arrive: datetime  # 청소 인력이 올 수 있는 시각 (cleaning_arrival, 환기 대기 반영)
    duration_min: int
    not_before: datetime | None = None  # 재생 중: 아직 시작하지 않은 청소는 지금보다 앞설 수 없다
    share_at: datetime | None = None  # 청소 예정표에 다음 입원 시각을 공유한 시각 (에이전트 수단)
    next_admission_at: datetime | None = None
    fixed_start: datetime | None = None  # 이미 시작한 청소


def schedule_cleaning(jobs: Iterable[CleanJob]) -> dict[str, datetime]:
    """병동마다 결정적 대기열로 퇴실 청소 시작 시각을 정한다. 근무 인원보다 많이 동시에 하지 않는다.

    기본 순서는 들어온 순서. 고르는 시각에 다음 입원 시각이 공유돼 있으면 그 시각이 이른 병상을 먼저 고른다
    (예정표 공유, 사람 배정 아님). 시작한 청소는 교대 시각을 넘겨도 끝낸다.
    """
    out: dict[str, datetime] = {}
    by_ward: dict[str, list[CleanJob]] = {}
    for j in jobs:
        by_ward.setdefault(j.ward, []).append(j)
    for ward in sorted(by_ward):
        js = by_ward[ward]
        busy: list[tuple[datetime, datetime]] = []
        for j in js:
            if j.fixed_start is not None:
                out[j.id] = j.fixed_start
                busy.append((j.fixed_start, j.fixed_start + j.duration_min * _M))
        pending = sorted((j for j in js if j.fixed_start is None), key=lambda j: (j.arrive, j.id))
        if not pending:
            continue
        elig = {j.id: max(j.arrive, j.not_before) if j.not_before else j.arrive for j in pending}
        t = min(elig.values())

        def key(j: CleanJob, now: datetime):
            if j.share_at is not None and j.next_admission_at is not None and j.share_at <= now:
                return (0, j.next_admission_at, j.arrive, j.id)
            return (1, j.arrive, j.arrive, j.id)

        while pending:
            ready = [j for j in pending if elig[j.id] <= t]
            n_busy = sum(1 for s, e in busy if s <= t < e)
            if ready and n_busy < cleaners_on_duty(t):
                j = min(ready, key=lambda x: key(x, t))
                out[j.id] = t
                busy.append((t, t + j.duration_min * _M))
                pending.remove(j)
                continue
            cands = [e for s, e in busy if e > t] + [s for s, _ in busy if s > t]
            cands += [elig[j.id] for j in pending if elig[j.id] > t]
            cands.append(_next_shift_change(t))
            t = min(cands)
    return out


__all__ = ["AIDE_WEEKDAYS", "CleanJob", "DAY_CREW", "PREP_BATCH_TIMES", "bed_timeline", "cleaners_on_duty",
           "cleaning_arrival", "next_prep_batch", "schedule_cleaning"]
