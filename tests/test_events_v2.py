"""S1: 설계 개정 새 이벤트 타입 (추가만, 옛 타입·의미 불변)."""

from datetime import datetime

from turnaround.events import Event, EventType
from turnaround.evaluation.scenario import ACTION_TYPES
from turnaround.evaluation.scoring import EVENT_TO_ACTION
from turnaround.fhir.adapter import bundle_to_events, events_to_bundle

NEW = ["billing_review_requested", "billing_review_done", "billing_review_reopened", "discharge_notice_given",
       "receipt_checked", "medication_handed_over", "guardian_delay_reported", "lounge_offered", "lounge_accepted",
       "bed_prep_requested", "bed_prep_done", "cleaning_inspected", "bed_vacancy_revoked",
       "ward_assignment_notified", "ward_objection_raised", "ward_prep_done", "patient_call_up", "patient_arrived",
       "screening_ordered", "screening_result_reported", "isolation_risk_alerted", "isolation_registration_mismatch",
       "contact_trace_requested", "room_change_requested", "room_change_withdrawn", "room_change_completed",
       "exception_raised", "exception_resolved", "action_proposed", "action_executed", "action_rejected",
       "action_approval_requested", "action_approved", "action_reported", "recheck_scheduled",
       "promise_made", "promise_kept", "promise_at_risk"]


def test_new_event_types_exist_and_old_ones_unchanged():
    values = {t.value for t in EventType}
    assert set(NEW) <= values
    assert EventType.WARD_NOTIFIED.value == "ward_notified"  # 철회 통지 의미 그대로
    assert EventType.BED_READY.value == "bed_ready"


def test_system_outputs_map_to_scoring_actions_and_scenarios_accept_them():
    want = {EventType.BED_PREP_REQUESTED: "request_bed_prep", EventType.BED_VACANCY_REVOKED: "revoke_vacancy",
            EventType.WARD_ASSIGNMENT_NOTIFIED: "notify_ward_assignment",
            EventType.CONTACT_TRACE_REQUESTED: "request_contact_trace",
            EventType.ISOLATION_RISK_ALERTED: "alert_isolation_risk",
            EventType.ISOLATION_REGISTRATION_MISMATCH: "alert_isolation_mismatch",
            EventType.EXCEPTION_RAISED: "raise_exception", EventType.ACTION_EXECUTED: "execute_action",
            EventType.ACTION_REJECTED: "reject_action", EventType.ACTION_APPROVAL_REQUESTED: "request_action_approval",
            EventType.PROMISE_MADE: "make_promise", EventType.PROMISE_AT_RISK: "flag_promise_at_risk"}
    for et, name in want.items():
        assert EVENT_TO_ACTION[et] == name
        assert name in ACTION_TYPES


def test_new_types_round_trip_through_fhir():
    t = datetime(2026, 3, 2, 9)
    evs = [Event(event_id=f"X:{v}", case_id="X", type=EventType(v), occurred_at=t, observed_at=t, source="his",
                 payload={"bed_id": "W72-R04-B1"}) for v in NEW]
    back = bundle_to_events(events_to_bundle(evs))
    assert [(e.event_id, e.type) for e in back] == [(e.event_id, e.type) for e in evs]
