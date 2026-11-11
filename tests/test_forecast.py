"""8단계: 정오(및 오후 입원 시각) 가용 병상 몬테카를로 추정 (O2, O3)."""

from datetime import datetime, time, timedelta

import pytest

from turnaround.events import EventStore, hospital_to_events
from turnaround.forecast import ForecastConfig, evaluate, forecast_noon
from turnaround.sim import generate
from turnaround.sim.distributions import DEFAULT_STAGE_DISTRIBUTIONS as D
from turnaround.sim.models import GeneratorConfig, StageDistribution

CFG15 = ForecastConfig(noon=time(15), samples=400)


def _day(h, i=1):
    return datetime.combine(h.config.base_date + timedelta(days=i), time())


def test_forecast_is_deterministic_and_ordered():
    h = generate(seed=3)
    st = EventStore(":memory:")
    st.extend(hospital_to_events(h, seed=3))
    ev = st.read(as_of=datetime.combine(_day(h).date(), time(9)))
    a = forecast_noon(ev, h, _day(h), CFG15, seed=1)
    assert a == forecast_noon(ev, h, _day(h), CFG15, seed=1)
    for f in a.values():
        assert f["p10"] <= f["p50"] <= f["p90"] <= f["weak_baseline"] == f["cases"]


def test_forecast_uses_only_known_events():
    """미래 정보 누수 없음: 추정 시점 뒤 이벤트를 지워도 결과가 같다."""
    h = generate(seed=4)
    st = EventStore(":memory:")
    st.extend(hospital_to_events(h, seed=4))
    as_of = datetime.combine(_day(h).date(), time(9))
    known = st.read(as_of=as_of)
    everything = st.read()
    cut = [e for e in everything if e.observed_at <= as_of]
    assert forecast_noon(known, h, _day(h), CFG15) == forecast_noon(cut, h, _day(h), CFG15)


def test_mc_beats_both_baselines_at_admission_time():
    r = evaluate(range(0, 8), cfg=CFG15)
    assert r["MAE_p50"] < r["MAE_strong_baseline"] <= r["MAE_weak_baseline"]


def test_interval_is_calibrated_in_distribution():
    """예측 구간 판정 기준 (DECISIONS 2026-11-12). 병상 수는 작은 정수라 P10~P90 구간(양 끝 포함)이 담는 확률은
    80%가 아니라 96% 안팎이다. 그래서 고정 숫자(70~90%)가 아니라 다음 셋으로 판정한다.
    1) 실제 포함률이 추정 분포가 스스로 말한 구간 확률과 5%p 안에서 같다
    2) 무작위화 PIT가 고른 분포다 (KS 5% 임계값 아래)
    3) 분포 전체를 채점한 RPS가 강한 기준선(점 추정의 절대 오차)보다 낮다
    개발에 쓰지 않은 시드(8~23)로 본다."""
    r = evaluate(range(8, 24), cfg=CFG15)
    assert abs(r["coverage_p10_p90"] - r["coverage_implied"]) <= 0.05
    assert r["pit_ks"] < r["pit_ks_crit05"]
    assert r["rps"] < r["MAE_strong_baseline"]


def test_noon_is_degenerate_in_synthetic_baseline_process():
    """합성 기존 방식에서는 정오 전 준비 완료가 드물다 (MVP 가정 MA-14). 숫자를 숨기지 않는다."""
    r = evaluate(range(0, 5), cfg=ForecastConfig(samples=200))
    assert r["MAE_p50"] < 0.1


@pytest.mark.slow
def test_distribution_shift_holdout():
    """홀드아웃: 실제 분포 P90 +30%, 보호자 지연 6 → 10건. 추정기는 기본 분포 그대로 (O2, O3)."""
    shift = {k: StageDistribution(median_min=v.median_min, p90_min=round(v.median_min + (v.p90_min - v.median_min) * 1.3))
             for k, v in D.items()}
    r1 = evaluate(range(10000, 10100), cfg=ForecastConfig(noon=time(15), samples=1000), distributions=shift)
    r2 = evaluate(range(10100, 10200), cfg=ForecastConfig(noon=time(15), samples=1000),
                  config=GeneratorConfig(guardian_delay_count=10))
    for r in (r1, r2):
        assert r["MAE_p50"] < r["MAE_strong_baseline"] and r["rps"] < r["MAE_strong_baseline"]  # 정확도는 기준선보다 낫다
        # 알려진 한계: 실제 분포가 추정기 가정과 달라지면 보정이 깨진다(준비 병상을 많게 보는 쪽). PIT 검사가 이를 잡아낸다.
        # 실제 병원에서는 그 병원 기록으로 단계 분포를 다시 맞춘 뒤에만 구간을 쓴다 (DECISIONS 2026-11-12)
        assert r["pit_ks"] > r["pit_ks_crit05"] and r["pit_mean"] < 0.5
    print(r1, r2)
