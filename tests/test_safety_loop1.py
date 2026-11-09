"""최고점 루프 1회차 안전 검토가 재현한 결함의 회귀 시험 (합성 데이터).

A 감염관리실 사전 승인을 원무가 문자열로 주장하면 통과하던 것
C 다인실 옆 침상: 같은 병실 격리 퇴실의 종말소독 전인데 자동 확정·직접 수정되던 것
D 격리·소아 같은 안전 검토 사유를 승격(apply)이나 병원 프로필로 지울 수 있던 것
"""

import pytest
import yaml

from test_gateway import adm, at, ev, make, prop  # noqa: F401 - 같은 합성 도우미
from turnaround.agent import autonomy
from turnaround.events import EventType
from turnaround.gateway import load_policy


def _mo(gw, case, bed, now, **kw):
    return gw.manual_override(case, bed, actor="u1", role="bed_manager", reason="clinical", now=now, **kw)


def test_self_claimed_ipc_approval_is_refused():
    store, gw = make([adm(gender="M", organism="MTB")])
    d = _mo(gw, "A1", "W71-R02-B1", at(12), safety_exception_by=("내가IPC", "ipc"))
    assert d.status == "refused" and d.tier == "preapproval"


def test_recorded_ipc_approval_lets_override_through_and_names_approver():
    store, gw = make([adm(gender="M", organism="MTB")])
    a = gw.approve_safety_exception("A1", "W71-R02-B1", approver="ipc-1", role="ipc", now=at(11, 50))
    assert a.status == "recorded"
    d = _mo(gw, "A1", "W71-R02-B1", at(12), safety_exception_by=("ipc-1", "ipc"))
    assert d.status == "applied"


def test_safety_exception_approval_rejects_wrong_role_and_self_approval():
    store, gw = make([adm(gender="M", organism="MTB")])
    assert gw.approve_safety_exception("A1", "W71-R02-B1", approver="u1", role="bed_manager", now=at(11)).status == "refused"
    gw.approve_safety_exception("A1", "W71-R02-B1", approver="u1", role="ipc", now=at(11))
    # 요청자(u1)와 승인자(u1)가 같으면 인정하지 않는다
    assert _mo(gw, "A1", "W71-R02-B1", at(12), safety_exception_by=("u1", "ipc")).status == "refused"


def test_approval_for_other_bed_does_not_count():
    store, gw = make([adm(gender="M", organism="MTB")])
    gw.approve_safety_exception("A1", "W71-R02-B2", approver="ipc-1", role="ipc", now=at(11))
    assert _mo(gw, "A1", "W71-R02-B1", at(12), safety_exception_by=("ipc-1", "ipc")).status == "refused"


def _cre_neighbor():
    return [adm(), ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W72-R04-B1",
                      isolation_order=True, precautions=["contact"], organism="CRE"),
            ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(8), bed_id="W72-R04-B1")]


def test_neighbor_bed_of_pending_terminal_cleaning_is_not_committed():
    store, gw = make(_cre_neighbor(), policy=load_policy())
    d = gw.submit(prop(bed="W72-R04-B2", store=store), at(9))
    assert d.status != "committed"


def test_neighbor_bed_manual_override_refused_until_terminal_done():
    store, gw = make(_cre_neighbor(), policy=load_policy())
    assert _mo(gw, "A1", "W72-R04-B3", at(9)).status == "refused"
    store.append(ev("D1", "terminal_done", EventType.CLEANING_DONE, at(10), source="human", bed_id="W72-R04-B1",
                    cleaning_type="terminal"))
    assert _mo(gw, "A1", "W72-R04-B3", at(11)).status == "applied"


@pytest.mark.parametrize("unit", ["isolation", "pediatric", "preemptive_isolation", "missing_input"])
def test_promotion_cannot_remove_never_units(unit):
    with pytest.raises(ValueError):
        autonomy.apply(load_policy(), unit, "auto")


def test_profile_without_safety_review_reasons_fails_to_load(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"review_reasons": ["no_bed", "missing_input"]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(profile=str(p))


def test_profile_cannot_shrink_never_list(tmp_path):
    p = tmp_path / "bad2.yaml"
    p.write_text(yaml.safe_dump({"autonomy_promotion": {"never": []}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(profile=str(p))


# ---- 상황별 금지 입력은 게이트웨이가 이벤트에서 직접 다시 계산한다 (안전 검토 4) ----

def _late_iso_weekend_night():
    from turnaround.saga import SagaPolicy, SagaRunner
    from test_gateway import H
    pol = load_policy()
    d0 = 5  # 2026-03-07 토요일
    evs = [adm(gender="F", dept="일반외과"),
           ev("A1", "prematch_created:W72-R04-B1", EventType.PREMATCH_CREATED, at(17, 40, day=d0 - 1), "agent",
              bed_id="W72-R04-B1"),
           ev("A1", "iso_late", EventType.ISOLATION_ORDER_ADDED, at(2, day=d0), "his", precautions=["airborne"],
              organism="MTB")]
    store, gw = make(evs, policy=pol)
    SagaRunner(store, SagaPolicy.from_policy(pol, H.rooms, H.beds)).step(at(2, 5, day=d0))
    return store, gw, pol, at(2, 10, day=d0)


@pytest.mark.parametrize("forged", [{}, {"exception": {"trigger": "late_isolation", "stage": "before_contact", "attempts": 0},
                                        "case_state": {"ipc_on_duty": True}}])
def test_wait_for_ipc_at_weekend_night_is_blocked_whatever_the_agent_claims(forged):
    from turnaround.agent import FakeLLM
    from turnaround.agent.brain import ActionChoice, ExceptionBrain
    store, gw, pol, now = _late_iso_weekend_night()
    brain = ExceptionBrain(gw, FakeLLM(), pol)
    ex = brain.open_exceptions(store.read())[0]
    assert ex.payload["trigger"] == "late_isolation"
    choice = ActionChoice(exception_id=ex.payload["exception_id"], trigger="late_isolation", case=ex.case_id,
                          action="wait_for_ipc", params={}, rationale="x",
                          basis_seq=max(e.seq or 0 for e in store.read()), mode="llm", basis_input=forged)
    d = gw.submit_action(choice, now)
    assert d.status == "rejected" and d.reasons == ["ipc_off_duty"]


def test_s2_counts_neighbor_bed_commit_before_terminal_cleaning():
    """안전 측정도 병실 단위로 본다 (측정이 게이트웨이와 같은 사각지대를 공유하던 문제)."""
    from turnaround.evaluation.safety_exec import executed_safety
    evs = _cre_neighbor() + [
        ev("D1", "terminal_req", EventType.CLEANING_REQUEST_ISSUED, at(8, 1), "saga", bed_id="W72-R04-B1",
           cleaning_type="terminal"),
        ev("A1", "neighbor_commit", EventType.MATCH_COMMITTED, at(9), "gateway", bed_id="W72-R04-B2")]
    store, gw = make(evs, policy=load_policy())
    s = executed_safety(gw, store.read())
    assert s["S2"]["violations"] >= 1 and "commit_in_room_before_disinfection" in s["S2"]["codes"]
