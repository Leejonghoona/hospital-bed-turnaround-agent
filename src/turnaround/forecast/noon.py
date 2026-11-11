"""정오 가용 병상 추정: 단계별 소요시간 분포 몬테카를로, P10~P90 구간 (8단계, O2·O3).

추정 대상: 병동·일마다 "그날 퇴원 병상 중 정오까지 준비 완료되는 병상 수".
추정 시점(기본 09:00)까지 인지한 사실(이벤트 as_of 조회)만 쓴다. 이미 일어난 단계는 그 시각을 쓰고,
아직 안 일어난 단계만 분포에서 뽑는다. 분포는 과거 데이터에서 맞춘 값이라고 가정한다(합성: 기본 분포).

기준선 두 개 (평가표 O2):
- 약한 기준선(설계 문서): 퇴원이 확정된 병상은 정오에 모두 빈다고 본다.
- 강한 기준선: 단계마다 중앙값을 넣은 결정적 추정.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from turnaround.events.models import Event, EventType
from turnaround.events.store import EventStore
from turnaround.rules import aerosol_wait_applies
from turnaround.saga import fold
from turnaround.sim.distributions import DEFAULT_STAGE_DISTRIBUTIONS
from turnaround.sim.models import StageDistribution, SyntheticHospital

_M = timedelta(minutes=1)


@dataclass(frozen=True)
class ForecastConfig:
    """과정 모형의 가정. 실제 병원에서는 과거 이벤트로 맞춘다 (설계 가정)."""

    as_of: time = time(9, 0)
    noon: time = time(12, 0)
    samples: int = 2000
    round_start: time = time(8, 30)
    guardian_delay_p: float = 0.2  # 보호자 지연 확률 (과거 비율)
    guardian_early_max_min: int = 30
    cleaning_delay_p: float = 0.13  # 청소 지연 확률
    default_guardian_eta: time = time(11, 0)  # 도착 예정이 없을 때
    dists: dict[str, StageDistribution] = field(default_factory=lambda: dict(DEFAULT_STAGE_DISTRIBUTIONS))


@dataclass
class _Case:
    ward: str
    isolation: bool
    aerosol_wait: int
    facts: dict
    eta: datetime | None


def _cases(events: list[Event], day: datetime, ward_of: dict[str, str], aerosol: dict[str, int]) -> list[_Case]:
    s = fold(events)
    out = []
    for d in s.discharges.values():
        if d.discharge_date != day.date() or EventType.DISCHARGE_CANCELLED in d.facts or d.bed_id not in ward_of:
            continue
        eta = None
        for e in events:
            if e.case_id == d.id and e.type in (EventType.GUARDIAN_ETA_SET, EventType.GUARDIAN_ETA_UPDATED):
                eta = datetime.fromisoformat(e.payload["eta"])
        wait = aerosol.get(d.bed_id, 0) if d.isolation and aerosol_wait_applies(d.precautions) else 0
        out.append(_Case(ward_of[d.bed_id], d.isolation, wait, dict(d.facts), eta))
    return out


def _ready_time(c: _Case, day: datetime, cfg: ForecastConfig, draw) -> datetime:
    """한 사례의 준비 완료 시각 한 표본. draw(stage)는 분 단위 소요시간."""
    f = c.facts
    T = EventType
    if T.BED_READY in f:
        return f[T.BED_READY]
    if T.CLEANING_DONE in f:
        return f[T.CLEANING_DONE] + draw("bed_make") * _M
    clean = "cleaning_terminal" if c.isolation else "cleaning_general"
    if T.CLEANING_STARTED in f:
        return f[T.CLEANING_STARTED] + (draw(clean) + draw("bed_make")) * _M
    if T.PATIENT_DEPARTED in f:
        dep = f[T.PATIENT_DEPARTED]
    else:
        order = f.get(T.ORDER_CONFIRMED) or (datetime.combine(day.date(), cfg.round_start)
                                             + draw("order_after_round") * _M)
        med = f.get(T.MEDICATION_READY) or order + draw("medication") * _M
        edu = f.get(T.EDUCATION_DONE) or order + draw("education") * _M
        bill = f.get(T.BILLING_DONE) or med + draw("billing") * _M
        if T.GUARDIAN_ARRIVED in f:
            arr = f[T.GUARDIAN_ARRIVED]
        else:
            eta = c.eta or datetime.combine(day.date(), cfg.default_guardian_eta)
            if draw("_guardian_delayed"):
                arr = eta + draw("guardian_delay") * _M
            else:
                arr = eta - draw("_guardian_early") * _M
        dep = max(bill, edu, arr) + draw("exit_prep") * _M
    req = f.get(T.CLEANING_REQUESTED) or dep + draw("baseline_cleaning_request_delay") * _M
    extra = draw("cleaning_extra_delay") if draw("_cleaning_delayed") else 0
    start = max(req + (draw("cleaning_response") + extra) * _M, dep + c.aerosol_wait * _M)
    return start + (draw(clean) + draw("bed_make")) * _M


def forecast_noon(events: list[Event], h: SyntheticHospital, day: datetime, cfg: ForecastConfig = ForecastConfig(),
                  seed: int = 0) -> dict[str, dict]:
    ward_of = {b.id: b.ward_id for b in h.beds}
    room = {r.id: r for r in h.rooms}
    from turnaround.rules import aerosol_wait_min
    aerosol = {b.id: aerosol_wait_min(room[b.room_id].air_changes_per_hour) for b in h.beds}
    cases = _cases(events, day, ward_of, aerosol)
    noon = datetime.combine(day.date(), cfg.noon)
    rng = random.Random(seed)

    def rand(stage):
        if stage == "_guardian_delayed":
            return rng.random() < cfg.guardian_delay_p
        if stage == "_cleaning_delayed":
            return rng.random() < cfg.cleaning_delay_p
        if stage == "_guardian_early":
            return rng.uniform(0, cfg.guardian_early_max_min)
        return cfg.dists[stage].sample(rng)

    def median(stage):
        if stage in ("_guardian_delayed", "_cleaning_delayed"):
            return False
        if stage == "_guardian_early":
            return cfg.guardian_early_max_min / 2
        return cfg.dists[stage].median_min

    out = {}
    for w in h.wards:
        mine = [c for c in cases if c.ward == w.id]
        counts = sorted(sum(_ready_time(c, day, cfg, rand) <= noon for c in mine) for _ in range(cfg.samples))
        q = lambda p: counts[min(len(counts) - 1, int(p * len(counts)))]  # noqa: E731
        pmf = {k: counts.count(k) / len(counts) for k in sorted(set(counts))}  # 추정 분포 전체 (보정 검사용)
        out[w.id] = {"p10": q(0.1), "p50": q(0.5), "p90": q(0.9), "mean": round(statistics.fmean(counts), 3), "pmf": pmf,
                     "weak_baseline": len(mine), "strong_baseline": sum(_ready_time(c, day, cfg, median) <= noon
                                                                         for c in mine),
                     "cases": len(mine)}
    return out


def actual_noon(h: SyntheticHospital, day_idx: int, cutoff: time = time(12)) -> dict[str, int]:
    ward_of = {b.id: b.ward_id for b in h.beds}
    noon = datetime.combine(h.config.base_date + timedelta(days=day_idx), cutoff)
    out = {w.id: 0 for w in h.wards}
    for d in h.discharges:
        if d.day == day_idx and d.actual.bed_ready_at <= noon:
            out[ward_of[d.bed_id]] += 1
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - r, 4), round(c + r, 4))


def evaluate(seeds, config=None, cfg: ForecastConfig = ForecastConfig(), samples: int | None = None,
             distributions: dict[str, StageDistribution] | None = None) -> dict:
    """시드마다 합성 병원을 만들고, 날마다 추정 시점까지의 이벤트로 추정해 실제와 비교한다."""
    from turnaround.events import hospital_to_events
    from turnaround.sim import generate

    if samples:
        cfg = ForecastConfig(**{**cfg.__dict__, "samples": samples})
    err = {"p50": [], "weak": [], "strong": []}
    covered = n = 0
    implied, pit, rps = 0.0, [], []
    for seed in seeds:
        h = generate(seed=seed, config=config, distributions=distributions)  # 분포 이동 홀드아웃: 실제만 바뀐다
        store = EventStore(":memory:")
        store.extend(hospital_to_events(h, seed=seed))
        for day in range(h.config.days):
            d0 = datetime.combine(h.config.base_date + timedelta(days=day), time())
            as_of = datetime.combine(d0.date(), cfg.as_of)
            known = store.read(as_of=as_of)
            fc = forecast_noon(known, h, d0, cfg, seed=seed * 100 + day)
            act = actual_noon(h, day, cfg.noon)
            for w, f in fc.items():
                if f["cases"] == 0:
                    continue
                a = act[w]
                err["p50"].append(abs(f["p50"] - a))
                err["weak"].append(abs(f["weak_baseline"] - a))
                err["strong"].append(abs(f["strong_baseline"] - a))
                covered += f["p10"] <= a <= f["p90"]
                n += 1
                # 보정 검사 (DECISIONS 2026-11-12 예측 구간 판정 기준). 병상 수는 작은 정수라 P10~P90 구간(양 끝 포함)이
                # 담는 확률이 80%보다 크다. 그래서 "명목 80%"가 아니라 추정 분포가 스스로 말한 구간 확률과 견준다
                pmf = f["pmf"]
                implied += sum(p for k, p in pmf.items() if f["p10"] <= k <= f["p90"])
                below, at = sum(p for k, p in pmf.items() if k < a), pmf.get(a, 0.0)
                pit.append(below + random.Random(f"pit-{seed}-{day}-{w}").random() * at)  # 무작위화 PIT (이산 분포용)
                top = max(max(pmf), a)
                rps.append(sum((sum(p for k, p in pmf.items() if k <= x) - (a <= x)) ** 2 for x in range(top + 1)))
    pit.sort()
    ks = max(max((i + 1) / n - u, u - i / n) for i, u in enumerate(pit))
    return {"ward_days": n, "MAE_p50": round(statistics.fmean(err["p50"]), 4),
            "MAE_weak_baseline": round(statistics.fmean(err["weak"]), 4),
            "MAE_strong_baseline": round(statistics.fmean(err["strong"]), 4),
            "coverage_p10_p90": round(covered / n, 4), "coverage_ci95": wilson(covered, n),
            # 추정 분포가 스스로 말한 구간 확률의 평균(잘 맞는 추정이면 실제 포함률이 이 값 근처다)
            "coverage_implied": round(implied / n, 4),
            # 무작위화 PIT: 잘 맞는 추정이면 0~1 고른 분포. ks는 고른 분포와의 최대 차이, 5% 임계값은 1.36/sqrt(n)
            "pit_mean": round(statistics.fmean(pit), 4), "pit_ks": round(ks, 4), "pit_ks_crit05": round(1.36 / math.sqrt(n), 4),
            # 순위 확률 점수(낮을수록 좋음, 분포 전체를 채점). 점 추정의 RPS는 절대 오차와 같아 강한 기준선 MAE와 견준다
            "rps": round(statistics.fmean(rps), 4)}
