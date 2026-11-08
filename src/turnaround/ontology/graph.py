"""합성 병원 상태 → RDF 그래프. 개념 계층(concepts.ttl)을 함께 넣어 하위 개념 사슬을 SHACL이 따라가게 한다."""

from __future__ import annotations

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
_ISO_CLASS = {IsolationType.AIRBORNE: TA.AirborneIsolationPatient, IsolationType.CONTACT: TA.ContactIsolationPatient}


@lru_cache(maxsize=1)
def _concepts_ttl() -> str:
    return (_DIR / "concepts.ttl").read_text(encoding="utf-8")


def load_concepts() -> Graph:
    return Graph().parse(data=_concepts_ttl(), format="turtle")


def node(kind: str, ident: str) -> URIRef:
    return URIRef(f"{TA}{kind}/{ident}")


def build_graph(rooms: Iterable[Room], beds: Iterable[Bed], occupants: Mapping[str, Patient],
                turnovers: Iterable[BedTurnover] = (), extra_triples: Iterable[tuple] = ()) -> Graph:
    g = load_concepts()
    for r in rooms:
        rn = node("room", r.id)
        g.add((rn, RDF.type, _ROOM_CLASS[r.room_type]))
        g.add((rn, TA.capacity, Literal(r.capacity)))
        if r.gender is not None:
            g.add((rn, TA.gender, Literal(r.gender.value)))
    for b in beds:
        g.add((node("bed", b.id), RDF.type, TA.Bed))
        g.add((node("bed", b.id), TA.inRoom, node("room", b.room_id)))
    for bed_id, p in occupants.items():
        pn = node("patient", p.id)
        g.add((pn, RDF.type, TA.Patient))
        if p.isolation_order:
            g.add((pn, RDF.type, _ISO_CLASS.get(p.isolation_type, TA.IsolationPatient)))
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
                          (TA.vacantAt, t.vacant_at)):
            if val is not None:
                g.add((tn, pred, Literal(val, datatype=XSD.dateTime)))
    for triple in extra_triples:
        g.add(triple)
    return g
