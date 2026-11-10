"""병상 직접 배정: 병동→병실→병상 현황판과 추천 후보 (API 응답 모양). 합성 데이터.

조사(docs/검토/화면재설계/06_병상배정_화면방식.md): 실제 배정 화면은 대기 환자와 병상 현황판(병동→병실→병상 격자)을 함께 쓰고
(Cerner Bed Board), 국내 대형병원은 가용 병상을 병실 등급별·병동별로 보여 주며 추천 순위를 붙인다(분당서울대 스누워드가이드).
도면이 주 화면이라는 근거는 찾지 못했다. 그래서 '추천 후보 + 현황판'으로 두고, 병상마다 고를 수 있는지는 게이트웨이의
사전 검사(override_precheck)로 정한다. 실제 확정도 같은 게이트웨이(manual_override)가 한다.
"""

from __future__ import annotations

from turnaround.events import EventType
from turnaround.saga import fold
from turnaround.ui import views

BLOCK_KO = {"bed_occupied": "사용 중", "precondition_unmet": "같은 병실 종말소독 전", "room_out_of_service": "병실 사용 중지",
            "bed_on_hold": "다른 환자용으로 잡아 둠", "after_death_care": "사후 처치 중", "surveillance_hold": "감시 중이라 이동 제한",
            "unknown_case_or_bed": "선택할 수 없음"}
VIOLATION_KO = {"gender_room": "다른 성별 병실", "tb_shared_room": "결핵 환자와 병실 공유 불가", "same_name_room": "같은 병실 동명이인",
                "gender_missing": "성별 정보 없음", "unknown_bed": "없는 병상", "airborne_needs_negative_pressure": "음압실 필요",
                "non_cohort_in_cohort_room": "다른 병원체 코호트 병실", "protective_in_cohort": "보호 격리 환자는 코호트 불가",
                "preemptive_in_cohort": "선제 격리 환자는 코호트 불가", "isolation_room": "격리 환자를 다인실에(차선책)"}
REASON_CODES = {"patient_request": "환자·보호자 요청", "clinical": "진료상 필요(의료진 요청)", "operational": "병동 운영 사정",
                "vip": "병원 지정 요청", "other": "기타"}
GRADE = {"negative_pressure": "음압 1인실", "single": "1인실"}
SEX = {"M": "남", "F": "여"}


def _fit(pre: dict) -> tuple[str, str]:
    if pre["blocked"]:
        return "no", ", ".join(BLOCK_KO.get(x, x) for x in pre["blocked"])
    why = ", ".join(VIOLATION_KO.get(x, x) for x in pre["violations"])
    return {None: ("ok", ""), "reason_entry": ("reason", why + ". 사유를 적으면 가능"),
            "preapproval": ("ipc", why + ". 감염관리실 승인 필요")}.get(pre["tier"], ("no", why))


def board(st, case: str) -> dict | None:
    from turnaround.agent.tools import PlanningTools
    ev = st.store.read()
    people = views._people(ev)
    if case not in people:
        return None
    p = people[case]
    s = fold(ev)
    holder = {b: c for c, b in st.gw._assignments(ev).items()}
    leaving = {d.bed_id: d for d in s.discharges.values() if EventType.DISCHARGE_CANCELLED not in d.facts}
    ward_names = {w.id: w.name for w in st.h.wards}
    dept_wards = [w.name for w in st.h.wards if p.get("department") in w.departments]
    wards = []
    for w in st.h.wards:
        rooms = []
        for r in (x for x in st.h.rooms if x.ward_id == w.id):
            beds = []
            for b in (x for x in st.h.beds if x.room_id == r.id):
                if b.id in holder and holder[b.id] != case:
                    status = f"배정됨 {views._people(ev).get(holder[b.id], {}).get('name', '')}".strip()
                elif b.id in leaving:
                    f = leaving[b.id].facts
                    status = ("준비 완료" if EventType.BED_READY in f else "청소 중" if EventType.CLEANING_STARTED in f
                              else "청소 대기" if EventType.PATIENT_DEPARTED in f else "퇴원 예정")
                elif b.id in st.gw.census:
                    status = "사용 중"
                else:
                    status = "빈 병상"
                fit, why = _fit(st.gw.override_precheck(case, b.id, ev))
                if status.startswith("배정됨"):
                    why = "다른 환자에게 배정됨"
                if holder.get(b.id) == case:
                    fit, why = "no", "지금 배정된 병상"
                beds.append({"id": b.id, "label": b.id.split("-")[-1].replace("B", "") + "번", "status": status,
                             "fit": fit, "why": why, "mine": holder.get(b.id) == case})
            rt = r.room_type.value
            rooms.append({"id": r.id, "label": r.id.split("-")[-1].replace("R", "") + "호",
                          "grade": GRADE.get(rt) or f"{r.capacity}인실", "sex": SEX.get(r.gender.value if r.gender else "", ""),
                          "cohort": r.cohort_key or "", "beds": beds})
        wards.append({"id": w.id, "name": w.name, "specialty": w.specialty, "rooms": rooms})
    recommend = []
    try:
        mine = next((b for b, c in holder.items() if c == case), None)
        for c in [x for x in PlanningTools(st.gw, st.h.wards).candidates(case, st.now, ev) if x.bed_id != mine][:3]:
            recommend.append({"bed": c.bed_id, "label": views.bed_ko(c.bed_id),
                              "ready": "지금 가능" if c.ready_at <= st.now else f"{views._hm(c.ready_at)} 준비 예상",
                              "basis": ", ".join(_basis_parts(c.parts)) or "배정 기준 충족"})
    except Exception:  # noqa: BLE001 - 추천은 보조 정보라 실패하면 비운다
        recommend = []
    need = {"sex": SEX.get(str(p.get("gender") or ""), "성별 미상"),
            "isolation": ", ".join(views.PREC_KO.get(x, x) for x in (p.get("precautions") or [])) or "없음",
            "department": p.get("department") or "", "dept_wards": dept_wards,
            "grade_wish": "1인실 희망" if p.get("wants_single_room") else "다인실(기준 병실)"}
    current = next((views.bed_ko(b) for b, c in holder.items() if c == case), "")
    return {"case": case, "patient": p.get("name"), "need": need, "current": current, "recommend": recommend,
            "wards": wards, "reasons": [{"code": k, "label": v} for k, v in REASON_CODES.items()], "ward_names": ward_names}


def _basis_parts(parts: dict) -> list[str]:
    from turnaround.api.worklist import BASIS_KO
    return [BASIS_KO[k] for k, v in parts.items() if k in BASIS_KO and (v > 0 or k == "ready_delay")]


def refusal_ko(reasons: list[str]) -> str:
    return ", ".join(BLOCK_KO.get(x) or VIOLATION_KO.get(x) or {"role_not_allowed": "배정 권한 없음",
                                                                  "reason_required": "사유를 골라 주세요"}.get(x, x) for x in reasons)
