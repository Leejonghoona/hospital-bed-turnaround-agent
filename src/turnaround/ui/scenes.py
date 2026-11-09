"""시연 대본 3장면 (08 계획 4단계). 화면에서 "다음" 버튼을 누를 때마다 한 단계씩 진행한다. 합성 데이터 전용.

1. 평범한 하루: 에이전트가 확정하고 병동·원무에 통보한다. 사람은 환자 연락과 병동 준비만 한다.
2. 예외와 승인: 약속 위험에서 머리가 원무 재연락을 제안하고 사람이 승인한다. 늦은 격리 오더가 오면 감염관리실과
   원무가 근거 카드를 받고, 연락 뒤 자동 변경은 게이트웨이가 막는다.
3. 새 병원: 처음 보는 내보내기(emr_f)를 올려 질문 묶음에 답하고, 재생 증명 → 승인 → 운영 적용까지 간다.
   그 병원 하루를 돌려 관리자가 자율권 카드를 본다.
각 단계는 (보는 역할, 설명, 화면 데이터)를 돌려준다. 사람 동작(승인·답)은 화면의 버튼이 하는 일과 같은 함수를 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable

SEED_DAY, SEED_NEW = 0, 4


@dataclass
class Step:
    title: str
    role: str  # admission | ward | ipc | manager | onboarding
    run: Callable[["SceneRun"], str]  # 사람 동작·시각 진행을 하고 설명을 돌려준다


@dataclass
class Scene:
    id: str
    title: str
    summary: str
    steps: list[Step]


@dataclass
class SceneRun:
    scene: Scene
    index: int = -1
    st: object = None
    session: object = None
    pkg: object = None
    log: list[str] = field(default_factory=list)
    memo: dict = field(default_factory=dict)

    @property
    def done(self) -> bool:
        return self.index >= len(self.scene.steps) - 1

    def next(self) -> dict:
        if self.done:
            return self.view()
        self.index += 1
        step = self.scene.steps[self.index]
        caption = step.run(self)
        self.log.append(f"{self.index + 1}. {step.title}: {caption}")
        return self.view()

    def view(self) -> dict:
        from turnaround.ui import views
        if self.index < 0:
            return {"role": None, "title": self.scene.title, "caption": self.scene.summary, "data": {}}
        step = self.scene.steps[self.index]
        data = {}
        if step.role == "onboarding":
            data = views.onboarding_view(self.session) if self.session is not None else {}
            if self.pkg is not None:
                data["package"] = {"version": self.pkg.version, "status": self.pkg.status,
                                   "proof": {k: self.pkg.proof.get(k) for k in ("passed", "order_violations",
                                                                                "safety_violations", "commits", "plausibility")}}
        elif self.st is not None:
            data = {"admission": views.admission_view, "ward": views.ward_view, "ipc": views.ipc_view,
                    "manager": views.manager_view}[step.role](self.st)
        return {"role": step.role, "title": step.title, "caption": self.log[-1], "data": data,
                "now": self.st.now.strftime("%H:%M") if self.st is not None else None, "step": self.index + 1,
                "steps": len(self.scene.steps)}


def _policy():
    from turnaround.gateway import load_policy
    return load_policy(profile="field_default")


def _day(run: SceneRun, seed: int, until: time, mapped=None) -> None:
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    run.st = build_demo(seed=seed, day=3, until=until, via_fhir=False, policy=_policy(),
                        config=GeneratorConfig.realistic(), occupancy="sparse" if mapped is not None else "full",
                        mapped=mapped)


def _to(run: SceneRun, hh: int, mm: int = 0) -> None:
    run.st.advance(datetime.combine(run.st.now.date(), time(hh, mm)))


# ---------------- 1. 평범한 하루 ----------------

def _a1(run):
    _day(run, SEED_DAY, time(9, 30))
    v = __import__("turnaround.ui.views", fromlist=["admission_view"]).admission_view(run.st)
    return f"09:30 원무 화면. 에이전트가 확정하고 통보한 배정 {len(v['notices'])}건(근거·되돌리기 가능), 환자 연락 할 일 {len(v['contacts'])}건"


def _a2(run):
    _to(run, 13)
    v = __import__("turnaround.ui.views", fromlist=["ward_view"]).ward_view(run.st)
    steps = {}
    for r in v["flow"]:
        steps[r["step"]] = steps.get(r["step"], 0) + 1
    return "13:00 병동 화면. 통보 → 병동 준비 → 올려주세요 → 입실: " + ", ".join(f"{k} {n}" for k, n in steps.items())


def _a3(run):
    _to(run, 18)
    hl = __import__("turnaround.evaluation.human_load", fromlist=["human_load"]).human_load(run.st.store.read())
    return (f"18:00 관리자 화면. 입원 {hl['admission_cases']}건, 확정 {hl['committed']}건, "
            f"사람 손 없이 확정 {hl['no_touch_committed']}건. 실행 안전 위반은 아래 표")


SCENE_DAY = Scene("day", "1. 평범한 하루", "에이전트가 확정하고 통보한다. 사람은 환자 연락과 병동 준비만 한다.",
                  [Step("에이전트 확정 통보", "admission", _a1), Step("병동 입실 흐름", "ward", _a2),
                   Step("하루 결과", "manager", _a3)])


# ---------------- 2. 예외와 승인 ----------------

def _b1(run):
    _day(run, SEED_DAY, time(13, 30))
    from turnaround.ui.views import pending_approvals
    ap = pending_approvals(run.st)
    run.memo["approval"] = ap[0]["exception_id"] if ap else None
    return (f"13:30 원무 화면. 약속 위험: 머리가 '{ap[0]['action_ko']}'을 제안했고 사람 승인을 기다린다"
            if ap else "13:30 원무 화면. 승인 대기 카드 없음")


def _b2(run):
    exid = run.memo.get("approval")
    if not exid:
        return "승인할 카드가 없다"
    d = run.st.gw.approve_action(exid, "bed_manager", run.st.now)
    ko = {"executed": "실행됨", "rejected": "거부됨"}.get(d.status, d.status)
    return f"원무가 '승인'을 눌렀다 → 게이트웨이가 다시 확인한 뒤 {ko} (재연락 할 일이 생긴다)"


def _b3(run):
    from turnaround.events import Event, EventType
    from turnaround.saga import fold
    _to(run, 14)
    s = fold(run.st.store.read())
    cands = sorted((m for m in s.matches.values() if m.active and EventType.PATIENT_ARRIVED not in m.facts),
                   key=lambda m: (EventType.PATIENT_CONTACTED not in m.facts, m.admission_id))
    if not cands:
        return "늦은 격리를 보여 줄 매칭이 없다"
    case = cands[0].admission_id
    now = run.st.now
    run.st.store.append(Event(event_id=f"{case}:demo_isolation", case_id=case, type=EventType.ISOLATION_ORDER_ADDED,
                              occurred_at=now, observed_at=now, source="his",
                              payload={"precautions": ["airborne"], "organism": "MTB", "precaution_status": "suspected",
                                       "isolation_ordered_by": "physician"}))
    _to(run, 14, 30)
    run.memo["iso_case"] = case
    return f"14:00 {case}에 늦은 공기주의 격리 오더(결핵 의심). 14:30 감염관리실 화면"


def _b4(run):
    from turnaround.ui.views import blocked_decisions
    b = blocked_decisions(run.st)
    return f"원무 화면의 '막힌 판단' {len(b)}건: 게이트웨이가 금지 행동을 막고 사람에게 올린 기록"


SCENE_EXCEPTION = Scene("exception", "2. 예외와 승인",
                        "약속 위험은 사람 승인으로 재연락, 늦은 격리는 감염관리실·원무 근거 카드, 금지 행동은 게이트웨이가 막는다.",
                        [Step("약속 위험 승인 카드", "admission", _b1), Step("원무 승인", "admission", _b2),
                         Step("늦은 격리 오더", "ipc", _b3), Step("막힌 판단 기록", "admission", _b4)])


# ---------------- 3. 새 병원 온보딩과 자율권 ----------------

def _gold():
    from turnaround.sim.exports import gold_mapping
    return gold_mapping("emr_f")


def _tables():
    from turnaround.agent.lexicon import _canonical
    from turnaround.sim.exports import export
    h, ev = _canonical(SEED_NEW)
    return export(h, ev, "emr_f")


def _c1(run):
    from turnaround.agent.onboarding_flow import OnboardingSession
    run.session = OnboardingSession(_tables(), hospital="새 병원(emr_f)")
    g = run.session.current()
    return f"내보내기 3개 표를 올렸다. 첫 묶음 '{g.stage}' 질문 {len(g.questions)}개(병상 표를 못 찾아 구조부터 묻는다)"


def _answer_until(run, stop_stage: str | None):
    from turnaround.agent.onboarding_flow import oracle
    n = 0
    while (g := run.session.current()) is not None and g.stage != stop_stage:
        a = oracle(_gold(), g)
        run.session.submit(a)
        n += len(a)
    return n


def _c2(run):
    n = _answer_until(run, "code")
    return f"구조·형식·열 묶음에 답했다(답 {n}개, 사람 모형). 다음은 코드표 묶음"


def _c3(run):
    n = run.session.import_code_master({k: dict(v) for k, v in _gold().codes.items()})
    m = _answer_until(run, None)
    return f"병원 코드표를 한 번에 넣었다(질문 {n}개 해결, 남은 {m}개 답). 매핑 완성"


def _c4(run):
    run.pkg = run.session.finalize(day=3, seed=SEED_NEW)
    p = run.pkg.proof
    return (f"재생 증명: {'통과' if p['passed'] else '실패'} (사건 순서 위반 {p['order_violations']}, 안전 위반 "
            f"{p['safety_violations']}, 배정 {p['commits']}건, 개연성 문제 {len(p['plausibility'])})")


def _c5(run):
    from turnaround.agent.onboarding_flow import apply_package
    run.pkg = run.pkg.approve(by="medical_informatics", acknowledge_profile_questions=True)
    mapped = apply_package(run.pkg, _tables())
    _day(run, SEED_NEW, time(18), mapped=mapped)
    return f"의료정보팀 승인(판 {run.pkg.version}) → 운영 적용 → 그 병원 하루 재생. 관리자 화면의 자율권 카드"


SCENE_ONBOARD = Scene("onboard", "3. 새 병원 온보딩과 자율권",
                      "처음 보는 내보내기를 올려 질문에 답하고, 재생 증명 → 승인 → 운영 적용까지 한 번에 간다.",
                      [Step("업로드와 첫 질문", "onboarding", _c1), Step("구조·형식·열 답", "onboarding", _c2),
                       Step("코드표 일괄 입력", "onboarding", _c3), Step("재생 증명", "onboarding", _c4),
                       Step("승인·운영 적용·자율권", "manager", _c5)])

SCENES = {s.id: s for s in (SCENE_DAY, SCENE_EXCEPTION, SCENE_ONBOARD)}

__all__ = ["SCENES", "Scene", "SceneRun", "Step"]
