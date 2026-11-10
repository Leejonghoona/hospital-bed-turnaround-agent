"""감염관리실 화면 (P4, 흐름: docs/화면재설계/flows/감염관리실_이준호.md). 합성 데이터.

병상과 관련된 감염관리 결정만 다룬다: 늦은 격리 병상 결정, 오늘 들어올 격리 환자 배정 확인, 동실 접촉자, 종말소독 확인,
격리 병상 현황, 격리 환자 목록, 밤사이 기록, 인계 메모. 검사·격리 오더·해제 판단은 EMR이 한다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from turnaround.events import EventType
from turnaround.saga import fold
from turnaround.sim.models import RoomType
from turnaround.ui import views

SEX = {"M": "남", "F": "여"}
GRADE = {"negative_pressure": "음압 1인실", "single": "1인실"}


def _short(bed: str | None) -> str:
    if not bed:
        return ""
    try:
        w, r, n = bed.split("-")
        return f"{w[1:]}-{r[1:]}-{n[1:]}"
    except ValueError:
        return bed


def _band(age) -> str:
    return f"{int(age) // 10 * 10}대" if isinstance(age, int) else ""


def _prec(x) -> str:
    return ", ".join(views.PREC_KO.get(p, p) for p in (x or []))


def home(st, notes_of) -> dict:
    ev = st.store.read()
    s = fold(ev)
    people = views._people(ev)
    gw = st.gw
    occ = gw._occupants(ev)  # 병상 → 환자(재원 + 확정된 입원)
    assigned = gw._assignments(ev)
    holder = {b: c for c, b in assigned.items()}
    room_of, room_by = gw.room_of, gw.room_by_id
    ward_name = {w.id: w.name for w in st.h.wards}
    states = {b["bed"]: b for b in views.bed_states(st)}

    # 격리 환자 (입원 예정, 재원, 퇴원 진행)
    iso = []
    for case, p in people.items():
        precs = list(p.get("precautions") or [])
        for e in ev:
            if e.case_id == case and e.type is EventType.ISOLATION_ORDER_ADDED:
                precs = list(dict.fromkeys(precs + list(e.payload.get("precautions") or [])))
                p = {**p, "organism": e.payload.get("organism") or p.get("organism")}
        if not precs:
            continue
        m = s.matches.get(case)
        d = s.discharges.get(case)
        if d is not None:
            if EventType.PATIENT_DEPARTED in d.facts:
                continue
            status, bed = "퇴원 진행", d.bed_id
        elif m is not None and m.active and EventType.PATIENT_ARRIVED in m.facts:
            status, bed = "재원", m.bed_id
        elif m is not None and m.active:
            status, bed = "입원 예정", m.bed_id
        else:
            status, bed = "병상 대기", None
        rt = room_by[room_of[bed]].room_type.value if bed in room_of else ""
        iso.append({"case": case, "patient": p.get("name") or "", "age": p.get("age"), "sex": SEX.get(str(p.get("gender") or ""), ""),
                    "organism": views.ORG_KO.get(p.get("organism"), p.get("organism") or ""), "precautions": _prec(precs),
                    "status": status, "bed": _short(bed), "room": GRADE.get(rt) or (f"{room_by[room_of[bed]].capacity}인실" if bed in room_of else ""),
                    "checked": any(n["tag"] == "ipc_checked" for n in notes_of(case))})
    order = {"병상 대기": 0, "입원 예정": 1, "재원": 2, "퇴원 진행": 3}
    iso.sort(key=lambda x: (order.get(x["status"], 9), x["patient"]))

    # 빈 격리 병상 (1인실, 음압 1인실): 비어 있고 확정·소독 대기 아님
    free = []
    for b in gw.beds:
        r = room_by[room_of[b.id]]
        if r.room_type not in (RoomType.SINGLE, RoomType.NEGATIVE_PRESSURE):
            continue
        if b.id in occ or b.id in holder or gw._room_terminal_pending(b.id, ev) or gw._out_of_service(b.id, ev):
            continue
        free.append({"bed": _short(b.id), "ward": ward_name.get(b.ward_id, ""), "grade": GRADE[r.room_type.value],
                     "state": states.get(b.id, {}).get("state_ko", "")})

    # 늦은 격리 병상 결정 (감염관리실만 결정)
    org = {}
    for e in ev:
        if e.type is EventType.ISOLATION_ORDER_ADDED:
            org[e.case_id] = e.payload
    decisions = []
    for x in views.pending_approvals(st):
        if x["decider"] != "ipc":
            continue
        bed = x.get("bed")
        room = room_of.get(bed or "")
        mates = []
        for ob, op in occ.items():
            if room and room_of.get(ob) == room and ob != bed:
                risk = [t for t, on in (("면역저하", op.immunocompromised), ("개방 상처", op.open_wound),
                                        ("격리 중", bool(op.precautions))) if on]
                mates.append({"bed": _short(ob), "sex": SEX.get(op.gender.value if op.gender else "", ""), "age": _band(op.age),
                              "risk": risk})
        o = org.get(x["case"], {})
        decisions.append({"exception_id": x["exception_id"], "case": x["case"],
                          "patient": (people.get(x["case"]) or {}).get("name", ""), "who": x["who"], "bed": _short(bed),
                          "room": GRADE.get(room_by[room].room_type.value) or f"{room_by[room].capacity}인실" if room else "",
                          "organism": views.ORG_KO.get(o.get("organism"), o.get("organism") or ""),
                          "precautions": _prec(o.get("precautions")), "deadline": x["deadline"], "since": x["since"],
                          "situation": x["situation"], "effect": x["effect"], "roommates": mates})

    # 종말소독 대기
    terminal = []
    for d in sorted(s.discharges.values(), key=lambda x: x.id):
        if d.isolation and EventType.PATIENT_DEPARTED in d.facts and not any(k == "terminal" for _, k in d.cleaning_done_kinds):
            dep = d.facts[EventType.PATIENT_DEPARTED]
            prog = "청소 중" if d.seen(EventType.CLEANING_STARTED) else "청소 요청됨" if d.seen(EventType.CLEANING_REQUEST_ISSUED) else "요청 전"
            mins = int((st.now - dep).total_seconds() // 60)
            terminal.append({"bed": _short(d.bed_id), "ward": ward_name.get(next((b.ward_id for b in gw.beds if b.id == d.bed_id), ""), ""),
                             "departed": dep.strftime("%H:%M"), "progress": prog, "elapsed": f"{mins // 60}시간 {mins % 60}분" if mins >= 60 else f"{mins}분"})

    # 동실 접촉자 (입실 뒤 격리 오더)
    contacts = [{"index": (people.get(e.case_id) or {}).get("name", ""), "room": e.payload.get("room_id", ""),
                 "count": len(e.payload.get("contacts") or []), "beds": [_short(c["bed_id"]) for c in e.payload.get("contacts") or []],
                 "time": e.observed_at.strftime("%H:%M")}
                for e in ev if e.type is EventType.CONTACT_TRACE_REQUESTED and e.observed_at <= st.now]

    # 밤사이 기록 (전날 18:00 ~ 오늘 09:00)
    start = datetime.combine(st.now.date() - timedelta(days=1), time(18, 0))
    end = datetime.combine(st.now.date(), time(9, 0))
    night = []
    for e in ev:
        if not (start <= e.observed_at < end):
            continue
        if e.type is EventType.ISOLATION_ORDER_ADDED:
            night.append({"time": e.observed_at.strftime("%m/%d %H:%M"), "what": "격리 오더",
                          "detail": f"{(people.get(e.case_id) or {}).get('name', '')}, {_prec(e.payload.get('precautions'))}"})
        elif e.type in (EventType.ACTION_APPROVED, EventType.ACTION_REJECTED) and e.payload.get("actor") in ("ipc", "id_oncall"):
            night.append({"time": e.observed_at.strftime("%m/%d %H:%M"), "what": "당직 병상 결정",
                          "detail": (people.get(e.case_id) or {}).get("name", "")})

    # 격리 병상 현황판: 1인실·음압실과 격리 환자가 있는 다인실
    iso_beds = {x["bed"] for x in iso if x["bed"]}
    board = []
    for w in st.h.wards:
        rooms = []
        for r in (x for x in st.h.rooms if x.ward_id == w.id):
            beds = [b for b in st.h.beds if b.room_id == r.id]
            if r.room_type is RoomType.MULTI and not any(_short(b.id) in iso_beds for b in beds) and not r.cohort_key:
                continue
            cells = []
            for b in beds:
                p = occ.get(b.id)
                tag = "소독 대기" if gw._room_terminal_pending(b.id, ev) else ("격리 " + _prec(p.precautions)) if p and p.precautions \
                    else ("사용 중" if p else states.get(b.id, {}).get("state_ko", "빈 병상"))
                kind = "terminal" if tag == "소독 대기" else "iso" if tag.startswith("격리") else "used" if p else "free"
                cells.append({"bed": b.id.split("-")[-1].replace("B", "") + "번", "tag": tag, "kind": kind})
            rooms.append({"label": r.id.split("-")[-1].replace("R", "") + "호",
                          "grade": GRADE.get(r.room_type.value) or f"{r.capacity}인실" + (" 코호트" if r.cohort_key else ""), "beds": cells})
        board.append({"ward": w.name, "rooms": rooms})

    summary = {"decisions": len(decisions), "first_deadline": min((d["deadline"] for d in decisions), default=""),
               "isolated": len(iso), "incoming": sum(x["status"] == "입원 예정" for x in iso),
               "free_np": sum(f["grade"] == "음압 1인실" for f in free), "free_single": sum(f["grade"] == "1인실" for f in free),
               "terminal": len(terminal)}
    return {"summary": summary, "decisions": decisions, "free": free, "isolated": iso, "terminal": terminal,
            "contacts": contacts, "night": night, "board": board}
