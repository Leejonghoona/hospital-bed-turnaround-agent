"""원무 작업 목록과 환자 상세 (API 응답 모양). 데이터는 ui/views.py의 같은 함수에서 나온다. 합성 데이터.

화면 문구 규칙(DECISIONS 2026-11-10): 표의 칸은 한 줄에 들어가는 짧은 문구로, 가운데 점(·)은 쓰지 않는다.
"""

from __future__ import annotations

import re
from datetime import datetime

from turnaround.events import EventType
from turnaround.ui import views

SEX = {"M": "남", "F": "여"}


def _bed(b: str | None) -> str:
    """W71-R03-B3 → 71-03-3 (표에 들어가는 짧은 병상 표기)."""
    if not b:
        return ""
    try:
        w, r, n = b.split("-")
        return f"{w[1:]}-{r[1:]}-{n[1:]}"
    except ValueError:
        return b


def _reg_no(pid: str) -> str:
    """합성 환자 ID(SYN-P0050)를 병원 등록번호처럼 8자리 숫자로 보여 준다(합성 번호, 설계 가정)."""
    digits = re.sub(r"\D", "", pid or "")
    return f"{10240000 + int(digits):08d}" if digits else ""


def _person(people: dict, case: str) -> dict:
    p = people.get(case) or {}
    return {"patient": str(p.get("name") or "환자"), "patient_id": _reg_no(str(p.get("patient_id") or "")),
            "age": p.get("age"), "sex": SEX.get(str(p.get("gender") or ""), "")}


def _due(t: datetime | None, now: datetime) -> tuple[str, str]:
    """(시각, 보조 문구). 보조 문구는 어제/지난 시간처럼 판단에 필요한 것만."""
    if t is None:
        return "", ""
    if t.date() < now.date():
        return t.strftime("%H:%M"), "어제" if (now.date() - t.date()).days == 1 else f"{(now.date() - t.date()).days}일 전"
    if t < now:
        mins = int((now - t).total_seconds() // 60)
        h, m = divmod(mins, 60)
        return t.strftime("%H:%M"), (f"{h}시간 {m}분 지남" if h and m else f"{h}시간 지남" if h else f"{m}분 지남")
    return t.strftime("%H:%M"), "오늘"


def _short(text: str) -> str:
    return (text or "").split(" · ")[0].strip()


def _hint(st, case: str) -> str:
    """병상 없음 사례의 다음 판단 근거: 곧 비는 병상이 있으면 그 병상과 준비 예상 시각, 없으면 그 사실."""
    rec = views.recommend_beds(st, case, n=1)
    if not rec:
        return "후보 병상 없음"
    first = rec[-1] if rec[0].startswith("지금 맞는 빈 병상 없음") and len(rec) > 1 else rec[0]
    if first.startswith("배정 기준에 맞는"):
        return "오늘 비는 병상 없음"
    bed = first.split("(")[0].replace("병동 ", "-").replace("호 ", "-").replace("번", "")
    when = first.split(", ")[-1].rstrip(")").replace("퇴원 후 ", "").replace(" 준비 예상", "")
    return f"{_bed_ko_short(bed)} {when} 준비 예상" if "지금" not in when else f"{_bed_ko_short(bed)} 지금 가능"


def _bed_ko_short(s: str) -> str:
    return s.strip()


# 병상이 없을 때 원무가 고를 수 있는 처리 (설계 가정: 원무 입원 창구의 통상 대응을 옮김)
NO_BED_CHOICES = [
    {"label": "대기 유지 안내", "desc": "병상 나면 바로 연락"},
    {"label": "입원 일정 변경 안내", "desc": "입원 날짜 다시 잡기"},
    {"label": "다른 병동 병상 요청", "desc": "희망 밖 병동에 요청"},
    {"label": "기타", "desc": "메모에 내용 적기"},
]
MEMO_MAX = 500


def notes(ev, case: str, accounts: dict | None = None) -> list[dict]:
    """사람이 남긴 자유 서술 메모(특이사항). 사람에게만 보여 주고, 계획 에이전트는 원문을 읽지 않는다(SEC-3)."""
    out = []
    for e in ev:
        if e.type is EventType.FREE_TEXT_NOTE and e.case_id == case and e.payload.get("text"):
            by = str(e.payload.get("by") or "")
            out.append({"time": e.observed_at.strftime("%H:%M"), "who": (accounts or {}).get(by, {}).get("name", by or "병동"),
                        "text": str(e.payload["text"]), "tag": str(e.payload.get("tag") or "")})
    return out


def _promised(text: str) -> str:
    m = re.search(r"(\d{1,2}:\d{2})에 오시라고", text or "")
    return m.group(1) if m else ""


def admission_rows(st) -> dict:
    a = views.admission_view(st)
    ev = st.store.read()
    people = views._people(ev)
    now = st.now
    rows: list[dict] = []

    def add(case, kind, kind_label, action, due_at, late, bed, reason, status, carried=False, ref=None, **more):
        t, note = _due(due_at, now)
        rows.append({"id": ref or f"{kind}:{case}", "case": case, "kind": kind, "kind_label": kind_label, "action": action,
                     "due": t, "due_note": note, "due_at": due_at.isoformat() if due_at else None, "late": bool(late),
                     "bed": _bed(bed), "reason": reason, "status": status, "carried": carried, **_person(people, case),
                     "situation": "", "effect": "", "choices": [], "suggest_at": "", **more})

    for x in a["approvals"]:
        carried = x["requested_at"].date() < now.date()
        if x["action"] == "request_recontact":
            when = _promised(x["situation"])
            label, action = "재연락", "환자에게 다시 안내"
            reason = f"{when} 안내했으나 앞 환자 퇴실 전" if when else (x["trigger_ko"] or _short(x["situation"]))
        else:
            label = {"propose_rematch": "병상 변경", "request_postpone_contact": "연락 보류"}.get(x["action"], "승인")
            action, reason = x["action_ko"], x["trigger_ko"] or _short(x["situation"])
        add(x["case"], "approval", label, action, x["due_at"], x["overdue"], x.get("bed"), reason, "승인 대기", carried,
            ref=x["exception_id"], situation=x["situation"], effect=x["effect"])
    for x in a.get("waiting_ipc") or []:
        add(x["case"], "ipc_wait", "감염관리", "결정 기다리기", x["due_at"], False, x.get("bed"), x["trigger_ko"] or "늦은 격리",
            "감염관리 결정 대기", ref="ipc:" + x["exception_id"], situation=x["situation"],
            effect="감염관리실이 병상을 결정하면 환자 재연락 작업이 생깁니다")
    for x in a["contacts"]:
        tries = sum(1 for n in notes(ev, x["case"]) if n["tag"] == "contact_no_answer")
        reason = f"{x['suggest_at']:%H:%M} 수속 안내" + (f", 연락 안 됨 {tries}회" if tries else "")
        # 입원 당일 오전에 연락한다(세브란스 "당일 오전(08:30~11:00)", 분당서울대 "오전 11시30분경"). 마감 12:00은 설계 가정
        due = now.replace(hour=12, minute=0, second=0, microsecond=0)
        add(x["case"], "contact", "입원 안내", "입원 안내 전화", due, due < now, x["bed"], reason,
            "다시 연락" if tries else "연락 전", suggest_at=f"{x['suggest_at']:%H:%M}",
            situation=f"{x['bed_ko']} 병상이 {x['committed']}에 확정됐습니다. 수속 시각과 병동을 안내하세요.")
    for x in a["tasks"]:
        esc = bool(x.get("escalated_to"))
        if x["raw_kind"] == "brain_escalate":
            pp = people.get(x["case"]) or {}
            hint = _hint(st, x["case"])
            # 근거 칸에는 '어떤 병상이 필요한데 없는지'를 쓴다(할 일 칸의 '병상 없음'을 되풀이하지 않게)
            # 병동 이름 대신 내과·외과 구분으로 쓴다 (사용자 결정 2026-11-10: 개인별 표기보다 진료 구분)
            spec = next((w.specialty for w in st.h.wards if pp.get("department") in w.departments), "")
            sex = SEX.get(str(pp.get("gender") or ""), "")
            need = "격리 병상" if pp.get("precautions") else " ".join(x for x in (spec, f"{sex}자" if sex else "") if x) + " 병상"
            label, action = "병상 없음", "입원 일정 정하기"
            reason = f"{need} 없음" if hint in ("오늘 비는 병상 없음", "후보 병상 없음") else f"{need}, {hint}"
            req = pp.get("requested_at")
            more = {"choices": NO_BED_CHOICES,
                    "situation": f"{need}이 오늘 나지 않아 에이전트가 배정을 멈추고 원무에 넘겼습니다."
                                 + (f" 입원 예약은 {datetime.fromisoformat(req):%-m월 %-d일 %H:%M}입니다." if req else ""),
                    "effect": "환자에게 어떻게 안내했는지 고르세요."}
        elif x["raw_kind"] == "ward_opinion":
            ev_t = next((e for e in ev if e.event_id == x["task_id"]), None)
            why = (ev_t.payload.get("reason") if ev_t else "") or ""
            label, action, reason = "병동 의견", "배정 다시 보기", f"병동: {why}"
            more = {"choices": [{"label": "배정 유지", "desc": "병동에 그대로 간다고 회신"},
                                {"label": "병상 바꿈", "desc": "병상 변경으로 다시 지정함"},
                                {"label": "기타", "desc": "메모에 내용 적기"}],
                    "situation": f"병동에서 이 배정에 의견을 보냈습니다({why}). 배정은 그대로이고 원무가 유지하거나 바꿉니다."}
        elif x["raw_kind"] == "fee_consent" and (people.get(x["case"]) or {}).get("precautions"):
            continue  # 격리 목적 1인실은 상급병실 비용 대상이 아니다(요양급여 기준 별표 2 단서). 동의 작업을 보이지 않는다
        elif x["raw_kind"] == "fee_consent":
            # 상급병실(1인실 등)에 배정되면 차액 동의 서명을 받아야 한다 (게이트웨이가 만드는 작업, LP-22)
            rt = st.gw.room_by_id[st.gw.room_of[x["bed"]]].room_type.value if x.get("bed") in st.gw.room_of else ""
            grade = {"single": "1인실", "negative_pressure": "음압 1인실"}.get(rt, "상급병실")
            label, action, reason = "1인실 동의", "동의서 받기", f"{grade} 배정 검토 중, 동의 전"
            more = {"choices": [{"label": "서명 받음", "desc": "동의서 서명 완료"}, {"label": "동의 안 함", "desc": "기준 병실로 다시 찾아야 함"},
                                {"label": "기타", "desc": "메모에 내용 적기"}],
                    "situation": f"{grade} 배정을 검토 중입니다. 상급병실은 입원실 이용 비용 전액을 본인이 부담하므로(요양급여 기준 별표 2) 환자나 보호자의 동의가 필요합니다."}
        else:
            label, action, reason = "작업", x["kind"], _short(x.get("why") or "")
            more = {"choices": [{"label": "처리함", "desc": "처리를 마쳤습니다"}, {"label": "기타", "desc": "메모에 내용 적기"}],
                    "situation": x.get("why") or ""}
        status = "책임자 확인 중" if esc else "처리 전"
        # '병상 없음'에는 기다리던(지금은 못 쓰는) 병상을 보이지 않는다: 병상 칸이 비어야 할 일과 맞다
        add(x["case"], "task", label, action, x["due_at"], x["overdue"], None if label == "병상 없음" else x.get("bed"), reason,
            status, x["stale"],
            ref=x["task_id"], **more)
    for x in a["notices"]:
        if not (x.get("active") and x.get("undo_open")):
            continue
        add(x["case"], "confirmed", "자동 확정", "배정 확인", None, False, None,
            f"차순위 {x['runner_up']}" if x.get("runner_up") else "차순위 없음", "되돌리기 가능", ref="notice:" + x["case"],
            situation=f"에이전트가 {x['bed_ko']}로 확정했습니다. {x['undo_until']}까지 되돌릴 수 있습니다.")
        rows[-1]["bed"] = _bed(x["bed"])
        rows[-1]["due"], rows[-1]["due_note"] = x.get("undo_until") or "", "되돌리기 시한"

    def key(r):
        return (not r["late"], r["due_at"] is None, r["due_at"] or "", r["patient"])

    rows.sort(key=key)
    groups: dict[str, int] = {}
    for r in rows:
        groups[r["kind_label"]] = groups.get(r["kind_label"], 0) + 1
    counts = {"all": len(rows), "late": sum(r["late"] for r in rows), "carried": sum(r["carried"] for r in rows)}
    return {"now": now.isoformat(timespec="minutes"), "rows": rows, "counts": counts, "groups": groups}


# 경과: 사람이 읽는 말로 바꾸고, 내부 처리 기록(실행, 예외 종료, 거절된 계획 등)은 뺀다(화면 사전 검토 2026-11-10)
TIMELINE_KO = {"예외 감지": "문제 발견", "담당 검토로 보냄": "배정 보류", "사람 승인 요청": "원무 승인 요청",
               "병동·원무에 배정 통보": "배정 통보", "환자에게 입원 안내 연락": "입원 안내 연락"}
TIMELINE_HIDE = {"실행", "예외 종료", "막음·거절", "안내 시각 위험", "작업 완료"}


def _timeline(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    for r in rows:
        what, detail = r["what"], r["detail"] or ""
        if what in TIMELINE_HIDE:
            continue
        if what == "작업 생성":
            if "사람 확인 요청" not in detail:
                continue
            what, detail = "원무 확인 요청", detail.split(" · ")[0]
        parts = [x for x in detail.split(" · ") if x and not re.search(r"\d+회 반복", x)]
        what = TIMELINE_KO.get(what, what)
        if not r.get("mine", True):
            what = "앞 환자 " + what
        item = {"time": r["time"], "who": r["who"], "what": what, "detail": ", ".join(parts)}
        if (item["what"], item["detail"]) in seen:  # 계획 주기마다 다시 남는 같은 기록은 처음 한 번만
            continue
        seen.add((item["what"], item["detail"]))
        out.append(item)
    return out


def patient_detail(st, case: str, accounts: dict | None = None) -> dict | None:
    ev = st.store.read()
    people = views._people(ev)
    if case not in people:
        return None
    p = people[case]
    t = views.case_timeline(st, case)
    reg = _person(people, case)["patient_id"]
    req = p.get("requested_at")
    # 환자가 고르는 것은 병동이 아니라 병실 등급이다(1인실, 다인실 등. 서울대·분당서울대 입원 안내). 병동은 진료과로 정해진다.
    pref = "1인실 희망" if p.get("wants_single_room") else "다인실(기준 병실)"
    dept_ward = ", ".join(w.name for w in st.h.wards if p.get("department") in w.departments)
    specialty = next((w.specialty for w in st.h.wards if p.get("department") in w.departments), "")
    # 원무 상세에 두는 것: 배정과 연락에 필요한 것만(docs/검토/화면재설계/05_원무_열람범위.md). 병원체명, 주민번호, 경과기록,
    # 검사 수치는 두지 않는다. 연락처는 합성 번호(010-0000-xxxx)다.
    return {"case": case, **_person(people, case), "department": p.get("department") or "",
            "status": t["status"], "bed": t["bed_ko"], "isolation": t["isolation"],
            "from_icu": bool(p.get("from_icu")), "room_pref": pref, "dept_ward": dept_ward, "specialty": specialty,
            "reserved_at": f"{datetime.fromisoformat(req):%-m월 %-d일 %H:%M}" if req else "",
            "phone": f"010-0000-{reg[-4:]}", "guardian_phone": f"010-0000-{int(reg[-4:]) + 5000:04d}",
            "notes": notes(ev, case, accounts), "timeline": _timeline(t["rows"])[-12:] + _web_rows(ev, case, accounts)}


def _web_rows(ev, case: str, accounts: dict | None) -> list[dict]:
    """화면에서 사람이 한 처리(연락 기록, 작업 처리)를 경과 끝에 붙인다."""
    out = []
    for e in ev:
        if e.case_id != case or ":web:" not in e.event_id:
            continue
        who = (accounts or {}).get(str(e.payload.get("by")), {}).get("name", "원무")
        if e.type is EventType.HUMAN_TASK_DONE:
            out.append({"time": e.observed_at.strftime("%H:%M"), "who": who, "what": f"{who} 처리", "detail": e.payload.get("result", "")})
        elif e.type is EventType.PATIENT_CONTACTED:
            out.append({"time": e.observed_at.strftime("%H:%M"), "who": who, "what": f"{who} 입원 안내 연락",
                        "detail": f"수속 {datetime.fromisoformat(e.payload['promised_at']):%H:%M}"})
        elif e.type is EventType.FREE_TEXT_NOTE and e.payload.get("tag") == "contact_no_answer":
            out.append({"time": e.observed_at.strftime("%H:%M"), "who": who, "what": f"{who} 연락 안 됨", "detail": ""})
    return out


# 배정 근거: 에이전트 점수 항목을 사람이 읽는 말로 (점수 숫자는 보여 주지 않는다)
BASIS_KO = {"department_ward_match": "진료과에 맞는 병동", "negative_pressure_preserve": "음압실은 아껴 둠",
            "positive_unordered_single": "격리 확인 전이라 1인실", "single_room_requested": "1인실 희망 반영",
            "near_nurse_station_high_risk": "낙상 위험이라 간호사실 가까이", "ward_load": "병동 업무량 나눔",
            "ready_delay": "앞 환자 퇴실 뒤 준비"}


def _runner(ru) -> str:
    """차순위 병상: 기록에 따라 병상 ID 문자열이거나 {'bed_id': ...} 사전이다."""
    bed = ru.get("bed_id") if isinstance(ru, dict) else ru
    return views.bed_ko(bed) if bed else ""


def _basis(text: str) -> str:
    keys = re.findall(r"([a-z_]+) [-+]", text or "")
    return ", ".join(BASIS_KO[k] for k in keys if k in BASIS_KO) or "배정 기준 충족"


def assignments(st, accounts: dict | None = None, day: str | None = None) -> dict:
    """에이전트와 사람이 확정한 배정 기록 (최근 것이 위). 하루 단위로 본다(day=YYYY-MM-DD, 없으면 오늘).
    확정 주체, 근거, 되돌리기 시한, 지금 상태. days: 기록이 있는 날짜 목록(날짜 고르기용)."""
    from datetime import date as _date
    from turnaround.saga import fold
    ev = st.store.read()
    people = views._people(ev)
    s = fold(ev)
    note = {e.payload.get("proposal_id"): e for e in ev if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED}
    cancelled = {e.case_id for e in ev if e.type is EventType.ADMISSION_CANCELLED and e.observed_at <= st.now}
    # 사람 직접 배정(manual_override): 확정 사건의 proposal_id(mo…)로 누가 어떤 사유로 했는지 찾는다
    manual = {e.event_id.split(":")[1]: e.payload for e in ev if e.type is EventType.MANUAL_OVERRIDE_APPLIED}
    want = _date.fromisoformat(day) if day else st.now.date()
    days = sorted({e.observed_at.date().isoformat() for e in ev if e.type is EventType.MATCH_COMMITTED and e.observed_at <= st.now},
                  reverse=True)
    rows = []
    for e in ev:
        if e.type is not EventType.MATCH_COMMITTED or e.observed_at > st.now or e.observed_at.date() != want:
            continue
        bed = e.payload.get("bed_id")
        n = note.get(e.payload.get("proposal_id"))
        b = (n.payload.get("basis") if n else {}) or {}
        until = datetime.fromisoformat(n.payload["objection_until"]) if n and n.payload.get("objection_until") else None
        m = s.matches.get(e.case_id)
        if e.case_id in cancelled:
            state = "취소"
        elif m is not None and m.active and m.bed_id == bed:
            state = "입실 완료" if EventType.PATIENT_ARRIVED in m.facts else "입실 대기"
        else:
            state = "되돌림"
        mo = manual.get(str(e.payload.get("proposal_id")))
        actor = str((mo or {}).get("actor") or e.payload.get("actor") or "")
        by = "에이전트" if e.payload.get("committed_by") == "agent" else (accounts or {}).get(actor, {}).get("name", "원무")
        if mo:
            from turnaround.api.beds import REASON_CODES
        rows.append({"at": e.observed_at.isoformat(timespec="minutes"), "time": e.observed_at.strftime("%H:%M"), "case": e.case_id,
                     **_person(people, e.case_id), "bed": views.bed_ko(bed), "bed_short": _bed(bed), "by": by,
                     "basis": f"직접 지정, {REASON_CODES.get(mo.get('reason_code'), '사유 없음')}" if mo else _basis(b.get("rationale", "")),
                     "runner_up": _runner(b.get("runner_up")),
                     "undo_until": until.strftime("%H:%M") if until else "", "undo_open": bool(until and st.now < until and state == "입실 대기"),
                     "state": state})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return {"now": st.now.isoformat(timespec="minutes"), "day": want.isoformat(), "days": days, "rows": rows}
