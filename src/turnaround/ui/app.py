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

ROLE_KO = {"admission": "원무 · 병상 배정", "ward": "병동 · 책임간호사", "ipc": "감염관리실", "manager": "관리자",
           "onboarding": "새 병원 온보딩", "vendor": "청소 협력업체"}
ROLE_DESC = {"admission": "에이전트가 확정한 배정을 확인하고, 사람 승인이 필요한 일만 처리합니다",
             "ward": "배정 통보를 받고 병동 준비부터 입실까지의 흐름을 봅니다",
             "ipc": "격리 사안 통지를 확인하고 종말소독 진행을 봅니다",
             "manager": "오늘의 흐름, 안전 검사, 자율권 제안을 한눈에 봅니다",
             "onboarding": "새 병원의 내보내기 표를 질문 묶음으로 매핑하고, 재생 증명 뒤 승인합니다",
             "vendor": "청소 요청만 봅니다. 환자 정보는 보이지 않습니다"}


def _event(store, case, key, etype, now, **payload):
    from turnaround.events import Event
    store.append(Event(event_id=f"{case}:ui:{key}", case_id=case, type=etype, occurred_at=now, observed_at=now,
                       source="human", payload=payload))


# ---------------- 역할 화면 ----------------

def show_admission(d: dict, sv=None, beds=None, key: str = "") -> None:
    overdue = sum(a["overdue"] for a in d["approvals"])
    st.html(T.kpis([("승인 대기", len(d["approvals"]), f"마감 지남 {overdue}건" if overdue else "사람 승인이 필요한 행동",
                     "warn" if overdue else "accent"),
                    ("환자 연락", len(d["contacts"]), "확정됐고 아직 알리지 않은 입원", ""),
                    ("작업", len(d["tasks"]), "재연락·차액 동의 등", ""),
                    ("오늘 에이전트 확정", len(d["notices"]), "통보받고 시한 안에 되돌릴 수 있음", "")]))
    todo = len(d["approvals"]) + len(d["contacts"]) + len(d["tasks"])
    st.html(T.section("지금 할 일", todo, "마감 지난 일이 맨 위"))
    if not todo:
        st.html(T.empty("지금 처리할 일이 없습니다. 평범한 배정은 에이전트가 확정하고 아래에 알립니다."))
    for a in d["approvals"]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        facts = [T.chip(f, "gray") for f in a["facts"]] + [T.chip(f"마감 {a['deadline']}", "red" if a["overdue"] else "amber")]
        c1.html(T.card(f"{a['case']} · {a['action_ko']}", f"에이전트 근거: {T.e(a['rationale'] or '-')}",
                       [T.chip(a["trigger_ko"], "violet"), T.chip("승인 필요", "blue")]
                       + ([T.chip("마감 지남", "red")] if a["overdue"] else []), facts,
                       "urgent" if a["overdue"] else "todo"))
        if sv is not None and c2.button("승인", key=f"ap-{key}-{a['exception_id']}", type="primary",
                                        use_container_width=True):
            dec = sv.gw.approve_action(a["exception_id"], "bed_manager", sv.now)
            st.toast("게이트웨이가 다시 확인한 뒤 " + ("실행했습니다" if dec.status == "executed" else "거부했습니다"))
            st.rerun()
    for c in d["contacts"]:
        st.html(T.card(f"{c['case']} 환자에게 입원 안내", f"병상 {T.e(c['bed'])} · 확정 {T.e(c['committed'])}",
                       [T.chip("환자 연락", "blue")], kind="todo"))
    for t in d["tasks"][:5]:
        st.html(T.card(f"{t['case']} · {t['kind']}", T.e(t.get("why") or ""), [T.chip("작업", "gray")],
                       [T.chip(f"마감 {t['due']}", "amber")], "todo"))
    if len(d["tasks"]) > 5:
        with st.expander(f"작업 {len(d['tasks']) - 5}건 더 보기"):
            st.html(T.table(["사례", "작업", "사유", "마감"], [[t["case"], t["kind"], t.get("why") or "", t["due"]]
                                                       for t in d["tasks"][5:]]))
    st.html(T.section("에이전트가 확정하고 알린 배정", len(d["notices"]), "확정 직전 두 번 다시 확인했고, 시한 안에 되돌릴 수 있음"))
    if not d["notices"]:
        st.html(T.empty("오늘 에이전트가 확정한 배정이 아직 없습니다."))
    for n in d["notices"]:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        facts = [T.chip(f"확정 {n['time']}", "gray"), T.chip(f"차순위 {n['runner_up'] or '없음'}", "gray"),
                 T.chip(f"되돌리기 {n['undo_until'] or '-'}까지", "amber")]
        if n["waived"]:
            facts.append(T.chip("생략한 검토: " + ", ".join(n["waived"]), "violet"))
        c1.html(T.card(f"{n['case']} → {n['bed']}", "", [T.chip("에이전트 확정", "green" if n["active"] else "gray")]
                       + ([] if n["active"] else [T.chip("이미 바뀜", "gray")]), facts))
        if sv is not None and n["active"] and n["proposal_id"] and c2.button("되돌리기", key=f"undo-{key}-{n['case']}",
                                                                              use_container_width=True):
            sv.gw.ward_objection(n["proposal_id"], "원무 되돌림", sv.now)
            st.toast("되돌렸습니다. 연락 전이면 다시 찾고, 연락 뒤면 담당 검토와 재연락으로 갑니다")
            st.rerun()
    if beds is not None:
        st.html(T.section("병상 현황판", len(beds), "색과 글자로 상태를 함께 표시"))
        st.html(T.bed_board(beds))
    with st.expander(f"담당 검토 {len(d['reviews'])}건 · 오늘 막힌 판단 {len(d['blocked'])}건 (안전장치 기록)"):
        st.html(T.table(["사례", "병상", "검토 사유", "마감"],
                        [[r["case"], r["bed"] or "병상 없음", ", ".join(views.REVIEW_KO.get(x, x) for x in r["reasons"]), r["due"]]
                         for r in d["reviews"]]) if d["reviews"] else T.empty("담당 검토 없음"))
        st.html(T.table(["시각", "사례", "에이전트가 고른 행동", "막은 이유"], [[b["time"], b["case"], b["action"], b["reason"]]
                                                                for b in d["blocked"]]) if d["blocked"] else T.empty("오늘 막힌 판단 없음"))


def show_ward(d: dict, sv=None, beds=None) -> None:
    cols = {"준비 필요": [], "병동 준비 완료": [], "올려주세요 보냄": [], "입실": []}
    for f in d["flow"]:
        hold = T.chip("호출 보류: " + f["hold"], "red") if f["hold"] else ""
        cols.setdefault(f["step"], []).append(T.mini(f"{f['case']} → {f['bed']}", f"통보 {f['notified']} · {f['by']}",
                                                     f'<div style="margin-top:6px">{hold}</div>' if hold else ""))
    st.html(T.kpis([("준비할 입원", len(cols["준비 필요"]), "통보를 받고 아직 준비 전", "accent"),
                    ("호출 대기", len(cols["병동 준비 완료"]), "준비 끝, 병상·수속 대기", ""),
                    ("올려주세요 보냄", len(cols["올려주세요 보냄"]), "원무가 환자를 올리는 중", ""),
                    ("오늘 입실", len(cols["입실"]), "", "")]))
    st.html(T.section("지금 할 일", len(d["todo"]), "준비 완료를 누르면 병상·수속이 갖춰질 때 '올려주세요'가 나갑니다"))
    if not d["todo"]:
        st.html(T.empty("준비할 입원이 없습니다."))
    for f in d["todo"]:
        c1, c2, c3 = st.columns([5, 1, 1], vertical_alignment="center")
        chips = [T.chip(f["by"], "green")] + ([T.chip("호출 보류: " + f["hold"], "red")] if f["hold"] else [])
        c1.html(T.card(f"{f['case']} → {f['bed']}", f"통보 {T.e(f['notified'])} · {T.e(f['step'])}", chips,
                       kind="urgent" if f["hold"] else "todo"))
        if sv is not None and f["step"] == "준비 필요":
            from turnaround.events import EventType
            if c2.button("준비 완료", key=f"prep-{f['case']}", type="primary", use_container_width=True):
                _event(sv.store, f["case"], "ward_prep_done", EventType.WARD_PREP_DONE, sv.now, bed_id=f["bed"])
                st.rerun()
            if f["proposal_id"] and c3.button("이의", key=f"obj-{f['case']}", use_container_width=True):
                sv.gw.ward_objection(f["proposal_id"], "병동 이의", sv.now)
                st.rerun()
    st.html(T.section("입실 흐름", len(d["flow"])))
    st.html(T.kanban(list(cols.items())))
    if beds is not None:
        st.html(T.section("병상 현황판", len(beds)))
        st.html(T.bed_board(beds))


def show_ipc(d: dict, sv=None) -> None:
    todo = [a for a in d["alerts"] if not a["checked"]]
    waiting = [t for t in d["terminal"] if t["status"] != "종말소독 완료"]
    st.html(T.kpis([("확인할 통지", len(todo), "늦은 격리 등 감염관리실에 온 통지", "warn" if todo else ""),
                    ("오늘 격리 오더", len(d["isolation_orders"]), "", ""),
                    ("종말소독 대기", len(waiting), "격리 퇴실 병상", "accent" if waiting else "")]))
    st.html(T.section("지금 할 일", len(todo)))
    if not todo:
        st.html(T.empty("확인할 통지가 없습니다."))
    for a in todo:
        c1, c2 = st.columns([6, 1], vertical_alignment="center")
        c1.html(T.card(f"{a['case']} · {a['kind']}", T.e(a["card"]), [T.chip("격리", "red")],
                       [T.chip(f"받은 시각 {a['time']}", "gray")], "urgent"))
        if sv is not None and c2.button("확인", key=f"ipc-{a['task_id']}", type="primary", use_container_width=True):
            from turnaround.events import EventType
            _event(sv.store, a["case"], f"ipc_ack:{a['task_id']}", EventType.HUMAN_TASK_DONE, sv.now,
                   task_id=a["task_id"], by="ipc")
            st.rerun()
    st.html(T.section("격리 오더", len(d["isolation_orders"])))
    st.html(T.table(["사례", "시각", "격리 주의", "균"], [[o["case"], o["time"], o["precautions"], o["organism"]]
                                                for o in d["isolation_orders"]]) if d["isolation_orders"]
            else T.empty("오늘 격리 오더 없음"))
    st.html(T.section("격리 퇴실 병상 종말소독", len(d["terminal"])))
    st.html(T.checks([(t["status"] == "종말소독 완료", f"{t['bed']} · {t['status']}", f"퇴실 {t['departed']}")
                      for t in d["terminal"]]) if d["terminal"] else T.empty("오늘 격리 퇴실 병상 없음"))


def show_manager(d: dict) -> None:
    hl = d["human_load"]
    ratio = f"{hl['no_touch_committed'] / hl['committed']:.0%}" if hl["committed"] else "-"
    bad = sum(r["위반"] for r in d["safety"])
    st.html(T.kpis([("오늘 입원", hl["admission_cases"], "합성 병원 기준", ""), ("확정", hl["committed"], "병상이 정해진 입원", ""),
                    ("사람 손 없이 확정", ratio, f"{hl['no_touch_committed']}건, 에이전트 확정 후 통보", "accent"),
                    ("안전 위반", bad, "실행된 기록으로 검사, 0이어야 함", "warn" if bad else "")]))
    st.html(T.section("흐름 지표", desc="퇴원부터 입실까지 구간"))
    st.html(T.kpis([(k, v, "", "") for k, v in d["flow"].items()]))
    if d["unassigned"]:
        st.html(T.card("아직 확정 안 된 입원", " · ".join(f"{T.e(k)} {v}건" for k, v in d["unassigned"].items()),
                       [T.chip("확인", "amber")]))
    st.html(T.section("안전 검사", 4, "실행된 기록으로 검사"))
    st.html(T.checks([(r["위반"] == 0, f"{r['지표']} · 위반 {r['위반']}건 / 검사 {r['검사 수']}건", r["뜻"])
                      for r in d["safety"]]))
    st.html(T.section("자율권 제안", len(d["cards"]), "근거로 승격·강등을 제안하고, 적용은 사람이 승인"))
    for c in d["cards"]:
        tone = {"승격 제안": "blue", "강등 제안": "red", "유지": "gray"}[c["recommendation"]]
        st.html(T.card(c["unit"], T.e(c["evidence"]), [T.chip(c["recommendation"], tone), T.chip(f"지금: {c['mode']}", "gray")]
                       + ([T.chip("승격 금지", "red")] if c["locked"] else [])))


ONBOARD_STEPS = ["구조", "형식", "열", "코드표", "재생 증명", "승인·운영"]


def show_onboarding(d: dict) -> None:
    if not d:
        return
    idx = {"structure": 0, "format": 1, "column": 2, "code": 3}.get(d.get("stage"), 4)
    if d.get("package") and d["package"]["status"] == "승인됨":
        idx = 5
    st.html(T.steps(ONBOARD_STEPS, idx))
    if d.get("stage") != "done":
        st.html(T.kpis([("이번 묶음 질문", len(d["questions"]), d.get("stage_ko", ""), "accent"),
                        ("제안 없는 질문", d.get("open", 0), "사람이 답을 찾아야 함", ""),
                        ("남은 질문", d["remaining"], "", ""), ("답한 질문", d["answered"], "", "")]))
        st.html(T.section(f"지금 묶음: {d['stage_ko']}", len(d["questions"]), "구조 → 형식 → 열 → 코드표 순서로 묻습니다"))
        for q in d["questions"][:8]:
            chips = [T.chip("제안 " + str(q["proposal"]), "blue")] if q["proposal"] is not None else [T.chip("제안 없음", "gray")]
            st.html(T.card(q["item"], T.e(q["why"]), chips, [T.chip(str(o), "gray") for o in q["options"]]))
        if len(d["questions"]) > 8:
            st.html(T.empty(f"외 {len(d['questions']) - 8}개 질문"))
    else:
        st.html(T.card("질문 끝", f"답 {d['answered']}개", [T.chip("매핑 완성", "green")]))
    if d.get("package"):
        p = d["package"]
        st.html(T.section(f"매핑 판 · {p['status']}", desc="정답 없는 새 병원에서도 쓸 수 있는 검사로 증명"))
        st.html(T.checks([(bool(r["결과"]), r["검사"], f"{r['결과']}건") if r["검사"] == "하루 재생 배정"
                          else (str(r["결과"]) in ("통과", "0"), r["검사"], f"결과: {r['결과']}") for r in p["proof"]]))


def render_role(role: str, sv=None, data=None, key: str = "") -> None:
    if role == "onboarding":
        show_onboarding(data or {})
        return
    if role == "vendor":
        from turnaround.ui.demo import vendor_view
        rows = vendor_view(sv)
        cols = {"bed_id": "병상", "eta": "시작 가능", "cleaning_type": "청소 유형"}
        st.html(T.table(list(cols.values()), [[r[k] for k in cols] for r in rows]) if rows
                else T.empty("대기 중인 청소 요청이 없습니다"))
        return
    beds = views.bed_states(sv) if sv is not None else None
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
    st.html('<div class="wl-brand"><div class="wl-logo">W</div>병실관리AI</div>')
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
    st.html(T.topbar("시연 대본 · " + run.scene.title, v.get("now"), f"{run.index + 1}/{len(run.scene.steps)} 단계"))
    st.html(T.steps([s.title for s in run.scene.steps], run.index))
    if v["role"] is None:
        st.html(T.guide("시연 대본", run.scene.summary, ["왼쪽 '다음 ▶'을 누르면 첫 단계로 갑니다"]))
    else:
        step = run.scene.steps[run.index]
        st.html(T.guide(f"{run.index + 1}단계 · {ROLE_KO[v['role']]} 화면에서 볼 것", v["caption"].split(": ", 1)[-1], step.look))
        st.html(T.page_header(ROLE_KO[v["role"]], ROLE_DESC[v["role"]]))
        render_role(v["role"], run.st, v["data"] if v["role"] == "onboarding" else None, key="scene")
else:
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo

    role = st.sidebar.radio("역할", ["admission", "ward", "ipc", "manager", "vendor"], format_func=ROLE_KO.get)
    seed = st.sidebar.number_input("합성 병원 번호", 0, 999, 0)
    t = st.sidebar.select_slider("시각", options=[time(h, m) for h in range(8, 20) for m in (0, 30)],
                                 value=time(13, 30), format_func=lambda x: x.strftime("%H:%M"))

    @st.cache_resource(show_spinner="합성 병원 하루를 재생하는 중")
    def _state(seed: int, hh: int, mm: int):
        return build_demo(seed=seed, day=3, until=time(hh, mm), via_fhir=False, policy=load_policy(),
                          config=GeneratorConfig.realistic(), occupancy="full")

    s = _state(int(seed), t.hour, t.minute)
    st.html(T.topbar(f"합성 병원 {int(seed)} · 71·72병동 24병상", s.now.strftime("%m월 %d일 %H:%M"), "역할 화면"))
    st.html(T.page_header(ROLE_KO[role], ROLE_DESC[role]))
    render_role(role, s, key="free")
