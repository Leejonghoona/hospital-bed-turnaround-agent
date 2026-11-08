"""경량 온톨로지와 SHACL 제약 테스트 (구현 우선순위 6단계의 최소판, 4단계 게이트웨이용).

같은 규칙을 독립 파이썬 검사기(evaluation.invariants)와 SHACL이 각각 구현한다.
두 구현이 무작위 상태에서 같은 위반을 내는지 교차 확인한다.
"""

import random
from datetime import datetime, timedelta

import pytest
from rdflib import RDF, RDFS, URIRef

from turnaround.evaluation.invariants import BedTurnover, check_assignment, check_turnover
from turnaround.ontology import TA, build_graph, load_concepts, validate
from turnaround.sim import CleaningType, Gender, IsolationType, Patient, generate

H = generate(seed=0)
T0 = datetime(2026, 3, 2, 12, 0)


def pt(pid, name="가나다", gender=Gender.FEMALE, iso=None):
    return Patient(id=f"SYN-T{pid}", name=name, gender=gender, age=50, department="소화기내과",
                   isolation_order=iso is not None, isolation_type=iso)


def codes(vs):
    return sorted(v.code for v in vs)


# ---------- 개념 계층 ----------

def test_concept_hierarchy_matches_design_example():
    g = load_concepts()
    assert (TA.NegativePressureRoom, RDFS.subClassOf, TA.SingleRoom) in g
    assert (TA.SingleRoom, RDFS.subClassOf, TA.Room) in g
    assert (TA.AirborneIsolationPatient, RDFS.subClassOf, TA.IsolationPatient) in g
    assert (TA.SuspectedTuberculosisPatient, RDFS.subClassOf, TA.AirborneIsolationPatient) in g


# ---------- S1 배정 기준 ----------

def test_valid_state_conforms():
    occ = {"W71-R01-B1": pt(1, iso=IsolationType.AIRBORNE), "W71-R02-B1": pt(2, iso=IsolationType.CONTACT),
           "W71-R04-B1": pt(3, name="김가온"), "W71-R04-B2": pt(4, name="이나라"), "W71-R03-B1": pt(5, gender=Gender.MALE)}
    assert validate(H.rooms, H.beds, occ) == []


@pytest.mark.parametrize("occ,expected", [
    ({"W71-R03-B1": pt(1, gender=Gender.MALE, iso=IsolationType.CONTACT)}, ["isolation_room"]),
    ({"W71-R02-B1": pt(1, iso=IsolationType.AIRBORNE)}, ["airborne_needs_negative_pressure"]),
    ({"W71-R03-B1": pt(1, gender=Gender.FEMALE)}, ["gender_room"]),
    ({"W71-R04-B1": pt(1, gender=None)}, ["gender_missing"]),
    ({"W71-R04-B1": pt(1, name="김가온"), "W71-R04-B3": pt(2, name="김가온")}, ["same_name_room"]),
    ({"W99-R01-B1": pt(1)}, ["unknown_bed"]),
])
def test_each_shacl_mutant_is_caught(occ, expected):
    assert codes(validate(H.rooms, H.beds, occ)) == expected


def test_new_disease_is_one_line_in_concepts():
    """설계: 새 질환은 '공기감염 격리 필요'의 하위 개념으로 한 줄만 추가하면 제약이 따라온다."""
    p = pt(1, iso=None)
    extra = [(URIRef(TA + "patient/SYN-T1"), RDF.type, TA.SuspectedTuberculosisPatient)]
    assert codes(validate(H.rooms, H.beds, {"W71-R02-B1": p}, extra_triples=extra)) == ["airborne_needs_negative_pressure"]
    assert validate(H.rooms, H.beds, {"W71-R01-B1": p}, extra_triples=extra) == []


# ---------- S2 종말소독 ----------

def turnover(**kw):
    base = dict(bed_id="W71-R02-B1", previous_isolation=True, cleaning_type=CleaningType.TERMINAL,
                cleaning_done_at=T0, committed_at=T0 + timedelta(minutes=30), vacant_at=T0 + timedelta(minutes=10))
    base.update(kw)
    return BedTurnover(**base)


@pytest.mark.parametrize("kw,expected", [
    ({}, []),
    ({"cleaning_type": CleaningType.GENERAL}, ["terminal_cleaning_missing"]),
    ({"committed_at": T0 - timedelta(minutes=1)}, ["commit_before_disinfection"]),
    ({"vacant_at": T0 - timedelta(minutes=1)}, ["vacant_before_disinfection"]),
    ({"cleaning_done_at": None}, ["commit_before_disinfection", "vacant_before_disinfection"]),
    ({"previous_isolation": False, "cleaning_type": CleaningType.GENERAL}, []),
])
def test_turnover_shapes(kw, expected):
    assert codes(validate(H.rooms, H.beds, {}, turnovers=[turnover(**kw)])) == expected


# ---------- 교차 확인: 독립 검사기와 SHACL이 같은 판정 ----------

def _random_state(rng):
    beds = rng.sample([b.id for b in H.beds], rng.randint(1, 8))
    names = ["김가온", "이나라", "박하늘"]
    occ = {}
    for i, bed in enumerate(beds):
        occ[bed] = pt(i, name=rng.choice(names), gender=rng.choice([Gender.MALE, Gender.FEMALE, None]),
                      iso=rng.choice([None, None, IsolationType.CONTACT, IsolationType.AIRBORNE]))
    if rng.random() < 0.1:
        occ["W99-R01-B1"] = pt(99)
    return occ


def _random_turnover(rng):
    done = rng.choice([None, T0])
    return BedTurnover(bed_id="W72-R01-B1", previous_isolation=rng.random() < 0.7,
                       cleaning_type=rng.choice([CleaningType.TERMINAL, CleaningType.GENERAL, None]),
                       cleaning_done_at=done,
                       committed_at=rng.choice([None, T0 - timedelta(minutes=5), T0 + timedelta(minutes=5)]),
                       vacant_at=rng.choice([None, T0 - timedelta(minutes=5), T0 + timedelta(minutes=5)]))


@pytest.mark.parametrize("seed", range(60))
def test_shacl_agrees_with_independent_checker(seed):
    rng = random.Random(seed)
    occ = _random_state(rng)
    tos = [_random_turnover(rng)]
    expected = codes(check_assignment(H.rooms, H.beds, occ) + [v for t in tos for v in check_turnover(t)])
    assert codes(validate(H.rooms, H.beds, occ, turnovers=tos)) == expected
