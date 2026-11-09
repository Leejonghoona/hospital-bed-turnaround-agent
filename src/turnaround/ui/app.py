"""병실관리AI 화면 (08 계획 4단계, Streamlit). 합성 데이터 전용.

실행: streamlit run src/turnaround/ui/app.py
- 시연 대본: 3장면을 "다음" 버튼으로 한 단계씩 재현한다.
- 역할 화면: 시드와 시각을 골라 원무·병동·감염관리실·관리자·청소 협력업체 화면을 본다.
화면 데이터는 `ui/views.py`, 대본은 `ui/scenes.py`가 만든다(시험과 같은 함수).
"""

from __future__ import annotations

from datetime import time

import streamlit as st

from turnaround.ui import views
from turnaround.ui.scenes import SCENES, SceneRun

st.set_page_config(page_title="병실관리AI", layout="wide")
ROLE_KO = {"admission": "원무(병상 배정)", "ward": "병동(책임간호사)", "ipc": "감염관리실", "manager": "관리자",
           "onboarding": "온보딩(의료정보팀)", "vendor": "청소 협력업체"}


COL_KO = {"case": "사례", "bed": "병상", "kind": "종류", "due": "마감", "time": "시각", "action": "행동", "reason": "이유", "why": "사유",
          "committed": "확정 시각", "precautions": "격리 주의", "organism": "균", "departed": "퇴실", "status": "상태",
          "bed_id": "병상", "eta": "시작 가능", "cleaning_type": "청소 유형"}


def ko(rows: list[dict]) -> list[dict]:
    """화면 표의 열 이름을 한국어로."""
    return [{COL_KO.get(k, k): v for k, v in r.items()} for r in rows]


# ---------------- 역할별 그리기 ----------------

def _now_event(store, case, key, etype, now, **payload):
    from turnaround.events import Event
    store.append(Event(event_id=f"{case}:ui:{key}", case_id=case, type=etype, occurred_at=now, observed_at=now,
                       source="human", payload=payload))


def show_admission(d: dict, gw=None, now=None, key: str = "") -> None:
    st.markdown("### 지금 할 일")
    c = st.columns(4)
    c[0].metric("승인 대기", len(d["approvals"]), help="에이전트가 제안했고 사람 승인이 필요한 행동")
    c[1].metric("환자 연락", len(d["contacts"]), help="에이전트가 확정했고 원무가 환자에게 알릴 입원")
    c[2].metric("작업", len(d["tasks"]))
    c[3].metric("오늘 막힌 판단", len(d["blocked"]), help="게이트웨이가 금지 행동을 막고 사람에게 올린 수")
    for a in d["approvals"]:
        with st.container(border=True):
            late = " · :red[마감 지남, 지금 처리]" if a["overdue"] else ""
            st.markdown(f"**{a['trigger_ko']} · {a['case']}** → {a['action_ko']}  (요청 {a['since']}, 마감 {a['deadline']}){late}")
            st.write("에이전트 근거: " + (a["rationale"] or "-"))
            if a["facts"]:
                st.caption(" · ".join(a["facts"]))
            if gw is not None and st.button("승인", key=f"ap-{key}-{a['exception_id']}"):
                dec = gw.approve_action(a["exception_id"], "bed_manager", now)
                st.toast("게이트웨이가 다시 확인한 뒤 " + ("실행했습니다" if dec.status == "executed" else f"거부했습니다 {dec.reasons}"))
                st.rerun()
    if d["contacts"]:
        st.markdown("**환자 연락할 입원**")
        st.dataframe(ko(d["contacts"]), hide_index=True, width="stretch")
    if d["tasks"]:
        st.markdown("**작업**")
        st.dataframe(ko(d["tasks"]), hide_index=True, width="stretch")
    st.markdown("### 에이전트가 확정하고 알린 배정 (시한 안에 되돌릴 수 있음)")
    for n in d["notices"]:
        cols = st.columns([4, 1])
        waived = f" · 생략한 검토: {', '.join(n['waived'])}" if n["waived"] else ""
        cols[0].write(f"{n['time']} **{n['case']} → {n['bed']}** · 차순위 {n['runner_up'] or '없음'}{waived} · "
                      f"되돌리기 시한 {n['undo_until'] or '-'}" + ("" if n["active"] else " · (이미 바뀜)"))
        if gw is not None and n["active"] and n["proposal_id"] and cols[1].button("되돌리기", key=f"undo-{key}-{n['case']}"):
            dec = gw.ward_objection(n["proposal_id"], "원무 되돌림", now)
            st.toast("되돌렸습니다: 연락 전이면 다시 찾고, 연락 뒤면 담당 검토로 갑니다" if dec.status else "")
            st.rerun()
    with st.expander(f"담당 검토 {len(d['reviews'])}건"):
        st.dataframe([{"사례": r["case"], "병상": r["bed"] or "병상 없음", "사유": ", ".join(r["reasons"]), "마감": r["due"]}
                      for r in d["reviews"]], hide_index=True, width="stretch")
    with st.expander(f"오늘 막힌 판단 {len(d['blocked'])}건 (안전장치 기록)"):
        st.dataframe(ko(d["blocked"]), hide_index=True, width="stretch")


def show_ward(d: dict, st_=None) -> None:
    st.markdown("### 지금 할 일")
    if not d["todo"]:
        st.success("준비할 입원이 없습니다")
    for f in d["todo"]:
        cols = st.columns([4, 1, 1])
        hold = f" · :red[올려주세요 보류: {f['hold']}]" if f["hold"] else ""
        cols[0].write(f"**{f['case']} → {f['bed']}** ({f['by']}, 통보 {f['notified']}) · {f['step']}{hold}")
        if st_ is not None and f["step"] == "준비 필요":
            from turnaround.events import EventType
            if cols[1].button("준비 완료", key=f"prep-{f['case']}"):
                _now_event(st_.store, f["case"], "ward_prep_done", EventType.WARD_PREP_DONE, st_.now, bed_id=f["bed"])
                st.rerun()
            if f["proposal_id"] and cols[2].button("이의", key=f"obj-{f['case']}"):
                st_.gw.ward_objection(f["proposal_id"], "병동 이의", st_.now)
                st.rerun()
    st.markdown("### 입실 흐름 (통보 → 준비 → 올려주세요 → 입실)")
    st.dataframe([{"사례": f["case"], "병상": f["bed"], "통보": f["notified"], "단계": f["step"], "보류": f["hold"],
                   "확정": f["by"]} for f in d["flow"]], hide_index=True, width="stretch")
    if d["notes"]:
        st.markdown("**알림**")
        st.dataframe(ko(d["notes"]), hide_index=True, width="stretch")


def show_ipc(d: dict, st_=None) -> None:
    st.markdown("### 지금 할 일 (감염관리실이 받은 통지)")
    todo = [a for a in d["alerts"] if not a["checked"]]
    if not todo:
        st.success("확인할 통지가 없습니다")
    for a in todo:
        cols = st.columns([5, 1])
        cols[0].write(f"{a['time']} **{a['case']}** · {a['kind']} · {a['card']}")
        if st_ is not None and cols[1].button("확인", key=f"ipc-{a['task_id']}"):
            from turnaround.events import EventType
            _now_event(st_.store, a["case"], f"ipc_ack:{a['task_id']}", EventType.HUMAN_TASK_DONE, st_.now,
                       task_id=a["task_id"], by="ipc")
            st.rerun()
    st.markdown("### 격리 오더")
    st.dataframe(ko(d["isolation_orders"]), hide_index=True, width="stretch")
    st.markdown("### 격리 퇴실 병상 종말소독")
    if d["terminal"]:
        st.dataframe(ko(d["terminal"]), hide_index=True, width="stretch")
    else:
        st.caption("오늘 격리 퇴실 병상 없음")


def show_manager(d: dict) -> None:
    hl = d["human_load"]
    st.markdown("### 오늘 한눈에")
    c = st.columns(4)
    c[0].metric("오늘 입원", hl["admission_cases"])
    c[1].metric("확정", hl["committed"])
    c[2].metric("사람 손 없이 확정", hl["no_touch_committed"], help="에이전트가 확정하고 사람은 통보만 받은 배정")
    c[3].metric("입원당 사람 판단 횟수", hl["accountable_per_case"], help="환자 연락처럼 사람이 책임지는 동작 수 / 입원 수")
    st.dataframe([{"구간 지표": k, "값": v} for k, v in d["flow"].items()], hide_index=True)
    if d["unassigned"]:
        st.caption("아직 확정 안 된 입원: " + ", ".join(f"{k} {v}건" for k, v in d["unassigned"].items()))
    st.markdown("### 안전 (실행된 기록으로 검사, 위반 0이어야 함)")
    st.dataframe(d["safety"], hide_index=True, width="stretch")
    st.markdown("### 자율권 카드 (근거로 승격·강등 제안, 적용은 사람이 승인)")
    st.dataframe([{"판단 단위": c["unit"], "지금 누가": c["mode"], "근거": c["evidence"], "제안": c["recommendation"],
                   "잠금": "승격 금지" if c["locked"] else ""} for c in d["cards"]], hide_index=True, width="stretch")
    st.caption("근거의 '하한'은 사람 결정과 에이전트 판단이 일치한 비율의 95% 신뢰 하한입니다. 합성 데이터 기준 추정.")


def show_onboarding(d: dict) -> None:
    if not d:
        return
    if d.get("stage") == "done":
        st.success(f"질문 끝. 답 {d['answered']}개")
    else:
        st.markdown(f"### 지금 묶음: {d['stage_ko']}")
        st.caption(f"질문 {len(d['questions'])}개(제안 없는 질문 {d['open']}개) · 남은 질문 {d['remaining']}개 · 답 {d['answered']}개")
        st.dataframe([{"질문": q["item"], "이유": q["why"], "제안": q["proposal"] if q["proposal"] is not None else "-",
                       "보기": ", ".join(map(str, q["options"]))} for q in d["questions"]], hide_index=True, width="stretch")
    if d.get("package"):
        p = d["package"]
        st.markdown(f"### 매핑 판 · {p['status']}")
        st.dataframe(p["proof"], hide_index=True)
        st.caption(f"판 지문 {p['version']} (승인 뒤 내용이 바뀌면 지문 검사가 실패해 운영에 쓰지 않습니다)")


SHOW = {"admission": show_admission, "ward": show_ward, "ipc": show_ipc, "manager": show_manager,
        "onboarding": show_onboarding}


# ---------------- 화면 ----------------

with st.sidebar:
    st.title("병실관리AI")
    st.caption("합성(가상) 데이터. 실제 환자·병원 정보 없음. 수치는 합성 데이터 기준 추정.")
    mode = st.radio("보기", ["시연 대본", "역할 화면"])

if mode == "시연 대본":
    sid = st.sidebar.selectbox("장면", list(SCENES), format_func=lambda k: SCENES[k].title)
    if st.session_state.get("scene_id") != sid or "run" not in st.session_state:
        st.session_state.scene_id, st.session_state.run = sid, SceneRun(SCENES[sid])
    run: SceneRun = st.session_state.run
    st.header(run.scene.title)
    st.caption(run.scene.summary)
    def _reset():
        st.session_state.run = SceneRun(SCENES[sid])

    c1, c2 = st.columns([1, 5])
    c1.button("다음 ▶", disabled=run.done, key="next", on_click=run.next)  # 누르면 먼저 한 단계 진행하고 다시 그린다
    c2.button("처음부터", key="reset", on_click=_reset)
    run = st.session_state.run
    total = len(run.scene.steps)
    st.progress((run.index + 1) / total, text=f"{run.index + 1}/{total} 단계")
    for line in run.log:
        st.write(line)
    v = run.view()
    if v["role"]:
        st.subheader(f"{ROLE_KO[v['role']]} 화면" + (f" · {v['now']}" if v.get("now") else ""))
        SHOW[v["role"]](v["data"])
else:
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo, vendor_view

    seed = st.sidebar.number_input("합성 병원 시드", 0, 999, 0)
    t = st.sidebar.select_slider("시각 (4일차)", options=[time(h, m) for h in range(8, 20) for m in (0, 30)],
                                 value=time(13, 30), format_func=lambda x: x.strftime("%H:%M"))
    role = st.sidebar.radio("역할", ["admission", "ward", "ipc", "manager", "vendor"], format_func=ROLE_KO.get)

    @st.cache_resource(show_spinner="합성 병원 하루를 재생하는 중")
    def _state(seed: int, hh: int, mm: int):
        return build_demo(seed=seed, day=3, until=time(hh, mm), via_fhir=False,
                          policy=load_policy(profile="field_default"), config=GeneratorConfig.realistic(),
                          occupancy="full")

    s = _state(int(seed), t.hour, t.minute)
    st.header(f"{ROLE_KO[role]} 화면 · {s.now:%m-%d %H:%M}")
    if role == "admission":
        show_admission(views.admission_view(s), gw=s.gw, now=s.now, key="free")
    elif role == "ward":
        show_ward(views.ward_view(s), s)
    elif role == "ipc":
        show_ipc(views.ipc_view(s), s)
    elif role == "vendor":
        st.caption("보이는 정보: 병상 번호, 시작 가능 시각, 청소 유형 (환자 정보 없음)")
        st.dataframe(ko(vendor_view(s)), hide_index=True, width="stretch")
    else:
        show_manager(views.manager_view(s))
