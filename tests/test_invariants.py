"""독립 불변 조건 검사기 테스트 (평가표 S1, S2, G4).

검사기는 생성기, 배정 도구, SHACL과 별개로 짠 순수 파이썬 코드다. 위반을 일부러 넣은
상태(뮤턴트)를 하나씩 만들어 검사기가 정확한 코드로 잡는지 확인한다.
"""

from datetime import datetime, timedelta

import pytest

from turnaround.evaluation.invariants import (
    BedTurnover,
    check_assignment,
    check_turnover,
    zero_event_upper_bound,
)
from turnaround.sim import CleaningType, Gender, IsolationType, Patient, generate

H = generate(seed=0)
T0 = datetime(2026, 3, 2, 12, 0)


def pt(pid, name="가나다", gender=Gender.FEMALE, iso=None):
    return Patient(id=f"SYN-T{pid}", name=name, gender=gender, age=50, department="소화기내과",
                   isolation_order=iso is not None, isolation_type=iso)


def codes(violations):
    return sorted(v.code for v in violations)


# ---------- S1 배정 기준 ----------

def test_valid_assignment_has_no_violation():
    occ = {
        "W71-R01-B1": pt(1, iso=IsolationType.AIRBORNE),
        "W71-R02-B1": pt(2, iso=IsolationType.CONTACT),
        "W71-R04-B1": pt(3, name="김가온"),
        "W71-R04-B2": pt(4, name="이나라"),
        "W71-R03-B1": pt(5, gender=Gender.MALE),
    }
    assert check_assignment(H.rooms, H.beds, occ) == []


@pytest.mark.parametrize("occ,expected", [
    ({"W71-R03-B1": pt(1, gender=Gender.MALE, iso=IsolationType.CONTACT)}, ["isolation_room"]),
    ({"W71-R02-B1": pt(1, iso=IsolationType.AIRBORNE)}, ["airborne_needs_negative_pressure"]),
    ({"W71-R03-B1": pt(1, gender=Gender.FEMALE)}, ["gender_room"]),
    ({"W71-R04-B1": pt(1, gender=None)}, ["gender_missing"]),
    ({"W71-R04-B1": pt(1, name="김가온"), "W71-R04-B3": pt(2, name="김가온")}, ["same_name_room"]),
    ({"W99-R01-B1": pt(1)}, ["unknown_bed"]),
])
def test_each_assignment_mutant_is_caught(occ, expected):
    assert codes(check_assignment(H.rooms, H.beds, occ)) == expected


def test_same_name_in_different_rooms_is_allowed():
    occ = {"W71-R04-B1": pt(1, name="김가온"), "W71-R05-B1": pt(2, name="김가온")}
    assert check_assignment(H.rooms, H.beds, occ) == []


def test_single_room_gender_is_not_restricted():
    occ = {"W72-R01-B1": pt(1, gender=Gender.MALE), "W72-R02-B1": pt(2, gender=None)}
    assert check_assignment(H.rooms, H.beds, occ) == []


def test_all_violations_are_tagged_s1():
    occ = {"W71-R03-B1": pt(1, gender=Gender.FEMALE, iso=IsolationType.CONTACT)}
    vs = check_assignment(H.rooms, H.beds, occ)
    assert codes(vs) == ["gender_room", "isolation_room"]
    assert {v.metric for v in vs} == {"S1"}


# ---------- S2 종말소독 ----------

def turnover(**kw):
    base = dict(bed_id="W71-R02-B1", previous_isolation=True, cleaning_type=CleaningType.TERMINAL,
                cleaning_done_at=T0, committed_at=T0 + timedelta(minutes=30), vacant_at=T0 + timedelta(minutes=10))
    base.update(kw)
    return BedTurnover(**base)


def test_valid_terminal_turnover():
    assert check_turnover(turnover()) == []


@pytest.mark.parametrize("kw,expected", [
    ({"cleaning_type": CleaningType.GENERAL}, ["terminal_cleaning_missing"]),
    ({"cleaning_type": None, "cleaning_done_at": None, "vacant_at": None, "committed_at": None},
     ["terminal_cleaning_missing"]),
    ({"committed_at": T0 - timedelta(minutes=1)}, ["commit_before_disinfection"]),
    ({"vacant_at": T0 - timedelta(minutes=1)}, ["vacant_before_disinfection"]),
    ({"cleaning_done_at": None}, ["commit_before_disinfection", "vacant_before_disinfection"]),
])
def test_each_turnover_mutant_is_caught(kw, expected):
    vs = check_turnover(turnover(**kw))
    assert codes(vs) == expected
    assert {v.metric for v in vs} == {"S2"}


def test_non_isolation_turnover_is_not_checked_for_terminal():
    assert check_turnover(turnover(previous_isolation=False, cleaning_type=CleaningType.GENERAL)) == []


# ---------- 생성기와 교차 확인 (서로 다른 구현이 같은 규칙에 동의하는지) ----------

@pytest.mark.parametrize("seed", range(0, 50))
def test_generator_states_satisfy_independent_checker(seed):
    h = generate(seed=seed)
    for day in range(h.config.days):
        occ = {d.bed_id: d.patient for d in h.discharges if d.day == day}
        assert check_assignment(h.rooms, h.beds, occ) == [], (seed, day)
    for d in h.discharges:
        t = BedTurnover(bed_id=d.bed_id, previous_isolation=d.patient.isolation_order,
                        cleaning_type=d.cleaning_type, cleaning_done_at=d.actual.cleaning_done_at,
                        committed_at=None, vacant_at=d.actual.bed_ready_at)
        assert check_turnover(t) == [], (seed, d.id)


# ---------- 통계 표기 ----------

def test_zero_event_upper_bound_rule_of_three():
    assert zero_event_upper_bound(600) == pytest.approx(0.005)
    with pytest.raises(ValueError):
        zero_event_upper_bound(0)


# ---------- LP-16~20 전파경로 주의 체계 ----------
from _precaution_cases import ASSIGNMENT_CASES, TURNOVER_CASES  # noqa: E402


@pytest.mark.parametrize("name,rooms,occ,expected", ASSIGNMENT_CASES, ids=[c[0] for c in ASSIGNMENT_CASES])
def test_precaution_assignment_rules(name, rooms, occ, expected):
    assert codes(check_assignment(rooms, H.beds, occ)) == sorted(expected)


@pytest.mark.parametrize("name,t,expected", TURNOVER_CASES, ids=[c[0] for c in TURNOVER_CASES])
def test_precaution_turnover_rules(name, t, expected):
    vs = check_turnover(t)
    assert codes(vs) == sorted(expected)
    assert {v.metric for v in vs} <= {"S2"}
