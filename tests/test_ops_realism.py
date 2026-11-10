"""운영 현실 옵션 ops_realism (DECISIONS 2026-11-10): 침상 정리 일괄 회차, 청소 인력 시간대, 에이전트 수단 두 가지.

끄면(기본) 기존 출력이 그대로여야 한다. 켜면 결정적이어야 한다. 효과 크기는 설계 가정.
"""
import hashlib
from datetime import datetime, time, timedelta

import pytest

from turnaround.sim import GeneratorConfig, generate
from turnaround.sim import ops
from turnaround.sim.ops import CleanJob, cleaners_on_duty, cleaning_arrival, next_prep_batch, schedule_cleaning
from turnaround.sim.reactive import Levers, ReactiveEffects, retime

M = timedelta(minutes=1)
MON = datetime(2026, 3, 2)  # 월요일
SAT, SUN = MON + timedelta(days=5), MON + timedelta(days=6)


def at(day: datetime, hh: int, mm: int = 0) -> datetime:
    return day.replace(hour=hh, minute=mm)


def _digest(h) -> str:
    return hashlib.sha256("".join(d.model_dump_json() for d in h.discharges).encode()).hexdigest()[:16]


# ---------- 끄면 그대로 ----------

@pytest.mark.parametrize("cfg,seed,want", [
    (GeneratorConfig(), 0, "85d9263071fab523"), (GeneratorConfig(), 7, "85ac63099564b420"),
    (GeneratorConfig.realistic(), 0, "b55fe7b969a68cc7"), (GeneratorConfig.realistic(), 7, "93c1d4fec4705b76"),
])
def test_option_off_keeps_generator_output_identical(cfg, seed, want):
    # 옵션 추가 전 커밋에서 잰 퇴원 기록 지문 (2026-11-10)
    assert cfg.ops_realism is False
    assert _digest(generate(seed=seed, config=cfg)) == want


def test_option_requires_realistic_discharge_mix():
    with pytest.raises(ValueError):
        GeneratorConfig(ops_realism=True)
    assert GeneratorConfig.realistic(ops_realism=True).ops_realism


# ---------- 침상 정리 일괄 회차 ----------

def test_bed_prep_batches_at_fixed_times_on_aide_days():
    assert next_prep_batch(at(MON, 9, 0)) == at(MON, 10, 30)
    assert next_prep_batch(at(MON, 12, 0)) == at(MON, 13, 30)
    assert next_prep_batch(at(MON, 13, 30)) == at(MON, 13, 30)
    assert next_prep_batch(at(MON, 14, 1)) == at(MON, 16, 0)
    assert next_prep_batch(at(MON, 16, 1)) is None  # 마지막 회차 뒤: 간호사가 직접
    assert next_prep_batch(at(SAT, 11, 0)) == at(SAT, 13, 30)  # 보조 인력은 토요일도 근무 (3-1)
    assert next_prep_batch(at(SUN, 11, 0)) is None


def test_bed_timeline_uses_batch_or_direct_fallback():
    # 청소 12:00 시작 30분 → 12:30 끝, 다음 회차 13:30
    cs, cd, ps, pd, rd = ops.bed_timeline(at(MON, 12), 30, 15, direct_response_min=20)
    assert (cs, cd, ps, pd, rd) == (at(MON, 12), at(MON, 12, 30), at(MON, 13, 30), at(MON, 13, 45), at(MON, 13, 45))
    # 마지막 회차 뒤에는 기존 응답 시간
    _, cd, ps, _, _ = ops.bed_timeline(at(MON, 16, 10), 30, 15, direct_response_min=20)
    assert ps == cd + 20 * M


def test_early_bed_prep_request_bypasses_batch_with_minimum_response():
    # 퇴실 11:00에 요청, 청소 12:00~12:30. 시작은 max(청소 끝 + 10, 요청 + 20) = 12:40 (회차 13:30보다 이르다)
    _, cd, ps, _, rd = ops.bed_timeline(at(MON, 12), 30, 15, 20, prep_request_at=at(MON, 11), min_response_min=10)
    assert ps == at(MON, 12, 40) and rd == at(MON, 12, 55)
    # 청소 끝 직전에 요청하면 요청 + 응답 시간
    _, _, ps, _, _ = ops.bed_timeline(at(MON, 12), 30, 15, 20, prep_request_at=at(MON, 12, 25), min_response_min=10)
    assert ps == at(MON, 12, 45)
    # 회차가 더 이르면 회차를 따른다(늦추지 않는다)
    _, _, ps, _, _ = ops.bed_timeline(at(MON, 13), 20, 15, 60, prep_request_at=at(MON, 13), min_response_min=10)
    assert ps == at(MON, 13, 30)


# ---------- 청소 인력 시간대 ----------

def test_cleaning_staff_by_time_of_day():
    assert cleaners_on_duty(at(MON, 5, 59)) == 0
    assert cleaners_on_duty(at(MON, 6)) == 2
    assert cleaners_on_duty(at(MON, 14, 59)) == 2
    assert cleaners_on_duty(at(MON, 15)) == 1
    assert cleaners_on_duty(at(MON, 22)) == 0
    assert cleaners_on_duty(at(SAT, 10)) == 1 and cleaners_on_duty(at(SUN, 16)) == 1


def test_cleaning_arrival_scales_response_by_staffing_and_waits_for_shift():
    assert cleaning_arrival(at(MON, 10), 20, 0) == at(MON, 10, 20)
    assert cleaning_arrival(at(MON, 16), 20, 0) == at(MON, 16, 40)  # 저녁 1명: 2배
    assert cleaning_arrival(at(SAT, 10), 20, 5) == at(SAT, 10, 45)  # 주말 1명: 2배, 지연 추가분은 그대로
    assert cleaning_arrival(at(MON, 23), 20, 0) == at(MON + timedelta(days=1), 6, 20)  # 밤: 06시부터


def test_queue_respects_capacity_per_ward():
    jobs = [CleanJob("a", "W1", at(MON, 15, 10), 30), CleanJob("b", "W1", at(MON, 15, 20), 30),
            CleanJob("c", "W2", at(MON, 15, 20), 30), CleanJob("d", "W1", at(MON, 10), 30), CleanJob("e", "W1", at(MON, 10), 30)]
    s = schedule_cleaning(jobs)
    assert s["d"] == s["e"] == at(MON, 10)  # 주간 2명
    assert s["a"] == at(MON, 15, 10) and s["b"] == at(MON, 15, 40)  # 저녁 1명: 줄을 선다
    assert s["c"] == at(MON, 15, 20)  # 병동이 다르면 따로


def test_queue_night_request_starts_at_morning_shift_and_finishes_across_shift_change():
    s = schedule_cleaning([CleanJob("n", "W1", at(MON, 22, 30), 30), CleanJob("x", "W1", at(MON, 21, 50), 40),
                           CleanJob("y", "W1", at(MON, 21, 55), 30)])
    assert s["x"] == at(MON, 21, 50)
    assert s["y"] == at(MON + timedelta(days=1), 6)  # 22시 전에 사람이 비지 않음
    assert s["n"] == at(MON + timedelta(days=1), 6)  # 아침 2명이라 둘 다 06시


def test_shared_schedule_prefers_earliest_next_admission_and_may_delay_others():
    base = [CleanJob("a", "W1", at(MON, 15), 60), CleanJob("b", "W1", at(MON, 15, 10), 30)]
    c = dict(id="c", ward="W1", arrive=at(MON, 15, 20), duration_min=30)
    fifo = schedule_cleaning(base + [CleanJob(**c)])
    assert fifo["b"] == at(MON, 16) and fifo["c"] == at(MON, 16, 30)
    shared = schedule_cleaning(base + [CleanJob(**c, share_at=at(MON, 15, 5), next_admission_at=at(MON, 16, 30))])
    assert shared["c"] == at(MON, 16) and shared["b"] == at(MON, 16, 30)  # 다른 병상은 늦어진다(자르지 않는다)
    # 공유가 고르는 시각 뒤에 오면 순서를 바꾸지 않는다
    late = schedule_cleaning(base + [CleanJob(**c, share_at=at(MON, 16, 5), next_admission_at=at(MON, 16, 30))])
    assert late == fifo


def test_fixed_jobs_hold_their_start_and_pending_jobs_respect_not_before():
    s = schedule_cleaning([CleanJob("a", "W1", at(MON, 15), 60, fixed_start=at(MON, 15, 5)),
                           CleanJob("b", "W1", at(MON, 15), 30, not_before=at(MON, 15, 30))])
    assert s == {"a": at(MON, 15, 5), "b": at(MON, 16, 5)}


# ---------- 생성기 ----------

def _ops_hospital(seed=3):
    return generate(seed=seed, config=GeneratorConfig.realistic(ops_realism=True))


def test_generator_with_option_is_deterministic_and_changes_only_bed_side():
    a, b = _ops_hospital(), _ops_hospital()
    assert a.discharges == b.discharges
    off = generate(seed=3, config=GeneratorConfig.realistic())
    for x, y in zip(a.discharges, off.discharges):
        for f in ("order_confirmed_at", "medication_ready_at", "billing_done_at", "departed_at",
                  "baseline_cleaning_requested_at"):
            assert getattr(x.actual, f) == getattr(y.actual, f), f
        assert x.patient == y.patient
        assert x.actual.cleaning_started_at >= y.actual.cleaning_started_at  # 대기열과 저녁 응답은 늦추기만 한다


def test_generator_prep_starts_at_batch_or_fallback_and_capacity_holds():
    h = _ops_hospital()
    ward = {b.id: b.ward_id for b in h.beds}
    for d in h.discharges:
        t = d.actual
        b = next_prep_batch(t.cleaning_done_at)
        want = b if b is not None else t.cleaning_done_at + d.durations_min["bed_prep_direct_response"] * M
        assert t.bed_prep_started_at == want
        assert t.bed_ready_at == max(t.cleaning_done_at, t.bed_prep_done_at)
    for d in h.discharges:  # 시작 시각마다 같은 병동에서 동시에 청소 중인 수가 근무 인원을 넘지 않는다
        s = d.actual.cleaning_started_at
        busy = [x for x in h.discharges if ward[x.bed_id] == ward[d.bed_id]
                and x.actual.cleaning_started_at <= s < x.actual.cleaning_done_at]
        assert len(busy) <= cleaners_on_duty(s)


# ---------- 에이전트 수단 (retime) ----------

def _batched_discharge(h):
    for d in h.discharges:
        t = d.actual
        if d.discharge_type == "normal" and next_prep_batch(t.cleaning_done_at) is not None \
                and t.bed_prep_started_at - t.cleaning_done_at >= 40 * M:
            return d
    raise AssertionError("일괄 회차를 오래 기다리는 합성 퇴원 없음")


def test_bed_prep_request_lever_moves_prep_earlier_but_after_cleaning():
    d = _batched_discharge(_ops_hospital())
    t = d.actual
    t2 = retime(d, Levers(bed_prep_request_at=t.departed_at), ReactiveEffects())
    assert t2.cleaning_done_at == t.cleaning_done_at
    assert t.cleaning_done_at + 10 * M <= t2.bed_prep_started_at < t.bed_prep_started_at
    assert t2.bed_ready_at < t.bed_ready_at
    # 수단을 끄면 그대로
    off = ReactiveEffects(enabled=frozenset({"order_nudge", "lounge", "cleaning_request"}))
    assert retime(d, Levers(bed_prep_request_at=t.departed_at), off) == t


def test_new_levers_do_nothing_without_the_option():
    h = generate(seed=3, config=GeneratorConfig.realistic())
    d = next(x for x in h.discharges if x.discharge_type == "normal")
    lv = Levers(bed_prep_request_at=d.actual.departed_at, schedule_share_at=d.actual.departed_at,
                next_admission_at=d.actual.departed_at + 60 * M)
    assert retime(d, lv, ReactiveEffects()) == d.actual


def test_retime_with_all_levers_disabled_reproduces_generator():
    h = _ops_hospital()
    none = ReactiveEffects(enabled=frozenset())
    for d in h.discharges:
        if d.discharge_type != "normal":
            continue
        t = d.actual
        lv = Levers(order_nudge_at=t.order_confirmed_at - 60 * M, cleaning_request_at=t.departed_at,
                    bed_prep_request_at=t.departed_at)
        assert retime(d, lv, none) == t, d.id


# ---------- 반응형 재생 ----------

def _run(seed, cfg, fx):
    from turnaround.gateway import load_policy
    from turnaround.ui.demo import build_demo
    return build_demo(seed=seed, day=3, until=time(23, 45), via_fhir=False, policy=load_policy(), config=cfg,
                      reactive=fx, past_admissions=False, occupancy="full")


def _arrivals(st):
    from turnaround.events import EventType
    return sorted((e.case_id, e.observed_at) for e in st.store.read() if e.type is EventType.PATIENT_ARRIVED)


@pytest.mark.parametrize("seed", [0, 3])
def test_replay_with_option_and_levers_disabled_matches_no_agent(seed):
    cfg = GeneratorConfig.realistic(ops_realism=True)
    none = ReactiveEffects(enabled=frozenset())
    from turnaround.events import EventType
    a, b = _run(seed, cfg, None), _run(seed, cfg, none)
    pick = lambda st: sorted((e.event_id, e.observed_at) for e in st.store.read() if e.source == "human"  # noqa: E731
                             and e.type in (EventType.CLEANING_STARTED, EventType.BED_PREP_DONE, EventType.BED_READY))
    assert pick(a) == pick(b)


def test_replay_with_option_applies_bed_prep_and_schedule_levers():
    cfg = GeneratorConfig.realistic(ops_realism=True)
    seen = set()
    for seed in range(6):
        st = _run(seed, cfg, ReactiveEffects())
        seen |= {x.get("lever") for x in st.feed.applied}
    assert {"bed_prep_request", "schedule_share"} <= seen


def test_bed_prep_sensitivity_without_advance_notice_uses_drawn_response_after_cleaning():
    _, cd, ps, _, _ = ops.bed_timeline(at(MON, 12), 30, 15, 20, prep_request_at=at(MON, 11), min_response_min=None)
    assert ps == cd + 20 * M  # 회차(13:30)보다 이르지만 미리 알린 이득은 없다
