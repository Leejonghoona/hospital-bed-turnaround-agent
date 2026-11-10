"""핵심 온톨로지 선언 읽기와 역량 질문 (로드맵 07 M6, docs/검토/온톨로지_검토.md).

- `load_core()`: core.ttl(객체 종류·속성·관계·사건 종류·행동 종류·역할·근거 등급) + concepts.ttl(개념 계층).
- `event_kinds()`: 매핑이 쓰는 사건 사전(사건 이름 → 저장소 이벤트 타입, 원천, 실어 오는 속성, 정의).
- `instance_graph(gw, events, now)`: 지금 상태를 선언된 어휘로 그린 인스턴스 그래프.
- `cq1`~`cq7`: 온톨로지가 답해야 하는 역량 질문. 답은 SPARQL(또는 안전 제약은 SHACL)로 낸다.
  선언과 코드가 어긋나지 않는지는 시험이 묶는다(같은 질문에 코드와 같은 답).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from rdflib import RDF, RDFS, Graph, Literal, Namespace

from turnaround.events import Event, EventType

from .graph import TA, load_concepts, node

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _core_ttl() -> str:
    return (_DIR / "core.ttl").read_text(encoding="utf-8")


def load_core() -> Graph:
    g = load_concepts()
    g.parse(data=_core_ttl(), format="turtle")
    return g


@dataclass(frozen=True)
class EventKind:
    key: str
    event_type: EventType
    source: str
    carries: tuple[str, ...]
    of: str
    definition: str


@lru_cache(maxsize=1)
def event_kinds() -> dict[str, EventKind]:
    g = Graph().parse(data=_core_ttl(), format="turtle")
    out = {}
    for s in g.subjects(RDF.type, TA.EventKind):
        key = str(g.value(s, TA.key))
        out[key] = EventKind(key=key, event_type=EventType(str(g.value(s, TA.eventType))),
                             source=str(g.value(s, TA.source)),
                             carries=tuple(sorted(str(x) for x in g.objects(s, TA.carries))),
                             of=str(g.value(s, TA.of)).rsplit(":", 1)[-1],
                             definition=str(g.value(s, RDFS.comment) or ""))
    return out


# ---------------- 인스턴스 그래프 ----------------

def instance_graph(gw, events: list[Event], now: datetime) -> Graph:
    from turnaround.saga import fold
    g = load_core()
    for r in gw.rooms:
        rn = node("room", r.id)
        g.add((rn, RDF.type, TA.Room))
        g.add((rn, TA.roomType, Literal(r.room_type.value)))
        g.add((rn, TA.inWard, node("ward", r.ward_id)))
        g.add((rn, TA.capacity, Literal(r.capacity)))
    for b in gw.beds:
        g.add((node("bed", b.id), RDF.type, TA.Bed))
        g.add((node("bed", b.id), TA.inRoom, node("room", b.room_id)))
    for bed, p in gw._occupants(events).items():
        pn = node("patient", p.id)
        g.add((pn, RDF.type, TA.Patient))
        g.add((pn, TA.assignedTo, node("bed", bed)))
        for prec in p.precautions:
            g.add((pn, TA.precautions, Literal(prec.value)))
    s = fold(events)
    for d in s.discharges.values():
        dn = node("discharge", d.id)
        g.add((dn, RDF.type, TA.DischargeCase))
        g.add((dn, TA.stage, Literal(d.stage.value)))
        if d.bed_id:
            g.add((dn, TA.vacates, node("bed", d.bed_id)))
    for e in events:
        if e.type is EventType.DELAY_REASON_RECORDED:
            g.add((node("discharge", e.case_id), TA.delayReason, Literal(e.payload.get("reason"))))
        elif e.type is EventType.ADMISSION_REQUESTED:
            an = node("admission", e.case_id)
            g.add((an, RDF.type, TA.AdmissionRequest))
            for prec in e.payload.get("precautions") or []:
                g.add((an, TA.precautions, Literal(prec)))
        elif e.type is EventType.ISOLATION_ORDER_ADDED:
            for prec in e.payload.get("precautions") or []:
                g.add((node("admission", e.case_id), TA.precautions, Literal(prec)))
    for m in s.matches.values():
        mn = node("match", f"{m.admission_id}-{m.bed_id}")
        g.add((mn, RDF.type, TA.Match))
        g.add((mn, TA.stage, Literal(m.stage.value)))
        g.add((mn, TA.matchedTo, node("bed", m.bed_id)))
        g.add((mn, TA.forAdmission, node("admission", m.admission_id)))
    pol = gw.policy
    for key, mode in pol.autonomy.items():
        g.add((TA[f"autonomy_{key}"], TA.autonomyOf, Literal(key)))
        g.add((TA[f"autonomy_{key}"], TA.mode, Literal(mode)))
    for key, prov in pol.provenance.items():
        pn = TA[f"rule_{key.replace('.', '_')}"]
        g.add((pn, TA.ruleKey, Literal(key)))
        g.add((pn, TA.tier, TA[prov.tier]))
        g.add((pn, TA.cite, Literal(prov.cite)))
    for r in pol.autonomy_promotion.never:
        g.add((TA[f"unit_{r}"], TA.neverPromote, Literal(r)))
    return g


# ---------------- 역량 질문 ----------------

def cq1_safe_beds(gw, case: str, events: list[Event], now: datetime) -> set[str]:
    """이 입원 환자를 지금 안전하게 받을 수 있는 병상. 안전 기준은 선언된 SHACL 제약(shapes.ttl)으로 판정한다."""
    from turnaround.agent.tools import PlanningTools
    t = PlanningTools(gw, [])
    patient, _ = t.patient(case, events)
    if patient is None:
        return set()
    occupied = gw._occupants(events, exclude_case=case)
    held = t.held_beds(events, exclude_case=case)
    out = set()
    for b in gw.beds:
        if b.id in occupied or b.id in held or gw._out_of_service(b.id, events) or gw._on_hold(b.id, events):
            continue
        if not gw._shacl(case, patient, b.id, events):  # 선언된 제약 위반 없음
            out.add(b.id)
    return out


def _q(g: Graph, sparql: str) -> list[tuple]:
    return [tuple(str(x) if x is not None else None for x in row) for row in g.query(sparql, initNs={"ta": TA})]


def cq2_bed_blocker(g: Graph, bed_id: str) -> list[tuple]:
    """이 병상을 비우는 퇴원 사례, 그 단계, 기록된 지연 사유와 풀 수 있는 역할."""
    return _q(g, f"""SELECT ?d ?stage ?reason ?role WHERE {{
        ?d a ta:DischargeCase ; ta:vacates <{node('bed', bed_id)}> ; ta:stage ?stage .
        OPTIONAL {{ ?d ta:delayReason ?reason . OPTIONAL {{ ?r ta:reasonCode ?reason ; ta:responsible ?role }} }}
    }} ORDER BY ?d""")


def cq3_who_resolves(g: Graph, reason: str) -> list[str]:
    return [r[0] for r in _q(g, f'SELECT ?role WHERE {{ ?r ta:reasonCode "{reason}" ; ta:responsible ?role }}')]


def cq4_action_rule(g: Graph, autonomy_key: str) -> dict:
    """이 행동을 시스템이 해도 되는가: 자율 단계, 잠금, 승인·수행 역할."""
    rows = _q(g, f"""SELECT ?a ?mode ?locked ?by WHERE {{
        ?a a ta:ActionType ; ta:autonomyKey "{autonomy_key}" .
        OPTIONAL {{ ?x ta:autonomyOf "{autonomy_key}" ; ta:mode ?mode }}
        OPTIONAL {{ ?a ta:locked ?locked }} OPTIONAL {{ ?a ta:by ?by }} }}""")
    if not rows:
        return {}
    a, mode, locked, by = rows[0]
    return {"action": a, "mode": mode or "human", "locked": locked == "true", "by": by}


def cq5_late_info_hits(g: Graph) -> list[tuple]:
    """격리 정보가 붙은 입원의 매칭 중 이미 연락·확정된 것 (늦게 온 정보가 진행 중인 일에 닿음)."""
    return _q(g, """SELECT DISTINCT ?m ?stage WHERE {
        ?m a ta:Match ; ta:stage ?stage ; ta:forAdmission ?a . ?a ta:precautions ?p .
        FILTER(?stage IN ("contacted", "committed")) } ORDER BY ?m""")


def cq6_rule_evidence(g: Graph, key: str) -> tuple | None:
    rows = _q(g, f"""SELECT ?tier ?rank ?cite WHERE {{
        ?r ta:ruleKey "{key}" ; ta:tier ?tier ; ta:cite ?cite . ?tier ta:rank ?rank }}""")
    return rows[0] if rows else None


def cq7_never_promote(g: Graph) -> set[str]:
    return {r[0] for r in _q(g, "SELECT ?u WHERE { ?x ta:neverPromote ?u }")}


__all__ = ["EventKind", "cq1_safe_beds", "cq2_bed_blocker", "cq3_who_resolves", "cq4_action_rule",
           "cq5_late_info_hits", "cq6_rule_evidence", "cq7_never_promote", "event_kinds", "instance_graph",
           "load_core"]
