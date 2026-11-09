"""지침 값 (SAG-2a, DAT-9, POL-1, POL-2)."""

import pytest

from turnaround.gateway import load_policy
from turnaround.rules import AEROSOL_REMOVAL_TABLE, aerosol_wait_min, guideline_delta
from turnaround.sim.generator import generate
from turnaround.sim.models import Room, RoomType


def test_table_values_checked_against_guideline_text():
    # 작업자가 지침 원문에서 직접 확인한 세 값 (LP-23)
    assert aerosol_wait_min(12) == 35 and aerosol_wait_min(6) == 69 and aerosol_wait_min(4) == 104


def test_off_table_ach_uses_formula_rounded_up():
    assert aerosol_wait_min(5) == 83  # ln(1000)/5*60 = 82.9
    assert aerosol_wait_min(4, 99.0) == 69
    with pytest.raises(ValueError):
        aerosol_wait_min(0)


def test_table_is_monotone():
    vals = [AEROSOL_REMOVAL_TABLE[k][1] for k in sorted(AEROSOL_REMOVAL_TABLE)]
    assert vals == sorted(vals, reverse=True)


def test_room_ach_defaults_by_type_and_can_be_set():
    assert Room(id="R", ward_id="W", room_type=RoomType.NEGATIVE_PRESSURE, capacity=1).air_changes_per_hour == 12
    assert Room(id="R", ward_id="W", room_type=RoomType.MULTI, capacity=4).air_changes_per_hour == 4
    assert Room(id="R", ward_id="W", room_type=RoomType.SINGLE, capacity=1, air_changes_per_hour=6).air_changes_per_hour == 6


def test_generator_wait_follows_room_ach():
    h = generate(seed=3)
    room_of = {b.id: b.room_id for b in h.beds}
    rooms = {r.id: r for r in h.rooms}
    aerosol = [d for d in h.discharges if d.aerosol_wait_min]
    assert aerosol
    for d in aerosol:
        assert d.aerosol_wait_min == aerosol_wait_min(rooms[room_of[d.bed_id]].air_changes_per_hour)


def test_guideline_delta_labels():
    assert guideline_delta(104, None) is None and guideline_delta(104, 104) is None
    assert guideline_delta(104, 60)["label"] == "지침 대비 44분 단축"
    assert guideline_delta(35, 60)["label"] == "지침 대비 25분 연장"


# ---------- POL-1 출처 계층 ----------

TIERS = {"law", "guideline", "hospital_rule", "field_practice", "design_assumption"}


def test_every_policy_value_has_provenance():
    p = load_policy()
    keys = {"autonomy_stage", "pediatric_age", "score_gap_min", "ventilation", "manual_override.safety_tiers",
            "manual_override.safety_exception_roles", "external_llm"}
    assert keys <= set(p.provenance)
    for k, v in p.provenance.items():
        assert v.tier in TIERS, k
        assert v.cite, k


def test_guideline_prohibition_cannot_be_overridden_by_hospital_rule(tmp_path):
    import yaml
    from turnaround.gateway.policy import DEFAULT_POLICY, load_policy as lp
    raw = yaml.safe_load(DEFAULT_POLICY.read_text(encoding="utf-8"))
    raw["manual_override"]["safety_tiers"]["tb_shared_room"] = "reason_entry"
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="tb_shared_room"):
        lp(f)


def test_gender_room_tier_is_locked_forbidden():
    import pytest, yaml
    from turnaround.gateway.policy import Policy
    from turnaround.gateway import policy as pol_mod
    raw = yaml.safe_load(open(pol_mod.POLICY_PATH if hasattr(pol_mod, "POLICY_PATH") else "config/policy.yaml", encoding="utf-8"))
    raw["manual_override"]["safety_tiers"]["gender_room"] = "reason_entry"
    with pytest.raises(Exception):
        Policy(**raw)
