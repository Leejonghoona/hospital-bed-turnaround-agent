"""원무 작업 목록과 환자 상세 (API 응답 모양). 데이터는 ui/views.py의 같은 함수에서 나온다. 합성 데이터.

화면 문구 규칙(DECISIONS 2026-11-10): 표의 칸은 한 줄에 들어가는 짧은 문구로, 가운데 점(·)은 쓰지 않는다.
"""

from __future__ import annotations

from datetime import datetime

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


def _person(people: dict, case: str) -> dict:
    p = people.get(case) or {}
    return {"patient": str(p.get("name") or "환자"), "patient_id": str(p.get("patient_id") or "").replace("SYN-", ""),
            "age": p.get("age"), "sex": SEX.get(str(p.get("gender") or ""), "")}


def _due(t: datetime | None, now: datetime) -> tuple[str, str]:
    """(시각, 보조 문구). 보조 문구는 어제/지난 시간처럼 판단에 필요한 것만."""
    if t is None:
        return "", "오늘"
    if t.date() < now.date():
        return t.strftime("%H:%M"), "어제" if (now.date() - t.date()).days == 1 else f"{(now.date() - t.date()).days}일 전"
    if t < now:
        mins = int((now - t).total_seconds() // 60)
        return t.strftime("%H:%M"), (f"{mins // 60}시간 지남" if mins >= 60 else f"{mins}분 지남")
    return t.strftime("%H:%M"), ""


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


def admission_rows(st) -> dict:
    a = views.admission_view(st)
    ev = st.store.read()
    people = views._people(ev)
    now = st.now
    rows: list[dict] = []

    def add(case, kind, kind_label, action, due_at, late, bed, reason, status, carried=False, ref=None):
        t, note = _due(due_at, now)
        rows.append({"id": ref or f"{kind}:{case}", "case": case, "kind": kind, "kind_label": kind_label, "action": action,
                     "due": t, "due_note": note, "due_at": due_at.isoformat() if due_at else None, "late": bool(late),
                     "bed": _bed(bed), "reason": reason, "status": status, "carried": carried, **_person(people, case)})

    for x in a["approvals"]:
        carried = x["requested_at"].date() < now.date()
        add(x["case"], "approval", "승인", {"request_recontact": "재연락", "propose_rematch": "병상 다시 찾기",
                                          "request_postpone_contact": "연락 보류"}.get(x["action"], x["action_ko"]),
            x["due_at"], x["overdue"], x.get("bed"), x["trigger_ko"] or _short(x["situation"]),
            "이전 근무" if carried else "대기", carried, ref=x["exception_id"])
    for x in a.get("waiting_ipc") or []:
        add(x["case"], "ipc_wait", "감염관리", "병상 결정", x["due_at"], False, x.get("bed"), x["trigger_ko"] or "늦은 격리",
            "결정 대기", ref="ipc:" + x["exception_id"])
    for x in a["contacts"]:
        add(x["case"], "contact", "연락", "입원 안내", None, False, x["bed"], f"추천 안내 {x['suggest_at']:%H:%M}", "대기")
    for x in a["tasks"]:
        esc = bool(x.get("escalated_to"))
        status = "책임자 확인" if esc else "이전 근무" if x["stale"] else "대기"
        if x["raw_kind"] == "brain_escalate":
            # 할 일 칸에는 무슨 일인지(병상 없음 등), 근거 칸에는 다음 판단에 쓸 사실을 둔다(같은 말 반복 금지)
            label, action, reason = "확인 필요", _short(x.get("why") or "") or "판단 필요", _hint(st, x["case"])
        else:
            label, action, reason = "작업", x["kind"], _short(x.get("why") or "")
        add(x["case"], "task", label, action, x["due_at"], x["overdue"], x.get("bed"), reason, status, x["stale"], ref=x["task_id"])
    for x in a["notices"]:
        if not (x.get("active") and x.get("undo_open")):
            continue
        add(x["case"], "confirmed", "확정", "에이전트 배정", None, False, None,
            f"차순위 {x['runner_up']}" if x.get("runner_up") else "차순위 없음", "되돌리기 가능", ref="notice:" + x["case"])
        rows[-1]["bed"] = x["bed_ko"]
        rows[-1]["due"], rows[-1]["due_note"] = x.get("undo_until") or "", "되돌리기 시한"

    def key(r):
        return (not r["late"], r["due_at"] is None, r["due_at"] or "", r["patient"])

    rows.sort(key=key)
    counts = {"all": len(rows), "late": sum(r["late"] for r in rows), "approval": sum(r["kind"] == "approval" for r in rows),
              "contact": sum(r["kind"] == "contact" for r in rows), "ipc_wait": sum(r["kind"] == "ipc_wait" for r in rows),
              "carried": sum(r["carried"] for r in rows)}
    return {"now": now.isoformat(timespec="minutes"), "rows": rows, "counts": counts}


def patient_detail(st, case: str) -> dict | None:
    ev = st.store.read()
    people = views._people(ev)
    if case not in people:
        return None
    p = people[case]
    t = views.case_timeline(st, case)
    return {"case": case, **_person(people, case), "department": p.get("department") or "",
            "status": t["status"], "bed": t["bed_ko"], "isolation": t["isolation"],
            "timeline": [{"time": r["time"], "who": r["who"], "what": r["what"], "detail": r["detail"]} for r in t["rows"]][-12:]}
