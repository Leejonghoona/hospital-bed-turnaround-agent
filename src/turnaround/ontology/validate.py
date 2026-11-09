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


def _cache_sparql_translation() -> None:
    """pySHACL은 SPARQL 제약 문자열을 호출마다 다시 해석한다(검사 한 번 약 0.4초). 해석 결과(준비된 질의)를 재사용한다.
    rdflib의 준비된 질의(prepareQuery)와 같은 방식이라 결과는 같다."""
    from rdflib.plugins.sparql import processor as _p
    if getattr(_p.SPARQLProcessor.query, "_turnaround_cached", False):
        return
    orig = _p.SPARQLProcessor.query
    cache: dict = {}

    def query(self, strOrQuery, initBindings=None, initNs=None, base=None, DEBUG=False):
        if isinstance(strOrQuery, str):
            k = (strOrQuery, base, tuple(sorted((str(a), str(b)) for a, b in (initNs or {}).items())))
            q = cache.get(k)
            if q is None:
                q = cache[k] = _p.translateQuery(_p.parseQuery(strOrQuery), base, initNs)
            strOrQuery = q
        return orig(self, strOrQuery, initBindings, initNs, base, DEBUG)

    query._turnaround_cached = True
    _p.SPARQLProcessor.query = query


_cache_sparql_translation()
_DIR = Path(__file__).resolve().parent
_METRIC = {"terminal_cleaning_missing": "S2", "commit_before_disinfection": "S2", "vacant_before_disinfection": "S2",
           "cleaning_before_aerosol_wait": "S2", "sporicidal_missing": "S2"}


@lru_cache(maxsize=1)
def load_shapes() -> Graph:
    return Graph().parse(_DIR / "shapes.ttl", format="turtle")


_CACHE: dict[str, list[Violation]] = {}
_CACHE_MAX = 4096


def validate(rooms: Iterable[Room], beds: Iterable[Bed], occupants: Mapping[str, Patient],
             turnovers: Iterable[BedTurnover] = (), extra_triples: Iterable[tuple] = ()) -> list[Violation]:
    """같은 입력이면 같은 결과다. pySHACL이 SPARQL 제약을 호출마다 다시 해석해 느리므로 입력 기준으로 기억한다."""
    rooms, beds, turnovers, extra_triples = list(rooms), list(beds), list(turnovers), list(extra_triples)
    key = "|".join([
        ";".join(r.model_dump_json() for r in rooms), ";".join(b.model_dump_json() for b in beds),
        ";".join(f"{k}={v.model_dump_json()}" for k, v in sorted(occupants.items())),
        ";".join(repr(t) for t in turnovers), ";".join(repr(t) for t in extra_triples)])
    hit = _CACHE.get(key)
    if hit is None:
        hit = _validate(rooms, beds, occupants, turnovers, extra_triples)
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = hit
    return list(hit)


def _validate(rooms, beds, occupants, turnovers, extra_triples) -> list[Violation]:
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
