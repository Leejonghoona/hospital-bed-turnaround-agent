"""LLM 판단의 규칙표 반영 (rule_learning): 세는 규칙, 기준, 금지 유형, 승인, 규칙표 우선 적용."""
import pytest
import yaml

from turnaround.agent import rule_learning as RL
from turnaround.agent.llm import _fake_exception_action

CTX = {"exception": {"trigger": "guardian_delay_same_day", "stage": "before_contact", "attempts": 0},
       "case_state": {"next_admission_waiting": False},
       "allowed_actions": [{"id": "wait"}, {"id": "offer_lounge"}, {"id": "share_eta"}, {"id": "escalate"}]}


def _cases(n_support, n_rule=0, n_seen=0, trigger="guardian_delay_same_day", stage="before_contact"):
    f = RL.features(CTX)
    out = []
    for i in range(n_support):
        out.append(RL.Case(f"s{i}", trigger, stage, f, "wait", "share_eta", "share_eta"))
    for i in range(n_rule):
        out.append(RL.Case(f"r{i}", trigger, stage, f, "wait", "share_eta", "wait"))
    for i in range(n_seen):
        out.append(RL.Case(f"v{i}", trigger, stage, f, "wait", "share_eta", "share_eta", saw_model=True))
    return out


def test_rule_table_unchanged_without_overrides():
    assert _fake_exception_action(CTX, overrides=[])["action"] == "wait"


def test_ready_needs_25_clean_all_supporting():
    p = RL.propose(_cases(25))[0]
    assert (p.from_action, p.to_action, p.n, p.k) == ("wait", "share_eta", 25, 25) and p.ready
    p = RL.propose(_cases(24, n_rule=1))[0]  # 25건 중 1건이라도 반대면 하한 0.85 아래
    assert p.n == 25 and p.kept_rule == 1 and not p.ready


def test_cases_that_saw_llm_answer_are_not_evidence():
    p = RL.propose(_cases(20, n_seen=30))[0]
    assert p.n == 20 and p.excluded_seen == 30 and not p.ready


def test_rule_default_situations_shown_to_human_are_excluded():
    f = RL.features(CTX)
    cs = [RL.Case(f"d{i}", "guardian_delay_same_day", "before_contact", f, "wait", "share_eta", "share_eta",
                  shown_to_human=True) for i in range(40)]
    assert RL.propose(cs) == []


def test_safety_types_and_locked_stages_never_ready():
    assert RL.propose(_cases(40, trigger="late_isolation"))[0].blocked == "안전 유형"
    assert RL.propose(_cases(40, stage="after_contact"))[0].blocked == "연락 뒤 단계"


def test_approve_then_rule_table_uses_override(tmp_path):
    path = tmp_path / "ov.yaml"
    p = RL.propose(_cases(30))[0]
    sha = RL.approve(p, "원무팀장", path=path)
    assert len(sha) == 64
    ov = RL.load_overrides(path)
    assert ov[0]["approved_by"] == "원무팀장" and ov[0]["evidence"]["k"] == 30
    out = _fake_exception_action(CTX, overrides=ov)
    assert out["action"] == "share_eta" and p.id in out["rationale"]
    # 다른 유형(기다리는 입원 있음)에는 쓰지 않는다
    other = {**CTX, "case_state": {"next_admission_waiting": True}}
    assert _fake_exception_action(other, overrides=ov)["action"] != "share_eta"
    # 허용 목록 밖이면 쓰지 않는다
    narrow = {**CTX, "allowed_actions": [{"id": "wait"}]}
    assert _fake_exception_action(narrow, overrides=ov)["action"] == "wait"
    RL.revoke(p.id, path=path)
    assert RL.load_overrides(path) == []
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["overrides"][0]["active"] is False


def test_cannot_approve_below_threshold_or_without_approver(tmp_path):
    with pytest.raises(ValueError):
        RL.approve(RL.propose(_cases(10))[0], "원무팀장", path=tmp_path / "a.yaml")
    with pytest.raises(ValueError):
        RL.approve(RL.propose(_cases(30))[0], "", path=tmp_path / "b.yaml")


def test_override_never_applies_in_locked_stage():
    f = RL.features({**CTX, "exception": {**CTX["exception"], "stage": "after_contact"}})
    ov = [{"id": "x", "trigger": "guardian_delay_same_day", "stage": "after_contact", "features": f,
           "action": "share_eta"}]
    ctx = {**CTX, "exception": {**CTX["exception"], "stage": "after_contact"}}
    assert RL.match_override(ctx, ov) is None


def test_join_cases_from_shadow_log_and_decisions():
    log = [{"exception_id": "e1", "trigger": "t", "stage": "s", "features": {"a": True}, "status": "ok",
            "rule_action": "wait", "model_action": "share_eta:x", "shown_to_human": False},
           {"exception_id": "e2", "trigger": "t", "stage": "s", "status": "format_error", "rule_action": "wait",
            "model_action": None}]
    cs = RL.join_cases(log, [{"exception_id": "e1", "action": "share_eta", "saw_model": False}])
    assert len(cs) == 1 and cs[0].model_action == "share_eta" and cs[0].clean


def test_shadow_record_feeds_rule_learning():
    """실제 그림자 실행 기록이 유형 키와 '제안으로 보여 줌' 표시를 남기고, 담당자 결정과 이어진다."""
    from datetime import datetime

    from test_brain import FIELD, _Script, _setup
    from turnaround.agent.brain import ExceptionBrain
    store, gw = _setup()
    log = []
    ExceptionBrain(gw, _Script({"action": "escalate", "params": {"role": "ipc"}}), FIELD, shadow=True,
                   shadow_log=log).step(datetime(2026, 3, 4, 11, 30))
    r = log[0]
    assert isinstance(r["features"], dict) and "waiting" in r["features"]
    assert r["shown_to_human"] is False  # 규칙표가 정한 사례(연락 전 규칙 재매칭): LLM 답은 아무에게도 보이지 않는다
    cs = RL.join_cases(log, [{"exception_id": r["exception_id"], "action": "escalate"}])
    assert len(cs) == 1 and cs[0].clean


def test_new_situation_attaches_llm_hint_to_human_card_without_executing():
    """규칙표가 정하지 못해 사람에게 넘기는 새 상황: LLM 답은 카드의 참고 의견으로만 붙고 실행되지 않는다."""
    from datetime import datetime

    from test_brain import FIELD, _Script, _setup, _types
    from turnaround.agent.brain import ExceptionBrain
    from turnaround.events import EventType

    store, gw = _setup()
    log = []
    brain = ExceptionBrain(gw, _Script({"action": "rematch_now"}), FIELD, shadow=True, shadow_log=log)
    import turnaround.agent.llm as llm_mod
    orig = llm_mod._fake_exception_action
    llm_mod._fake_exception_action = lambda ctx, overrides=None: {
        "action": "escalate", "params": {"role": "bed_manager"}, "rationale": "허용 목록 밖: 사람에게", "default": True}
    try:
        brain.step(datetime(2026, 3, 4, 11, 30))
    finally:
        llm_mod._fake_exception_action = orig
    r = log[0]
    assert r["shown_to_human"] is True
    assert r["status"] == "ok" and not r["would_block"]
    executed = [e.payload["action"] for e in _types(store, EventType.ACTION_EXECUTED)]
    assert executed == ["escalate"]  # 실행은 규칙표(사람에게)뿐, LLM 답(rematch_now)은 실행되지 않는다
    cards = [e.payload.get("card") or {} for e in _types(store, EventType.HUMAN_TASK_CREATED)
             if e.payload.get("kind") == "brain_escalate"]
    assert cards and cards[0].get("ai_hint") == "rematch_now"
