"""합성 병원 상태 → RDF 그래프. 개념 계층(concepts.ttl)을 함께 넣어 하위 개념 사슬을 SHACL이 따라가게 한다."""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

from rdflib import RDF, XSD, Graph, Literal, Namespace, URIRef

from turnaround.evaluation.invariants import BedTurnover
from turnaround.sim.models import Bed, IsolationType, Patient, Room, RoomType

TA = Namespace("urn:turnaround:")
_DIR = Path(__file__).resolve().parent
_ROOM_CLASS = {RoomType.NEGATIVE_PRESSURE: TA.NegativePressureRoom, RoomType.SINGLE: TA.SingleRoom,
               RoomType.MULTI: TA.MultiRoom}
_ISO_CLASS = {IsolationType.AIRBORNE: TA.AirborneIsolationPatient, IsolationType.CONTACT: TA.ContactIsolationPatient,
              IsolationType.DROPLET: TA.DropletIsolationPatient}
_AEROSOL = {IsolationType.AIRBORNE, IsolationType.DROPLET}


@lru_cache(maxsize=1)
def _concepts_ttl() -> str:
    return (_DIR / "concepts.ttl").read_text(encoding="utf-8")


def load_concepts() -> Graph:
    return Graph().parse(data=_concepts_ttl(), format="turtle")


def node(kind: str, ident: str) -> URIRef:
    return URIRef(f"{TA}{kind}/{ident}")


def build_graph(rooms: Iterable[Room], beds: Iterable[Bed], occupants: Mapping[str, Patient],
                turnovers: Iterable[BedTurnover] = (), extra_triples: Iterable[tuple] = (),
                aerosol_wait_min: int = 104) -> Graph:
    g = load_concepts()
    for r in rooms:
        rn = node("room", r.id)
        g.add((rn, RDF.type, _ROOM_CLASS[r.room_type]))
        g.add((rn, TA.capacity, Literal(r.capacity)))
        if r.gender is not None:
            g.add((rn, TA.gender, Literal(r.gender.value)))
        if r.cohort_key is not None:
            g.add((rn, RDF.type, TA.CohortRoom))
            g.add((rn, TA.cohortKey, Literal(r.cohort_key)))
    for b in beds:
        g.add((node("bed", b.id), RDF.type, TA.Bed))
        g.add((node("bed", b.id), TA.inRoom, node("room", b.room_id)))
    for bed_id, p in occupants.items():
        pn = node("patient", p.id)
        g.add((pn, RDF.type, TA.Patient))
        if p.isolation_order:
            g.add((pn, RDF.type, TA.IsolationPatient))
            for prec in p.precautions:  # 주의가 여러 개면 클래스도 여러 개 (수두 = 공기 + 접촉)
                g.add((pn, RDF.type, _ISO_CLASS[prec]))
        if p.organism is not None:
            g.add((pn, TA.organism, Literal(p.organism)))
        if p.precaution_status is not None:
            g.add((pn, TA.precautionStatus, Literal(p.precaution_status.value)))
        if p.immunocompromised:
            g.add((pn, TA.immunocompromised, Literal(True)))
        if p.open_wound:
            g.add((pn, TA.openWound, Literal(True)))
        if p.gender is not None:
            g.add((pn, TA.gender, Literal(p.gender.value)))
        g.add((pn, TA.name, Literal(p.name)))
        g.add((pn, TA.assignedTo, node("bed", bed_id)))
    for i, t in enumerate(turnovers):
        tn = node("turnover", f"{t.bed_id}-{i}")
        g.add((tn, RDF.type, TA.BedTurnover))
        g.add((tn, TA.bed, node("bed", t.bed_id)))
        g.add((tn, TA.previousIsolation, Literal(t.previous_isolation)))
        if t.cleaning_type is not None:
            g.add((tn, TA.cleaningType, Literal(t.cleaning_type.value)))
        for pred, val in ((TA.cleaningDoneAt, t.cleaning_done_at), (TA.committedAt, t.committed_at),
                          (TA.vacantAt, t.vacant_at), (TA.cleaningStartedAt, t.cleaning_started_at)):
            if val is not None:
                g.add((tn, pred, Literal(val, datatype=XSD.dateTime)))
        if t.previous_organism is not None:
            g.add((tn, TA.previousOrganism, Literal(t.previous_organism)))
        if t.sporicidal_done:
            g.add((tn, TA.sporicidalDone, Literal(True)))
        # 환기 대기가 필요한 퇴실이면 "청소 시작 가능 시각"을 정책값으로 미리 계산해 둔다 (SPARQL 날짜 연산 회피)
        if {IsolationType(x) for x in t.previous_precautions} & _AEROSOL and t.departed_at is not None:
            g.add((tn, TA.aerosolReadyAt, Literal(t.departed_at + timedelta(minutes=t.aerosol_wait_min if t.aerosol_wait_min is not None else aerosol_wait_min),
                                                   datatype=XSD.dateTime)))
    for triple in extra_triples:
        g.add(triple)
    return g
