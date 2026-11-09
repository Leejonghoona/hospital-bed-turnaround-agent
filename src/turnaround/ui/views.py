"""역할별 화면 데이터 (08 계획 4단계). 화면(Streamlit)과 시험이 같은 함수를 쓴다. 합성 데이터 전용.

- 원무(병상 배정 담당): 에이전트 확정 통보(근거·되돌리기), 사람 승인 카드, 환자 연락 할 일, 담당 검토, 막힌 판단 기록
- 병동(책임간호사): 배정 통보 → 병동 준비 → "올려주세요" → 입실, 참고 알림
- 감염관리실: 늦은 격리·격리 사안 올림, 종말소독 진행
- 관리자: 자율권 승격·강등 카드(근거), 사람 개입, 실행 안전
- 온보딩: 지금 묶음의 질문과 진행률
"""

from __future__ import annotations

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
PREC_KO = {"contact": "접촉주의", "droplet": "비말주의", "airborne": "공기주의"}
KIND_KO = {"isolation_notice": "늦은 격리 통지", "call_up_held": "올려주세요 보류(격리 확인)", "recontact": "환자 재연락", "fee_consent": "상급병실 차액 동의", "brain_escalate": "예외 올림",
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


def _hm(t) -> str:
    return t.strftime("%H:%M") if t else "-"


def _kind(k: str | None) -> str:
    k = k or ""
    if k.startswith("step_nudge:"):
        return {"step_nudge:medication": "퇴원약 재촉(약제부)", "step_nudge:billing_review": "퇴원심사 재촉",
                "step_nudge:billing_payment": "수납 재촉"}.get(k, k)
    return KIND_KO.get(k, k)


def _decided(ev) -> set[str]:
    return {e.payload.get("exception_id") for e in ev if e.payload.get("actor")
            and e.type in (EventType.ACTION_APPROVED, EventType.ACTION_REJECTED)}


def pending_approvals(st) -> list[dict]:
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
                    "deadline": r.payload.get("deadline", "")[11:16] if r else "",
                    "rationale": (prop.payload.get("rationale") if prop else "") or "",
                    "facts": _facts(ctx)})
    # 최신 요청이 위, 마감이 지난 요청은 아래(마감 지남 표시)
    return sorted(out, key=lambda a: (a["overdue"], -a["requested_at"].timestamp()))


def blocked_decisions(st, limit: int = 8) -> list[dict]:
    """게이트웨이가 막은 머리 판단 (안전장치가 실제로 일한 기록)."""
    ev = st.store.read()
    out = [{"time": _hm(e.observed_at), "case": e.case_id, "action": ACTION_KO.get(e.payload["action"], e.payload["action"]),
            "reason": ", ".join(REASON_KO.get(r, r) for r in e.payload.get("reasons", []))}
           for e in ev if e.type is EventType.ACTION_REJECTED and "stale_basis" not in e.payload.get("reasons", [])]
    return out[-limit:]


def admission_view(st) -> dict:
    from turnaround.ui.demo import review_queue
    ev = st.store.read()
    s = fold(ev)
    notices = []
    for e in ev:
        if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.payload.get("committed_by") == "agent":
            b = e.payload.get("basis") or {}
            m = s.matches.get(e.case_id)
            notices.append({"case": e.case_id, "bed": e.payload.get("bed_id"), "time": _hm(e.observed_at),
                            "score": b.get("score"), "runner_up": b.get("runner_up"),
                            "waived": b.get("waived_reasons") or [], "alternatives": b.get("alternatives") or [],
                            "undo_until": (e.payload.get("objection_until") or "")[11:16],
                            "active": bool(m and m.active and m.bed_id == e.payload.get("bed_id"))})
    contacts = [{"case": c, "bed": m.bed_id, "committed": _hm(m.facts[EventType.MATCH_COMMITTED])}
                for c, m in sorted(s.matches.items()) if m.active and EventType.MATCH_COMMITTED in m.facts
                and EventType.PATIENT_CONTACTED not in m.facts]
    ops = load_operations()
    tasks = [{"kind": _kind(t.payload.get("kind") or t.payload.get("reason")), "case": t.case_id,
              "bed": t.payload.get("bed_id"), "due": _hm(ops.due(t))}
             for t in open_tasks(ev, st.now).values() if t.type is EventType.HUMAN_TASK_CREATED and not is_notice(t)
             and (t.payload.get("assignee") or "bed_manager") in ("bed_manager", "admission_office", "cashier")]
    return {"notices": notices, "approvals": pending_approvals(st), "contacts": contacts, "reviews": review_queue(st),
            "tasks": tasks, "blocked": blocked_decisions(st)}


def ward_view(st) -> dict:
    ev = st.store.read()
    s = fold(ev)
    flow = []
    for e in ev:
        if e.type is not EventType.WARD_ASSIGNMENT_NOTIFIED:
            continue
        m = s.matches.get(e.case_id)
        f = m.facts if m else {}
        step = ("입실" if EventType.PATIENT_ARRIVED in f else "올려주세요 보냄" if EventType.PATIENT_CALL_UP in f
                else "병동 준비 완료" if EventType.WARD_PREP_DONE in f else "통보 받음")
        flow.append({"case": e.case_id, "bed": e.payload.get("bed_id"), "notified": _hm(e.observed_at), "step": step,
                     "by": "에이전트 확정" if e.payload.get("committed_by") == "agent" else "사람 확정"})
    open_ = open_tasks(ev, st.now).values()
    notes = [{"kind": _kind(t.payload.get("kind")), "case": t.case_id, "bed": t.payload.get("bed_id"),
              "time": _hm(t.observed_at)} for t in open_ if t.type is EventType.HUMAN_TASK_CREATED
             and t.payload.get("assignee") == "ward"]
    return {"flow": flow, "notes": notes}


def ipc_view(st) -> dict:
    ev = st.store.read()
    s = fold(ev)
    def _card(c: dict, trig) -> str:
        parts = [TRIGGER_KO.get(trig, "")] if trig else []
        if c.get("rejected_action"):
            parts.append(f"막은 행동 {ACTION_KO.get(c['rejected_action'], c['rejected_action'])}"
                         f"({REASON_KO.get(c.get('rejected_reason'), c.get('rejected_reason'))})")
        if c.get("agent_action"):
            parts.append(f"에이전트 행동 {ACTION_KO.get(c['agent_action'], c['agent_action'])}")
        if c.get("deadline"):
            parts.append(f"마감 {str(c['deadline'])[11:16]}")
        return " · ".join(p for p in parts if p)
    alerts = [{"case": e.case_id, "kind": _kind(e.payload.get("kind")), "time": _hm(e.observed_at),
               "card": _card(e.payload.get("card") or {}, e.payload.get("trigger"))} for e in ev if e.type is EventType.HUMAN_TASK_CREATED
              and (e.payload.get("assignee") == "ipc" or "ipc" in (e.payload.get("also_notify") or []))]
    iso = [{"case": e.case_id, "time": _hm(e.observed_at),
            "precautions": ", ".join(PREC_KO.get(p, p) for p in e.payload.get("precautions") or []),
            "organism": e.payload.get("organism")} for e in ev if e.type is EventType.ISOLATION_ORDER_ADDED]
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
    return {"cards": cards, "human_load": human_load(ev),
            "safety": {k: f"{sf[k]['violations']}/{sf[k].get('denominator', '-')}"
                       for k in ("S1", "S2", "S3", "S4")}}


def onboarding_view(session) -> dict:
    g = session.current()
    answered = len(session.answers)
    if g is None:
        return {"stage": "done", "questions": [], "answered": answered, "remaining": 0}
    stage_ko = {"structure": "구조", "format": "형식", "column": "열", "code": "코드표"}[g.stage]
    qs = [{"id": q.id, "item": q.item, "why": q.why, "proposal": q.proposal, "options": list(q.options or [])[:4]}
          for q in g.questions]
    return {"stage": g.stage, "stage_ko": stage_ko, "round": g.round, "questions": qs, "answered": answered,
            "remaining": sum(len(x.questions) for x in session.groups()),
            "open": sum(1 for q in g.questions if q.proposal is None)}


__all__ = ["admission_view", "blocked_decisions", "ipc_view", "manager_view", "onboarding_view", "pending_approvals",
           "ward_view"]
