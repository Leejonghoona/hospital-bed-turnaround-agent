"""의료정보팀 'AI 운영' 화면 데이터 (DECISIONS 2026-11-10).

에이전트라는 시스템이 제대로 도는지 본다: 성과, 막은 판단, 자율 범위, 모델과 평가, 새 병원 설정.
측정값은 측정 요약 파일(config/results_summary.yaml)(합성 데이터 기준 추정)에서 읽는다.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

UNIT_KO = {"official_commit": "공식 확정", "close_scores": "1·2순위 점수 근접", "single_room_preference": "1인실 희망",
           "grade_upgrade": "상급병실 차액", "pediatric": "소아", "icu_stepdown": "중환자실 하향",
           "isolation": "격리", "preemptive_isolation": "선제 격리", "no_bed": "병상 없음",
           "isolation_discharge_bed": "격리 퇴실 병실", "same_name_same_ward": "같은 병동 동명이인", "missing_input": "입력 누락",
           "integrated_care_consent_missing": "통합병동 동의 미확인"}
UNIT_WHAT = {"official_commit": "평범한 입원의 병상 확정", "close_scores": "1순위와 2순위 병상 점수가 비슷할 때",
             "single_room_preference": "환자가 1인실을 원할 때", "grade_upgrade": "차액이 드는 병실로 배정할 때",
             "pediatric": "소아 환자", "icu_stepdown": "중환자실에서 일반 병동으로 내려올 때",
             "isolation": "격리 환자", "preemptive_isolation": "선제 격리 환자", "no_bed": "조건 맞는 병상이 없을 때"}
REC_KO = {"promote": "에이전트에 맡기기 제안", "demote": "사람 확인으로 되돌리기 제안", "hold": "유지"}


@lru_cache(maxsize=1)
def results() -> dict:
    return yaml.safe_load((ROOT / "config" / "results_summary.yaml").read_text(encoding="utf-8"))


def autonomy(st) -> list[dict]:
    from turnaround.agent.autonomy import evaluate, unit_outcomes
    rule = st.gw.policy.autonomy_promotion
    rows = evaluate(unit_outcomes(st.store.read()), st.gw.policy)
    out = []
    for r in rows:
        if r["human_n"]:
            ev = f"사람 결정 {r['human_n']}건 중 {r['human_agreed']}건이 에이전트 판단과 같음"
        elif r["agent_n"]:
            ev = f"에이전트 확정 {r['agent_n']}건, 최근 {rule.demote_window}건 중 사람이 뒤집은 것 {r['agent_recent_disagreements']}건"
        else:
            ev = "아직 기록 없음"
        out.append({"unit": r["unit"], "name": UNIT_KO.get(r["unit"], r["unit"]), "what": UNIT_WHAT.get(r["unit"], ""),
                    "mode": r["mode"], "mode_ko": "에이전트" if r["mode"] == "auto" else "사람 확인",
                    "evidence": ev, "lower95": r["agreement_lower95"], "human_n": r["human_n"], "human_agreed": r["human_agreed"],
                    "recommendation": r["recommendation"], "recommendation_ko": REC_KO[r["recommendation"]],
                    "locked": r["never"]})
    order = {"promote": 0, "demote": 1, "hold": 2}
    return sorted(out, key=lambda x: (order[x["recommendation"]], x["locked"], x["mode"] != "auto", x["name"]))


def rule_text(st) -> dict:
    r = st.gw.policy.autonomy_promotion
    return {"promote": f"사람 결정 {r.min_cases}건 이상, 에이전트 판단과의 일치율 하한(95% 신뢰) {r.min_agreement_lower95:.0%} 이상",
            "demote": f"위임 뒤 최근 {r.demote_window}건 중 {r.demote_disagreements}건 이상을 사람이 뒤집으면"}


def set_autonomy(st, unit: str, to: str) -> None:
    """to = auto(에이전트에 맡김) | human(사람 확인으로 되돌림). 승격 금지 유형은 맡길 수 없다."""
    from turnaround.agent.autonomy import apply
    if to == "auto" and unit in set(st.gw.policy.autonomy_promotion.never):
        raise ValueError(f"{UNIT_KO.get(unit, unit)}은(는) 사람이 확인해야 하는 유형이라 에이전트에 맡길 수 없습니다")
    st.gw.policy = apply(st.gw.policy, unit, to)


def model_info(env=None) -> dict:
    """모델과 평가 탭: 일마다 지금 누가 하는지, 시험 결과, 배포 전 시험. 처음 보는 사람도 읽히게 쉬운 말로 쓴다."""
    env = os.environ if env is None else env
    kind = env.get("TURNAROUND_LLM", "fake")
    connected = kind != "fake" and bool(env.get("TURNAROUND_LLM_MODEL"))
    ai_now = ("원내 AI" if kind == "local" else "외부 AI") if connected else "규칙 코드"
    r = results()
    m = r["models"]
    sf = r["safety"]
    return {
        "using": ("외부 AI 연결됨" if connected and kind != "local" else "원내 AI 연결됨") if connected else "외부 AI 연결 안 됨",
        "external": connected and kind != "local",
        "roles": [
            {"job": "병상 고르기, 숫자 계산", "now": "규칙 코드", "test": "AI에 맡기지 않음"},
            {"job": "예외 상황 대응 제안", "now": "규칙 코드",
             "test": f"후보 AI {m['candidates']}개 모두 기준 미달" if not m["exception_selected"] else "기준 통과 AI 있음"},
            {"job": "판단 이유 설명", "now": ai_now,
             "test": f"후보 AI {m['candidates']}개 통과, 가장 저렴한 AI 선정" if m["explain_selected"] else "기준 통과 AI 없음"},
            {"job": "메모에서 정보 뽑기", "now": ai_now if connected else "규칙 코드", "test": "뽑은 정보는 배정에 바로 쓰지 않음"},
        ],
        "guard": "가상(합성) 데이터만 보내고, 이름과 연락처는 가린 뒤 보냅니다",
        "checks": [
            {"name": "상황 재현 시험", "what": "늦은 격리, 청소 지연 같은 상황에서 판단이 그대로인지",
             "value": f"{r['regression']['dev']}개", "sub": f"따로 둔 {r['regression']['holdout']}개"},
            {"name": "안전 재생", "what": "가상 병원의 하루를 돌려 격리, 소독, 중복 배정 위반을 셈",
             "value": f"위반 {sf['violations']}건", "sub": f"검사 {sf['checks']}회"},
            {"name": "처음 보는 병원 재생", "what": "개발 중에 보지 않은 가상 병원으로 같은 검사",
             "value": f"위반 {sf['holdout']['violations']}건", "sub": f"검사 {sf['holdout']['checks']}회"},
            {"name": "AI 선정 시험", "what": "후보 AI를 같은 문제로 채점, 합격 기준은 시험 전에 정함",
             "value": f"후보 {m['candidates']}개", "sub": m["date"]},
        ],
        "note": r["note"],
    }


# ---- 새 병원 설정(온보딩): 표본 새 병원(emr_f) 설정판 ----
def build_onboarding():
    """표본 새 병원 내보내기 표로 설정판을 만든다(약 2초). 시연용으로 질문은 정답표가 대신 답한다."""
    from turnaround.agent.onboarding_flow import OnboardingSession, oracle
    from turnaround.ui.scenes import SEED_NEW, _gold, _tables
    s = OnboardingSession(_tables(), hospital="표본 병원 EMR")
    asked = proposed = 0
    code_master = 0
    while (g := s.current()) is not None:
        if g.stage == "code" and not code_master:
            code_master = s.import_code_master({k: dict(v) for k, v in _gold().codes.items()}) or -1
            continue
        asked += len(g.questions)
        proposed += sum(1 for q in g.questions if q.proposal is not None)
        s.submit(oracle(_gold(), g))
    pkg = s.finalize(day=3, seed=SEED_NEW)
    from turnaround.ui.views import onboarding_view
    v = onboarding_view(s)
    return {"pkg": pkg, "asked": asked, "proposed": proposed, "code_master": max(code_master, 0), "view": v}


APPROVER_KO = {"medical_informatics": "의료정보팀", "ipc_lead": "감염관리 책임자"}


def onboarding_out(ob: dict) -> dict:
    pkg, v = ob["pkg"], ob["view"]
    p = pkg.proof
    got = {a["role"] for a in pkg.approvals}
    need = ["medical_informatics"] + (["ipc_lead"] if pkg.needs_ipc_approval else [])
    return {
        "hospital": pkg.hospital, "tables": v.get("tables"), "mapped": v.get("mapped"),
        "by_stage": v.get("by_stage", {}), "asked": ob["asked"], "code_master": ob["code_master"],
        "proof": {"passed": bool(p.get("passed")), "order": p.get("order_violations"), "safety": p.get("safety_violations"),
                  "commits": p.get("commits"), "events": p.get("events"), "beds": p.get("beds")},
        "approvals": [{"role": r, "name": APPROVER_KO[r], "done": r in got,
                       "who": next((a["user_id"] for a in pkg.approvals if a["role"] == r), None)} for r in need],
        "status": {"approved": "승인됨, 운영 시작 가능", "partially_approved": "승인 1명 남음"}.get(pkg.status, "승인 대기"),
        "version": pkg.version[:12],
    }


def differentiators(ob: dict) -> dict:
    """시연 끝의 차별점 표. 짧고 쉬운 문장으로(2026-11-10 사용자 지적: 읽고 싶지 않다)."""
    r = results()
    sf = r["safety"]
    eb = r.get("entry_breakdown_realistic") or r.get("entry_breakdown")
    curves = r["autonomy"]["curves"]
    last = sum(c["periods"][-1]["ratio"] for c in curves) / len(curves)
    if eb:
        a_val = f"{eb['change'] * 100:.0f}%".replace("-", "−")
        a_sub = f"안내한 수속 시각부터 입실까지 {eb['off_min']:.0f}분 → {eb['on_min']:.0f}분"
    else:
        a_val = f"{r['entry_wait']['change'] * 100:.0f}%".replace("-", "−")
        a_sub = "수속부터 입실까지"
    rows = [
        {"key": "G2", "title": "늦게 온 격리 정보 반영", "what": "병상 확정 뒤 격리 오더가 나면, 입실을 멈추고 감염관리실에 넘깁니다.",
         "value": "자동 보류", "value_sub": "입실 호출 전에 막음", "where": "감염관리실"},
        {"key": "A", "title": "병상 먼저 만들기", "what": "퇴실하는 순간 청소와 침상 정리를 요청하고, 퇴원 준비를 재촉합니다.",
         "value": a_val, "value_sub": a_sub, "where": "병동"},
        {"key": "G1", "title": "실행 직전 재검사", "what": "AI 판단도 사람 승인도, 실행 바로 전에 안전 조건을 다시 확인합니다.",
         "value": f"{sf['violations'] + sf['holdout']['violations']}건", "value_sub": f"안전 위반, 검사 {sf['checks'] + sf['holdout']['checks']}회", "where": "AI 운영 개요"},
        {"key": "C", "title": "기록이 정하는 자율 범위", "what": "사람 결정과 충분히 일치한 유형만 AI에 맡기고, 어긋나면 되돌립니다.",
         "value": f"{last * 100:.0f}%", "value_sub": "위임 뒤 자동 확정 비율(사람 응답은 가정)", "where": "AI 운영 자율 범위"},
        {"key": "B", "title": "온톨로지로 병원 데이터 연결", "what": "병원마다 다른 표와 코드를 공통 개념 체계에 연결해, 같은 규칙이 어느 병원에서나 돕니다.",
         "value": f"{ob['mapped']}개", "value_sub": "연결한 항목, 시험 운영 " + ("통과" if ob["proof"]["passed"] else "실패"), "where": "AI 운영 병원 연동"},
        {"key": "LLM", "title": "시험을 통과한 AI만 사용", "what": "배정은 규칙 코드가 맡고, AI는 시험을 통과한 일에만 씁니다.",
         "value": f"{r['regression']['dev']}가지", "value_sub": "상황 재현 시험", "where": "AI 운영 모델과 평가"},
    ]
    return {"rows": rows, "note": r["note"]}


__all__ = ["differentiators", "autonomy", "build_onboarding", "model_info", "onboarding_out", "results", "rule_text", "set_autonomy"]
