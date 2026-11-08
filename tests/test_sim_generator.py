"""1단계: 합성 데이터 생성기 테스트.

설계 기준: 병동 2개, 병상 24개, 퇴원 30건, 입원 대기 15건, 단계별 소요시간 분포,
보호자 지연, 청소 지연, 입원 취소, 퇴원 예정 미등록. 해석은 docs/DECISIONS.md 참고.
"""

import random
from datetime import timedelta
import statistics
from collections import Counter

import pytest

from turnaround.sim import (
    DEFAULT_STAGE_DISTRIBUTIONS,
    CleaningType,
    GeneratorConfig,
    IsolationType,
    RoomType,
    StageDistribution,
    generate,
)


@pytest.fixture(scope="module")
def hospital():
    return generate(seed=42)


# ---------- 규모 ----------

def test_scale_matches_design(hospital):
    assert len(hospital.wards) == 2
    assert len(hospital.beds) == 24
    assert len(hospital.discharges) == 30
    assert len(hospital.admissions) == 15


def test_rooms_have_at_most_four_beds(hospital):
    per_room = Counter(b.room_id for b in hospital.beds)
    assert max(per_room.values()) <= 4
    room_ids = {r.id for r in hospital.rooms}
    assert set(per_room) <= room_ids


def test_every_bed_belongs_to_known_ward_and_room(hospital):
    ward_ids = {w.id for w in hospital.wards}
    rooms = {r.id: r for r in hospital.rooms}
    for bed in hospital.beds:
        assert bed.ward_id in ward_ids
        assert rooms[bed.room_id].ward_id == bed.ward_id


def test_each_ward_has_isolation_capable_rooms(hospital):
    for ward in hospital.wards:
        types = {r.room_type for r in hospital.rooms if r.ward_id == ward.id}
        assert types & {RoomType.NEGATIVE_PRESSURE, RoomType.SINGLE}


# ---------- 합성 데이터 표시 ----------

def test_all_patients_are_marked_synthetic(hospital):
    patients = [d.patient for d in hospital.discharges] + [a.patient for a in hospital.admissions]
    assert all(p.id.startswith("SYN-") for p in patients)
    assert all(p.synthetic for p in patients)
    assert hospital.synthetic is True


def test_patient_ids_are_unique(hospital):
    ids = [d.patient.id for d in hospital.discharges] + [a.patient.id for a in hospital.admissions]
    assert len(ids) == len(set(ids))


# ---------- 재현성 ----------

def test_same_seed_gives_identical_output():
    assert generate(seed=7).model_dump_json() == generate(seed=7).model_dump_json()


def test_different_seed_gives_different_output():
    assert generate(seed=7).model_dump_json() != generate(seed=8).model_dump_json()


def test_does_not_touch_global_random_state():
    random.seed(123)
    expected = random.random()
    random.seed(123)
    generate(seed=1)
    assert random.random() == expected


# ---------- 단계 소요시간 분포 ----------

def test_stage_distribution_median_and_p90_are_respected():
    dist = StageDistribution(median_min=40, p90_min=90)
    rng = random.Random(0)
    samples = sorted(dist.sample(rng) for _ in range(5000))
    median = statistics.median(samples)
    p90 = samples[int(len(samples) * 0.9)]
    assert median == pytest.approx(40, rel=0.08)
    assert p90 == pytest.approx(90, rel=0.10)


def test_stage_distribution_rejects_p90_below_median():
    with pytest.raises(ValueError):
        StageDistribution(median_min=60, p90_min=30)


def test_default_distributions_cover_all_stages():
    expected = {
        "order_after_round", "medication", "education", "billing", "exit_prep",
        "guardian_delay", "baseline_cleaning_request_delay", "cleaning_response",
        "cleaning_extra_delay", "cleaning_general", "cleaning_terminal", "bed_make",
    }
    assert expected <= set(DEFAULT_STAGE_DISTRIBUTIONS)


# ---------- 퇴원 사례의 시각 순서 ----------

def test_discharge_timeline_is_causally_ordered(hospital):
    for d in hospital.discharges:
        t = d.actual
        assert t.order_confirmed_at <= t.medication_ready_at <= t.billing_done_at
        assert t.order_confirmed_at <= t.education_done_at
        assert t.departed_at >= max(t.billing_done_at, t.education_done_at, t.guardian_arrived_at)
        assert t.cleaning_started_at >= t.departed_at
        assert t.cleaning_done_at > t.cleaning_started_at
        assert t.bed_ready_at > t.cleaning_done_at


def test_discharge_decision_is_made_the_day_before(hospital):
    for d in hospital.discharges:
        assert d.decided_at.date() < d.actual.order_confirmed_at.date()


def test_discharges_are_spread_over_five_days_six_each(hospital):
    per_day = Counter(d.day for d in hospital.discharges)
    assert sorted(per_day) == [0, 1, 2, 3, 4]
    assert set(per_day.values()) == {6}


def test_no_bed_is_discharged_twice_on_the_same_day(hospital):
    keys = [(d.day, d.bed_id) for d in hospital.discharges]
    assert len(keys) == len(set(keys))


def test_baseline_cleaning_request_is_after_departure(hospital):
    for d in hospital.discharges:
        assert d.actual.baseline_cleaning_requested_at >= d.actual.departed_at


# ---------- 예외 상황 ----------

def test_guardian_delays(hospital):
    delayed = [d for d in hospital.discharges if d.guardian_delayed]
    assert len(delayed) == GeneratorConfig().guardian_delay_count
    for d in delayed:
        assert d.actual.guardian_arrived_at > d.guardian_expected_at
    for d in hospital.discharges:
        if not d.guardian_delayed:
            assert d.actual.guardian_arrived_at <= d.guardian_expected_at


def test_cleaning_delays_take_longer_than_response_alone(hospital):
    delayed = [d for d in hospital.discharges if d.cleaning_delayed]
    assert len(delayed) == GeneratorConfig().cleaning_delay_count
    assert all(d.cleaning_extra_delay_min > 0 for d in delayed)
    assert all(d.cleaning_extra_delay_min == 0 for d in hospital.discharges if not d.cleaning_delayed)


def test_unregistered_discharges_have_no_prior_registration(hospital):
    unregistered = [d for d in hospital.discharges if d.registered_at is None]
    assert len(unregistered) == GeneratorConfig().unregistered_count
    for d in hospital.discharges:
        if d.registered_at is not None:
            assert d.registered_at <= d.actual.order_confirmed_at


def test_admission_cancellations(hospital):
    cancelled = [a for a in hospital.admissions if a.cancelled_at is not None]
    assert len(cancelled) == GeneratorConfig().admission_cancel_count
    for a in cancelled:
        assert a.cancelled_at < a.requested_at.replace(hour=23, minute=59)


def test_isolation_discharges_need_terminal_cleaning_in_isolation_rooms(hospital):
    rooms = {r.id: r for r in hospital.rooms}
    beds = {b.id: b for b in hospital.beds}
    iso = [d for d in hospital.discharges if d.patient.isolation_order]
    assert len(iso) == GeneratorConfig().isolation_discharge_count
    for d in hospital.discharges:
        room = rooms[beds[d.bed_id].room_id]
        if d.patient.isolation_order:
            assert d.cleaning_type is CleaningType.TERMINAL
            assert room.room_type in {RoomType.NEGATIVE_PRESSURE, RoomType.SINGLE}
            if IsolationType.AIRBORNE in d.patient.precautions:  # 공기주의는 음압격리실만 (LP-17)
                assert room.room_type is RoomType.NEGATIVE_PRESSURE
        else:
            assert d.cleaning_type is CleaningType.GENERAL


@pytest.mark.parametrize("seed", range(0, 30))
def test_isolation_patients_follow_organism_precaution_table(seed):
    from turnaround.sim.models import ORGANISM_PRECAUTIONS
    h = generate(seed=seed)
    patients = [d.patient for d in h.discharges] + [a.patient for a in h.admissions]
    for p in patients:
        if p.isolation_order:
            assert p.organism in ORGANISM_PRECAUTIONS
            assert set(p.precautions) == set(ORGANISM_PRECAUTIONS[p.organism])
        else:
            assert p.precautions == () and p.organism is None


@pytest.mark.parametrize("seed", range(0, 30))
def test_turnover_requirements_follow_precautions(seed):
    """비말·공기주의 퇴실은 병실 공기순환 횟수로 정한 환기 대기 뒤 청소, C. difficile은 아포 대응 소독 (LP-23)."""
    from turnaround.rules import aerosol_wait_min
    h = generate(seed=seed)
    ach = {b.id: r.air_changes_per_hour for r in h.rooms for b in h.beds if b.room_id == r.id}
    for d in h.discharges:
        aerosol = bool({IsolationType.AIRBORNE, IsolationType.DROPLET} & set(d.patient.precautions))
        assert d.aerosol_wait_min == (aerosol_wait_min(ach[d.bed_id]) if aerosol else 0)
        assert d.actual.cleaning_started_at >= d.actual.departed_at + timedelta(minutes=d.aerosol_wait_min)
        assert d.sporicidal == (d.patient.organism == "CDIFF")


def test_patient_gender_matches_shared_room_gender(hospital):
    rooms = {r.id: r for r in hospital.rooms}
    beds = {b.id: b for b in hospital.beds}
    for d in hospital.discharges:
        room = rooms[beds[d.bed_id].room_id]
        if room.gender is not None:
            assert d.patient.gender == room.gender


# ---------- 담당 검토로 가야 할 입원 대기 사례 ----------

def test_admissions_include_review_cases(hospital):
    adm = hospital.admissions
    assert any(a.patient.isolation_order for a in adm)
    assert any(a.patient.age < 15 for a in adm)
    assert any(a.from_icu for a in adm)
    assert any(a.wants_single_room for a in adm)
    assert any(a.patient.gender is None for a in adm)  # 입력 누락
    assert any(IsolationType.DROPLET in a.patient.precautions for a in adm)  # 비말주의
    pre = [a for a in adm if a.patient.precaution_status and a.patient.precaution_status.value == "preemptive"]
    assert pre and all(a.patient.isolation_ordered_by == "ipc" for a in pre)  # 선제 격리는 감염관리실
    assert any(a.integrated_care_requested and a.integrated_care_consent is None for a in adm)  # 동의 미확인
    for a in adm:
        if a.wants_single_room:
            assert a.single_room_reason == "preference"
        if a.patient.isolation_order:
            assert a.single_room_reason == "isolation"  # 격리 사유 1인실은 차액 동의 대상 아님 (LP-22)


def test_same_name_pair_exists_between_admission_and_inpatient(hospital):
    inpatient_names = {d.patient.name for d in hospital.discharges}
    assert any(a.patient.name in inpatient_names for a in hospital.admissions)


def test_admissions_spread_over_five_days(hospital):
    assert {a.day for a in hospital.admissions} <= {0, 1, 2, 3, 4}
    assert len({a.day for a in hospital.admissions}) == 5


# ---------- 설정 ----------

def test_config_counts_are_validated():
    with pytest.raises(ValueError):
        generate(seed=1, config=GeneratorConfig(guardian_delay_count=31))


@pytest.mark.parametrize("seed", range(0, 20))
def test_discharge_rx_stages_are_ordered_and_revision_rate_is_15pct(seed):
    """DAT-7 퇴원약: 처방 발행 → (수정) → 조제 → 병동 도착. 수정 비율은 합성 가정 15%."""
    h = generate(seed=seed)
    revised = [d for d in h.discharges if d.actual.rx_revised_at is not None]
    assert len(revised) == round(len(h.discharges) * 0.15)
    for d in h.discharges:
        t = d.actual
        assert t.order_confirmed_at <= t.rx_issued_at < t.medication_ready_at
        assert (t.rx_revised_at or t.rx_issued_at) <= t.rx_dispensed_at <= t.medication_ready_at
        assert (d.rx_revision_reason is not None) == (t.rx_revised_at is not None)
