"""FHIR R4 경계 어댑터 (EVT-7). 대응표: DECISIONS.md 2026-11-08."""

import hashlib
import json

import pytest

from turnaround.events import NoiseConfig, hospital_to_events
from turnaround.fhir import bundle_to_events, events_to_bundle, layout_to_locations, locations_to_layout
from turnaround.sim import generate


def _hash(events) -> str:
    return hashlib.sha256(json.dumps([e.model_dump(mode="json") for e in events], ensure_ascii=False,
                                     sort_keys=True).encode()).hexdigest()


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_round_trip_is_identical(seed):
    ev = hospital_to_events(generate(seed=seed), seed=seed)
    assert _hash(bundle_to_events(events_to_bundle(ev))) == _hash(ev)


def test_round_trip_keeps_noise_semantics():
    """중복 도착, 순서 뒤바뀜(인지 지연), 완료 신호 누락이 그대로 남는다."""
    h = generate(seed=5)
    ev = hospital_to_events(h, seed=5, noise=NoiseConfig(duplicates=5, reorders=5, drops=2))
    back = bundle_to_events(events_to_bundle(ev))
    assert [e.model_dump() for e in back] == [e.model_dump() for e in ev]
    assert any(e.observed_at > e.occurred_at for e in back)


def test_layout_round_trip():
    h = generate(seed=0)
    assert locations_to_layout(layout_to_locations(h.wards, h.rooms, h.beds)) == (h.wards, h.rooms, h.beds)


def test_precautions_use_snomed_flags():
    ev = hospital_to_events(generate(seed=0), seed=0)
    b = events_to_bundle(ev)
    codes = {c["code"] for e in b["entry"] if e["resource"]["resourceType"] == "Flag"
             for c in e["resource"]["code"]["coding"]}
    assert codes and codes <= {"409524006", "409525007", "409529001"}


def test_snapshot_bundle_is_refused():
    with pytest.raises(ValueError, match="history"):
        bundle_to_events({"resourceType": "Bundle", "type": "collection", "entry": []})


def test_resources_pass_r4_structure_validation():
    """R4 기본 구조 검증 (fhir.resources R4B 모듈). KR Core 프로파일 검증은 하지 않는다."""
    r4 = pytest.importorskip("fhir.resources.R4B")
    from fhir.resources.R4B.bundle import Bundle
    from fhir.resources.R4B.location import Location
    h = generate(seed=0)
    ev = hospital_to_events(h, seed=0)
    Bundle.model_validate(events_to_bundle(ev[:120]))  # Task, Patient, Flag 포함
    for loc in layout_to_locations(h.wards, h.rooms, h.beds):
        Location.model_validate(loc)
    assert r4


@pytest.mark.slow
def test_dual_run_equivalence_seeds_0_199():
    """이중 실행: 생성기 → 이벤트, 생성기 → 이벤트 → FHIR → 어댑터 → 이벤트. 시드 200개에서 해시가 같다."""
    bad = []
    for seed in range(200):
        ev = hospital_to_events(generate(seed=seed), seed=seed)
        if _hash(bundle_to_events(events_to_bundle(ev))) != _hash(ev):
            bad.append(seed)
    assert bad == []


def test_concept_mappings_are_annotations_only():
    from rdflib import URIRef
    from rdflib.namespace import SKOS
    from turnaround.ontology import load_concepts
    g = load_concepts()
    m = {str(o) for o in g.objects(URIRef("urn:turnaround:AirborneIsolationPatient"), SKOS.exactMatch)}
    assert m == {"http://snomed.info/id/409524006"}
    assert not list(g.objects(URIRef("urn:turnaround:NegativePressureRoom"), SKOS.exactMatch))


def test_graph_from_fhir_layout_passes_same_shacl():
    """FHIR 번들에서 되살린 배치로 만든 그래프가 원래 배치와 같은 SHACL 결과를 낸다."""
    from turnaround.ontology import validate
    from turnaround.sim.models import Gender, Patient
    h = generate(seed=0)
    _, rooms, beds = locations_to_layout(layout_to_locations(h.wards, h.rooms, h.beds))
    occ = {"W71-R03-B1": Patient(id="X", name="가", gender=Gender.FEMALE, age=50, department="소화기내과")}
    a = sorted(v.code for v in validate(h.rooms, h.beds, occ))
    b = sorted(v.code for v in validate(rooms, beds, occ))
    assert a == b == ["gender_room"]
