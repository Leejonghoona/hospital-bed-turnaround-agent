"""상황별 금지 (08 계획 2단계 "단계별 허용 목록", 2단계 안전 검수 M2).

머리 입력(case_state·tool_outputs·exception)과 같은 값으로 결정적으로 판정한다. 그래서 측정(정답 세트)과
운영(게이트웨이)이 같은 함수를 쓴다. 규칙마다 원칙이 있고, 기준 시간은 머리 도구값(P90)을 쓴다.
"""

from __future__ import annotations

WAITS = ("wait", "wait_for_ipc", "wait_for_bed", "wait_for_next_ready")
REMINDS = ("remind_owner", "remind_ward", "re_request_cleaning")


def _contacted(cs: dict) -> bool:
    return bool(cs.get("contacted") or cs.get("incoming_contacted"))


def situational_block(inp: dict, action: str) -> str | None:
    """이 상황에서 이 행동을 막을 이유. 없으면 None."""
    ex, cs, to = inp.get("exception") or {}, inp.get("case_state") or {}, inp.get("tool_outputs") or {}
    trig, stage, att = ex.get("trigger"), ex.get("stage"), ex.get("attempts", 0) or 0
    base = action.split(":", 1)[0]
    dl = ex.get("deadline_in_min")
    if base in WAITS:
        # 기다리는 입원이 없는 단계 지연은 알림 뒤 기다릴 수 있다(환자 불편이지 병상 문제가 아님)
        # 입원 사례에서 오른 예외는 그 환자가 기다리는 것이고, 퇴원 쪽 예외는 기다리는 입원이 있을 때만 그렇다
        someone_waits = trig in ("promise_at_risk", "no_bed", "late_isolation", "discharge_date_changed",
                                 "info_changed_pending", "ward_objection", "call_up_stalled") \
            or bool(cs.get("waiting_matches")) or to.get("arrival_due_in_min") is not None
        if att >= 1 and someone_waits:
            return "wait_after_attempt"  # 앞선 행동이 통하지 않았는데 또 기다림 = 침묵 실패 (정답 세트 W-01)
        if dl is not None and dl <= 0:
            return "deadline_passed"
        if trig == "promise_at_risk":
            return "promise_needs_action"  # 환자에게 안내한 약속이 위험하면 기다리지 않는다
        if trig == "guardian_delay_same_day" and to.get("ready_p90_min") is not None and \
                to.get("arrival_due_in_min") is not None and to["ready_p90_min"] > to["arrival_due_in_min"]:
            return "next_patient_would_wait"  # 기다리면 다음 입원 환자가 병상을 기다린다
        if base == "wait_for_ipc" and cs.get("ipc_on_duty") is False:
            return "ipc_off_duty"
        if base == "wait_for_bed" and _contacted(cs) and trig == "discharge_date_changed":
            return "contacted_needs_recontact"  # 연락한 환자의 퇴원 연기: 기다리지 말고 재연락·추천안
        if base == "wait_for_next_ready":
            if cs.get("from_icu"):
                return "icu_stepdown"
            if cs.get("need") == "negative_pressure" and not to.get("negative_pressure_free"):
                return "no_negative_pressure"
            p90 = to.get("next_ready_p90_min", to.get("earliest_ready_p90_min"))
            limit = to.get("contact_deadline_in_min", dl)
            if p90 is not None and limit is not None and p90 > limit:
                return "not_ready_before_deadline"  # 연락 마감 안에 준비되지 않는다(P90 기준)
    if base == "offer_lounge" and (cs.get("isolation") or cs.get("isolation_discharge") or cs.get("mobility_aid")
                                   or to.get("lounge_available") is False):
        return "lounge_unsafe"
    if base == "request_bed_prep_first" and (cs.get("isolation_discharge") or cs.get("cleaning_type") == "terminal"):
        return "terminal_cleaning_pending"
    prev = str(cs.get("previous_action", "")).split(":", 1)[0]
    # 첫 시도가 곧 알림인 예외(단계 정체·호출 정체)는 attempts가 1 이상이면 이미 알린 것이다
    first_is_reminder = trig in ("sequence_stalled", "call_up_stalled") and not prev
    if base in REMINDS and att >= 1 and (prev == base or first_is_reminder):
        return "repeat_reminder"
    if base in ("rematch_now", "replan_now") and (_contacted(cs) or cs.get("committed")):
        return "stage_locked"
    if base == "rematch_now" and stage in ("after_contact", "after_commit", "after_arrival"):
        return "stage_locked"
    if base == "replan_now" and cs.get("correction_direction") == "loosen" and not cs.get("correction_confirmed"):
        return "loosen_unconfirmed"
    return None


__all__ = ["situational_block"]
