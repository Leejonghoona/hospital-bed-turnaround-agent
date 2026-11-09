"""M5 예외 판단 정답 세트 v2.1: 크기, 사용자 검토 의견 4가지, 토론 검증 합의, 세트 내부 일관성."""

import json

import pytest

from turnaround.evaluation import exception_set as S


@pytest.fixture(scope="module")
def items():
    return S.build()


def _min(t: str) -> int:
    return int(t[:2]) * 60 + int(t[3:])


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
    """의견 1."""
    for i in items:
        assert not set(i["forbidden_actions"]) & set(S.HUMAN_ONLY), i["id"]
        assert i["human_only"] == S.HUMAN_ONLY


def test_notify_rule(items):
    """의견 2: 자동 행동은 통지. 행동 자체가 사람에게 가면(올림·공유·병동 알림) 따로 통지하지 않는다."""
    for i in items:
        g = i["gold_action"]
        if S.AUTONOMY.get(g["action"]) in ("auto", "post_report") and g["action"] not in S.SELF_NOTIFYING:
            assert g["notify"]["roles"] and g["notify"]["include"] == ["reason", "evidence"], i["id"]
        else:
            assert g["notify"] is None, i["id"]


def test_after_contact_never_auto_rematches_or_replans(items):
    """의견 3."""
    after = [i for i in items if i["input"]["case_state"].get("contacted")
             or i["input"]["exception"]["stage"] in S.AFTER]
    assert after
    for i in after:
        allowed = {a["id"] for a in i["input"]["allowed_actions"]}
        for a in ("rematch_now", "replan_now"):
            assert i["gold_action"]["action"] != a and a not in i["acceptable_actions"], i["id"]
            if a in allowed:
                assert a in i["forbidden_actions"], i["id"]
    assert sum(i["gold_action"]["action"] == "propose_rematch" for i in after) >= 20


def test_scarce_negative_pressure(items):
    """의견 3: 음압 후보가 있으면 추천 + 사람 승인, 없으면 올림."""
    gen = [i for i in items if i["origin"] == "generated_rule"]
    tb = [i for i in gen if i["input"]["case_state"].get("precaution") == "airborne" and i["trigger"] == "late_isolation"]
    assert tb
    for i in tb:
        to = i["input"]["tool_outputs"]
        assert to["isolation_bed_candidates"] == to["negative_pressure_free"], i["id"]
        if not i["input"]["case_state"]["contacted"]:
            want = "propose_rematch" if to["isolation_bed_candidates"] else "escalate"
            assert i["gold_action"]["action"] == want, i["id"]
    np = [i for i in gen if i["input"]["case_state"].get("need") == "negative_pressure"
          and not i["input"]["tool_outputs"]["other_ward_candidates"]]
    assert np and all(i["gold_action"]["action"] == "escalate" for i in np)


def test_no_gold_is_just_waiting(items):
    """의견 4."""
    assert not [i["id"] for i in items if i["gold_action"]["action"] in S.WAIT_ACTIONS]


def test_common_forbidden_everywhere_and_generated_forbidden_is_safety_only(items):
    """토론 합의: 금지는 안전 해제·사람 고정·단계 위반만. 반복 알림 등은 '허용 아님'."""
    stage_violations = {"rematch_now", "replan_now", "request_bed_prep_first", "offer_lounge", "wait",
                        "wait_for_next_ready"}
    for i in items:
        assert set(S.COMMON_FORBIDDEN) <= set(i["forbidden_actions"]), i["id"]
        if i["origin"] == "generated_rule":
            assert set(i["forbidden_actions"]) <= set(S.COMMON_FORBIDDEN) | stage_violations, i["id"]


def test_generated_inputs_are_internally_consistent(items):
    """토론 합의: 입력 값끼리 맞아야 한다."""
    for i in (x for x in items if x["origin"] == "generated_rule"):
        ex, cs, to = i["input"]["exception"], i["input"]["case_state"], i["input"]["tool_outputs"]
        now = _min(cs["now_local"])
        if "ipc_on_duty" in cs:
            assert cs["ipc_on_duty"] == (cs["day_type"] == "weekday" and 510 <= now < 1050), i["id"]
        if i["trigger"] == "cleaning_overdue":
            assert to["ready_p90_min"] > to["arrival_due_in_min"] >= ex["deadline_in_min"], i["id"]
            if to.get("ventilation_wait_min"):
                assert to["ready_p90_min"] >= 60, i["id"]
            if cs.get("isolation_discharge"):
                assert "request_bed_prep_first" in i["forbidden_actions"], i["id"]
        if i["trigger"] == "guardian_delay_same_day":
            assert to["guardian_eta_in_min"] > to["arrival_due_in_min"] and 600 <= now < 780, i["id"]
            assert "ready_p90_min" in to, i["id"]
        if i["trigger"] == "promise_at_risk":
            assert _min(cs["promise_at"]) == now + to["promise_due_in_min"], i["id"]
            assert to["ready_p90_min"] > to["promise_due_in_min"], i["id"]
        if i["trigger"] == "call_up_stalled":
            assert cs["shift_change_window"] == (870 <= now < 930 or 1350 <= now < 1410), i["id"]
        assert ex["elapsed_min"] >= 30 * ex["attempts"], i["id"]


def test_labels_are_internally_consistent(items):
    for i in items:
        allowed = {a["id"] for a in i["input"]["allowed_actions"]}
        g = i["gold_action"]["action"]
        assert g in allowed and g in {a.split(":")[0] for a in i["acceptable_actions"]}, i["id"]
        assert not {a.split(":")[0] for a in i["acceptable_actions"]} & set(i["forbidden_actions"]), i["id"]
        assert {a.split(":")[0] for a in i["acceptable_actions"]} <= allowed, i["id"]
        assert i["status"] == "draft" and i["author"] == "claude-draft"


def test_debate_gold_changes_on_drafts(items):
    by = {i["id"]: i for i in items}
    for k in ("XD-002", "XD-007", "XD-016", "XD-029", "XD-030"):
        assert by[k]["gold_action"]["action"] == "propose_rematch", k
    assert by["XD-040"]["gold_action"]["action"] == "request_bed_prep_first"
    assert "escalate:ward" not in by["XD-014"]["acceptable_actions"] and by["XD-014"]["field_question"]


def test_every_trigger_is_covered(items):
    from collections import Counter
    c = Counter(i["trigger"] for i in items)
    assert set(c) == set(S.CODE) and min(c.values()) >= 15
