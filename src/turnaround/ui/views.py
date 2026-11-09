"""역할별 화면 데이터 (08 계획 4단계). 화면(Streamlit)과 시험이 같은 함수를 쓴다. 합성 데이터 전용.

- 원무(병상 배정 담당): 에이전트 확정 통보(근거·되돌리기), 사람 승인 카드, 환자 연락 할 일, 담당 검토, 막힌 판단 기록
- 병동(책임간호사): 배정 통보 → 병동 준비 → "올려주세요" → 입실, 참고 알림
- 감염관리실: 늦은 격리·격리 사안 올림, 종말소독 진행
- 관리자: 자율권 승격·강등 카드(근거), 사람 개입, 실행 안전
- 온보딩: 지금 묶음의 질문과 진행률
"""

from __future__ import annotations

from datetime import datetime

from turnaround.events import EventType
from turnaround.saga import fold
from turnaround.saga.worklist import is_notice, load_operations, open_tasks

ACTION_KO = {"propose_rematch": "다른 병상 추천안 승인", "request_recontact": "환자 재연락", "rematch_now": "규칙으로 다시 찾기",
             "escalate": "사람에게 올림", "remind_owner": "단계 담당에게 알림", "remind_ward": "병동에 알림",
             "re_request_cleaning": "청소 재요청", "request_bed_prep_first": "침상 정리 먼저", "share_eta": "예상 시각 공유",
             "offer_lounge": "보호자 라운지 제안", "wait": "기다림", "wait_for_next_ready": "다음 병상 기다림",
             "wait_for_bed": "병상 기다림", "wait_for_ipc": "감염관리실 기다림", "replan_now": "정정 값으로 다시 찾기",
             "request_postpone_contact": "연락 보류"}
TRIGGER_KO = {"late_isolation": "늦은 격리", "sequence_stalled": "퇴원 단계 정체", "guardian_delay_same_day": "보호자 지연",
              "cleaning_overdue": "청소 지연", "no_bed": "병상 없음", "discharge_date_changed": "퇴원 취소·연기",
              "info_changed_pending": "환자 정보 정정", "ward_objection": "병동 이의", "promise_at_risk": "약속 위험",
              "call_up_stalled": "호출 정체"}
REASON_KO = {"stage_locked": "연락·확정·입실 뒤 자동 변경 금지", "wait_after_attempt": "시도 뒤 또 기다림 금지",
             "promise_needs_action": "약속 위험에서 기다림 금지", "not_ready_before_deadline": "마감 안에 준비 안 됨",
             "lounge_unsafe": "격리·이동 보조 환자 라운지 금지", "not_departed": "퇴실 전 청소·침상 정리 금지",
             "terminal_cleaning_pending": "종말소독 전 침상 정리 금지", "max_attempts": "같은 알림 반복 금지",
             "deadline_passed": "마감 뒤 기다림 금지", "not_allowed": "허용 목록 밖", "no_request_to_remind": "재요청할 청소 요청 없음",
             "repeat_reminder": "같은 알림 반복 금지", "next_patient_would_wait": "다음 환자가 기다리게 됨",
             "loosen_unconfirmed": "안전을 푸는 정정 확인 전", "stale_basis": "판단 정보가 바뀜(다시 고름)",
             "approver_role": "승인 역할 아님", "contacted": "연락한 환자"}
ORG_KO = {"MTB": "결핵균(MTB)", "MRSA": "MRSA", "VRE": "VRE", "CRE": "CRE", "CDIFF": "C. difficile"}
PREC_KO = {"contact": "접촉주의", "droplet": "비말주의", "airborne": "공기주의"}
REASON_TASK_KO = {"isolation_order_after_commit": "확정 뒤 격리 오더", "isolation_after_commit": "확정 뒤 격리 오더",
                  "bed_occupancy_conflict": "병상 점유 기록 충돌", "sequence_stalled": "퇴원 단계 정체"}
SAFETY_KO = {"S1": ("격리·성별 배정 위반", "격리 환자를 맞지 않는 병실에, 성별 병실에 다른 성별을 배정한 경우"),
             "S2": ("종말소독 누락", "격리 퇴실 병상을 종말소독 없이 다음 환자에게 쓴 경우"),
             "S3": ("청소 전 빈 병상 전환", "청소·침상 정리가 끝나기 전에 빈 병상으로 바꾼 경우"),
             "S4": ("한 병상 두 환자", "앞 환자가 나가기 전에 다음 환자를 입실시킨 경우")}
KIND_KO = {"isolation_notice": "늦은 격리 통지", "call_up_held": "올려주세요 보류", "recontact": "환자 재연락", "fee_consent": "상급병실 차액 동의", "brain_escalate": "예외 올림",
           "order_nudge": "퇴원 처방 재촉", "call_up_reminder": "올려주세요 알림", "approval_rejected": "승인 거부 안내",
           "postpone_contact": "연락 보류", "data_fix": "입력 보완", "recontact_prepare": "재연락 준비",
           "nudge_late_discharges": "늦은 퇴원 재촉", "cleaning_track": "청소 추적", "missing_bed_prep": "침상 정리 누락"}


FACT_KO = {"promise_at": ("안내한 시각", None), "cleaning_started": ("청소 시작", {True: "함", False: "안 함"}),
           "departed": ("앞 환자 퇴실", {True: "함", False: "안 함"}),
           "isolation_discharge": ("격리 퇴실", {True: "예", False: "아니오"}),
           "contacted": ("환자 연락", {True: "함", False: "안 함"}), "step": ("단계", None)}


def _facts(ctx: dict) -> list[str]:
    out = []
    for k, (label, m) in FACT_KO.items():
        if k in ctx:
            out.append(f"{label} {m.get(ctx[k], ctx[k]) if m else ctx[k]}")
    return out


_TODAY = {"d": None}


def _hm(t) -> str:
    """오늘이면 시:분, 다른 날이면 '어제 18:15'처럼 (4단계 화면 검수 1: 전날 일이 섞여 보임)."""
    if not t:
        return "-"
    d = _TODAY["d"]
    if d is not None and t.date() != d:
        diff = (d - t.date()).days
        return f"{'어제' if diff == 1 else f'{diff}일 전' if diff > 1 else t.strftime('%m-%d')} {t:%H:%M}"
    return t.strftime("%H:%M")


def _today(st) -> None:
    _TODAY["d"] = st.now.date()


def _kind(k: str | None) -> str:
    k = k or ""
    if k in REASON_TASK_KO:
        return REASON_TASK_KO[k]
    if k.startswith("step_nudge:"):
        return {"step_nudge:medication": "퇴원약 재촉(약제부)", "step_nudge:billing_review": "퇴원심사 재촉",
                "step_nudge:billing_payment": "수납 재촉"}.get(k, k)
    return KIND_KO.get(k, k)


def _decided(ev) -> set[str]:
    return {e.payload.get("exception_id") for e in ev if e.payload.get("actor")
            and e.type in (EventType.ACTION_APPROVED, EventType.ACTION_REJECTED)}


def pending_approvals(st) -> list[dict]:
    _today(st)
    ev = st.store.read()
    done = _decided(ev) | {e.payload.get("exception_id") for e in ev if e.type is EventType.EXCEPTION_RESOLVED}
    raised = {e.payload["exception_id"]: e for e in ev if e.type is EventType.EXCEPTION_RAISED}
    out = []
    for e in ev:
        if e.type is not EventType.ACTION_APPROVAL_REQUESTED or e.payload["exception_id"] in done:
            continue
        r = raised.get(e.payload["exception_id"])
        prop = next((p for p in reversed(ev) if p.type is EventType.ACTION_PROPOSED
                     and p.payload.get("exception_id") == e.payload["exception_id"]
                     and p.payload.get("action") == e.payload["action"]), None)
        ctx = (r.payload.get("context") if r else {}) or {}
        from datetime import datetime as _dt
        dl = _dt.fromisoformat(r.payload["deadline"]) if r and r.payload.get("deadline") else None
        out.append({"exception_id": e.payload["exception_id"], "case": e.case_id, "action": e.payload["action"],
                    "overdue": bool(dl and dl < st.now), "requested_at": e.observed_at,
                    "action_ko": ACTION_KO.get(e.payload["action"], e.payload["action"]),
                    "trigger_ko": TRIGGER_KO.get(r.payload["trigger"], "") if r else "",
                    "bed": r.payload.get("bed_id") if r else None, "since": _hm(e.observed_at),
                    "deadline": _hm(datetime.fromisoformat(r.payload["deadline"])) if r and r.payload.get("deadline") else "",
                    "rationale": (prop.payload.get("rationale") if prop else "") or "",
                    "facts": _facts(ctx)})
    # 마감이 지난 요청이 맨 위(지금 처리), 그다음 최신 요청 (4단계 화면 검수 3)
    return sorted(out, key=lambda a: (not a["overdue"], -a["requested_at"].timestamp()))


def blocked_decisions(st, limit: int = 8) -> list[dict]:
    """게이트웨이가 막은 에이전트 판단 (안전장치가 실제로 일한 기록). 오늘 것만."""
    _today(st)
    ev = [e for e in st.store.read() if e.observed_at.date() == st.now.date()]
    out = [{"time": _hm(e.observed_at), "case": e.case_id, "action": ACTION_KO.get(e.payload["action"], e.payload["action"]),
            "reason": ", ".join(REASON_KO.get(r, r) for r in e.payload.get("reasons", []))}
           for e in ev if e.type is EventType.ACTION_REJECTED and "stale_basis" not in e.payload.get("reasons", [])]
    return out[-limit:]


def admission_view(st) -> dict:
    from turnaround.ui.demo import review_queue
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    notices = []
    for e in ev:
        if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.payload.get("committed_by") == "agent" \
                and e.observed_at.date() == st.now.date():
            b = e.payload.get("basis") or {}
            m = s.matches.get(e.case_id)
            notices.append({"case": e.case_id, "bed": e.payload.get("bed_id"), "time": _hm(e.observed_at),
                            "proposal_id": e.payload.get("proposal_id"),
                            "score": b.get("score"), "runner_up": b.get("runner_up"),
                            "waived": b.get("waived_reasons") or [], "alternatives": b.get("alternatives") or [],
                            "undo_until": (e.payload.get("objection_until") or "")[11:16],
                            "active": bool(m and m.active and m.bed_id == e.payload.get("bed_id"))})
    contacts = [{"case": c, "bed": m.bed_id, "committed": _hm(m.facts[EventType.MATCH_COMMITTED])}
                for c, m in sorted(s.matches.items()) if m.active and EventType.MATCH_COMMITTED in m.facts
                and EventType.PATIENT_CONTACTED not in m.facts]
    ops = load_operations()
    tasks = [{"kind": _kind(t.payload.get("kind") or t.payload.get("reason")),
              "why": REASON_TASK_KO.get(t.payload.get("reason"), ""), "case": t.case_id,
              "bed": t.payload.get("bed_id"), "due": _hm(ops.due(t))}
             for t in open_tasks(ev, st.now).values() if t.type is EventType.HUMAN_TASK_CREATED and not is_notice(t)
             and (t.payload.get("assignee") or "bed_manager") in ("bed_manager", "admission_office", "cashier")]
    return {"notices": notices, "approvals": pending_approvals(st), "contacts": contacts, "reviews": review_queue(st),
            "tasks": tasks, "blocked": blocked_decisions(st)}


def ward_view(st) -> dict:
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    held = {t.case_id: REASON_TASK_KO.get(t.payload.get("reason"), "") for t in open_tasks(ev, st.now).values()
            if t.type is EventType.HUMAN_TASK_CREATED and t.payload.get("kind") == "call_up_held"}
    flow = []
    for e in ev:
        if e.type is not EventType.WARD_ASSIGNMENT_NOTIFIED:
            continue
        m = s.matches.get(e.case_id)
        if m is None or not m.active or m.bed_id != e.payload.get("bed_id"):
            continue  # 철회·재매칭된 통보는 지금 할 일이 아니다
        f = m.facts
        step = ("입실" if EventType.PATIENT_ARRIVED in f else "올려주세요 보냄" if EventType.PATIENT_CALL_UP in f
                else "병동 준비 완료" if EventType.WARD_PREP_DONE in f else "준비 필요")
        flow.append({"case": e.case_id, "bed": e.payload.get("bed_id"), "notified": _hm(e.observed_at), "step": step,
                     "hold": held.get(e.case_id, ""), "proposal_id": e.payload.get("proposal_id"),
                     "by": "에이전트 확정" if e.payload.get("committed_by") == "agent" else "사람 확정"})
    todo = [f for f in flow if f["step"] == "준비 필요" or f["hold"]]
    open_ = open_tasks(ev, st.now).values()
    notes = [{"kind": _kind(t.payload.get("kind")), "case": t.case_id, "bed": t.payload.get("bed_id"),
              "time": _hm(t.observed_at)} for t in open_ if t.type is EventType.HUMAN_TASK_CREATED
             and t.payload.get("assignee") == "ward"]
    return {"todo": todo, "flow": flow, "notes": notes}


def ipc_view(st) -> dict:
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    def _card(c: dict, trig) -> str:
        parts = [TRIGGER_KO.get(trig, "")] if trig else []
        if c.get("rejected_action"):
            parts.append(f"막은 행동 {ACTION_KO.get(c['rejected_action'], c['rejected_action'])}"
                         f"({REASON_KO.get(c.get('rejected_reason'), c.get('rejected_reason'))})")
        if c.get("agent_action"):
            parts.append(f"에이전트 제안: {ACTION_KO.get(c['agent_action'], c['agent_action'])}"
                         + (" (원무 승인 대기)" if c["agent_action"] in ("propose_rematch", "request_recontact") else ""))
        if c.get("deadline"):
            parts.append(f"마감 {str(c['deadline'])[11:16]}")
        return " · ".join(p for p in parts if p)
    done = {e.payload.get("task_id") for e in ev if e.type is EventType.HUMAN_TASK_DONE}
    alerts = [{"task_id": e.event_id, "checked": e.event_id in done, "case": e.case_id,
               "kind": _kind(e.payload.get("kind")), "time": _hm(e.observed_at),
               "card": _card(e.payload.get("card") or {}, e.payload.get("trigger"))} for e in ev if e.type is EventType.HUMAN_TASK_CREATED
              and (e.payload.get("assignee") == "ipc" or "ipc" in (e.payload.get("also_notify") or []))]
    iso = [{"case": e.case_id, "time": _hm(e.observed_at),
            "precautions": ", ".join(PREC_KO.get(p, p) for p in e.payload.get("precautions") or []),
            "organism": ORG_KO.get(e.payload.get("organism"), e.payload.get("organism") or "-")}
           for e in ev if e.type is EventType.ISOLATION_ORDER_ADDED]
    terminal = []
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        if d.isolation and EventType.PATIENT_DEPARTED in d.facts:
            done = any(k == "terminal" for _, k in d.cleaning_done_kinds)
            terminal.append({"bed": d.bed_id, "departed": _hm(d.facts[EventType.PATIENT_DEPARTED]),
                             "status": "종말소독 완료" if done else "종말소독 대기"})
    return {"alerts": alerts, "isolation_orders": iso, "terminal": terminal}


def manager_view(st) -> dict:
    from turnaround.agent.autonomy import evaluate, unit_outcomes
    from turnaround.evaluation.human_load import human_load
    from turnaround.evaluation.safety_exec import executed_safety
    ev = st.store.read()
    rows = evaluate(unit_outcomes(ev), st.gw.policy)
    rec_ko = {"promote": "승격 제안", "demote": "강등 제안", "hold": "유지"}
    unit_ko = {"official_commit": "공식 확정", "close_scores": "1·2순위 점수 근접", "single_room_preference": "1인실 희망",
               "grade_upgrade": "상급병실 배정(차액)", "pediatric": "소아", "icu_stepdown": "중환자실 하향",
               "isolation": "격리", "preemptive_isolation": "선제 격리", "no_bed": "병상 없음"}
    cards = [{"unit": unit_ko.get(r["unit"], r["unit"]), "mode": "에이전트" if r["mode"] == "auto" else "사람 확인",
              "evidence": f"사람 결정 {r['human_n']}건 중 동의 {r['human_agreed']}건(하한 {r['agreement_lower95']:.0%})",
              "recommendation": rec_ko[r["recommendation"]], "locked": r["never"]} for r in rows]
    sf = executed_safety(st.gw, ev)
    from turnaround.evaluation.flow import day_flow
    day = (st.now.date() - st.h.config.base_date).days
    fl = day_flow(st.h, ev, day)
    today_adm = [a.id for a in st.h.admissions if a.day == day and a.cancelled_at is None]
    committed = {e.case_id for e in ev if e.type is EventType.MATCH_COMMITTED}
    why = {}
    for c in today_adm:
        if c in committed:
            continue
        r = next((e.payload.get("reasons") for e in reversed(ev) if e.type is EventType.CASE_ROUTED_REVIEW
                  and e.case_id == c), None)
        k = "조건 맞는 병상 없음(퇴원 병상 대기)" if r and "no_bed" in r else "담당 검토 중" if r else "아직 배정 시각 전"
        why[k] = why.get(k, 0) + 1
    return {"cards": cards, "human_load": human_load(ev),
            "flow": {"퇴실(12시 이전/전체)": f"{fl['departed_before_noon']}/{fl['discharges']}",
                     "퇴실 시각 중앙값": fl["departure_median"] or "-", "병상 준비 중앙값": fl["ready_median"] or "-",
                     "수속→입실 평균(분)": fl["entry_wait_mean_min"] if fl["entry_wait_mean_min"] is not None else "-"},
            "unassigned": why,
            "safety": [{"지표": SAFETY_KO[k][0], "뜻": SAFETY_KO[k][1], "위반": sf[k]["violations"],
                        "검사 수": sf[k].get("denominator", "-")} for k in ("S1", "S2", "S3", "S4")]}


STAGE_KO = {"structure": "구조(병상 표)", "format": "형식(시각·구분자)", "column": "열", "code": "코드표"}


def onboarding_view(session) -> dict:
    g = session.current()
    answered = len(session.answers)
    if g is None:
        return {"stage": "done", "questions": [], "answered": answered, "remaining": 0}
    stage_ko = STAGE_KO[g.stage]
    qs = [{"id": q.id, "item": q.item, "why": q.why, "proposal": q.proposal, "options": list(q.options or [])[:4]}
          for q in g.questions]
    return {"stage": g.stage, "stage_ko": stage_ko, "round": g.round, "questions": qs, "answered": answered,
            "remaining": sum(len(x.questions) for x in session.groups()),
            "open": sum(1 for q in g.questions if q.proposal is None)}


REVIEW_KO = {"isolation": "격리", "preemptive_isolation": "선제 격리", "pediatric": "소아", "icu_stepdown": "중환자실 하향",
             "single_room_preference": "1인실 희망", "grade_upgrade": "상급병실 차액", "no_bed": "조건 맞는 병상 없음",
             "close_scores": "1·2순위 점수 근접", "missing_input": "입력 누락", "same_name_same_ward": "같은 병동 동명이인",
             "integrated_care_consent_missing": "통합병동 동의 미확인", "isolation_discharge_bed": "격리 퇴실 병실",
             "ward_objection": "병동 이의"}
ROOM_KO = {"negative_pressure": "음압실", "single": "1인실", "multi": "다인실"}
STATE_KO = {"occupied": "재원 중", "discharging": "퇴원 진행", "dirty": "청소 대기", "cleaning": "청소 중",
            "ready": "입실 가능", "empty": "빈 병상", "arrived": "새 환자 입실"}


def bed_states(st) -> list[dict]:
    """병상 현황판: 병상마다 지금 상태와 다음 환자 (색과 글자로 함께 보여 준다)."""
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    room = {r.id: r for r in st.h.rooms}
    out = []
    for b in st.h.beds:
        ds = sorted((d for d in s.discharges.values() if d.bed_id == b.id and EventType.DISCHARGE_CANCELLED not in d.facts),
                    key=lambda d: min(d.facts.values()) if d.facts else st.now)
        d = ds[-1] if ds else None
        incoming = [m for m in s.matches.values() if m.active and m.bed_id == b.id]
        arrived = [m for m in incoming if EventType.PATIENT_ARRIVED in m.facts]
        nxt = next((m for m in incoming if EventType.PATIENT_ARRIVED not in m.facts), None)
        detail = ""
        if arrived:
            state, detail = "arrived", f"{arrived[0].admission_id} 입실 {_hm(arrived[0].facts[EventType.PATIENT_ARRIVED])}"
        elif d is not None and EventType.PATIENT_DEPARTED not in d.facts:
            state, detail = "discharging", f"{d.id} 퇴원 진행"
        elif d is not None and not (d.seen(EventType.BED_MARKED_VACANT) or d.seen(EventType.BED_READY)):
            state = "cleaning" if d.seen(EventType.CLEANING_STARTED) else "dirty"
            detail = "종말소독" if d.isolation else "일반 청소"
        elif d is not None:
            state = "ready"
        elif b.id in st.gw.census:
            state = "occupied"
        else:
            state = "empty"
        if nxt is not None and state != "arrived":
            detail = (detail + " · " if detail else "") + f"다음 {nxt.admission_id}"
        out.append({"bed": b.id, "label": b.id.replace("-", " ").replace("W", "").replace(" R", "-").replace(" B", "-"),
                    "ward": b.ward_id, "room": ROOM_KO.get(room[b.room_id].room_type.value, ""), "state": state,
                    "state_ko": STATE_KO[state], "detail": detail, "isolation": bool(d and d.isolation
                                                                                      and state in ("dirty", "cleaning"))})
    return out


__all__ = ["bed_states", "SAFETY_KO", "STAGE_KO", "admission_view", "blocked_decisions", "ipc_view", "manager_view", "onboarding_view", "pending_approvals",
           "ward_view"]
