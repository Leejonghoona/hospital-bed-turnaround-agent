"""S7: 통보형 병동 확인 (DECISIONS 2026-11-09 C, 인터뷰 Q1).

- 병동에 수용 여부를 묻지 않는다. 원무 배정 담당의 한 동작이 확정이고, 확정 뒤 병동에 통보한다.
- 병동은 이의를 제기할 수 있다: 환자 연락 전이면 자동 해제·재매칭, 연락 뒤면 담당 검토·재연락.
- 안전 관련 검토 사례는 지금처럼 담당 검토(판단)를 거친다.
"""

from test_gateway import adm, at, ev, make, prop
from turnaround.events import EventType
from turnaround.gateway import load_policy

FIELD = load_policy(profile="field_default")


def _types(store, t):
    return [e for e in store.read() if e.type is t]


def test_notify_mode_one_action_commits_and_notifies_ward_without_asking():
    store, gw = make([adm()], policy=FIELD)
    d = gw.submit(prop(store=store), at(9))
    assert d.status == "awaiting_manager"
    d2 = gw.manager_approve("p1", at(9, 5))
    assert d2.status == "committed"
    assert not _types(store, EventType.WARD_ACCEPTANCE_ASKED) and not _types(store, EventType.MANAGER_APPROVED)
    note = _types(store, EventType.WARD_ASSIGNMENT_NOTIFIED)
    assert len(note) == 1 and note[0].payload["bed_id"] == "W72-R04-B2"


def test_review_cases_still_need_a_judgement_then_commit_in_notify_mode():
    store, gw = make([adm(organism="MRSA", gender="M")], policy=FIELD)
    d = gw.submit(prop(bed="W71-R02-B1", store=store), at(9))
    assert d.status == "awaiting_review"
    d2 = gw.manager_approve("p1", at(9, 5))
    assert d2.status == "committed" and _types(store, EventType.MANAGER_APPROVED)


def test_ward_objection_before_contact_releases_and_requests_rematch():
    store, gw = make([adm()], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    gw.manager_approve("p1", at(9, 5))
    d = gw.ward_objection("p1", "staffing", at(9, 20))
    assert d.status == "released"
    rel = [e for e in _types(store, EventType.PREMATCH_RELEASED) if e.source == "gateway"]
    assert rel and rel[0].payload["reason"] == "ward_objection"
    assert _types(store, EventType.REMATCH_REQUESTED)
    assert "A1" not in gw._assignments(store.read())


def test_ward_objection_after_contact_goes_to_review_and_recontact():
    store, gw = make([adm()], policy=FIELD)
    gw.submit(prop(store=store), at(9))
    gw.manager_approve("p1", at(9, 5))
    store.append(ev("A1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9, 10), "human"))
    d = gw.ward_objection("p1", "staffing", at(9, 20))
    assert d.status == "awaiting_review"
    assert [e for e in _types(store, EventType.HUMAN_TASK_CREATED) if e.payload.get("kind") == "recontact"]
    assert "A1" in gw._assignments(store.read())  # 연락한 배정은 자동으로 풀지 않는다


def test_reply_mode_is_unchanged():
    store, gw = make([adm()])
    gw.submit(prop(store=store), at(9))
    assert gw.manager_approve("p1", at(9, 5)).status == "awaiting_ward"
    assert _types(store, EventType.WARD_ACCEPTANCE_ASKED)
