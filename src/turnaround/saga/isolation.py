"""격리 정보 처리 (설계 개정 3.7 (가), DECISIONS 2026-11-09).

- 고위험 조건·내성균 과거력: 근거를 붙여 알림만 한다. 막지 않고 확인 등급도 바꾸지 않는다. 격리 여부와 병실은 사람이 정한다.
- 양성 결과 + 격리 오더 없음: 정해진 시간이 지나면 감염관리실에 불일치 알림과 작업. 확정은 멈추지 않는다(인터뷰 2 Q7).
- 양성 결과는 격리 오더가 아니다. 격리 규칙은 사람의 ISOLATION_ORDER_ADDED 뒤에만 적용한다.
"""

from __future__ import annotations

from datetime import datetime

from turnaround.events.models import EventType

# 위험 조건 코드 → 입원 요청 payload에서 읽는 법
_RISK_READERS = {
    "mdro_history": lambda p: [f"mdro_history:{x}" for x in (p.get("mdro_history") or [])],
    "recent_other_hospital_stay": lambda p: ["recent_other_hospital_stay"] if p.get("recent_other_hospital_stay") else [],
    "overseas_hospital_stay": lambda p: ["overseas_hospital_stay"] if p.get("overseas_hospital_stay") else [],
    "icu_transfer": lambda p: ["icu_transfer"] if p.get("from_icu") else [],
}


def risk_basis(payload: dict, conditions) -> list[str]:
    out: list[str] = []
    for c in conditions:
        out += _RISK_READERS.get(c, lambda p: [])(payload)
    return out


def decide_isolation_info(s, now: datetime, out, policy) -> None:
    if policy.isolation_risk_conditions:
        for case, payload in sorted(s.admission_payload.items()):
            if EventType.ADMISSION_CANCELLED in s.admission_facts.get(case, {}):
                continue
            basis = risk_basis(payload, policy.isolation_risk_conditions)
            seen = s.admission_facts.get(case, {}).get(EventType.ADMISSION_REQUESTED)
            if basis and seen is not None:
                out.cmd(case, "isolation_risk_alert", EventType.ISOLATION_RISK_ALERTED, seen, basis=basis,
                        to=list(policy.isolation_alert_to), screening_pending=bool(s.screening_pending.get(case)))
    for case, results in sorted(s.screening_results.items()):
        for event_id, at, organism, result in results:
            if result != "positive":
                continue
            due = at + policy.isolation_mismatch_after
            ordered = s.isolation_ordered.get(case)
            if now < due or (ordered is not None and ordered <= due):
                continue
            out.cmd(case, f"isolation_mismatch:{event_id}", EventType.ISOLATION_REGISTRATION_MISMATCH, due,
                    organism=organism, result_event=event_id)
            out.cmd(case, f"task:isolation_mismatch:{event_id}", EventType.HUMAN_TASK_CREATED, due,
                    reason="isolation_mismatch", kind="isolation_mismatch", assignee="ipc", organism=organism)


def _bed_fits(bed_room: dict, bed: str, payload: dict) -> bool:
    """격리 오더가 붙은 뒤에도 매칭된 병상이 기준에 맞는가. 병실 정보가 없으면 판단하지 않는다(맞는다고 본다)."""
    if bed not in bed_room:
        return True
    room_type, cohort = bed_room[bed]
    prec = set(payload.get("precautions") or [])
    if "airborne" in prec:
        return room_type == "negative_pressure"
    if prec:
        return room_type in ("single", "negative_pressure") or (cohort is not None and cohort == payload.get("organism"))
    return True


def decide_late_isolation_admission(s, out, policy) -> None:
    """입원 측 늦은 격리 오더 보상 (설계 개정 3.7 (나)). 되돌릴 수 있는 것은 자동, 연락·확정 뒤는 사람."""
    from .machine import _notify_ward_if_accepted  # 순환 import 피함
    for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
        ordered = s.isolation_ordered.get(m.admission_id)
        created = min(m.facts.values()) if m.facts else None
        if ordered is None or created is None or ordered < created or not m.active:
            continue
        if _bed_fits(policy.bed_room, m.bed_id, s.isolation_order_payload.get(m.admission_id, {})):
            continue
        case, bed = m.admission_id, m.bed_id
        stage = ("after_commit" if EventType.MATCH_COMMITTED in m.facts
                 else "after_contact" if EventType.PATIENT_CONTACTED in m.facts else "before_contact")
        if policy.exceptions_enabled:  # 필수 행동 뒤 무엇을 할지는 예외 판단 머리가 고른다 (E1)
            out.cmd(case, f"exception:E1:{bed}", EventType.EXCEPTION_RAISED, ordered, exception_id=f"E1:{case}:{bed}",
                    trigger="late_isolation", admission_id=case, bed_id=bed, stage=stage,
                    deadline=(ordered + policy.exception_deadline).isoformat())
        if EventType.MATCH_COMMITTED in m.facts:
            out.cmd(case, f"task:isolation_after_commit:{bed}", EventType.HUMAN_TASK_CREATED, ordered, bed_id=bed,
                    reason="isolation_order_after_commit", assignee="bed_manager")
        elif EventType.PATIENT_CONTACTED in m.facts:
            out.cmd(case, f"review:isolation:{bed}", EventType.CASE_RECLASSIFIED_REVIEW, ordered, bed_id=bed,
                    reason="isolation_order_after_contact")
            out.cmd(case, f"task:recontact:isolation:{bed}", EventType.HUMAN_TASK_CREATED, ordered, bed_id=bed,
                    reason="recontact")
        else:
            out.cmd(case, f"prematch_released:isolation:{bed}", EventType.PREMATCH_RELEASED, ordered, bed_id=bed,
                    reason="isolation_order_added")
            _notify_ward_if_accepted(m, out, ordered, "isolation_order_added")
            out.cmd(case, f"rematch:isolation:{bed}", EventType.REMATCH_REQUESTED, ordered, target="admission",
                    admission_id=case)


def decide_contact_trace(s, out, policy) -> None:
    """입실한 환자에게 격리 오더가 오면 같은 다인실 동실자를 감염관리실에 올린다 (이름 없음, 결정은 사람)."""
    for case, ordered in sorted(s.isolation_ordered.items()):
        m = s.matches.get(case)
        if m is None or EventType.PATIENT_ARRIVED not in m.facts or m.facts[EventType.PATIENT_ARRIVED] > ordered:
            continue
        room_type = policy.bed_room.get(m.bed_id, ("multi", None))[0]
        if room_type != "multi":
            continue
        room = m.bed_id.rsplit("-", 1)[0]  # 합성 병상 ID 규칙: 병실ID-B번호
        contacts = []
        for o in s.matches.values():
            arrived = o.facts.get(EventType.PATIENT_ARRIVED)
            if o.admission_id != case and o.bed_id.rsplit("-", 1)[0] == room and arrived and arrived <= ordered:
                contacts.append({"case": o.admission_id, "bed_id": o.bed_id})
        for d in s.discharges.values():
            if d.bed_id and d.bed_id.rsplit("-", 1)[0] == room and EventType.DISCHARGE_CANCELLED not in d.facts:
                dep = d.seen(EventType.PATIENT_DEPARTED)
                if dep is None or dep > m.facts[EventType.PATIENT_ARRIVED]:
                    contacts.append({"case": d.id, "bed_id": d.bed_id})
        out.cmd(case, f"contact_trace:{room}", EventType.CONTACT_TRACE_REQUESTED, ordered, index_case=case,
                room_id=room, contacts=sorted(contacts, key=lambda c: c["bed_id"]), to="ipc",
                window={"from": m.facts[EventType.PATIENT_ARRIVED].isoformat(), "to": ordered.isoformat()})
