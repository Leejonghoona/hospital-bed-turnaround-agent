"""pySHACL 검증. 위반을 독립 검사기와 같은 코드 체계(Violation)로 돌려준다."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

from pyshacl import validate as shacl_validate
from rdflib import RDFS, Graph, Namespace

from turnaround.evaluation.invariants import BedTurnover, Violation
from turnaround.sim.models import Bed, Patient, Room

from .graph import build_graph

SH = Namespace("http://www.w3.org/ns/shacl#")
_DIR = Path(__file__).resolve().parent
_METRIC = {"terminal_cleaning_missing": "S2", "commit_before_disinfection": "S2", "vacant_before_disinfection": "S2",
           "cleaning_before_aerosol_wait": "S2", "sporicidal_missing": "S2"}


@lru_cache(maxsize=1)
def load_shapes() -> Graph:
    return Graph().parse(_DIR / "shapes.ttl", format="turtle")


def validate(rooms: Iterable[Room], beds: Iterable[Bed], occupants: Mapping[str, Patient],
             turnovers: Iterable[BedTurnover] = (), extra_triples: Iterable[tuple] = ()) -> list[Violation]:
    data = build_graph(rooms, beds, occupants, turnovers, extra_triples)
    shapes = load_shapes()
    _, results, _ = shacl_validate(data, shacl_graph=shapes, advanced=True, inference="none",
                                   allow_warnings=True)
    found: list[tuple[str, str, str]] = []  # (코드, 초점 노드, 메시지)
    for r in results.subjects(SH.resultSeverity, SH.Violation):
        shape = results.value(r, SH.sourceShape)
        code = shapes.value(shape, RDFS.comment)
        if code is None:  # 속성 형상이면 부모 노드 형상의 코드
            parent = next(shapes.subjects(SH.property, shape), None)
            code = shapes.value(parent, RDFS.comment)
        value = results.value(r, SH.value)
        found.append((str(code), str(results.value(r, SH.focusNode)),
                      str(results.value(r, SH.resultMessage)) + (f" ({value})" if value is not None else "")))
    # 공기감염 환자가 다인실이면 격리 병실 위반도 함께 나온다. 같은 환자는 더 구체적인 위반 하나로 남긴다.
    airborne = {focus for code, focus, _ in found if code == "airborne_needs_negative_pressure"}
    return [Violation(code, _METRIC.get(code, "S1"), f"{focus} {msg}") for code, focus, msg in found
            if not (code == "isolation_room" and focus in airborne)]
