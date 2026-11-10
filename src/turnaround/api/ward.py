"""병동 화면 (P3): 우리 병동으로 올 환자와 준비 상태, 병상 현황. 합성 데이터.

원칙: 병동은 자기 병동만 본다(최소 권한). 병동은 배정을 직접 풀지 않고 원무에 의견을 보낸다.
근거: 서울고법 2013누25193 "그 병동의 수간호사가 병실 배정에 대해 이의를 제기하는 일이 종종 있기 때문에, 때로 해당 병동의
수간호사에게 전화하여 병실 배정에 대한 의견을 들은 후 환자가 입원할 병실을 결정하기도 한다"(docs/검토/현실괴리/02_병동간호.md).
"""

from __future__ import annotations

from datetime import datetime

from turnaround.events import EventType
from turnaround.saga import fold
from turnaround.ui import views

OPINION_REASONS = ("간호 인력 부족", "진료과와 맞지 않음", "감염 우려", "같은 병동에 입실 몰림", "기타")
STEP = {0: "준비 전", 1: "준비 완료", 2: "호출됨", 3: "입실 완료"}
SEX = {"M": "남", "F": "여"}


def _short(bed: str) -> str:
    try:
        w, r, n = bed.split("-")
        return f"{w[1:]}-{r[1:]}-{n[1:]}"
    except ValueError:
        return bed


def incoming(st, ward: str) -> dict:
    ev = st.store.read()
    s = fold(ev)
    people = views._people(ev)
    v = views.ward_view(st)
    ward_of = {b.id: b.ward_id for b in st.h.beds}
    bstate = {b["bed"]: b for b in views.bed_states(st)}
    rows = []
    for f in v["flow"]:
        if ward_of.get(f["bed"]) != ward:
            continue
        m = s.matches.get(f["case"])
        step = views.WARD_STEPS.index(f["step"]) if f["step"] in views.WARD_STEPS else 0
        if step == 3:
            continue  # 입실 끝난 환자는 할 일이 아니다
        p = people.get(f["case"]) or {}
        promised = s.contact_promise.get(f["case"])
        front = bstate.get(f["bed"], {})
        rows.append({"case": f["case"], "patient": p.get("name") or "환자", "age": p.get("age"),
                     "sex": SEX.get(str(p.get("gender") or ""), ""), "bed": _short(f["bed"]),
                     "checkin": f"{datetime.fromisoformat(promised):%H:%M} 수속" if promised else "안내 전",
                     "checkin_at": promised or "", "prep": f["prep"], "front": front.get("state_ko", ""),
                     "front_detail": (front.get("detail") or "").split(" · ")[0], "step": STEP[step],
                     "hold": f["hold"], "notified": f["notified"], "by": f["by"],
                     "arrived": bool(m and EventType.PATIENT_ARRIVED in m.facts)})
    rows.sort(key=lambda r: (r["step"] != "준비 전", r["checkin_at"] or "99"))
    name = next((w.name for w in st.h.wards if w.id == ward), ward)
    return {"ward": name, "rows": rows, "reasons": list(OPINION_REASONS)}


def board(st, ward: str) -> dict:
    states = {b["bed"]: b for b in views.bed_states(st)}
    rooms = []
    for r in (x for x in st.h.rooms if x.ward_id == ward):
        beds = [{"id": b.id, "label": b.id.split("-")[-1].replace("B", "") + "번", "state": states[b.id]["state"],
                 "state_ko": states[b.id]["state_ko"], "detail": states[b.id]["detail"].replace(" · ", ", ")}
                for b in st.h.beds if b.room_id == r.id]
        grade = {"negative_pressure": "음압 1인실", "single": "1인실"}.get(r.room_type.value) or f"{r.capacity}인실"
        rooms.append({"id": r.id, "label": r.id.split("-")[-1].replace("R", "") + "호", "grade": grade,
                      "sex": SEX.get(r.gender.value if r.gender else "", ""), "beds": beds})
    name = next((w.name for w in st.h.wards if w.id == ward), ward)
    return {"ward": name, "rooms": rooms}


DIS_STEPS = [(EventType.PATIENT_DEPARTED, "퇴실"), (EventType.GUARDIAN_ARRIVED, "보호자 도착"), (EventType.BILLING_DONE, "수납 완료"),
             (EventType.MEDICATION_READY, "퇴원약 도착"), (EventType.ORDER_CONFIRMED, "퇴원 오더"), (EventType.DISCHARGE_DECIDED, "퇴원 결정")]


def discharges(st, ward: str) -> dict:
    """우리 병동 오늘 퇴원 진행(병동 화면 '우리 병동'). 단계는 가장 최근에 일어난 일. 12시가 지나도 퇴실 전이면 표시."""
    ev = st.store.read()
    s = fold(ev)
    people = views._people(ev)
    ward_of = {b.id: b.ward_id for b in st.h.beds}
    rows = []
    for d in s.discharges.values():
        if ward_of.get(d.bed_id) != ward or EventType.DISCHARGE_CANCELLED in d.facts:
            continue
        dep = d.seen(EventType.PATIENT_DEPARTED)
        if dep is not None and dep.date() != st.now.date():
            continue
        if dep is None and not d.facts:
            continue
        step = next((label for t, label in DIS_STEPS if t in d.facts), "퇴원 예정")
        p = people.get(d.id) or {}
        flags = []
        if dep is None and st.now.hour >= 12:
            flags.append("12시 넘음")
        if dep is not None and d.isolation:
            flags.append("종말소독")
        rows.append({"case": d.id, "patient": p.get("name") or "환자", "age": p.get("age"),
                     "sex": SEX.get(str(p.get("gender") or ""), ""), "bed": _short(d.bed_id), "step": step,
                     "departed": dep.strftime("%H:%M") if dep else "", "flags": flags})
    rows.sort(key=lambda r: (r["departed"] != "", r["departed"] or "", r["bed"]))
    return {"rows": rows}
