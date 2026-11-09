"""현실 기준선 프리셋 (DECISIONS 2026-11-09 H, 근거 docs/검토/현장조사/제안요청서_QI_통계.md 4절).

에이전트 효과(M2)를 재기 전에 "에이전트 없음" 기준선을 국내 QI 기준선 범위에 맞춘다.
검증 목표: 12시 이전 퇴실 42~60%(아주대 42, 양산부산대 48, 인천성모 58~60), 퇴실 중앙값 11:30~12:30.
"""

import statistics
from datetime import time

from turnaround.sim import GeneratorConfig, generate


def _normal_departures(seeds):
    out = []
    for s in seeds:
        h = generate(seed=s, config=GeneratorConfig.realistic())
        out += [d.actual.departed_at for d in h.discharges if d.discharge_type == "normal"]
    return out


def test_realistic_preset_shape():
    c = GeneratorConfig.realistic()
    assert (c.days, c.discharges_per_day, c.admission_count) == (10, 3, 30)
    assert c.admission_mix == "realistic" and c.discharge_mix == "realistic"


def test_realistic_baseline_departure_is_within_domestic_qi_range():
    dep = _normal_departures(range(60))
    before_noon = sum(1 for t in dep if t.time() < time(12, 0)) / len(dep)
    assert 0.42 <= before_noon <= 0.60, before_noon
    mins = statistics.median(t.hour * 60 + t.minute for t in dep)
    assert 11 * 60 + 30 <= mins <= 12 * 60 + 30, mins


def test_test_preset_is_unchanged():
    a = generate(seed=42).discharges[0].actual.departed_at
    b = generate(seed=42, config=GeneratorConfig()).discharges[0].actual.departed_at
    assert a == b and GeneratorConfig().discharge_mix == "test"
