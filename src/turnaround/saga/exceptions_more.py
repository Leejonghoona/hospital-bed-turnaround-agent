"""남은 예외 감지 (08 계획 2단계, 정답 세트 v2.1 트리거 이름). 감지는 결정적, 선택은 머리, 실행은 게이트웨이.

- E2 확장 `sequence_stalled`: 퇴원 당일 단계(심사, 퇴원약, 수납)가 기준보다 늦으면 단계 담당에게
- E6 `discharge_date_changed`: 퇴원 취소·연기로 매칭이 풀렸거나(연락 전) 담당 검토로 갔을 때(연락 뒤)
- E7 `info_changed_pending`: 확정 전 입원 환자의 정보가 정정됐을 때
- E8 `ward_objection`: 연락 뒤 병동 이의(연락 전은 게이트웨이가 결정적으로 해제·재매칭)
- E9 `promise_at_risk`: 연락한 입실 예정 시각 60분 전에도 병상이 준비되지 않았을 때
- E10 `call_up_stalled`: 병상 준비·수속 뒤 20분이 지나도 병동 준비(올려주세요 전제)가 없을 때
기준 시간은 모두 설계 가정이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from turnaround.events.models import EventType

STEP_LIMITS = {"billing_review": timedelta(minutes=90), "medication": timedelta(minutes=120),
               "billing_payment": timedelta(minutes=60)}
STEP_OWNER = {"billing_review": "billing", "medication": "pharmacy", "billing_payment": "billing"}
PROMISE_MARGIN = timedelta(minutes=60)
CALL_UP_GRACE = timedelta(minutes=20)


def _raise(out, case, key, at, exid, trigger, stage, p, **ctx):
    out.cmd(case, f"exception:{key}", EventType.EXCEPTION_RAISED, at, exception_id=exid, trigger=trigger,
            stage=stage, deadline=(at + p.exception_deadline).isoformat(), **ctx)


def _resolve(out, case, key, at, exid):
    out.cmd(case, f"exception_resolved:{key}", EventType.EXCEPTION_RESOLVED, at, exception_id=exid,
            how="condition_cleared")


def decide_more(s, now: datetime, out, p) -> None:
    if not p.exceptions_enabled:
        return
    _stalled_steps(s, now, out, p)
    _date_changed(s, now, out, p)
    _info_changed(s, now, out, p)
    _ward_objection(s, now, out, p)
    _promise_and_call_up(s, now, out, p)


def _stalled_steps(s, now, out, p):
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        if d.discharge_type not in ("normal", "ama") or d.seen(EventType.PATIENT_DEPARTED):
            continue
        waiting = sorted(m.admission_id for m in s.matches.values() if m.active and m.bed_id == d.bed_id)
        checks = (
            ("billing_review", d.seen(EventType.ORDER_CONFIRMED), EventType.BILLING_REVIEW_DONE,
             d.seen(EventType.BILLING_REVIEW_DONE) is not None or d.seen(EventType.DISCHARGE_NOTICE_GIVEN) is not None),
            ("medication", d.seen(EventType.DISCHARGE_RX_ISSUED), EventType.MEDICATION_READY, None),
            ("billing_payment", d.seen(EventType.DISCHARGE_NOTICE_GIVEN), EventType.BILLING_DONE, None),
        )
        for step, base, done_type, _ in checks:
            if base is None:
                continue
            due = base + STEP_LIMITS[step]
            done = d.seen(done_type)
            key, exid = f"stalled:{step}", f"E2:{d.id}:{step}"
            if now >= due and (done is None or done > due):
                if step == "billing_review" and not p.field_sequence:
                    continue  # 현장형 순서(심사 사건)를 쓰지 않는 병원이면 보지 않는다
                _raise(out, d.id, key, due, exid, "sequence_stalled", step, p, bed_id=d.bed_id,
                       context={"step": step, "step_owner": STEP_OWNER[step], "waiting_matches": len(waiting),
                                "next_admission_waiting": bool(waiting)})
            if done is not None and any(k.endswith(f"exception:{key}") for k in d.commands):
                _resolve(out, d.id, key, done, exid)


def _date_changed(s, now, out, p):
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        cancelled = d.seen(EventType.DISCHARGE_CANCELLED)
        if cancelled is None:
            continue
        for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
            if m.discharge_id != d.id:
                continue
            contacted = EventType.PATIENT_CONTACTED in m.facts
            _raise(out, m.admission_id, f"date_changed:{d.id}", cancelled, f"E6:{m.admission_id}:{d.id}",
                   "discharge_date_changed", "after_contact" if contacted else "before_contact", p,
                   admission_id=m.admission_id, bed_id=d.bed_id,
                   context={"contacted": contacted, "committed": EventType.MATCH_COMMITTED in m.facts,
                            "waiting_admissions": [m.admission_id]})


def _info_changed(s, now, out, p):
    from turnaround.events.corrections import correction_direction
    for c in s.corrections:
        case = c.case_id
        m = s.matches.get(case)
        if m is not None and EventType.MATCH_COMMITTED in m.facts:
            continue  # 확정 뒤 정정은 기존 경로(고정 배치 감시, 늦은 격리 보상)
        if case not in s.admission_payload:
            continue
        direction = correction_direction(s.admission_payload.get(case) or {}, c.payload["field"], c.payload.get("value"))
        _raise(out, case, f"info_changed:{c.event_id}", c.observed_at, f"E7:{case}:{c.event_id}",
               "info_changed_pending", "pending_commit", p, admission_id=case,
               context={"changed_field": c.payload["field"], "correction_direction": direction,
                        "correction_confirmed": c.event_id in s.confirmed_corrections,
                        "contacted": m is not None and EventType.PATIENT_CONTACTED in m.facts,
                        "waiting_admissions": [case] if m is not None and m.active else []})


def _ward_objection(s, now, out, p):
    for e in s.objections:
        m = s.matches.get(e.case_id)
        if m is None or EventType.PATIENT_CONTACTED not in m.facts:
            continue  # 연락 전은 게이트웨이가 결정적으로 해제·재매칭한다
        _raise(out, e.case_id, f"ward_objection:{e.event_id}", e.observed_at, f"E8:{e.case_id}:{e.event_id}",
               "ward_objection", "after_contact", p, admission_id=e.case_id, bed_id=e.payload.get("bed_id"),
               context={"contacted": True, "committed": EventType.MATCH_COMMITTED in m.facts, "ward_notified": True,
                        "objection_reason": e.payload.get("reason"), "waiting_admissions": [e.case_id]})


def _promise_and_call_up(s, now, out, p):
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        if not m.active or EventType.PATIENT_ARRIVED in m.facts:
            continue
        req = (s.admission_payload.get(m.admission_id) or {}).get("requested_at")
        if not req:
            continue
        promise = datetime.fromisoformat(req)
        from .machine import current_discharge
        d = current_discharge(s, m)
        ready_at = (d.seen(EventType.BED_MARKED_VACANT) or d.seen(EventType.BED_READY)) if d is not None else promise
        contacted = m.facts.get(EventType.PATIENT_CONTACTED)
        # E9 약속 위험: 연락했고(입실 시각을 알렸고) 약속 60분 전에도 병상이 준비되지 않음
        risk_at = promise - PROMISE_MARGIN
        key9, ex9 = f"promise:{m.bed_id}", f"E9:{m.admission_id}"
        if contacted is not None and d is not None and now >= risk_at and (ready_at is None or ready_at > risk_at):
            at = max(risk_at, contacted)
            out.cmd(m.admission_id, f"promise_at_risk:{m.bed_id}", EventType.PROMISE_AT_RISK, at, bed_id=m.bed_id,
                    promise_at=promise.isoformat())
            _raise(out, m.admission_id, key9, at, ex9, "promise_at_risk",
                   "cleaning_in_progress" if d.seen(EventType.CLEANING_STARTED) else "cleaning_pending", p,
                   admission_id=m.admission_id, bed_id=m.bed_id, discharge_id=d.id,
                   context={"contacted": True, "promise_at": promise.strftime("%H:%M"),
                            "cleaning_started": d.seen(EventType.CLEANING_STARTED) is not None,
                            "isolation_discharge": d.isolation})
        if ready_at is not None and any(k.endswith(f"exception:{key9}") for k in s.admission_commands(m.admission_id)):
            _resolve(out, m.admission_id, key9, ready_at, ex9)
        # E10 호출 정체: 확정, 병상 준비, 수속 뒤 20분이 지나도 병동 준비가 없음
        if EventType.MATCH_COMMITTED in m.facts and ready_at is not None:
            due = max(promise, ready_at) + CALL_UP_GRACE
            key10, ex10 = f"call_up:{m.bed_id}", f"E10:{m.admission_id}"
            prep = m.facts.get(EventType.WARD_PREP_DONE)
            if now >= due and (prep is None or prep > due):
                _raise(out, m.admission_id, key10, due, ex10, "call_up_stalled", "ready_no_callup", p,
                       admission_id=m.admission_id, bed_id=m.bed_id,
                       context={"bed_ready": True, "patient_waiting_at": "admission_office"})
            if prep is not None and any(k.endswith(f"exception:{key10}") for k in s.admission_commands(m.admission_id)):
                _resolve(out, m.admission_id, key10, prep, ex10)


__all__ = ["decide_more"]
