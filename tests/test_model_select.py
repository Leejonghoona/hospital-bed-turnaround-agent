"""M5 역할별 채점(예외 판단, 설명)과 사전 등록 결정 규칙 (docs/검토/모델선정_사전등록.md)."""

import statistics

import pytest

from turnaround.agent import FakeLLM
from turnaround.evaluation import model_check as M
from turnaround.evaluation import model_select as S


class Scripted:
    """정해진 답을 내는 가짜 모델."""

    name = "scripted"

    def __init__(self, fn):
        self.fn, self.usage = fn, FakeLLM().usage

    def complete_json(self, task, system, user):
        import json
        return self.fn(task, json.loads(user))


def _item(**kw):
    base = {"id": "T1", "trigger": "late_isolation", "difficulty": "easy",
            "input": {"exception": {"stage": "before_contact"}, "case_state": {"now_local": "09:10"},
                      "tool_outputs": {"isolation_bed_candidates": 1, "best_ready_in_min": 30},
                      "allowed_actions": [{"id": "rematch_now", "autonomy": "auto"},
                                          {"id": "escalate", "autonomy": "auto"},
                                          {"id": "propose_rematch", "autonomy": "human"}]},
            "gold_action": {"action": "rematch_now", "params": {}}, "acceptable_actions": ["rematch_now", "escalate"],
            "forbidden_actions": ["contact_patient", "approve_safety_exception"], "human_only": ["cancel_commit"]}
    return {**base, **kw}


@pytest.mark.parametrize("out,verdict", [
    ({"action": "rematch_now"}, "gold"),
    ({"action": "escalate", "params": {"role": "bed_manager"}}, "acceptable"),
    ({"action": "propose_rematch"}, "not_acceptable"),
    ({"action": "contact_patient"}, "forbidden"),
    ({"action": "cancel_commit"}, "human_only"),
    ({"action": "teleport"}, "out_of_list"),
    ({}, "format_error"),
])
def test_exception_verdicts(out, verdict):
    assert M.exception_verdict(_item(), out) == verdict


def test_role_specific_forbidden_escalation():
    it = _item(forbidden_actions=["contact_patient", "escalate:environment_vendor"])
    assert M.exception_verdict(it, {"action": "escalate", "params": {"role": "environment_vendor"}}) == "forbidden"


def test_notification_needs_input_evidence_and_no_made_up_numbers():
    inp = _item()["input"]
    assert M.notice_ok("격리 병상 후보 1개, 준비 30분이라 다시 찾는다", inp)
    assert not M.notice_ok("", inp)  # 사유 없음
    assert not M.notice_ok("다시 찾는 게 낫다", inp)  # 근거(입력 값) 없음
    assert not M.notice_ok("준비 45분이라 다시 찾는다", inp)  # 입력에 없는 숫자
    assert M.notice_ok("09:10 기준 후보 1개", inp)


def test_explain_fact_check_catches_foreign_beds_and_numbers():
    ctx = {"patient": {"age": 85}, "chosen_bed_id": "W71-R04-B2",
           "candidates": [{"bed_id": "W71-R04-B2", "score": 30.0, "ready_in_min": 0, "parts": {"department_ward_match": 30.0}},
                          {"bed_id": "W71-R02-B1", "score": 15.0, "ready_in_min": 0,
                           "parts": {"single_room_unrequested": -15.0}}]}
    assert M.explain_facts("W71-R04-B2가 30점으로 W71-R02-B1(15점)보다 높다", ctx) == {"ok": True, "mentions_chosen": True}
    assert not M.explain_facts("W72-R09-B9보다 낫다", ctx)["ok"]  # 후보에 없는 병상
    assert not M.explain_facts("W71-R04-B2가 42점이다", ctx)["ok"]  # 지어낸 숫자
    assert M.explain_facts("1인실 미요청 감점 15점", ctx)["ok"]  # 감점은 절댓값으로 써도 된다


def test_exception_eval_with_fake_llm_runs_on_full_set():
    r = M.exception_v2(M.Recorder(FakeLLM()), reps_n=5)
    assert r["n"] >= 250 and r["stopped"] is None
    assert sum(r["verdicts"].values()) == r["n"]
    assert len(r["items"]) == r["n"] and 0 <= r["A1_acceptable"] <= 1
    assert r["R1_same_action"] == 1.0  # 결정적 가짜 모델은 반복해도 같다
    assert r["R1_pass3"] == statistics.fmean(x["verdict"] in ("gold", "acceptable") for x in r["items"][:5])


def test_explain_eval_with_fake_llm_is_factual():
    r = M.explain_v2(M.Recorder(FakeLLM()), limit=20)
    assert r["n"] == 20 and r["fact_ok"] == 1.0 and r["format_error"] == 0


def test_upper95_rule_of_three():
    assert S.upper95(0, 200) == pytest.approx(0.0149, abs=5e-4)
    assert S.upper95(1, 250) < 0.03 < S.upper95(4, 250)


def test_non_inferiority_paired_bootstrap():
    ceil = [1.0] * 200
    same = [1.0] * 200
    worse = [1.0] * 170 + [0.0] * 30  # 15%p 낮음
    assert S.non_inferior(same, ceil, margin=0.05)["pass"]
    assert not S.non_inferior(worse, ceil, margin=0.05)["pass"]


def test_decide_role_applies_gates_then_non_inferiority_then_cost():
    ok = {"gates": {"G": True}, "score": [1.0] * 200}
    bad = {"gates": {"G": False}, "score": [1.0] * 200}
    d = S.decide_role({"opus": ok, "sonnet": ok, "haiku": bad}, ceiling="opus", cost_order=["haiku", "sonnet", "opus"],
                      margin=0.05)
    assert d["selected"] == "sonnet"
    assert d["rows"]["haiku"]["status"] == "탈락" and "G" in d["rows"]["haiku"]["reason"]
