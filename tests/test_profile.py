"""S0: 병원 프로필 뼈대 (설계 개정 아키텍처 6절, DECISIONS 2026-11-09).

이 단계는 동작을 바꾸지 않는다. 정책 파일 기본값은 지금 동작(legacy)과 같고,
현장값은 `config/profiles/field_default.yaml` 덮어쓰기로 읽는다.
"""

import pytest
import yaml

from turnaround.gateway.policy import DEFAULT_POLICY, LOCKED_HUMAN_ACTIONS, load_policy


def test_default_policy_keeps_current_behaviour():
    p = load_policy(profile="legacy", allow_legacy=True).hospital_profile
    assert p.assignment.timing == "prematch_evening"
    assert p.ward_confirmation.mode == "reply"
    assert p.discharge_sequence.preset == "legacy"
    assert p.cleaning.split is False
    assert load_policy(profile="legacy", allow_legacy=True).brain.enabled is False


def test_field_profile_overrides_to_field_values():
    p = load_policy(profile="field_default").hospital_profile
    assert p.assignment.timing == "same_day_morning"
    assert p.ward_confirmation.mode == "notify" and p.ward_confirmation.merge_manager_confirm_into_commit
    assert p.discharge_sequence.preset == "field" and p.discharge_sequence.departure_due_at == "11:00"
    assert p.cleaning.split is True
    assert p.cleaning.readiness_scope["multi"] == "bed_unit" and p.cleaning.readiness_scope["single"] == "room"
    assert p.room_change.display_chain is True


def test_unknown_profile_is_an_error():
    with pytest.raises(FileNotFoundError):
        load_policy(profile="no_such_profile")


def test_responsibility_actions_cannot_be_made_automatic(tmp_path):
    """환자 연락, 안전 예외 승인은 코드 상수로 잠근다 (사람만). 공식 확정은 정책 자율 단계다 (DECISIONS 2026-11-09)."""
    raw = yaml.safe_load(DEFAULT_POLICY.read_text(encoding="utf-8"))
    raw.setdefault("autonomy", {})["contact_patient"] = "auto"
    f = tmp_path / "p.yaml"
    f.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValueError, match="contact_patient"):
        load_policy(f)
    assert {"contact_patient", "approve_safety_exception"} <= LOCKED_HUMAN_ACTIONS
    assert "official_commit" not in LOCKED_HUMAN_ACTIONS
    assert load_policy(profile="legacy", allow_legacy=True).autonomy["official_commit"] == "human"  # legacy 기본은 지금 동작
    assert load_policy(profile="field_default").autonomy["official_commit"] == "auto"


def test_profile_values_have_provenance():
    p = load_policy(profile="legacy", allow_legacy=True)
    for k in ("hospital_profile.assignment", "hospital_profile.ward_confirmation", "hospital_profile.cleaning",
              "hospital_profile.discharge_sequence", "autonomy"):
        assert k in p.provenance, k
