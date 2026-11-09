"""병실관리AI 화면 (08 계획 4단계, UI 개편). 합성 데이터 전용.

실행: streamlit run src/turnaround/ui/app.py
- 시연 대본: 3장면을 "다음" 버튼으로 한 단계씩, 단계마다 "이 화면에서 볼 것" 안내.
- 역할 화면: 원무·병동·감염관리실·관리자·청소 협력업체를 골라 직접 눌러 본다.
화면 데이터는 `ui/views.py`, 디자인은 `ui/theme.py`, 대본은 `ui/scenes.py`(시험과 같은 함수).
"""

from __future__ import annotations

from datetime import time

import streamlit as st

from turnaround.ui import theme as T
from turnaround.ui import views
from turnaround.ui.scenes import SCENES, SceneRun

st.set_page_config(page_title="병실관리AI", page_icon="🛏️", layout="wide", initial_sidebar_state="expanded")
st.html(T.CSS)
st.html(T.LANG_SCRIPT, unsafe_allow_javascript=True)

ROLE_KO = {"audit": "결정 기록", "admission": "원무 · 병상 배정", "ward": "병동 · 책임간호사", "ipc": "감염관리실", "manager": "관리자",
           "onboarding": "새 병원 온보딩", "vendor": "청소 협력업체"}
ROLE_DESC = {"admission": "에이전트가 확정한 배정을 확인하고, 사람 승인이 필요한 일만 처리합니다",
             "ward": "배정 통보를 받고 병동 준비부터 입실까지의 흐름을 봅니다",
             "ipc": "격리 사안 통지를 확인하고 종말소독 진행을 봅니다",
             "manager": "오늘의 흐름, 안전 검사, 자동 처리 범위 제안을 한눈에 봅니다",
             "onboarding": "새 병원의 내보내기 표를 올리면 질문에 답해 설정을 만들고, 시험 운영 검사 뒤 승인합니다",
             "vendor": "청소 요청만 봅니다. 환자 정보는 보이지 않습니다",
             "audit": "누가 언제 무엇을 결정했는지 남긴 기록입니다(감사·되돌아보기)"}


def _event(store, case, key, etype, now, **payload):
    from turnaround.events import Event
    store.append(Event(event_id=f"{case}:ui:{key}", case_id=case, type=etype, occurred_at=now, observed_at=now,
                       source="human", payload=payload))


FILTER = {"ward": "전체", "q": ""}
CONTACT_KINDS = {"recontact", "recontact_prepare", "postpone_contact"}


def _task_done(sv, t: dict, by: str) -> None:
    from turnaround.events import EventType
    _event(sv.store, t["case"], f"done:{t['task_id']}", EventType.HUMAN_TASK_DONE, sv.now, task_id=t["task_id"], by=by)


def keep(item: dict) -> bool:
    """병동 필터와 환자 검색 (실사용 화면, UI 3차)."""
    ward = FILTER["ward"]
    if ward != "전체" and not str(item.get("bed_ko", "")).startswith(ward):
        return False
    q = FILTER["q"].strip()
    return not q or q in str(item.get("who", "")) or q in str(item.get("bed_ko", ""))


@st.dialog("환자 경과", width="large")
def patient_dialog(sv, case: str) -> None:
    t = views.case_timeline(sv, case)
    st.html(T.card(t["who"], f"{T.e(t['department'])} · 병상 {T.e(t['bed_ko'])}" + (f" · 기다리는 병상의 앞 환자 {T.e(t['discharge'])}" if t["discharge"] else ""),
                   [T.chip(t["status"], "blue"), T.chip(f"격리 {t['isolation']}", "red" if t["isolation"] != "없음" else "gray")]))
    st.html(T.table(["시각", "누가", "무엇을", "내용"], [[r["time"], r["who"], ("" if r["mine"] else "[앞 환자] ") + r["what"], r["detail"]]
                                                for r in t["rows"]]))


REASONS = {"decline": ["원무가 직접 처리", "병동과 통화로 해결", "환자 사정으로 보류", "지금 병상 유지(격리 조치 가능)", "기타"],
           "undo": ["다른 병상이 더 적합", "환자 요청", "병동 사정", "기타"],
           "object": ["간호 인력 부족", "격리 준비 불가", "다른 환자 우선", "기타"]}


@st.dialog("사유를 남겨 주세요")
def reason_dialog(sv, kind: str, target: str, label: str, actor: str = "bed_manager") -> None:
    st.write(label)
    r = st.selectbox("사유", REASONS[kind])
    memo = st.text_input("메모(선택)")
    text = r + (f" · {memo}" if memo else "")
    if st.button("확인", type="primary", use_container_width=True):
        if kind == "decline":
            sv.gw.decline_action(target, actor, text, sv.now,
                                 approver_id=next((u for u, r in sv.gw.staff.items() if r == actor), None))
        else:
            sv.gw.ward_objection(target, ("원무 되돌림: " if kind == "undo" else "병동 이의: ") + text, sv.now)
        st.rerun()


# ---------------- 역할 화면 ----------------

_PRIMARY = {"used": False}


def _btn_type() -> str:
    """화면마다 주 버튼(파랑)은 맨 위 할 일 하나에만 둔다 (UX 검토: 주 버튼 남발)."""
    if _PRIMARY["used"]:
        return "secondary"
    _PRIMARY["used"] = True
    return "primary"


@st.dialog("입원 안내 연락 기록")
def contact_dialog(sv, c: dict) -> None:
    from datetime import datetime as _dt
    from turnaround.events import EventType
    st.write(f"{c['who']} · {c['bed_ko']}")
    st.caption(f"추천 안내 시각 {c['suggest_at']:%H:%M}: 입원 예정과 연락 1시간 뒤 중 늦은 쪽, 병상이 아직 비지 않았으면 "
               "입원 수속 창 시작보다 이르지 않게 (설계 가정)")
    t = st.time_input("환자에게 실제로 안내한 수속 시각", value=c["suggest_at"].time(), step=900)
    if st.button("연락함으로 기록", type="primary", use_container_width=True):
        promised = _dt.combine(sv.now.date(), t)
        _event(sv.store, c["case"], "patient_contacted", EventType.PATIENT_CONTACTED, sv.now, bed_id=c["bed"],
               by="admission_office", promised_at=promised.isoformat())
        st.rerun()


def show_admission(d: dict, sv=None, beds=None, key: str = "") -> None:
    _PRIMARY["used"] = False
    overdue = sum(a["overdue"] for a in d["approvals"]) + sum(t["overdue"] for t in d["tasks"])
    st.html(T.kpis([("승인 대기", len(d["approvals"]), "에이전트 제안 중 사람 승인이 필요한 것", "accent"),
                    ("마감 지남", overdue, "지금 먼저 처리", "warn" if overdue else ""),
                    ("환자 연락", len(d["contacts"]) + sum(t["raw_kind"] in CONTACT_KINDS for t in d["tasks"]),
                     "첫 안내 + 재연락", ""),
                    ("오늘 에이전트 확정", len(d["notices"]), "통보받고 시한 안에 되돌릴 수 있음", "")]))
    todo = len(d["approvals"]) + len(d["contacts"]) + len(d["tasks"])
    st.html(T.section("지금 할 일", todo, "마감 지난 일 → 마감 이른 일 순서"))
    if not todo:
        st.html(T.empty("지금 처리할 일이 없습니다. 평범한 배정은 에이전트가 확정하고 아래에 알립니다."))
    d = {**d, "approvals": [x for x in d["approvals"] if keep(x)], "contacts": [x for x in d["contacts"] if keep(x)],
         "tasks": [x for x in d["tasks"] if keep(x)], "notices": [x for x in d["notices"] if keep(x)]}
    for a in d.get("waiting_ipc") or []:
        st.html(T.card(f"{a['who']} · 감염관리실 결정 대기", f"{T.e(a['situation'])} 병상 결정은 감염관리실이 승인합니다. 결정되면 환자 재연락 작업이 생깁니다.",
                       [T.chip("격리", "red"), T.chip("원무는 대기", "gray")], [T.chip(f"마감 {a['deadline']}", "amber")]))
    for a in d["approvals"]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        chips = [T.chip(a["trigger_ko"], "violet"), T.chip("승인 필요", "blue")] + ([T.chip("마감 지남", "red")] if a["overdue"] else [])
        facts = [T.chip(f, "gray") for f in a["facts"]] + [T.chip(f"마감 {a['deadline']}", "red" if a["overdue"] else "amber")]
        rec = (f"<br><span class='wl-note'>지금 기준 추천 병상: {T.e(' · '.join(a['recommend']))} (승인하면 확정 직전에 다시 확인)</span>"
               if a.get("recommend") else "")
        title = a["action_ko"]
        if a.get("recommend") and a["recommend"][0].startswith("배정 기준에 맞는 빈 병상 없음"):
            title = "병상 다시 찾기 승인 (지금은 맞는 병상 없음 · 못 찾으면 담당 검토로)"
        c1.html(T.card(f"{a['who']} · {title}", f"{T.e(a['situation'])}<br><b>{T.e(a['effect'])}</b>{rec}", chips, facts,
                       "urgent" if a["overdue"] else "todo"))
        if sv is not None:
            if c2.button("승인", key=f"ap-{key}-{a['exception_id']}", type=_btn_type(), use_container_width=True):
                dec = sv.gw.approve_action(a["exception_id"], "bed_manager", sv.now)
                st.toast("게이트웨이가 다시 확인한 뒤 " + ("실행했습니다" if dec.status == "executed" else "거부했습니다"))
                st.rerun()
            if c2.button("거절", key=f"dc-{key}-{a['exception_id']}", use_container_width=True):
                reason_dialog(sv, "decline", a["exception_id"], f"{a['who']}: '{a['action_ko']}' 제안을 거절합니다. 실행하지 않고 원무가 직접 처리합니다.")
            if c2.button("경과", key=f"tl-a-{key}-{a['exception_id']}", use_container_width=True):
                patient_dialog(sv, a["case"])
    for c in d["contacts"]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        c1.html(T.card(f"{c['who']} · 입원 안내 연락", f"{T.e(c['bed_ko'])} 확정 {T.e(c['committed'])} · 수속 시각과 병동을 안내하세요",
                       [T.chip("환자 연락", "blue")], [T.chip(f"추천 안내 시각 {c['suggest_at']:%H:%M}", "blue")], kind="todo"))
        if sv is not None:
            from turnaround.events import EventType
            if c2.button("연락함", key=f"ct-{key}-{c['case']}", type=_btn_type(), use_container_width=True):
                contact_dialog(sv, c)
            if c2.button("경과", key=f"tl-c-{key}-{c['case']}", use_container_width=True):
                patient_dialog(sv, c["case"])
    fresh = [t for t in d["tasks"] if not t["stale"]]
    stale = [t for t in d["tasks"] if t["stale"]]
    if stale:
        esc = sorted({t["escalated_to"] for t in stale if t["escalated_to"]})
        st.html(T.card(f"이전 근무에서 넘어온 일 {len(stale)}건", "마감이 지나 " + (", ".join(esc) + "에게 올라갔습니다. " if esc else "")
                       + "지금 근무자가 이어받아 처리하거나 책임자와 정리하세요. 목록은 아래 펼침에 있습니다.",
                       [T.chip("인계", "violet"), T.chip("마감 지남", "red")], kind="urgent"))
    for t in fresh[:5]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        extra = ([T.chip(f"올라감: {t['escalated_to']}", "violet")] if t["escalated_to"] else []) + \
                ([T.chip(f"인계: {t['handed_to']}", "gray")] if t["handed_to"] else [])
        c1.html(T.card(f"{t['who']} · {t['kind']}", T.e(t.get("why") or ""), [T.chip("작업", "gray")]
                       + ([T.chip("마감 지남", "red")] if t["overdue"] else []),
                       [T.chip(f"마감 {t['due']}", "red" if t["overdue"] else "amber")] + extra,
                       "urgent" if t["overdue"] else "todo"))
        if sv is not None:
            if c2.button("처리 완료", key=f"td-{key}-{t['task_id']}", type=_btn_type(), use_container_width=True):
                _task_done(sv, t, "admission_office")
                st.rerun()
            if c2.button("경과", key=f"tl-t-{key}-{t['task_id']}", use_container_width=True):
                patient_dialog(sv, t["case"])
    rest = fresh[5:]
    if rest:
        with st.expander(f"오늘 작업 {len(rest)}건 더 보기"):
            st.html(T.table(["환자", "작업", "사유", "마감"], [[t["who"], t["kind"], t.get("why") or "", t["due"]] for t in rest]))
    if stale:
        with st.expander(f"이전 근무에서 넘어온 일 {len(stale)}건 목록"):
            st.html(T.table(["환자", "작업", "사유", "마감", "올라간 곳"],
                            [[t["who"], t["kind"], t.get("why") or "", t["due"], t["escalated_to"] or "-"] for t in stale]))
    st.html(T.section("에이전트가 확정하고 알린 배정", len(d["notices"]), "확정 직전 두 번 다시 확인함 · 시한 안에 되돌릴 수 있음"))
    if not d["notices"]:
        st.html(T.empty("오늘 에이전트가 확정한 배정이 아직 없습니다."))
    for n in d["notices"]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        facts = [T.chip(f"확정 {n['time']}", "gray"), T.chip(f"차순위 {n['runner_up'] or '없음'}", "gray"),
                 T.chip(f"되돌리기 {n['undo_until'] or '-'}까지" if n["undo_open"] else "되돌리기 시한 지남", "amber" if n["undo_open"] else "gray")]
        if n["waived"]:
            facts.append(T.chip("생략한 검토: " + ", ".join(n["waived"]), "violet"))
        c1.html(T.card(f"{n['who']} → {n['bed_ko']}", "", [T.chip("에이전트 확정", "green" if n["active"] else "gray")]
                       + ([] if n["active"] else [T.chip("이미 바뀜", "gray")]), facts))
        if sv is not None and n["active"] and n["proposal_id"] and n["undo_open"] and \
                c2.button("되돌리기", key=f"undo-{key}-{n['case']}", use_container_width=True):
            reason_dialog(sv, "undo", n["proposal_id"], f"{n['who']} → {n['bed_ko']} 확정을 되돌립니다. 연락 전이면 다시 찾고, 연락 뒤면 담당 검토와 재연락으로 갑니다.")
        if sv is not None and c2.button("경과", key=f"tl-n-{key}-{n['case']}", use_container_width=True):
            patient_dialog(sv, n["case"])
    if d["blocked"]:
        st.html(T.section("오늘 막힌 판단", len(d["blocked"]), "에이전트가 골랐지만 안전 규칙이 막고 사람에게 올린 것"))
        st.html(T.table(["시각", "환자", "에이전트가 고른 행동", "막은 이유"], [[b["time"], b["case"], b["action"], b["reason"]]
                                                                for b in d["blocked"]]))
    if beds is not None:
        st.html(T.section("병상 현황판", len(beds), "색과 글자로 상태를 함께 표시"))
        st.html(T.bed_board(beds))
    if d["reviews"]:
        with st.expander(f"담당 검토 {len(d['reviews'])}건"):
            st.html(T.table(["입원", "병상", "검토 사유", "마감"],
                            [[r["case"], views.bed_ko(r["bed"]), ", ".join(views.REVIEW_KO.get(x, x) for x in r["reasons"]), r["due"]]
                             for r in d["reviews"]]))


def show_ward(d: dict, sv=None, beds=None) -> None:
    _PRIMARY["used"] = False
    S = views.WARD_STEPS
    d = {**d, "flow": [x for x in d["flow"] if keep(x)], "todo": [x for x in d["todo"] if keep(x)]}
    cols = {k: [] for k in S}
    for f in d["flow"]:
        hold = T.chip("호출 보류: " + f["hold"], "red") if f["hold"] else ""
        bs = T.chip("병상 " + f["bed_state"], "gray") if f.get("bed_state") else ""
        cols[f["step"]].append(T.mini(f"{f['who']}" + (f" · {f['reg']}" if f.get("reg") else ""),
                                      f"{f['bed_ko']} · 통보 {f['notified']} · " + " · ".join(f.get("prep") or []),
                                      f'<div style="margin-top:6px">{bs} {hold}</div>' if (hold or bs) else ""))
    st.html(T.kpis([(S[0], len(cols[S[0]]), "통보를 받고 아직 준비 전", "accent"), (S[1], len(cols[S[1]]), "병상·수속이 갖춰지면 호출", ""),
                    (S[2], len(cols[S[2]]), "원무가 환자를 병동으로 보냄", ""), (S[3], len(cols[S[3]]), "오늘", "")]))
    st.html(T.section("지금 할 일", len(d["todo"]), "'준비 완료'를 누르면 병상·수속이 갖춰질 때 원무가 환자를 보냅니다"))
    if not d["todo"]:
        st.html(T.empty("준비할 입원이 없습니다."))
    for f in d["todo"]:
        c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="center")
        chips = [T.chip(f["by"], "green")] + ([T.chip("호출 보류: " + f["hold"], "red")] if f["hold"] else [])
        facts = [T.chip(x, "red" if x.startswith("격리") else "gray") for x in f.get("prep") or []] + \
                ([T.chip("병상 " + f["bed_state"], "gray")] if f.get("bed_state") else [])
        c1.html(T.card(f"{f['who']}" + (f" · 등록 {f['reg']}" if f.get("reg") else "") + f" → {f['bed_ko']}",
                       f"통보 {T.e(f['notified'])} · {T.e(f['step'])}", chips, facts,
                       kind="urgent" if f["hold"] else "todo"))
        if sv is not None and f["step"] == S[0]:
            from turnaround.events import EventType
            if c2.button("준비 완료", key=f"prep-{f['case']}", type=_btn_type(), use_container_width=True):
                _event(sv.store, f["case"], "ward_prep_done", EventType.WARD_PREP_DONE, sv.now, bed_id=f["bed"])
                st.rerun()
            if f["proposal_id"] and c3.button("이의", key=f"obj-{f['case']}", use_container_width=True):
                reason_dialog(sv, "object", f["proposal_id"], f"{f['who']} → {f['bed_ko']} 배정에 이의를 냅니다. 연락 전이면 다른 병상을 다시 찾습니다.")
    st.html(T.section("입실 흐름", len(d["flow"]), " → ".join(S)))
    st.html(T.kanban(list(cols.items())))
    if beds is not None:
        st.html(T.section("병상 현황판", len(beds)))
        st.html(T.bed_board(beds))


def show_ipc(d: dict, sv=None) -> None:
    _PRIMARY["used"] = False
    todo = [a for a in d["alerts"] if not a["checked"]]
    waiting = [t for t in d["terminal"] if t["status"] != "종말소독 완료"]
    st.html(T.kpis([("결정·확인할 일", len(todo) + len(d.get("approvals") or []), "병상 결정 승인 + 늦은 격리 통지", "warn" if todo else ""),
                    ("오늘 격리 오더", len(d["isolation_orders"]), "", ""),
                    ("빈 음압실", d["np_free"], "지금 바로 쓸 수 있는", "accent"),
                    ("종말소독 대기", len(waiting), "격리 퇴실 병상", "")]))
    st.html(T.footnote(f"지금 이 통지의 담당: {d.get('holder', '감염관리실')} (근무 시간 밖에는 감염내과 당직이 대행, 설계 가정)"))
    aps = d.get("approvals") or []
    st.html(T.section("지금 할 일", len(todo) + len(aps), "격리 병실 결정은 감염관리실이 승인합니다. '확인, 조치 시작'은 이 사안을 맡았다는 기록(누가·언제)을 남깁니다"))
    if not todo and not aps:
        st.html(T.empty("확인할 통지가 없습니다."))
    for a in aps:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        rec = (f"<br><span class='wl-note'>지금 기준 옮길 병상: {T.e(' · '.join(a['recommend']))}</span>" if a.get("recommend") else "")
        none = bool(a.get("recommend")) and a["recommend"][0].startswith("배정 기준에 맞는 빈 병상 없음")
        title = "병상 결정: 지금 맞는 병상 없음 · 다시 찾을지, 지금 병상에서 격리 조치할지" if none else f"병상 결정: {a['action_ko']}"
        c1.html(T.card(f"{a['who']} · {title}", f"{T.e(a['situation'])}<br><b>{T.e(a['effect'])}</b>{rec}",
                       [T.chip("격리", "red"), T.chip("감염관리실 승인", "blue")],
                       [T.chip(f, "gray") for f in a["facts"]] + [T.chip(f"마감 {a['deadline']}", "red" if a["overdue"] else "amber")],
                       "urgent"))
        if sv is not None:
            role = d.get("holder_role") or "ipc"
            if c2.button("다시 찾기" if none else "승인", key=f"ipc-ap-{a['exception_id']}", type=_btn_type(), use_container_width=True,
                         help=f"{a['who']} 병상 결정 승인"):
                # 데모: 그 시각 담당 역할의 등록된 사람(합성 ID)으로 승인. 실제 병원은 로그인한 사람 (설계 가정)
                who_id = next((u for u, r in sv.gw.staff.items() if r == role), None)
                dec = sv.gw.approve_action(a["exception_id"], role, sv.now, approver_id=who_id)
                st.toast("다시 확인한 뒤 " + ("실행했습니다" if dec.status == "executed" else "거부했습니다"))
                st.rerun()
            if c2.button("지금 병상 유지" if none else "거절", key=f"ipc-dc-{a['exception_id']}", use_container_width=True,
                         help=f"{a['who']} 병상 결정 거절"):
                reason_dialog(sv, "decline", a["exception_id"], f"{a['who']}: '{a['action_ko']}'을 거절합니다.", actor=role)
    for a in todo:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        facts = [T.chip(f"지금 병상 {a['bed_ko']} ({a['room_type']})", "gray"),
                 T.chip(f"같은 병실 환자 {a['roommates']}명", "amber" if a["roommates"] else "gray"),
                 T.chip(f"빈 음압실 {d['np_free']}개", "green" if d["np_free"] else "red"),
                 T.chip("이미 입실" if a["arrived"] else "아직 입실 전", "red" if a["arrived"] else "gray"),
                 T.chip(f"받은 시각 {a['time']}", "gray")]
        alt = (f"<br><span class='wl-note'>옮길 수 있는 병상: {T.e(' · '.join(a['alternatives']))}</span>"
               if a.get("alternatives") else "")
        c1.html(T.card(f"{a['who']} · {a['kind']}", T.e(a["card"]) + alt, [T.chip("격리", "red")], facts, "urgent"))
        if sv is not None and c2.button("확인, 조치 시작", key=f"ipc-{a['task_id']}", type=_btn_type(), use_container_width=True):
            from turnaround.events import EventType
            _event(sv.store, a["case"], f"ipc_ack:{a['task_id']}", EventType.HUMAN_TASK_DONE, sv.now,
                   task_id=a["task_id"], by="ipc")
            st.rerun()
    st.html(T.section("오늘 격리 오더", len(d["isolation_orders"])))
    st.html(T.table(["환자", "시각", "격리 주의", "의심 병원체"], [[o["case"], o["time"], o["precautions"], o["organism"]]
                                                for o in d["isolation_orders"]]) if d["isolation_orders"]
            else T.empty("오늘 격리 오더 없음"))
    st.html(T.section("격리 퇴실 병상 종말소독", len(d["terminal"])))
    st.html(T.checks([(t["status"] == "종말소독 완료", f"{t['bed']} · {t['status']}", f"퇴실 {t['departed']}")
                      for t in d["terminal"]]) if d["terminal"] else T.empty("오늘 격리 퇴실 병상 없음"))


def show_manager(d: dict) -> None:
    hl = d["human_load"]
    ratio = f"{hl['no_touch_committed'] / hl['committed']:.0%}" if hl["committed"] else "-"
    bad = sum(r["위반"] for r in d["safety"])
    st.html(T.kpis([("오늘 입원", f"{hl['admission_cases']}명", "", ""), ("병상 확정", f"{hl['committed']}명", "", ""),
                    ("사람 손 없이 확정", ratio, f"{hl['no_touch_committed']}명, 에이전트 확정 후 통보", "accent"),
                    ("안전 위반", f"{bad}건", "실행된 기록으로 검사", "warn" if bad else "")]))
    st.html(T.section("퇴원부터 입실까지"))
    st.html(T.kpis([(k, v, "", "") for k, v in d["flow"].items()]))
    if d["unassigned"]:
        st.html(T.card("아직 병상이 확정되지 않은 입원", " · ".join(f"{T.e(k)} {v}명" for k, v in d["unassigned"].items()),
                       [T.chip("확인", "amber")]))
    st.html(T.section("안전 검사", 4, "실행된 기록으로 검사, 위반이 0이어야 함"))
    st.html(T.checks([(None if r["검사 수"] == 0 else r["위반"] == 0,
                       f"{r['지표']} · " + ("해당 없음(오늘 검사 대상 없음)" if r["검사 수"] == 0 else f"위반 {r['위반']}건 / 검사 {r['검사 수']}건"),
                       r["뜻"]) for r in d["safety"]]))
    st.html(T.section("자동 처리 범위 제안", sum(c["recommendation"] != "유지" for c in d["cards"]), "근거가 쌓이면 에이전트가 맡을 범위를 넓히자고 제안하고, 적용은 사람이 승인"))
    for c in d["cards"]:
        tone = {"승격 제안": "blue", "강등 제안": "red", "유지": "gray"}[c["recommendation"]]
        rec = {"승격 제안": "에이전트에 맡기자고 제안", "강등 제안": "사람 확인으로 되돌리자고 제안", "유지": "지금대로 유지"}[c["recommendation"]]
        st.html(T.card(c["unit"], T.e(c["evidence"]), [T.chip(rec, tone), T.chip(f"지금: {c['mode']}", "gray")]
                       + ([T.chip("항상 사람 확인", "red")] if c["locked"] else [])))
    st.html(T.footnote("모든 수치는 합성(가상) 병원 시험 운영 기준 추정입니다."))


ONBOARD_STEPS = ["표 구조", "값 형식", "열의 뜻", "코드 뜻", "시험 운영 검사", "승인하고 운영 시작"]


def show_onboarding(d: dict, progress: bool = True) -> None:
    if not d:
        return
    idx = {"structure": 0, "format": 1, "column": 2, "code": 3}.get(d.get("stage"), 4)
    if d.get("package") and d["package"]["status"] == "승인됨":
        idx = 5
    if progress:  # 시연 대본에서는 위의 단계 막대가 같은 진행을 보여 주므로 두 번 그리지 않는다
        st.html(T.section("설정 만들기 진행"))
        st.html(T.steps(ONBOARD_STEPS, idx))
    if d.get("stage") != "done":
        st.html(T.kpis([("이번 묶음 질문", len(d["questions"]), d.get("stage_ko", ""), "accent"),
                        ("제안 없는 질문", d.get("open", 0), "사람이 답을 찾아야 함", ""),
                        ("남은 질문", d["remaining"], "답에 따라 뒤 질문이 줄거나 새로 생김", ""), ("답한 질문", d["answered"], "", "")]))
        st.html(T.section(f"지금 묻는 것: {d['stage_ko']}", len(d["questions"]), "표 구조 → 값 형식 → 열의 뜻 → 코드 뜻 순서로 묻습니다"))
        for q in d["questions"][:8]:
            chips = [T.chip("제안 " + str(q["proposal"]), "blue")] if q["proposal"] is not None else [T.chip("제안 없음", "gray")]
            st.html(T.card(q["item"], T.e(q["why"]), chips, [T.chip(str(o), "gray") for o in q["options"]]))
        if len(d["questions"]) > 8:
            st.html(T.empty(f"외 {len(d['questions']) - 8}개 질문"))
    else:
        st.html(T.kpis([("답한 질문", d["answered"], "모든 묶음 완료", "accent"), ("읽어 낸 열·사건 코드", d.get("mapped", 0), "", ""),
                        ("표", d.get("tables", 0), "", "")]))
        if d.get("by_stage"):
            st.html(T.section("묶음별로 답한 질문"))
            st.html(T.table(["묶음", "답한 질문"], [[k, v] for k, v in d["by_stage"].items()]))
    if d.get("package"):
        p = d["package"]
        st.html(T.section(f"병원 설정판 · {p['status']}", desc="이 병원 데이터로 하루를 시험 운영해 검사합니다(정답 없이 쓸 수 있는 검사)"))
        st.html(T.checks([(bool(r["결과"]), r["검사"], f"{r['결과']}건") if r["검사"] == "시험 운영 배정"
                          else (str(r["결과"]) in ("통과", "0"), r["검사"], f"결과: {r['결과']}") for r in p["proof"]]))


def render_role(role: str, sv=None, data=None, key: str = "") -> None:
    if role == "onboarding":
        show_onboarding(data or {}, progress=key != "scene")
        return
    if role == "vendor":
        from turnaround.ui.demo import vendor_view
        rows = vendor_view(sv)
        cols = {"bed_id": "병상", "eta": "시작 가능", "cleaning_type": "청소 유형"}
        st.html(T.table(list(cols.values()), [[r[k] for k in cols] for r in rows]) if rows
                else T.empty("대기 중인 청소 요청이 없습니다"))
        return
    beds = views.bed_states(sv) if sv is not None else None
    if beds is not None and FILTER["ward"] != "전체":
        beds = [b for b in beds if b["ward"] == "W" + FILTER["ward"].replace("병동", "")]
    if role == "audit":
        rows = views.audit_log(sv, 80)
        st.html(T.section("오늘의 결정 기록", len(rows), "에이전트·게이트웨이·사람이 한 결정을 최신순으로"))
        st.html(T.table(["시각", "누가", "환자", "무엇을", "내용"], [[r["time"], r["actor"], r["patient"], r["what"], r["detail"]]
                                                          for r in rows]) if rows else T.empty("오늘 기록이 없습니다"))
        return
    if role == "admission":
        show_admission(views.admission_view(sv), sv, beds, key)
    elif role == "ward":
        show_ward(views.ward_view(sv), sv, beds)
    elif role == "ipc":
        show_ipc(views.ipc_view(sv), sv)
    else:
        show_manager(views.manager_view(sv))


# ---------------- 화면 ----------------

with st.sidebar:
    mode = st.radio("보기", ["시연 대본", "역할 화면"], horizontal=True)

if mode == "시연 대본":
    sid = st.sidebar.selectbox("장면", list(SCENES), format_func=lambda k: SCENES[k].title)
    if st.session_state.get("scene_id") != sid or "run" not in st.session_state:
        st.session_state.scene_id, st.session_state.run = sid, SceneRun(SCENES[sid])
    run: SceneRun = st.session_state.run

    def _reset():
        st.session_state.run = SceneRun(SCENES[sid])

    st.sidebar.caption(run.scene.summary)
    c1, c2 = st.sidebar.columns(2)
    c1.button("다음 ▶", disabled=run.done, key="next", on_click=run.next, type="primary", use_container_width=True)
    c2.button("처음부터", key="reset", on_click=_reset, use_container_width=True)
    run = st.session_state.run
    v = run.view()
    st.html(T.topbar(run.scene.title, v.get("now"), f"시연 {run.index + 1}/{len(run.scene.steps)} 단계"))
    st.html(T.steps([s.title for s in run.scene.steps], run.index))
    if v["role"] is None:
        st.html(T.guide("시연 대본", run.scene.summary, ["왼쪽 '다음 ▶'을 누르면 첫 단계로 갑니다"]))
    else:
        step = run.scene.steps[run.index]
        st.html(T.page_header(ROLE_KO[v["role"]], ROLE_DESC[v["role"]]))
        st.html(T.guide("이 화면에서 볼 것", v["caption"].split(": ", 1)[-1], step.look))
        nxt = run.scene.steps[run.index + 1] if not run.done else None
        if nxt is not None and nxt.act:
            st.button(nxt.act, key="act", on_click=run.next, type="primary", icon=":material/arrow_forward:")
        if v.get("changed"):
            with st.expander(f"이번 단계에서 바뀐 것 {len(v['changed'])}건 (결정 기록)", expanded=False):
                st.html(T.table(["시각", "누가", "환자", "무엇을", "내용"],
                                [[r["time"], r["actor"], r["patient"], r["what"], r["detail"]] for r in v["changed"]]))
        if v["role"] == "manager" and run.pkg is not None and run.pkg.status == "approved":
            st.html(T.card("새 병원 설정판이 승인되어 운영을 시작했습니다", "아래는 그 병원 데이터로 돌린 하루입니다",
                           [T.chip("승인: 의료정보팀 · 감염관리 책임자", "green"), T.chip(f"설정판 {run.pkg.version[:6]}", "gray")]))
        render_role(v["role"], run.st, v["data"] if v["role"] == "onboarding" else None, key="scene")
else:
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo

    seed = st.sidebar.number_input("합성 병원 번호", 0, 999, 0)
    t = st.sidebar.select_slider("시각", options=[time(h, m) for h in range(8, 20) for m in (0, 30)],
                                 value=time(13, 30), format_func=lambda x: x.strftime("%H:%M"))

    @st.cache_resource(show_spinner="합성 병원 하루를 재생하는 중")
    def _state(seed: int, hh: int, mm: int):
        return build_demo(seed=seed, day=3, until=time(hh, mm), via_fhir=False, policy=load_policy(),
                          config=GeneratorConfig.realistic(), occupancy="full")

    s = _state(int(seed), t.hour, t.minute)
    counts = views.role_counts(s)
    role = st.sidebar.radio("역할", ["admission", "ward", "ipc", "manager", "audit", "vendor"],
                            format_func=lambda r: ROLE_KO[r] + (f"  ·  할 일 {counts[r]}" if counts.get(r) else ""))
    FILTER["ward"] = st.sidebar.selectbox("병동", ["전체", "71병동", "72병동"])
    FILTER["q"] = st.sidebar.text_input("환자·병상 검색", placeholder="예: 김○, 04호")
    st.html(T.topbar(f"합성 병원 {int(seed)} · 71·72병동 24병상", s.now.strftime("%m월 %d일 %H:%M"), "역할 화면"))
    st.html(T.page_header(ROLE_KO[role], ROLE_DESC[role]))
    render_role(role, s, key="free")
