"""FHIR R4 경계 어댑터 (EVT-7). 대응표: DECISIONS.md 2026-11-08, 2026-11-12."""

import hashlib
import json
from datetime import datetime

import pytest

from turnaround.events import NoiseConfig, hospital_to_events
from turnaround.events.models import Event, EventType
from turnaround.fhir import adapter as A
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
    Bundle.model_validate(events_to_bundle(ev[:120]))  # Encounter, Location, Task, Patient, Flag 포함
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


# ---------- 사건 종류별 표준 리소스 (DECISIONS.md 2026-11-12 FHIR 어댑터 표준 의미로 정리) ----------

_T0 = datetime(2026, 3, 2, 9)
_PATIENT = {"patient_id": "SYN-P0001", "name": "가나다", "gender": "F", "age": 61, "department": "호흡기내과",
            "isolation_order": True, "isolation_type": "airborne", "precautions": ["airborne"], "organism": "MTB",
            "precaution_status": "confirmed", "isolation_ordered_by": "ipc", "immunocompromised": False,
            "open_wound": False}
_BED = "W71-R03-B1"


def _e(i: int, case: str, t: EventType, source: str = "his", **payload) -> Event:
    at = _T0.replace(minute=i)
    return Event(event_id=f"{case}:{i}:{t.value}", case_id=case, type=t, occurred_at=at, observed_at=at,
                 source=source, payload=payload)


def _stay() -> list[Event]:
    """한 병상의 퇴원 → 청소 → 다음 입원, 격리 오더, 사람 할 일, 병실 사용 중지, 감사 기록."""
    T = EventType
    return [_e(0, "D1", T.DISCHARGE_DECIDED, bed_id=_BED, **_PATIENT),
            _e(1, "D1", T.DISCHARGE_REGISTERED, bed_id=_BED),
            _e(2, "D1", T.PATIENT_DEPARTED, bed_id=_BED),
            _e(3, "D1", T.CLEANING_REQUESTED, "human", bed_id=_BED, cleaning_type="terminal"),
            _e(4, "D1", T.CLEANING_STARTED, "human", bed_id=_BED),
            _e(5, "D1", T.CLEANING_DONE, "human", bed_id=_BED, cleaning_type="terminal"),
            _e(6, "D1", T.BED_READY, "human", bed_id=_BED),
            _e(7, "A1", T.ADMISSION_REQUESTED, preferred_ward_id="W71", **{**_PATIENT, "patient_id": "SYN-P0002"}),
            _e(8, "A1", T.MATCH_COMMITTED, "gateway", bed_id=_BED, proposal_id="p1"),
            _e(9, "A1", T.HUMAN_TASK_CREATED, "gateway", kind="fee_consent", assignee="admission_office"),
            _e(10, "A1", T.TASK_ESCALATED, "saga", task_id="A1:9:human_task_created", to="admission_lead"),
            _e(11, "A1", T.HUMAN_TASK_DONE, "human", task_id="A1:9:human_task_created", by="u1"),
            _e(12, "A1", T.PATIENT_ARRIVED, "human", bed_id=_BED),
            _e(13, "A1", T.ISOLATION_ORDER_ADDED, organism="MTB", isolation_type="airborne",
               precautions=["airborne"], patient_id="SYN-P0002"),
            _e(14, "W71-R01", T.ROOM_OUT_OF_SERVICE, "equipment", room_id="W71-R01", reason="equipment_failure"),
            _e(15, "D1", T.GATEWAY_DECISION, "gateway", decision="committed", reasons=[])]


def _versions(bundle, rt):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == rt]


def _primary(bundle, event_id):
    for e in bundle["entry"]:
        if any(x["url"] == A.SD + "event-id" and x["valueString"] == event_id for x in e.get("extension", [])):
            return e["resource"]
    raise KeyError(event_id)


def _by_type(t: EventType) -> Event:
    return next(e for e in _stay() if e.type is t)


def test_hand_built_stay_round_trips():
    ev = _stay()
    assert [e.model_dump() for e in bundle_to_events(events_to_bundle(ev))] == [e.model_dump() for e in ev]


def test_admission_transfer_discharge_are_encounters():
    T = EventType
    b = events_to_bundle(_stay())
    p = lambda t: _primary(b, _by_type(t).event_id)  # noqa: E731
    dec, dep = p(T.DISCHARGE_DECIDED), p(T.PATIENT_DEPARTED)
    assert dec["resourceType"] == dep["resourceType"] == "Encounter"
    assert dec["class"]["code"] == "IMP" and dec["id"] == dep["id"]  # 사례 하나 = Encounter 하나
    assert (dec["status"], dec["location"][-1]["status"]) == ("in-progress", "active")
    assert (dep["status"], dep["location"][-1]["status"]) == ("finished", "completed")
    adm, com, arr = p(T.ADMISSION_REQUESTED), p(T.MATCH_COMMITTED), p(T.PATIENT_ARRIVED)
    assert adm["status"] == "planned" and adm["location"][0]["physicalType"]["coding"][0]["code"] == "wa"
    assert com["location"][-1]["status"] == "reserved"
    assert (arr["status"], arr["location"][-1]["status"]) == ("in-progress", "active")
    assert arr["location"][-1]["location"]["identifier"] == {"system": A.BED_ID_SYSTEM, "value": _BED}


def test_bed_status_uses_v2_0116_operational_status():
    b = events_to_bundle(_stay())
    bed = [r for r in _versions(b, "Location") if r["id"] == _BED]
    assert all(r["operationalStatus"]["system"] == "http://terminology.hl7.org/CodeSystem/v2-0116" for r in bed)
    # 재원(O) → 퇴실(K, 청소 필요) → 청소 요청(K) → 청소 중(H) → 청소 끝(H) → 사용 가능(U) → 입실(O)
    assert [r["operationalStatus"]["code"] for r in bed] == ["O", "O", "K", "K", "H", "H", "U", "O"]
    assert [r["meta"]["versionId"] for r in bed] == [str(i) for i in range(1, 9)]
    room = next(r for r in _versions(b, "Location") if r["id"] == "W71-R01")
    assert (room["status"], room["operationalStatus"]["code"]) == ("suspended", "C")


def test_one_patient_per_person_with_identifier():
    b = events_to_bundle(_stay())
    pats = _versions(b, "Patient")
    ids = {p["identifier"][0]["value"]: p["id"] for p in pats}
    assert set(ids) == {"SYN-P0001", "SYN-P0002"} and len(set(ids.values())) == 2
    assert all(p["identifier"][0]["system"] == A.PATIENT_ID_SYSTEM for p in pats)
    d1 = {r["subject"]["reference"] for r in _versions(b, "Encounter") if r["identifier"][0]["value"] == "D1"}
    assert d1 == {f"Patient/{ids['SYN-P0001']}"}  # 같은 환자의 Encounter 판은 모두 같은 Patient


def test_same_patient_across_seed_events_is_not_split():
    ev = hospital_to_events(generate(seed=0), seed=0)
    pats = _versions(events_to_bundle(ev), "Patient")
    people = {e.payload["patient_id"] for e in ev if "patient_id" in e.payload}
    assert len({p["id"] for p in pats}) == len(people)


def test_isolation_order_and_organism():
    b = events_to_bundle(_stay())
    sr = _primary(b, _by_type(EventType.ISOLATION_ORDER_ADDED).event_id)
    assert (sr["resourceType"], sr["status"], sr["intent"]) == ("ServiceRequest", "active", "order")
    assert [c["code"] for c in sr["code"]["coding"]] == ["361235007", "409524006"]  # 격리 시행 + 공기주의
    obs = _versions(b, "Observation")
    assert obs and all(o["valueCodeableConcept"]["coding"][0] == {
        "system": A.SNOMED, "code": "113861009", "display": "Mycobacterium tuberculosis"} for o in obs)
    assert [o["status"] for o in obs] == ["final", "final", "registered"]  # 판정 confirmed 두 번, 판정 없는 오더 한 번
    assert {f["code"]["coding"][0]["code"] for f in _versions(b, "Flag")} == {"409524006"}


def test_human_task_lifecycle():
    b = events_to_bundle(_stay())
    tasks: dict[str, list] = {}
    for r in _versions(b, "Task"):
        if "identifier" in r:
            tasks.setdefault(r["identifier"][0]["value"], []).append(r)
    work = tasks["A1:9:human_task_created"]
    assert [t["status"] for t in work] == ["requested", "requested", "completed"]
    assert work[1]["priority"] == "urgent" and work[1]["owner"]["display"] == "admission_lead"
    assert [t["status"] for t in tasks[f"cleaning:D1:{_BED}"]] == ["requested", "in-progress", "completed"]
    rec = _primary(b, _stay()[-1].event_id)  # 감사 기록: 사건마다 Task 하나, payload는 input
    assert rec["resourceType"] == "Task" and rec["status"] == "completed" and "identifier" not in rec
    assert {i["type"]["text"] for i in rec["input"]} == {"decision", "reasons"}


def test_extension_urls_are_structure_definitions():
    h = generate(seed=0)
    docs = [events_to_bundle(_stay() + hospital_to_events(h, seed=0)), layout_to_locations(h.wards, h.rooms, h.beds)]
    urls, systems = set(), set()

    def walk(x):
        if isinstance(x, dict):
            urls.update(e["url"] for e in x.get("extension", []) if ":" in e["url"])  # 복합 확장의 하위 이름(key 등)은 뺀다
            if isinstance(x.get("system"), str):
                systems.add(x["system"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(docs)
    assert urls and all(u.startswith("https://wardflow.example/fhir/StructureDefinition/") for u in urls)
    assert not any(s.startswith("urn:") for s in systems)


def test_non_local_timezone_is_refused():
    b = events_to_bundle(_stay()[:1])
    for e in b["entry"]:
        e["resource"]["meta"]["lastUpdated"] = e["resource"]["meta"]["lastUpdated"].replace("+09:00", "Z")
    with pytest.raises(ValueError, match="09:00"):
        bundle_to_events(b)


def test_every_event_type_round_trips():
    """모든 사건 종류: 표준 대응이 있으면 그 리소스로, 없으면 기록 Task로 나가고 왕복이 같다."""
    evs = []
    for i, t in enumerate(EventType):
        evs.append(_e(i % 60, f"C{i}", t, bed_id="W72-R04-B1", task_id="X:1", to="ward", room_id="W72-R04",
                      destination="icu", discharge_type="ama", extra={"a": [1, 2.5, None]}, flag=None))
        evs.append(_e(i % 60, f"P{i}", t, **{**_PATIENT, "isolation_order": None, "gender": "U"}))
    back = bundle_to_events(events_to_bundle(evs))
    assert [e.model_dump() for e in back] == [e.model_dump() for e in evs]


def test_full_bundle_passes_r4_structure_validation():
    """사건 종류별 리소스 전체를 R4 기본 구조로 검증 (fhir.resources R4B 모듈, 없으면 건너뜀)."""
    pytest.importorskip("fhir.resources.R4B")
    from fhir.resources.R4B.bundle import Bundle
    from turnaround.sim import GeneratorConfig
    ev = _stay() + hospital_to_events(generate(seed=1, config=GeneratorConfig.realistic()), seed=1)
    b = Bundle.model_validate(events_to_bundle(ev))
    assert {e.resource.get_resource_type() for e in b.entry} >= {
        "Encounter", "Location", "Patient", "Flag", "ServiceRequest", "Observation", "Task"}
