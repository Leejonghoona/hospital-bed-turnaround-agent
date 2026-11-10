"""M6-1: 핵심 온톨로지 선언과 역량 질문 (로드맵 07, docs/검토/온톨로지_검토.md).

선언(core.ttl)이 코드와 어긋나지 않는지, 그리고 선언된 어휘로 역량 질문 7개에 답할 수 있는지 본다.
"""

from datetime import time

import pytest
from rdflib import RDF

from turnaround.events import EventType
from turnaround.gateway import Gateway, load_policy
from turnaround.gateway.profile import LOCKED_HUMAN_ACTIONS
from turnaround.ontology.core import (cq1_safe_beds, cq2_bed_blocker, cq3_who_resolves, cq4_action_rule,
                                      cq5_late_info_hits, cq6_rule_evidence, cq7_never_promote, event_kinds,
                                      instance_graph, load_core)
from turnaround.ontology.graph import TA


@pytest.fixture(scope="module")
def core():
    return load_core()


def _vals(g, s, p):
    return [str(x) for x in g.objects(s, p)]


def test_object_types_point_to_real_code(core):
    import turnaround.saga.machine as saga
    import turnaround.sim.models as models
    for s in core.subjects(RDF.type, TA.ObjectType):
        model = core.value(s, TA.codeModel)
        ev = core.value(s, TA.codeEvent)
        assert model or ev, s
        if model:
            assert hasattr(models, str(model)) or hasattr(saga, str(model)), model
        if ev:
            EventType(str(ev))


def test_properties_point_to_real_fields(core):
    import turnaround.sim.models as models
    for p in core.subjects(TA.codeField, None):
        dom = core.value(p, TA.domain) or core.value(p, __import__("rdflib").RDFS.domain)
        model = core.value(dom, TA.codeModel) if dom else None
        if model and hasattr(models, str(model)):
            assert str(core.value(p, TA.codeField)) in getattr(models, str(model)).model_fields, (p, model)


def test_every_canonical_event_is_declared():
    from turnaround.events.replay import hospital_to_events
    from turnaround.sim import GeneratorConfig, generate
    kinds = event_kinds()
    seen = set()
    for seed in range(5):
        for e in hospital_to_events(generate(seed=seed, config=GeneratorConfig.realistic()), seed=seed):
            key = e.event_id.split(":", 1)[1]
            seen.add(key)
            assert key in kinds, key
            assert kinds[key].event_type is e.type and kinds[key].source == e.source, key
    assert {"billing_done", "patient_departed", "bed_ready", "admission_requested"} <= seen


def test_actions_match_policy_and_gateway(core):
    pol = load_policy(profile="field_default")
    declared = {}
    for a in core.subjects(RDF.type, TA.ActionType):
        key, meth = core.value(a, TA.autonomyKey), core.value(a, TA.gatewayMethod)
        if meth:
            assert hasattr(Gateway, str(meth)), meth
        if key:
            declared[str(key)] = str(core.value(a, TA.locked) or "false") == "true"
    assert {k for k, locked in declared.items() if locked} == set(LOCKED_HUMAN_ACTIONS)
    allowed = {a for rule in pol.exception_actions.values() for a in rule.allowed}
    assert allowed <= set(declared), allowed - set(declared)
    assert set(pol.autonomy) <= set(declared), set(pol.autonomy) - set(declared)


@pytest.fixture(scope="module")
def state():
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    pol = load_policy(profile="field_default")
    st = build_demo(seed=3, day=3, until=time(10, 30), via_fhir=False, policy=pol, config=GeneratorConfig.realistic())
    ev = st.store.read()
    return st, ev, instance_graph(st.gw, ev, st.now)


def test_cq1_safe_beds_equal_planning_tool_candidates(state):
    st, ev, _ = state
    from turnaround.agent.tools import PlanningTools
    tools = PlanningTools(st.gw, st.h.wards)
    cases = [e.case_id for e in ev if e.type is EventType.ADMISSION_REQUESTED][:5]
    assert cases
    for c in cases:
        assert cq1_safe_beds(st.gw, c, ev, st.now) == {x.bed_id for x in tools.candidates(c, st.now, ev)}, c


def test_cq2_cq3_bed_blocker_and_responsible_role(state):
    st, ev, g = state
    d = next(x for x in st.h.discharges if x.day == 3)
    rows = cq2_bed_blocker(g, d.bed_id)
    assert rows and rows[0][1]  # 단계가 있다
    assert cq3_who_resolves(g, "billing") == [str(TA.billing)]
    assert cq3_who_resolves(g, "guardian") == [str(TA.ward)]


def test_cq4_action_rules(state):
    _, _, g = state
    assert cq4_action_rule(g, "official_commit")["mode"] == "auto"  # 현장 프로필
    c = cq4_action_rule(g, "contact_patient")
    assert c["locked"] and c["mode"] == "human"
    assert cq4_action_rule(g, "propose_rematch")["mode"] == "human"


def test_cq5_cq6_cq7(state):
    _, _, g = state
    assert isinstance(cq5_late_info_hits(g), list)
    tier = cq6_rule_evidence(g, "autonomy")
    assert tier and tier[0].endswith("design_assumption") and tier[1] == "5"
    assert {"isolation", "single_room_preference"} <= cq7_never_promote(g)
