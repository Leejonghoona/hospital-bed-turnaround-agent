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
           "isolation": "격리", "preemptive_isolation": "선제 격리", "no_bed": "병상 없음"}
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
    env = os.environ if env is None else env
    kind = env.get("TURNAROUND_LLM", "fake")
    external = kind not in ("fake", "local") and bool(env.get("TURNAROUND_LLM_MODEL"))
    using = {"fake": "결정적 규칙(외부 전송 없음)", "local": "원내 실행 모델(외부 전송 없음)"}.get(kind, "외부 모델(합성 데이터만)")
    if kind != "fake" and not env.get("TURNAROUND_LLM_MODEL"):
        using = "결정적 규칙(외부 전송 없음)"
    r = results()
    m = r["models"]
    return {
        "using": using, "external": external,
        "guard": "합성 데이터 확인 값이 있을 때만 허용, 이름과 연락처는 가려서 보냄",
        "roles": [
            {"job": "병상 배정과 숫자", "who": "결정적 코드", "status": "고정"},
            {"job": "예외 상황 판단", "who": "결정적 규칙",
             "status": f"외부 모델 {m['candidates']}종 시험, 기준 미달" if not m["exception_selected"] else "시험 통과 모델 사용"},
            {"job": "판단 설명문", "who": "외부 모델" if m["explain_selected"] else "결정적 규칙",
             "status": "시험 통과 모델 중 최저 비용" if m["explain_selected"] else "시험 통과 모델 없음"},
            {"job": "메모 정보 추출", "who": "결정적 규칙", "status": "배정에 직접 쓰지 않음"},
        ],
        "checks": [
            {"name": "회귀 시나리오", "value": f"{r['regression']['dev']}개", "sub": f"홀드아웃 {r['regression']['holdout']}개 별도"},
            {"name": "안전 재생 검사", "value": f"위반 {r['safety']['violations']}건", "sub": f"시드 {r['safety']['seeds']}, 검사 {r['safety']['checks']}회"},
            {"name": "홀드아웃 시드", "value": f"위반 {r['safety']['holdout']['violations']}건", "sub": f"시드 {r['safety']['holdout_seeds']}, 검사 {r['safety']['holdout']['checks']}회"},
            {"name": "모델 선정 시험", "value": f"{m['candidates']}종", "sub": f"{m['date']}, 사전 등록 기준"},
        ],
        "note": r["note"],
    }


# ---- 새 병원 설정(온보딩): 표본 새 병원(emr_f) 설정판 ----
def build_onboarding():
    """표본 새 병원 내보내기 표로 설정판을 만든다(약 2초). 시연용으로 질문은 정답표가 대신 답한다."""
    from turnaround.agent.onboarding_flow import OnboardingSession, oracle
    from turnaround.ui.scenes import SEED_NEW, _gold, _tables
    s = OnboardingSession(_tables(), hospital="새 병원(emr_f)")
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
    r = results()
    ew, sf = r["entry_wait"], r["safety"]
    curves = r["autonomy"]["curves"]
    last = sum(c["periods"][-1]["ratio"] for c in curves) / len(curves)
    rows = [
        {"key": "G2", "title": "늦게 온 안전 정보도 되돌린다", "what": "확정 뒤에 격리 오더가 나면 환자 호출을 멈추고 감염관리실에 병상 결정을 올립니다",
         "value": "호출 보류", "value_sub": "확정 뒤 격리 오더가 난 환자", "where": "감염관리실"},
        {"key": "A", "title": "병상을 먼저 만든다", "what": "퇴실하는 순간 시스템이 청소를 요청하고 준비를 재촉해, 빈 병상이 더 일찍 생깁니다",
         "value": f"{ew['change'] * 100:.0f}%".replace("-", "−"), "value_sub": f"수속부터 입실까지 {ew['off_min']}분 → {ew['on_min']}분", "where": "병동"},
        {"key": "G1", "title": "실행 직전에 다시 검사", "what": "에이전트 판단도 사람 승인도 실행 직전에 게이트웨이가 안전 조건을 다시 확인합니다",
         "value": f"{sf['violations'] + sf['holdout']['violations']}건", "value_sub": f"실행 안전 위반, 검사 {sf['checks'] + sf['holdout']['checks']}회", "where": "AI 운영, 개요"},
        {"key": "C", "title": "맡기는 범위를 기록으로 넓힌다", "what": "사람 결정과 충분히 일치한 판단 유형만 에이전트에 맡기고, 어긋나면 사람 확인으로 되돌립니다",
         "value": f"{last * 100:.0f}%", "value_sub": f"위임 후 사람 손 없이 확정({r['autonomy']['days']}일 재생 마지막 구간)", "where": "AI 운영, 자율 범위"},
        {"key": "B", "title": "새 병원은 데이터로 세팅", "what": "병원 내보내기 표를 올리면 모르는 것만 묻고, 그 병원 데이터로 하루를 시험 운영한 뒤 두 사람이 승인합니다",
         "value": "통과" if ob["proof"]["passed"] else "실패", "value_sub": f"시험 운영, 질문 {ob['asked']}개", "where": "AI 운영, 새 병원 설정"},
        {"key": "LLM", "title": "모델은 바꿔 끼우고 시험으로 고른다", "what": "배정과 숫자는 결정적 코드가 하고, 모델은 설명과 추출만 합니다. 기준을 못 넘은 모델은 쓰지 않습니다",
         "value": f"시나리오 {r['regression']['dev']}개", "value_sub": f"회귀 평가, 외부 모델 {r['models']['candidates']}종 시험", "where": "AI 운영, 모델과 평가"},
    ]
    return {"rows": rows, "note": r["note"]}


__all__ = ["differentiators", "autonomy", "build_onboarding", "model_info", "onboarding_out", "results", "rule_text", "set_autonomy"]
