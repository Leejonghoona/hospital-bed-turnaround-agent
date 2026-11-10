"""M3 C: 자율권 승격·강등 (로드맵 07, DECISIONS 2026-11-09)."""

from test_gateway import adm, at, ev, make, prop
from turnaround.agent.autonomy import COMMIT, apply, evaluate, unit_outcomes, wilson_lower
from turnaround.events import EventType
from turnaround.gateway import load_policy

FIELD = load_policy(profile="field_default")
CONSERVATIVE = apply(FIELD, COMMIT, "human")


def test_wilson_lower_bound():
    assert wilson_lower(0, 0) == 0.0
    assert 0.83 < wilson_lower(20, 20) < 0.84
    assert wilson_lower(25, 25) > 0.85 and wilson_lower(24, 25) < 0.85


def test_human_approvals_of_ordinary_cases_promote_commit_autonomy():
    from turnaround.agent.autonomy import Outcome
    outs = [Outcome(COMMIT, f"A{i}", f"p{i}", True, "human") for i in range(25)]
    rows = {r["unit"]: r for r in evaluate(outs, CONSERVATIVE)}
    assert rows[COMMIT]["mode"] == "human" and rows[COMMIT]["recommendation"] == "promote"
    outs[3] = Outcome(COMMIT, "A3", "p3", False, "human")
    assert {r["unit"]: r for r in evaluate(outs, CONSERVATIVE)}[COMMIT]["recommendation"] == "hold"


def test_never_units_are_not_promoted_however_often_humans_agree():
    from turnaround.agent.autonomy import Outcome
    outs = [Outcome("isolation", f"A{i}", f"p{i}", True, "human") for i in range(100)]
    assert evaluate(outs, FIELD)[0]["recommendation"] == "hold"


def test_agent_disagreements_demote_automatically():
    from turnaround.agent.autonomy import Outcome
    outs = [Outcome(COMMIT, f"A{i}", f"p{i}", i % 5 != 0, "agent") for i in range(20)]
    row = {r["unit"]: r for r in evaluate(outs, FIELD)}[COMMIT]
    assert row["mode"] == "auto" and row["recommendation"] == "demote"
    assert apply(FIELD, COMMIT, "human").autonomy[COMMIT] == "human"


def test_apply_moves_a_review_reason_in_and_out():
    pol = apply(FIELD, "icu_stepdown", "auto")
    assert "icu_stepdown" not in pol.review_reasons
    assert "icu_stepdown" in apply(pol, "icu_stepdown", "human").review_reasons


def test_outcomes_from_gateway_events_human_and_agent():
    store, gw = make([adm(case="A1"), adm(case="A2")], policy=CONSERVATIVE)
    gw.submit(prop(case="A1", pid="p1", store=store), at(9))
    gw.manager_approve("p1", at(9, 5))
    store2, gw2 = make([adm(case="A2")], policy=FIELD)
    gw2.submit(prop(case="A2", pid="p2", store=store2), at(9))
    gw2.ward_objection("p2", "staffing", at(9, 20))
    o1 = unit_outcomes(store.read())
    o2 = unit_outcomes(store2.read())
    assert [(o.unit, o.agreed, o.by) for o in o1] == [(COMMIT, True, "human")]
    assert [(o.unit, o.agreed, o.by) for o in o2] == [(COMMIT, False, "agent")]


def test_demoted_unit_needs_fresh_evidence_before_promotion_again():
    """강등 뒤 옛 근거로 다음 날 다시 승격하면 날마다 오르내린다(2026-11-09 측정에서 발견). 강등 뒤 새 근거만 센다."""
    from turnaround.agent.autonomy import Outcome
    old = [Outcome(COMMIT, f"A{i}", f"p{i}", True, "human") for i in range(30)]
    bad = [Outcome(COMMIT, f"B{i}", f"q{i}", i >= 17, "agent") for i in range(20)]
    outs = old + bad
    cut = {COMMIT: len(outs)}  # 여기서 강등
    rows = {r["unit"]: r for r in evaluate(outs, CONSERVATIVE, since=cut)}
    assert rows[COMMIT]["recommendation"] == "hold" and rows[COMMIT]["human_n"] == 0
    fresh = [Outcome(COMMIT, f"C{i}", f"r{i}", True, "human") for i in range(25)]
    rows = {r["unit"]: r for r in evaluate(outs + fresh, CONSERVATIVE, since=cut)}
    assert rows[COMMIT]["recommendation"] == "promote"
