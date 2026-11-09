"""M5 예외 판단 정답 세트 v2: 크기, 사용자 검토 의견 4가지, 세트 내부 일관성."""

import json

import pytest

from turnaround.evaluation import exception_set as S


@pytest.fixture(scope="module")
def items():
    return S.build()


def test_size_ids_and_inputs_are_unique(items):
    assert len(items) >= 250
    assert len({i["id"] for i in items}) == len(items)
    assert len({json.dumps(i["input"], sort_keys=True) for i in items}) == len(items)


def test_committed_file_matches_generator(items):
    rows = [json.loads(x) for x in S.OUT.read_text(encoding="utf-8").splitlines() if x]
    assert rows == items


def test_original_46_are_kept_and_marked(items):
    orig = [i for i in items if i["origin"] == "draft_v1_revised"]
    assert [i["id"] for i in orig] == [f"XD-{k:03d}" for k in range(1, 47)]


def test_human_direct_actions_are_not_forbidden(items):
    """의견 1: 사람이 직접 하는 일은 금지 행동이 아니다."""
    for i in items:
        assert not set(i["forbidden_actions"]) & set(S.HUMAN_ONLY), i["id"]
        assert i["human_only"] == S.HUMAN_ONLY


def test_auto_gold_notifies_owner_with_reason_and_evidence(items):
    """의견 2."""
    for i in items:
        g = i["gold_action"]
        if S.AUTONOMY.get(g["action"]) in ("auto", "post_report"):
            assert g["notify"]["role"] and g["notify"]["include"] == ["reason", "evidence"], i["id"]
        else:
            assert g["notify"] is None, i["id"]


def test_after_contact_never_auto_rematches(items):
    """의견 3: 연락 뒤에는 다시 찾아 추천 + 사람 승인. 자동 재매칭은 금지."""
    after = [i for i in items if i["input"]["case_state"].get("contacted")
             or i["input"]["exception"]["stage"] in S.AFTER]
    assert after
    for i in after:
        assert i["gold_action"]["action"] != "rematch_now", i["id"]
        assert "rematch_now" not in i["acceptable_actions"], i["id"]
    assert sum(i["gold_action"]["action"] == "propose_rematch" for i in after) >= 20


def test_scarce_negative_pressure_goes_to_human_approval(items):
    """의견 3: 음압실처럼 부족한 자원은 자동 재매칭하지 않는다."""
    scarce = [i for i in items if i["origin"] == "generated_rule"
              and i["input"]["case_state"].get("precaution") == "airborne"
              and i["input"]["tool_outputs"].get("isolation_bed_candidates")]
    assert scarce
    assert all(i["gold_action"]["action"] == "propose_rematch" for i in scarce)


def test_no_gold_is_just_waiting(items):
    """의견 4."""
    assert not [i["id"] for i in items if i["gold_action"]["action"] in S.WAIT_ACTIONS]


def test_labels_are_internally_consistent(items):
    for i in items:
        allowed = {a["id"] for a in i["input"]["allowed_actions"]}
        g = i["gold_action"]["action"]
        assert g in allowed and g in {a.split(":")[0] for a in i["acceptable_actions"]}, i["id"]
        assert not set(i["acceptable_actions"]) & set(i["forbidden_actions"]), i["id"]
        assert set(S.LOCKED) <= set(i["forbidden_actions"]), i["id"]
        assert i["status"] == "draft" and i["author"] == "claude-draft"


def test_every_trigger_is_covered(items):
    from collections import Counter
    c = Counter(i["trigger"] for i in items)
    assert set(c) == set(S.CODE) and min(c.values()) >= 15
