"""병실관리AI 화면 목업 (9단계, Streamlit). 합성 데이터 전용.

실행: streamlit run src/turnaround/ui/app.py
역할마다 보는 화면이 다르다. 청소 협력업체 화면에는 병상 번호, 시작 가능 시각, 청소 유형만 보인다.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

import streamlit as st

from turnaround.evaluation.human_load import human_load
from turnaround.forecast import ForecastConfig, forecast_noon
from turnaround.ui.demo import bed_board, build_demo, review_queue, vendor_view, ward_requests

st.set_page_config(page_title="병실관리AI 목업", layout="wide")
REASON = {"isolation": "격리 오더", "preemptive_isolation": "선제 격리", "pediatric": "소아", "icu_stepdown": "중환자실 하향",
          "single_room_preference": "1인실 희망(차액 동의)", "same_name_same_ward": "같은 병동 동명이인",
          "no_bed": "조건 맞는 병상 없음", "close_scores": "1·2순위 점수 근접", "missing_input": "입력 누락",
          "integrated_care_consent_missing": "통합병동 동의 미확인", "isolation_discharge_bed": "격리 퇴실 병실"}


@st.cache_resource(show_spinner="합성 병원 하루를 재생하는 중 (FHIR 번들 → 어댑터 → 이벤트)")
def _state(seed: int, hh: int, mm: int):
    return build_demo(seed=seed, until=time(hh, mm))


with st.sidebar:
    st.title("병실관리AI")
    st.caption("합성(가상) 데이터 목업. 실제 환자·병원 정보 없음. 수치는 합성 데이터 기준 추정.")
    seed = st.number_input("합성 병원 시드", 0, 999, 42)
    t = st.select_slider("화면 시각 (2일차)", options=[time(h, m) for h in range(8, 18) for m in (0, 30)], value=time(9, 30),
                         format_func=lambda x: x.strftime("%H:%M"))
    role = st.radio("역할", ["병상관리 담당", "책임간호사", "청소 협력업체", "운영 현황"])

s = _state(int(seed), t.hour, t.minute)
st.caption(f"기준 시각 {s.now:%m-%d %H:%M} · FHIR 번들 항목 {s.fhir_entries}개를 들여와 이벤트 {s.store.last_seq()}건으로 재생")

if role == "병상관리 담당":
    st.header("담당 검토 큐")
    q = review_queue(s)
    if not q:
        st.success("검토할 사례가 없습니다.")
    for card in q:
        with st.container(border=True):
            c1, c2 = st.columns([3, 2])
            c1.subheader(f"{card['case']} → {card['bed'] or '병상 없음'}")
            c1.write("검토 사유: " + ", ".join(REASON.get(r, r) for r in card["reasons"]))
            c1.write("에이전트 설명: " + (card["rationale"] or "-"))
            c2.metric("마감", card["due"], help=f"{card['since']} 도착. 마감을 넘기면 원무 책임에게 올라갑니다")
            c2.write("대안: " + (", ".join(card["alternatives"]) or "없음"))
            if card["scores"]:
                c2.write({k: round(v, 1) for k, v in card["scores"].items()})
            st.button("승인", key=f"ok-{card['case']}", disabled=True, help="목업: 실제 실행은 게이트웨이를 거칩니다")
    st.header("병상 현황")
    st.dataframe(bed_board(s), use_container_width=True, hide_index=True)

elif role == "책임간호사":
    st.header("병동 수용 문의")
    rows = ward_requests(s)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.success("대기 중인 문의가 없습니다.")
    st.caption("수용 가능 회신 뒤 원무가 HIS에 확정합니다. 회신 철회는 사가가 병동에 통지합니다.")

elif role == "청소 협력업체":
    st.header("청소 요청")
    st.caption("보이는 정보: 병상 번호, 시작 가능 시각, 청소 유형 (환자 정보 없음)")
    rows = vendor_view(s)
    if rows:
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.success("대기 중인 요청이 없습니다.")

else:
    st.header("오늘 가용 병상 추정 (몬테카를로, 09:00 기준)")
    day = datetime.combine(s.now.date(), time())
    known = s.store.read(as_of=datetime.combine(day.date(), time(9)))
    cols = st.columns(2)
    for col, cutoff in zip(cols, (time(12), time(15))):
        fc = forecast_noon(known, s.h, day, ForecastConfig(noon=cutoff, samples=1000))
        col.subheader(f"{cutoff:%H:%M}까지 준비 완료")
        col.dataframe([{"병동": w, "P10": f["p10"], "P50": f["p50"], "P90": f["p90"], "퇴원 확정(약한 기준선)": f["weak_baseline"]}
                       for w, f in fc.items()], hide_index=True)
    st.header("사람 개입")
    hl = human_load(s.store.read())
    c = st.columns(4)
    c[0].metric("입원 사례", hl["admission_cases"])
    c[1].metric("사례당 판단 개입", hl["judgement_per_case"])
    c[2].metric("사례당 책임 개입", hl["accountable_per_case"])
    c[3].metric("완전 자동 비율", "-" if hl["fully_automatic_ratio"] is None else f"{hl['fully_automatic_ratio']:.0%}")
    st.caption("책임 개입(병동 회신, 공식 확정, 환자 연락)은 남기고 판단 개입을 줄이는 것이 목표입니다. 자율화 1단계는 모든 배정에 담당 확인이 있습니다.")
