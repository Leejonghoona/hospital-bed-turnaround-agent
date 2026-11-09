"""역할별 화면 데이터 (08 계획 4단계). 화면(Streamlit)과 시험이 같은 함수를 쓴다. 합성 데이터 전용.

- 원무(병상 배정 담당): 에이전트 확정 통보(근거·되돌리기), 사람 승인 카드, 환자 연락 할 일, 담당 검토, 막힌 판단 기록
- 병동(책임간호사): 배정 통보 → 병동 준비 → "올려주세요" → 입실, 참고 알림
- 감염관리실: 늦은 격리·격리 사안 올림, 종말소독 진행
- 관리자: 자동 처리 범위 제안 카드(근거), 사람 개입, 실행 안전
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
KIND_KO = {"isolation_notice": "늦은 격리 통지", "call_up_held": "호출 보류", "recontact": "환자 재연락", "fee_consent": "상급병실 차액 동의", "brain_escalate": "예외 올림",
           "order_nudge": "퇴원 처방 재촉", "call_up_reminder": "호출 독촉", "approval_rejected": "승인 거부 안내",
           "postpone_contact": "연락 보류", "data_fix": "입력 보완", "recontact_prepare": "재연락 준비",
           "nudge_late_discharges": "늦은 퇴원 재촉", "cleaning_track": "청소 추적", "missing_bed_prep": "침상 정리 누락",
           "promise_watch": "안내 시각 지켜보기"}


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


def bed_ko(bed: str | None, short: bool = False) -> str:
    """병상 표기 하나로: W72-R04-B1 → '72병동 04호 1번' (짧게는 '04호-1')."""
    if not bed:
        return "병상 없음"
    try:
        w, r, b = bed.split("-")
        return f"{r[1:]}호-{b[1:]}" if short else f"{w[1:]}병동 {r[1:]}호 {b[1:]}번"
    except ValueError:
        return bed


def _people(ev) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in ev:
        if e.type in (EventType.ADMISSION_REQUESTED, EventType.DISCHARGE_DECIDED) and e.payload.get("name"):
            out.setdefault(e.case_id, e.payload)
    return out


def who(people: dict, case: str) -> str:
    """가명 표시: 임가윤(74·남) → '임○윤 74세 남'. 합성 데이터라도 화면에서는 이름을 가린다."""
    p = people.get(case) or {}
    name = str(p.get("name") or "")
    masked = (name[0] + "○" + name[-1]) if len(name) >= 3 else (name[:1] + "○" if name else "환자")
    sex = {"M": "남", "F": "여"}.get(str(p.get("gender") or ""), "")
    age = f"{p['age']}세" if p.get("age") is not None else ""
    return " ".join(x for x in (masked, age, sex) if x)


def _rationale(trigger: str | None, action: str, ctx: dict) -> tuple[str, str]:
    """근거를 사람이 읽는 문장으로 (내부 메모 대신). (상황 문장, 승인하면 일어나는 일)"""
    if trigger == "promise_at_risk":
        why = ("앞 환자가 아직 퇴실 전" if not ctx.get("departed") else
               "청소가 아직 시작 전" if not ctx.get("cleaning_started") else "청소 중")
        sit = f"환자에게 {ctx.get('promise_at', '-')}에 오시라고 안내했는데, {why}이라 시간 안에 병상 준비가 어렵습니다."
    elif trigger == "late_isolation":
        sit = "확정 뒤에 격리 오더가 와서 지금 병상이 맞지 않습니다."
    elif trigger == "ward_objection":
        sit = f"병동이 이의를 냈습니다({ctx.get('objection_reason') or '사유 미기재'})."
    elif trigger == "discharge_date_changed":
        sit = "기다리던 병상의 퇴원이 취소·연기됐습니다."
    else:
        sit = TRIGGER_KO.get(trigger or "", "예외") + " 상황입니다."
    eff = {"request_recontact": "승인하면 원무에 환자 재연락 작업이 생깁니다.",
           "propose_rematch": "승인하면 규칙으로 다른 병상을 다시 찾고, 병동과 원무에 다시 알립니다.",
           "request_postpone_contact": "승인하면 환자 연락을 미루는 작업이 생깁니다."}.get(action, "")
    return sit, eff


def _fact_chips(ctx: dict) -> list[str]:
    out = []
    if ctx.get("promise_at"):
        out.append(f"안내 시각 {ctx['promise_at']}")
    if "departed" in ctx:
        out.append("앞 환자 퇴실 완료" if ctx["departed"] else "앞 환자 퇴실 전")
    if "cleaning_started" in ctx:
        out.append("청소 진행 중" if ctx["cleaning_started"] else "청소 시작 전")
    if ctx.get("isolation_discharge"):
        out.append("격리 퇴실 병상")
    if "contacted" in ctx:
        out.append("환자 연락 완료" if ctx["contacted"] else "환자 연락 전")
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
    people = _people(ev)
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
        sit, eff = _rationale(r.payload.get("trigger") if r else None, e.payload["action"], ctx)
        out.append({"exception_id": e.payload["exception_id"], "case": e.case_id, "action": e.payload["action"],
                    "who": who(people, e.case_id), "situation": sit, "effect": eff, "due_at": dl,
                    "bed_ko": bed_ko(r.payload.get("bed_id") if r else None),
                    "overdue": bool(dl and dl < st.now), "requested_at": e.observed_at,
                    "action_ko": ACTION_KO.get(e.payload["action"], e.payload["action"]),
                    "trigger_ko": TRIGGER_KO.get(r.payload["trigger"], "") if r else "",
                    "bed": r.payload.get("bed_id") if r else None, "since": _hm(e.observed_at),
                    "deadline": _hm(datetime.fromisoformat(r.payload["deadline"])) if r and r.payload.get("deadline") else "",
                    "rationale": (prop.payload.get("rationale") if prop else "") or "",
                    "facts": _fact_chips(ctx),
                    "recommend": recommend_beds(st, e.case_id) if e.payload["action"] == "propose_rematch" else []})
    # 마감이 지난 요청이 맨 위(지금 처리), 그다음 최신 요청 (4단계 화면 검수 3)
    return sorted(out, key=lambda a: (not a["overdue"], -a["requested_at"].timestamp()))


def blocked_decisions(st, limit: int = 8) -> list[dict]:
    """게이트웨이가 막은 에이전트 판단 (안전장치가 실제로 일한 기록). 오늘 것만."""
    _today(st)
    allev = st.store.read()
    people = _people(allev)
    ev = [e for e in allev if e.observed_at.date() == st.now.date()]
    out = [{"time": _hm(e.observed_at), "case": who(people, e.case_id), "case_id": e.case_id, "action": ACTION_KO.get(e.payload["action"], e.payload["action"]),
            "reason": ", ".join(REASON_KO.get(r, r) for r in e.payload.get("reasons", []))}
           for e in ev if e.type is EventType.ACTION_REJECTED and "stale_basis" not in e.payload.get("reasons", [])]
    return out[-limit:]


ADMISSION_KINDS = {"recontact", "fee_consent", "approval_rejected", "postpone_contact", "call_up_held", "brain_escalate",
                   "data_fix", "recontact_prepare", "promise_watch"}


def admission_view(st) -> dict:
    from turnaround.ui.demo import review_queue
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    people = _people(ev)
    notices = []
    for e in ev:
        if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.payload.get("committed_by") == "agent" \
                and e.observed_at.date() == st.now.date():
            b = e.payload.get("basis") or {}
            m = s.matches.get(e.case_id)
            until = datetime.fromisoformat(e.payload["objection_until"]) if e.payload.get("objection_until") else None
            notices.append({"case": e.case_id, "who": who(people, e.case_id), "bed": e.payload.get("bed_id"),
                            "bed_ko": bed_ko(e.payload.get("bed_id")), "time": _hm(e.observed_at),
                            "proposal_id": e.payload.get("proposal_id"),
                            "score": b.get("score"), "runner_up": bed_ko(b.get("runner_up")) if b.get("runner_up") else None,
                            "waived": [REVIEW_KO.get(x, x) for x in b.get("waived_reasons") or []],
                            "alternatives": b.get("alternatives") or [],
                            "undo_until": _hm(until) if until else "", "undo_open": bool(until and st.now < until),
                            "active": bool(m and m.active and m.bed_id == e.payload.get("bed_id"))})
    contacts = [{"case": c, "who": who(people, c), "bed": m.bed_id, "bed_ko": bed_ko(m.bed_id),
                 "committed": _hm(m.facts[EventType.MATCH_COMMITTED])}
                for c, m in sorted(s.matches.items()) if m.active and EventType.MATCH_COMMITTED in m.facts
                and EventType.PATIENT_CONTACTED not in m.facts]
    ops = load_operations()
    tasks = []
    for t in open_tasks(ev, st.now).values():
        kind = t.payload.get("kind") or t.payload.get("reason")
        if t.type is not EventType.HUMAN_TASK_CREATED or is_notice(t) or kind not in ADMISSION_KINDS:
            continue  # 원무가 할 일만 (청소 추적 같은 참고 작업은 빼고, 4단계 UX 검수)
        due = ops.due(t)
        why = REASON_TASK_KO.get(t.payload.get("reason"), "")
        if kind == "brain_escalate":
            card = t.payload.get("card") or {}
            why = " · ".join(x for x in (TRIGGER_KO.get(t.payload.get("trigger"), ""),
                                         "에이전트가 스스로 정하지 못해 사람에게 넘김",
                                         ("막은 행동: " + ACTION_KO.get(card["rejected_action"], card["rejected_action"]))
                                         if card.get("rejected_action") else "") if x)
        tasks.append({"task_id": t.event_id, "kind": _kind(kind), "raw_kind": kind, "why": why, "case": t.case_id,
                      "who": who(people, t.case_id), "bed": t.payload.get("bed_id"), "bed_ko": bed_ko(t.payload.get("bed_id")),
                      "due": _hm(due), "due_at": due, "overdue": due < st.now, "created": t.observed_at,
                      "stale": t.observed_at.date() < st.now.date()})
    # 마감 지난 일 먼저, 그다음 마감 이른 순 (새로 생긴 일이 묻히지 않게 같은 마감이면 최신 먼저)
    tasks.sort(key=lambda x: (not x["overdue"], x["due_at"], -x["created"].timestamp()))
    return {"notices": notices, "approvals": pending_approvals(st), "contacts": contacts, "reviews": review_queue(st),
            "tasks": tasks, "blocked": blocked_decisions(st)}


WARD_STEPS = ("병동 준비 필요", "준비 완료 · 호출 대기", "호출됨 · 이동 중", "입실 완료")


def ward_view(st) -> dict:
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    people = _people(ev)
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
        step = (WARD_STEPS[3] if EventType.PATIENT_ARRIVED in f else WARD_STEPS[2] if EventType.PATIENT_CALL_UP in f
                else WARD_STEPS[1] if EventType.WARD_PREP_DONE in f else WARD_STEPS[0])
        flow.append({"case": e.case_id, "who": who(people, e.case_id), "bed": e.payload.get("bed_id"),
                     "bed_ko": bed_ko(e.payload.get("bed_id")), "notified": _hm(e.observed_at), "step": step,
                     "hold": held.get(e.case_id, ""), "proposal_id": e.payload.get("proposal_id"),
                     "by": "에이전트 확정" if e.payload.get("committed_by") == "agent" else "사람 확정"})
    bstate = {b["bed"]: b["state_ko"] for b in bed_states(st)}
    for f in flow:
        f["bed_state"] = bstate.get(f["bed"], "")
    todo = [f for f in flow if f["step"] == WARD_STEPS[0] or f["hold"]]
    open_ = open_tasks(ev, st.now).values()
    notes = [{"kind": _kind(t.payload.get("kind")), "case": who(people, t.case_id), "bed": bed_ko(t.payload.get("bed_id")),
              "time": _hm(t.observed_at)} for t in open_ if t.type is EventType.HUMAN_TASK_CREATED
             and t.payload.get("assignee") == "ward"]
    return {"todo": todo, "flow": flow, "notes": notes}


def ipc_view(st) -> dict:
    _today(st)
    ev = st.store.read()
    s = fold(ev)
    people = _people(ev)
    from turnaround.sim.models import RoomType
    occ = st.gw._occupants(ev)
    np_free = sum(1 for b in st.gw.beds if st.gw.room_by_id[st.gw.room_of[b.id]].room_type is RoomType.NEGATIVE_PRESSURE
                  and b.id not in occ and not any(m.active and m.bed_id == b.id for m in s.matches.values()))

    def _context(case: str) -> dict:
        m = s.matches.get(case)
        bed = m.bed_id if m is not None and m.active else None
        room = st.gw.room_of.get(bed or "")
        mates = sum(1 for b, _ in occ.items() if room and st.gw.room_of.get(b) == room and b != bed)
        mates += sum(1 for x in s.matches.values() if x.active and x.admission_id != case and room
                     and st.gw.room_of.get(x.bed_id) == room and EventType.PATIENT_ARRIVED in x.facts)
        rt = st.gw.room_by_id[room].room_type.value if room else None
        return {"bed_ko": bed_ko(bed), "room_type": ROOM_KO.get(rt, "-") if rt else "-", "roommates": mates,
                "arrived": bool(m and EventType.PATIENT_ARRIVED in m.facts)}
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
    alerts = [{"task_id": e.event_id, "checked": e.event_id in done, "case": e.case_id, "who": who(people, e.case_id),
               **_context(e.case_id), "alternatives": recommend_beds(st, e.case_id) if e.event_id not in done else [],
               "kind": _kind(e.payload.get("kind")), "time": _hm(e.observed_at),
               "card": _card(e.payload.get("card") or {}, e.payload.get("trigger"))} for e in ev if e.type is EventType.HUMAN_TASK_CREATED
              and (e.payload.get("assignee") == "ipc" or "ipc" in (e.payload.get("also_notify") or []))]
    iso = [{"case": who(people, e.case_id), "time": _hm(e.observed_at),
            "precautions": ", ".join(PREC_KO.get(p, p) for p in e.payload.get("precautions") or []),
            "organism": ORG_KO.get(e.payload.get("organism"), e.payload.get("organism") or "-")}
           for e in ev if e.type is EventType.ISOLATION_ORDER_ADDED]
    terminal = []
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        if d.isolation and EventType.PATIENT_DEPARTED in d.facts:
            done = any(k == "terminal" for _, k in d.cleaning_done_kinds)
            terminal.append({"bed": bed_ko(d.bed_id), "departed": _hm(d.facts[EventType.PATIENT_DEPARTED]),
                             "status": "종말소독 완료" if done else "종말소독 대기"})
    return {"alerts": alerts, "isolation_orders": iso, "terminal": terminal, "np_free": np_free}


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
              "evidence": (f"사람 결정 {r['human_n']}건 중 {r['human_agreed']}건이 에이전트 판단과 같음"
                           f" (통계적으로 보수적으로 잡은 일치율 {r['agreement_lower95']:.0%}, 85% 이상이면 맡길 수 있음)") if r["human_n"] else "근거 부족 · 아직 사람 결정 기록 없음",
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
            "flow": {"12시 전 퇴실": f"{fl['departed_before_noon']}명 / {fl['discharges']}명",
                     "퇴실 시각(중앙값)": fl["departure_median"] or "-", "병상 준비 완료(중앙값)": fl["ready_median"] or "-",
                     "수속부터 입실까지(평균)": f"{round(fl['entry_wait_mean_min'])}분" if fl["entry_wait_mean_min"] is not None else "-"},
            "unassigned": why,
            "safety": [{"지표": SAFETY_KO[k][0], "뜻": SAFETY_KO[k][1], "위반": sf[k]["violations"],
                        "검사 수": sf[k].get("denominator", "-")} for k in ("S1", "S2", "S3", "S4")]}


STAGE_KO = {"structure": "구조(병상 표)", "format": "형식(시각·구분자)", "column": "열", "code": "코드표"}


VAL_KO = {"M": "남", "F": "여", "contact": "접촉주의", "droplet": "비말주의", "airborne": "공기주의",
          "negative_pressure": "음압실", "single": "1인실", "multi": "다인실", "general": "일반 청소", "terminal": "종말소독",
          "confirmed": "확진", "suspected": "의심", "preemptive": "선제", "physician": "주치의", "ipc": "감염관리실",
          "__ignore__": "쓰지 않음"}


def _key_ko(key: str) -> str:
    from turnaround.agent.mapping_agent import vocabulary
    voc = vocabulary()
    if key.startswith("t:"):
        return f"'{key[2:]}' 사건 시각"
    return voc.get(key, key)


def _question_ko(q) -> tuple[str, str]:
    """질문 ID를 사람이 읽는 문장으로. (질문, 제안)"""
    qid, prop = q.id, q.proposal
    if qid.startswith("col:"):
        _, t, c = qid.split(":", 2)
        text = f"'{t}' 표의 '{c}' 열은 무엇인가요?"
    elif qid.startswith("code:"):
        _, k, v = qid.split(":", 2)
        text = f"{_key_ko(k)}의 코드 '{v}'는 무엇을 뜻하나요?"
    elif qid.startswith("evt:"):
        text = f"사건 코드 '{qid[4:]}'는 어떤 사건인가요?"
    elif qid == "layout":
        text = "병동·병실·병상 번호가 있는 표는 어느 것인가요?"
        q = type(q)(q.id, q.item, "병상 표를 확신하지 못했습니다. 제안한 표가 맞는지 확인해 주세요", q.proposal, q.options)
    elif qid == "bed_parts":
        text = "병상 번호를 이루는 병동·병실·병상 열은 무엇인가요?"
    elif qid == "fmt:time":
        text = "시각은 어떤 형식으로 적혀 있나요?"
    elif qid == "fmt:sep":
        text = "한 칸에 여러 값이 있으면 무엇으로 구분하나요?"
    elif qid == "fmt:bool":
        text = "예/아니오는 어떻게 적혀 있나요?"
    else:
        text = q.item
    if isinstance(prop, (tuple, list)):
        shown = " · ".join(str(x) for x in prop if x)
    elif prop is None:
        shown = None
    else:
        shown = VAL_KO.get(str(prop), _key_ko(str(prop)) if ("." in str(prop) or str(prop).startswith("t:")) else str(prop))
    return text, shown


STAGE_KO = {"structure": "표 구조", "format": "값 형식", "column": "열의 뜻", "code": "코드 뜻"}


def onboarding_view(session) -> dict:
    g = session.current()
    answered = len(session.answers)
    by_stage: dict[str, int] = {}
    for stage, _rnd, ids in session.history:
        by_stage[STAGE_KO.get(stage, stage)] = by_stage.get(STAGE_KO.get(stage, stage), 0) + len(ids)
    if g is None:
        m = session.mapping
        cols = sum(len(tm.columns) + len(tm.event_codes) for tm in m.tables.values()) if m else 0
        return {"stage": "done", "questions": [], "answered": answered, "remaining": 0, "by_stage": by_stage,
                "mapped": cols, "tables": len(m.tables) if m else 0}
    qs = []
    for q in g.questions:
        text, shown = _question_ko(q)
        why = "병상 표를 확신하지 못했습니다. 제안한 표가 맞는지 확인해 주세요" if q.id == "layout" else q.why
        qs.append({"id": q.id, "item": text, "why": why, "proposal": shown, "raw_proposal": q.proposal,
                   "raw_options": list(q.options or []),
                   "options": [VAL_KO.get(str(o), str(o)) for o in list(q.options or [])[:4]]})
    return {"stage": g.stage, "stage_ko": STAGE_KO[g.stage], "round": g.round, "questions": qs, "answered": answered,
            "remaining": sum(len(x.questions) for x in session.groups()), "by_stage": by_stage,
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
    people = _people(ev)
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
            state, detail = "arrived", f"{who(people, arrived[0].admission_id)} · {_hm(arrived[0].facts[EventType.PATIENT_ARRIVED])} 입실"
        elif d is not None and EventType.PATIENT_DEPARTED not in d.facts:
            state, detail = "discharging", f"{who(people, d.id)} 퇴원 준비"
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
            detail = (detail + " · " if detail else "") + f"다음 입원 {who(people, nxt.admission_id)}"
        out.append({"bed": b.id, "label": bed_ko(b.id, short=True),
                    "ward": b.ward_id, "room": ROOM_KO.get(room[b.room_id].room_type.value, ""), "state": state,
                    "state_ko": STATE_KO[state], "detail": detail, "isolation": bool(d and d.isolation
                                                                                      and state in ("dirty", "cleaning"))})
    return out


__all__ = ["bed_states", "SAFETY_KO", "STAGE_KO", "admission_view", "blocked_decisions", "ipc_view", "manager_view", "onboarding_view", "pending_approvals",
           "ward_view"]


# ---------------- 환자 경과·감사 기록 (실사용 화면, UI 3차) ----------------

EVENT_KO = {
    "ADMISSION_REQUESTED": "입원 예약 접수", "ADMISSION_CANCELLED": "입원 예약 취소", "PREMATCH_CREATED": "병상 사전 매칭",
    "PREMATCH_RELEASED": "사전 매칭 해제", "MATCH_COMMITTED": "병상 확정", "WARD_ASSIGNMENT_NOTIFIED": "병동·원무에 배정 통보",
    "PATIENT_CONTACTED": "환자에게 입원 안내 연락", "WARD_PREP_DONE": "병동 준비 완료", "PATIENT_CALL_UP": "호출(환자 올려 보내기)",
    "PATIENT_ARRIVED": "병동 입실", "ISOLATION_ORDER_ADDED": "격리 오더", "WARD_OBJECTION_RAISED": "병동 이의",
    "CASE_ROUTED_REVIEW": "담당 검토로 보냄", "MANUAL_OVERRIDE_APPLIED": "원무 직접 수정", "REMATCH_REQUESTED": "병상 다시 찾기",
    "EXCEPTION_RAISED": "예외 감지", "EXCEPTION_RESOLVED": "예외 종료", "ACTION_APPROVAL_REQUESTED": "사람 승인 요청",
    "ACTION_APPROVED": "승인", "ACTION_EXECUTED": "실행", "ACTION_REJECTED": "막음·거절", "HUMAN_TASK_CREATED": "작업 생성",
    "HUMAN_TASK_DONE": "작업 완료", "DISCHARGE_DECIDED": "퇴원 결정", "ORDER_CONFIRMED": "퇴원 오더", "DISCHARGE_RX_ISSUED": "퇴원약 처방",
    "MEDICATION_READY": "퇴원약 도착", "BILLING_DONE": "수납 완료", "GUARDIAN_ARRIVED": "보호자 도착", "PATIENT_DEPARTED": "퇴실",
    "CLEANING_REQUEST_ISSUED": "청소 요청", "CLEANING_STARTED": "청소 시작", "CLEANING_DONE": "청소 완료", "BED_READY": "침상 준비 완료",
    "BED_MARKED_VACANT": "입실 가능 전환", "DISCHARGE_CANCELLED": "퇴원 취소", "PROMISE_AT_RISK": "안내 시각 위험",
    "PATIENT_INFO_CORRECTED": "환자 정보 정정", "LOUNGE_OFFERED": "라운지 제안", "BED_PREP_DONE": "침상 정리 완료",
}
ACTOR_KO = {"agent": "에이전트", "gateway": "실행 게이트웨이", "saga": "시스템", "human": "사람", "his": "병원 전산(HIS)",
            "timer": "시스템", "equipment": "장비"}


def _detail(e) -> str:
    p = e.payload
    out = []
    if p.get("bed_id"):
        out.append(bed_ko(p["bed_id"]))
    if p.get("action"):
        out.append(ACTION_KO.get(p["action"], p["action"]))
    if p.get("reasons"):
        out.append(", ".join(REASON_KO.get(r, REVIEW_KO.get(r, r)) for r in p["reasons"]))
    if p.get("trigger"):
        out.append(TRIGGER_KO.get(p["trigger"], p["trigger"]))
    if p.get("kind") and e.type is EventType.HUMAN_TASK_CREATED:
        out.append(_kind(p["kind"]))
    if p.get("precautions"):
        out.append(", ".join(PREC_KO.get(x, x) for x in p["precautions"]))
    if p.get("note") or p.get("reason") and isinstance(p.get("reason"), str) and e.type in (
            EventType.WARD_OBJECTION_RAISED, EventType.MANUAL_OVERRIDE_APPLIED):
        out.append(str(p.get("note") or p.get("reason")))
    if p.get("committed_by"):
        out.append("에이전트 확정" if p["committed_by"] == "agent" else "사람 확정")
    return " · ".join(dict.fromkeys(x for x in out if x))


def _actor(e) -> str:
    if e.payload.get("actor"):
        return {"bed_manager": "원무", "ward": "병동", "ipc": "감염관리실"}.get(e.payload["actor"], str(e.payload["actor"]))
    if e.payload.get("by"):
        return {"admission_office": "원무", "ipc": "감염관리실"}.get(e.payload["by"], str(e.payload["by"]))
    return ACTOR_KO.get(e.source, e.source)


def case_timeline(st, case: str) -> dict:
    """환자 한 명의 경과: 누가 언제 무엇을 했는지. 기다리는 병상의 퇴원 경과도 함께 보여 준다."""
    _today(st)
    ev = st.store.read()
    people = _people(ev)
    s = fold(ev)
    m = s.matches.get(case)
    rel = {case}
    if m is not None and m.discharge_id:
        rel.add(m.discharge_id)
    rows = [{"time": _hm(e.observed_at), "who": _actor(e), "what": EVENT_KO[e.type.name], "detail": _detail(e),
             "mine": e.case_id == case}
            for e in ev if e.case_id in rel and e.type.name in EVENT_KO and e.observed_at <= st.now
            and not (e.type is EventType.HUMAN_TASK_CREATED and is_notice(e))]
    folded: list[dict] = []
    for r in rows:  # 같은 기록이 이어지면 접는다 (계획 주기마다 다시 열리는 담당 검토 등)
        if folded and folded[-1]["what"] == r["what"] and folded[-1]["detail"] == r["detail"]:
            folded[-1]["repeat"] = folded[-1].get("repeat", 1) + 1
            folded[-1]["last"] = r["time"]
            continue
        folded.append(dict(r))
    for r in folded:
        if r.get("repeat"):
            r["detail"] = (r["detail"] + " · " if r["detail"] else "") + f"{r['repeat']}회 반복(마지막 {r['last']})"
    rows = folded
    p = people.get(case) or {}
    status = "입실 완료" if m and EventType.PATIENT_ARRIVED in m.facts else "병상 확정" if m and EventType.MATCH_COMMITTED in m.facts \
        else "사전 매칭" if m and m.active else "배정 대기"
    return {"who": who(people, case), "department": p.get("department") or "-", "status": status,
            "bed_ko": bed_ko(m.bed_id) if m and m.active else "-",
            "isolation": ", ".join(PREC_KO.get(x, x) for x in (p.get("precautions") or [])) or "없음",
            "discharge": (who(people, m.discharge_id) if m.discharge_id in people else "앞 환자") if m and m.discharge_id else None,
            "rows": rows}


AUDIT_TYPES = {EventType.MATCH_COMMITTED, EventType.ACTION_APPROVED, EventType.ACTION_EXECUTED, EventType.ACTION_REJECTED,
               EventType.WARD_OBJECTION_RAISED, EventType.MANUAL_OVERRIDE_APPLIED, EventType.HUMAN_TASK_DONE,
               EventType.PATIENT_CONTACTED, EventType.WARD_PREP_DONE, EventType.CASE_ROUTED_REVIEW}


SPOTLIGHT_TYPES = (AUDIT_TYPES - {EventType.CASE_ROUTED_REVIEW}) | {
    EventType.ISOLATION_ORDER_ADDED, EventType.EXCEPTION_RAISED, EventType.ACTION_APPROVAL_REQUESTED,
    EventType.HUMAN_TASK_CREATED, EventType.PATIENT_CALL_UP, EventType.PATIENT_ARRIVED, EventType.PATIENT_DEPARTED,
    EventType.DISCHARGE_CANCELLED, EventType.PROMISE_AT_RISK}


def audit_log(st, limit: int = 40, ids: set | None = None, types=None) -> list[dict]:
    """오늘의 결정 기록: 에이전트·게이트웨이·사람이 한 결정 (감사·되돌아보기용)."""
    _today(st)
    ev = st.store.read()
    people = _people(ev)
    seen_review: dict[str, tuple] = {}
    rows = []
    for e in ev:
        if e.type not in (types or AUDIT_TYPES) or e.observed_at.date() != st.now.date() or e.observed_at > st.now:
            continue
        if ids is not None and e.event_id not in ids:
            continue
        if e.type is EventType.CASE_ROUTED_REVIEW:  # 같은 사유의 재검토(계획 주기 반복)는 처음 것만
            key = tuple(sorted(e.payload.get("reasons") or []))
            if seen_review.get(e.case_id) == key:
                continue
            seen_review[e.case_id] = key
        rows.append({"time": _hm(e.observed_at), "actor": _actor(e),
                     "patient": who(people, e.case_id) if e.case_id in people else "-",
                     "what": EVENT_KO.get(e.type.name, e.type.name), "detail": _detail(e)})
    return rows[::-1][:limit]


def role_counts(st) -> dict[str, int]:
    """사이드바 배지: 역할마다 지금 할 일 수."""
    a = admission_view(st)
    w = ward_view(st)
    i = ipc_view(st)
    return {"admission": len(a["approvals"]) + len(a["contacts"]) + len(a["tasks"]), "ward": len(w["todo"]),
            "ipc": sum(1 for x in i["alerts"] if not x["checked"])}


__all__ += ["audit_log", "case_timeline", "role_counts"]


def recommend_beds(st, case: str, n: int = 2) -> list[str]:
    """이 환자에게 지금 규칙 모드가 고를 병상 후보(화면 표시용). 지금 병상은 뺀다. 실제 확정은 승인 뒤 게이트웨이가 한다."""
    from turnaround.agent.tools import PlanningTools
    ev = st.store.read()
    m = fold(ev).matches.get(case)
    cur = m.bed_id if m is not None and m.active else None
    try:
        cands = PlanningTools(st.gw, st.h.wards).candidates(case, st.now, ev)
    except Exception:  # noqa: BLE001 - 화면 보조 정보라 실패하면 비워 둔다
        return []
    out = []
    for c in cands:
        if c.bed_id == cur:
            continue
        rt = st.gw.room_by_id[st.gw.room_of[c.bed_id]].room_type.value
        ready = "지금 가능" if c.ready_at <= st.now else f"{_hm(c.ready_at)} 준비 예상"
        out.append(f"{bed_ko(c.bed_id)}({ROOM_KO.get(rt, '')}, {ready})")
        if len(out) >= n:
            break
    if out:
        return out
    # 지금 빈 병상이 없으면(만실) 오늘 퇴원으로 곧 비는 병상 중 배정 기준에 맞는 곳을 준비 예상 시각순으로 보여 준다
    try:
        pt = PlanningTools(st.gw, st.h.wards)
        patient, _ = pt.patient(case, ev)
        held = pt.held_beds(ev, exclude_case=case)
        state = fold(ev)
        soon = []
        for bed in st.gw.beds:
            b = bed.id
            if b == cur or b in held or patient is None:
                continue
            ready, disc = pt.ready_at(b, ev, st.now, state)
            if disc is None or st.gw._shacl(case, patient, b, ev):
                continue
            soon.append((ready, b))
    except Exception:  # noqa: BLE001
        return ["지금 맞는 빈 병상 없음"]
    soon.sort()
    if not soon:
        return ["배정 기준에 맞는 빈 병상 없음 · 오늘 퇴원으로 비는 자리도 없음 → 대기 유지 또는 다른 병동 검토"]
    tail = [f"{bed_ko(b)}({ROOM_KO.get(st.gw.room_by_id[st.gw.room_of[b]].room_type.value, '')}, "
            f"퇴원 후 {_hm(t)} 준비 예상)" for t, b in soon[:n]]
    return ["지금 맞는 빈 병상 없음 · 곧 비는 병상"] + tail


__all__ += ["recommend_beds"]
