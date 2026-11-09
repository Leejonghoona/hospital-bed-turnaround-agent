"""시연 대본 3장면 (08 계획 4단계). 화면에서 "다음" 버튼을 누를 때마다 한 단계씩 진행한다. 합성 데이터 전용.

1. 평범한 하루: 에이전트가 확정하고 병동·원무에 통보한다. 사람은 환자 연락과 병동 준비만 한다.
2. 예외와 승인: 안내 시각 지연 위험에서 머리가 원무 재연락을 제안하고 사람이 승인한다. 늦은 격리 오더가 오면 감염관리실과
   원무가 근거 카드를 받고, 연락 뒤 자동 변경은 게이트웨이가 막는다.
3. 새 병원: 처음 보는 내보내기(emr_f)를 올려 질문 묶음에 답하고, 재생 증명 → 승인 → 운영 적용까지 간다.
   그 병원 하루를 돌려 관리자가 자동 처리 범위 카드를 본다.
각 단계는 (보는 역할, 설명, 화면 데이터)를 돌려준다. 사람 동작(승인·답)은 화면의 버튼이 하는 일과 같은 함수를 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Callable

SEED_DAY, SEED_EXC, SEED_NEW = 2, 0, 4  # 장면 1은 측정 조건(지난 날 입원은 입실한 것으로 봄)과 같다


@dataclass
class Step:
    title: str
    role: str  # admission | ward | ipc | manager | onboarding
    run: Callable[["SceneRun"], str]  # 사람 동작·시각 진행을 하고 설명을 돌려준다
    look: list[str] = field(default_factory=list)  # 화면에서 볼 것 (시연 안내)
    act: str = ""  # 이 단계를 일으키는 사람의 동작 이름(앞 화면에 이 이름의 버튼을 둔다)


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
        step = self.scene.steps[self.index + 1]
        before = len(self.st.store.read()) if self.st is not None else 0
        caption = step.run(self)  # 단계가 끝까지 성공했을 때만 넘어간다(중간에 끊기면 같은 단계를 다시 한다)
        self.memo["changed"] = self._changed(before)
        self.index += 1
        self.log.append(f"{self.index + 1}. {step.title}: {caption}")
        return self.view()

    def _changed(self, before: int) -> list[dict]:
        """이번 단계에서 새로 생긴 결정 기록(시연에서 '무엇이 바뀌었나'를 바로 보이게). 시각 진행만 한 단계도 포함."""
        from turnaround.ui import views
        if self.st is None:
            return []
        new_ids = {e.event_id for e in self.st.store.read()[before:]}
        if not new_ids:
            return []
        return views.audit_log(self.st, 5, ids=new_ids, types=views.SPOTLIGHT_TYPES)

    def view(self) -> dict:
        from turnaround.ui import views
        if self.index < 0:
            return {"role": None, "title": self.scene.title, "caption": self.scene.summary, "data": {}}
        step = self.scene.steps[self.index]
        data = {}
        if step.role == "onboarding":
            data = views.onboarding_view(self.session) if self.session is not None else {}
            if self.pkg is not None:
                p = self.pkg.proof
                data["package"] = {"status": {"proposed": "승인 대기", "approved": "승인됨"}.get(self.pkg.status),
                                   "version": self.pkg.version,
                                   "proof": [{"검사": "시험 운영 검사", "결과": "통과" if p.get("passed") else "실패"},
                                             {"검사": "사건 순서 위반", "결과": p.get("order_violations")},
                                             {"검사": "시험 운영 안전 위반", "결과": p.get("safety_violations")},
                                             {"검사": "시험 운영 배정", "결과": p.get("commits")},
                                             {"검사": "코드 뜻 뒤바뀜 의심",
                                              "결과": len(p.get("plausibility") or [])}]}
        elif self.st is not None:
            data = {"admission": views.admission_view, "ward": views.ward_view, "ipc": views.ipc_view,
                    "manager": views.manager_view}[step.role](self.st)
        return {"role": step.role, "title": step.title, "caption": self.log[-1], "data": data,
                "now": self.st.now.strftime("%H:%M") if self.st is not None else None, "step": self.index + 1,
                "steps": len(self.scene.steps), "changed": self.memo.get("changed") or []}


def _policy():
    from turnaround.gateway import load_policy
    return load_policy(profile="field_default")


def _day(run: SceneRun, seed: int, until: time, mapped=None, past: bool = True) -> None:
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    run.st = build_demo(seed=seed, day=3, until=until, via_fhir=False, policy=_policy(),
                        config=GeneratorConfig.realistic(), occupancy="sparse" if mapped is not None else "full",
                        mapped=mapped, past_admissions=past)


def _to(run: SceneRun, hh: int, mm: int = 0) -> None:
    run.st.advance(datetime.combine(run.st.now.date(), time(hh, mm)))


# ---------------- 1. 평범한 하루 ----------------

def _a1(run):
    _day(run, SEED_DAY, time(8, 15), past=False)
    v = __import__("turnaround.ui.views", fromlist=["admission_view"]).admission_view(run.st)
    return f"08:15 원무 화면. 에이전트가 확정하고 통보한 배정 {len(v['notices'])}건(근거·되돌리기 가능), 환자 연락 할 일 {len(v['contacts'])}건"


def _a2(run):
    _to(run, 14)
    v = __import__("turnaround.ui.views", fromlist=["ward_view"]).ward_view(run.st)
    steps = {}
    for r in v["flow"]:
        steps[r["step"]] = steps.get(r["step"], 0) + 1
    return "14:00 병동 화면. 통보 → 병동 준비 → 호출 → 입실: " + ", ".join(f"{k} {n}" for k, n in steps.items())


def _a3(run):
    _to(run, 18)
    hl = __import__("turnaround.evaluation.human_load", fromlist=["human_load"]).human_load(run.st.store.read())
    return (f"18:00 관리자 화면. 입원 {hl['admission_cases']}건, 확정 {hl['committed']}건, "
            f"사람 손 없이 확정 {hl['no_touch_committed']}건. 실행 안전 위반은 아래 표")


SCENE_DAY = Scene("day", "1. 평범한 하루", "에이전트가 확정하고 통보한다. 사람은 환자 연락과 병동 준비만 한다.",
                  [Step("에이전트 확정 통보", "admission", _a1, ["'에이전트가 확정하고 알린 배정' 카드: 누가 어느 병상으로, 차순위, 되돌리기 시한", "사람이 할 일은 '지금 할 일'에만 모인다(평범한 배정은 처리할 것이 없음)", "병상 현황판: 색과 글자로 퇴원 진행·청소·입실 가능을 구분"]), Step("병동 입실 흐름", "ward", _a2, ["칸반: 병동 준비 필요 → 준비 완료·호출 대기 → 호출됨·이동 중 → 입실 완료", "병상·병동 준비·수속이 모두 갖춰질 때만 '환자 올려 보내기(호출)'가 나간다"]),
                   Step("하루 결과", "manager", _a3, ["사람 손 없이 확정한 비율과 안전 검사 4종(위반 0)", "흐름 지표: 퇴실·병상 준비·입실 시각", "자동 처리 범위 제안: 근거가 쌓이면 에이전트에 맡길 범위를 넓히자고 제안, 적용은 사람이 승인"])])


# ---------------- 2. 예외와 승인 ----------------

def _b1(run):
    _day(run, SEED_EXC, time(13, 30))
    from turnaround.ui.views import pending_approvals
    ap = pending_approvals(run.st)
    run.memo["approval"] = ap[0]["exception_id"] if ap else None
    return (f"13:30 원무 화면. 안내 시각 지연 위험: 에이전트가 {ap[0]['who']} 환자에게 '{ap[0]['action_ko']}'을 제안했고 사람 승인을 기다린다"
            if ap else "13:30 원무 화면. 승인 대기 카드 없음")


def _b2(run):
    exid = run.memo.get("approval")
    if not exid:
        return "승인할 카드가 없다"
    from turnaround.ui.views import _people, who
    ev = run.st.store.read()
    case = next((e.case_id for e in ev if e.type.name == "ACTION_APPROVAL_REQUESTED" and e.payload.get("exception_id") == exid), None)
    d = run.st.gw.approve_action(exid, "bed_manager", run.st.now)
    ko = {"executed": "실행됨", "rejected": "거부됨"}.get(d.status, d.status)
    name = who(_people(ev), case) if case else "환자"
    return f"원무가 {name} 카드의 '승인'을 눌렀다 → 게이트웨이가 다시 확인한 뒤 {ko} (그 환자의 재연락 할 일이 생긴다)"


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
    from turnaround.ui.views import _people, who
    return f"14:00 확정된 {who(_people(run.st.store.read()), case)} 환자에게 늦은 공기주의 격리 오더(결핵 의심)가 왔다"


def _b4(run):
    """연락·확정 뒤 자동 재매칭을 에이전트가 골랐다고 해 보고, 게이트웨이가 막는 것을 보여 준다(시험 입력)."""
    from turnaround.agent.brain import ActionChoice
    from turnaround.events import EventType
    case = run.memo.get("iso_case")
    ev = run.st.store.read()
    ex = next((e for e in reversed(ev) if e.type is EventType.EXCEPTION_RAISED and e.case_id == case
               and e.payload.get("trigger") == "late_isolation"), None)
    if ex is None:
        return "막을 판단을 보여 줄 늦은 격리 예외가 없다"
    choice = ActionChoice(exception_id=ex.payload["exception_id"], trigger="late_isolation", case=case,
                          action="rematch_now", params={}, rationale="시연: 확정 뒤 자동 재매칭을 시도",
                          basis_seq=max((e.seq or 0) for e in ev), mode="llm")
    d = run.st.gw.submit_action(choice, run.st.now)
    why = {"stage_locked": "연락·확정 뒤 자동 변경 금지"}.get((d.reasons or [""])[0], ",".join(d.reasons or []))
    from turnaround.ui.views import _people, who
    name = who(_people(run.st.store.read()), case)
    return (f"시연 입력: 에이전트가 {name} 환자를 스스로 다른 병상으로 옮기려 하면 → 게이트웨이가 "
            f"{'거부' if d.status == 'rejected' else d.status}({why}). 원무 화면 '막힌 판단'에 남는다")


SCENE_EXCEPTION = Scene("exception", "2. 예외와 승인",
                        "안내 시각 지연 위험은 사람 승인으로 재연락, 늦은 격리는 감염관리실·원무 근거 카드, 금지 행동은 게이트웨이가 막는다.",
                        [Step("안내 시각 지연 위험 승인 카드", "admission", _b1, ["'지금 할 일'의 승인 카드: 환자에게 안내한 시각이 위험해 에이전트가 재연락을 제안", "근거 칩: 안내한 시각, 앞 환자 퇴실 여부, 마감", "사람 승인이 필요한 행동만 이렇게 올라온다"]), Step("원무 승인", "admission", _b2, ["승인 순간에도 게이트웨이가 단계 잠금과 선행 조건을 다시 확인한다", "승인한 환자의 카드가 사라지고 그 환자의 재연락 작업이 생긴다(다른 환자 카드는 그대로 남는다)"]),
                         Step("늦은 격리 오더", "ipc", _b3, ["확정된 환자에게 늦은 공기주의 격리 오더(결핵 의심)", "감염관리실 '지금 할 일'에 통지, 에이전트 제안은 원무 승인 대기", "병상을 다시 확인하기 전에는 '환자 올려 보내기(호출)'를 보류한다"]), Step("막힌 판단 기록", "admission", _b4, ["시연용 시험 입력: 에이전트가 확정 환자를 스스로 다른 병상으로 옮기려 함", "게이트웨이가 거부하고 '막힌 판단' 기록에 남는다(아래 펼침)"])])


# ---------------- 3. 새 병원 시작하기 ----------------

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
    from turnaround.ui.views import STAGE_KO
    return f"내보내기 3개 표를 올렸다. 첫 묶음 '{STAGE_KO[g.stage]}' 질문 {len(g.questions)}개(병상 표를 못 찾아 구조부터 묻는다)"


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
    return f"표 구조·값 형식·열의 뜻 질문에 답했다(답 {n}개). 다음은 코드 뜻"


def _c3(run):
    n = run.session.import_code_master({k: dict(v) for k, v in _gold().codes.items()})
    m = _answer_until(run, None)
    return f"병원 코드표를 한 번에 넣어 코드 질문 {n}개를 해결했다(남은 {m}개는 직접 답). 질문 끝"


def _c4(run):
    run.pkg = run.session.finalize(day=3, seed=SEED_NEW)
    p = run.pkg.proof
    return (f"이 병원 데이터로 하루를 시험 운영해 검사: {'통과' if p['passed'] else '실패'} (사건 순서 위반 "
            f"{p['order_violations']}건, 안전 위반 {p['safety_violations']}건, 배정 {p['commits']}건)")


def _c5(run):
    from turnaround.agent.onboarding_flow import apply_package
    run.pkg = run.pkg.approve(by="medical_informatics", acknowledge_profile_questions=True)
    mapped = apply_package(run.pkg, _tables())
    _day(run, SEED_NEW, time(18), mapped=mapped)
    return "의료정보팀이 설정판을 승인하고 운영을 시작했다. 그 병원의 하루를 관리자 화면에서 본다"


SCENE_ONBOARD = Scene("onboard", "3. 새 병원 시작하기",
                      "처음 보는 병원의 내보내기 표를 올려 질문에 답하고, 시험 운영 검사 → 승인 → 운영 시작까지 한 번에 간다.",
                      [Step("업로드와 첫 질문", "onboarding", _c1, ["처음 보는 병원 내보내기 표 3개를 올렸다", "질문은 구조 → 형식 → 열 → 코드표 순서로 묶어서 묻는다", "병상 표를 못 찾아 구조부터 묻는다"]), Step("구조·형식·열 답", "onboarding", _c2, ["앞 묶음의 답으로 초안을 다시 만들어 뒤 질문이 줄어든다", "제안이 있는 질문은 확인만, 제안 없는 질문은 사람이 답을 찾는다"], act="제안대로 답하고 다음 묶음으로"),
                       Step("코드표 일괄 입력", "onboarding", _c3, ["병원 코드표를 한 번에 넣어 코드 질문을 해결", "'모름'은 승인이 아니다: 안전 코드는 늘 사람이 확인"], act="병원 코드표 올리기"), Step("시험 운영 검사", "onboarding", _c4, ["정답이 없는 새 병원에서도 쓸 수 있는 검사: 이 병원 데이터로 하루를 시험 운영", "사건 순서, 안전, 배정이 실제로 생기는지, 코드 뜻이 뒤바뀌지 않았는지"], act="시험 운영 검사 돌리기"),
                       Step("승인하고 운영 시작", "manager", _c5, ["의료정보팀 승인 → 설정판 고정 → 운영 시작", "관리자 화면에서 그 병원의 흐름·안전·자동 처리 범위 제안을 본다"], act="설정판 승인하고 운영 시작")])

SCENES = {s.id: s for s in (SCENE_DAY, SCENE_EXCEPTION, SCENE_ONBOARD)}

__all__ = ["SCENES", "Scene", "SceneRun", "Step"]
