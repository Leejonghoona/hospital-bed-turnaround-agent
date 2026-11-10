"""오래 걸리는 검사 (사용자 승인 8종 중 6종. 나머지 2종은 test_fhir.py, test_forecast.py의 slow 테스트).

실행: pytest -m slow
"""

import random
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest

from turnaround.evaluation import runners
from turnaround.evaluation.board import _safety
from turnaround.evaluation.invariants import check_assignment
from turnaround.evaluation.scenario import load_scenario
from turnaround.events import EventStore, EventType, hospital_to_events
from turnaround.gateway import Gateway, Proposal, load_policy
from turnaround.saga import SagaRunner
from turnaround.sim import generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS, Gender, Patient

pytestmark = pytest.mark.slow
DEV = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "dev"
DAY0 = datetime(2026, 3, 2)


# ---------- 1. 안전 스윕 시드 0~999 ----------

def test_safety_sweep_seeds_0_999():
    r = _safety(range(1000))
    assert r["S1"]["violations"] == 0 and r["S2"]["violations"] == 0
    print(r)


def test_executed_safety_sweep_50_seeds():
    """시스템이 실행한 상태의 안전 (DECISIONS 2026-11-09). 위 시드 0~999 스윕은 생성기 자료 점검이다."""
    from turnaround.evaluation.safety_exec import executed_safety_sweep
    r = executed_safety_sweep(range(50))
    print(r)
    assert r["S1"]["violations"] == 0 and r["S2"]["violations"] == 0
    assert r["S1"]["denominator"] > 0 and r["S2"]["denominator"] > 0


# ---------- 2. 강제 종료 경계 전수 ----------

def _sig(store):
    return sorted((x.event_id, x.type.value) for x in store.read()
                  if x.source == "saga" and x.type is not EventType.CASE_RESUMED)


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_kill_at_every_state_transition_boundary(tmp_path, seed):
    """사가가 명령을 낸 모든 시각 직후에 강제 종료하고 재개해도 무중단 실행과 결과가 같다 (D1, D2)."""
    events = hospital_to_events(generate(seed=seed), seed=seed)
    start, end, step = DAY0 - timedelta(hours=8), DAY0 + timedelta(hours=18), timedelta(minutes=15)
    ref = EventStore(":memory:")
    ref.extend(events)
    rr, t, boundaries = SagaRunner(ref), start, []
    while t <= end:
        before = ref.last_seq()
        rr.step(t)
        if ref.last_seq() > before:
            boundaries.append(t)
        t += step
    # 마지막 단계에서 끊으면 재개가 기준 실행보다 한 단계 뒤(종료 시각 + 15분)에서 시작해 비교가 어긋난다.
    # 그래서 종료 시각 전의 경계만 쓴다 (시드 2, 18:00에서 확인: 재개 결과는 18:15까지 돈 무중단 실행과 같았다)
    boundaries = [b for b in boundaries if b < end]
    assert len(boundaries) >= 10
    for i, kill in enumerate(boundaries):
        path = tmp_path / f"{seed}_{i}.sqlite"
        s1 = EventStore(path)
        s1.extend(events)
        r1, t = SagaRunner(s1), start
        while t <= kill:
            r1.step(t)
            t += step
        del r1, s1
        s2 = EventStore(path)
        r2 = SagaRunner(s2)
        r2.resume(t)
        while t <= end:
            r2.step(t)
            t += step
        assert _sig(s2) == _sig(ref), (seed, kill)
        s2.close()


# ---------- 3. 게이트웨이 퍼징 ----------

ORGS = [None, None, None, "MTB", "MEASLES", "MRSA", "VRE", "CRE", "INFLUENZA", "CDIFF"]


def _random_adm(rng, i):
    org = rng.choice(ORGS)
    prec = [p.value for p in ORGANISM_PRECAUTIONS[org]] if org else []
    return {"patient_id": f"SYN-F{i}", "name": rng.choice(["가온", "나래", "다솜", f"F{i}"]),
            "gender": rng.choice(["M", "F", None]), "age": rng.choice([5, 40, 70]),
            "department": rng.choice(["소화기내과", "일반외과"]), "isolation_order": bool(org), "precautions": prec,
            "organism": org, "precaution_status": rng.choice(["confirmed", "preemptive"]) if org else None,
            "from_icu": rng.random() < .1, "immunocompromised": rng.random() < .1, "open_wound": False}


@pytest.mark.parametrize("seed", range(20))
def test_gateway_fuzzing_never_executes_unsafe_or_double_booked(seed):
    """무작위 제안·병동 회신·확정·직접 수정을 섞어도 실행된 배치는 독립 검사기를 통과하고 이중 배정이 없다.
    예외 승인이나 사유 입력으로 실행된 직접 수정(GW-10)만 검사기 위반이 허용된다."""
    rng = random.Random(seed)
    h = generate(seed=0)
    store = EventStore(":memory:")
    room_g = {r.id: r.gender for r in h.rooms}
    census = {b.id: Patient(id=f"SYN-C{i}", name=f"재원{i}", gender=room_g[b.room_id], age=60, department="소화기내과")
              for i, b in enumerate(h.beds) if rng.random() < .3 and room_g[b.room_id] is not None}
    gw = Gateway(store, h.rooms, h.beds, load_policy(), census=census)
    now = DAY0 + timedelta(hours=8)
    from turnaround.events import Event
    for i in range(12):
        store.append(Event(event_id=f"F{i}:admission_requested", case_id=f"F{i}", type=EventType.ADMISSION_REQUESTED,
                           occurred_at=now, observed_at=now, source="his", payload=_random_adm(rng, i)))
    beds = [b.id for b in h.beds]
    for k in range(150):
        now += timedelta(minutes=2)
        case, bed, op = f"F{rng.randrange(12)}", rng.choice(beds), rng.random()
        if op < .45:
            gw.submit(Proposal(proposal_id=f"p{k}", case=case, bed_id=bed, basis_seq=store.last_seq()), now)
        elif op < .6:
            pids = [e.payload["proposal_id"] for e in store.read() if e.type is EventType.PROPOSAL_RECEIVED]
            if pids:
                gw.manager_approve(rng.choice(pids), now)
        elif op < .75:
            pids = [e.payload["proposal_id"] for e in store.read() if e.type is EventType.WARD_ACCEPTANCE_ASKED]
            if pids:
                gw.ward_reply(rng.choice(pids), rng.random() < .8, now)
        elif op < .9:
            pids = [e.payload["proposal_id"] for e in store.read() if e.type is EventType.WARD_ACCEPTED]
            if pids:
                pid = rng.choice(pids)
                prop = gw._proposal(pid, store.read())
                gw.commit(pid, gw.location_version(prop.bed_id), now)
        else:
            claim = rng.choice([None, ("ipc-1", "ipc")])
            if claim and rng.random() < 0.5:  # 절반은 감염관리실이 실제로 승인한 기록이 있다
                gw.approve_safety_exception(case, bed, approver="ipc-1", role="ipc", now=now)
            gw.manual_override(case, bed, actor="u", role="bed_manager", reason="other", now=now,
                               reason_text=rng.choice([None, "사유"]),
                               safety_exception_by=claim)
    ev = store.read()
    assigned = gw._assignments(ev)
    assert len(set(assigned.values())) == len(assigned), "이중 배정"
    excepted = {e.case_id for e in ev if e.type is EventType.GATEWAY_DECISION and e.payload.get("step") == "override"
                and e.payload.get("decision") == "applied" and e.payload.get("safety_exception")}
    occ = dict(gw._occupants(ev))
    checked = {b: p for b, p in occ.items() if not any(assigned.get(c) == b for c in excepted)}
    bad = [v for v in check_assignment(h.rooms, h.beds, checked)
           if not any(gw._admission(c, ev)[0] and gw._admission(c, ev)[0].id in v.detail for c in excepted)]
    assert bad == [], bad


# ---------- 4. 시나리오 민감도 전수 (G4) ----------

def _no_shacl(self, *a, **k):
    return []


MUTANTS = {
    "gateway_shacl_off": ("turnaround.gateway.gateway", "Gateway", "_shacl", _no_shacl, ["A01", "A02", "A03"]),
    "terminal_precondition_off": ("turnaround.gateway.gateway", "Gateway", "_isolation_discharge_pending",
                                  lambda self, *a, **k: None, ["A04"]),
    "agent_safety_filter_off": ("turnaround.agent.tools", "PlanningTools", "candidates", None, ["A01", "A03", "F01"]),
    "review_routing_off": ("turnaround.gateway.gateway", "Gateway", "_review_reasons", lambda self, *a, **k: [],
                           ["D01", "D02", "D03"]),
    "rematch_off": ("turnaround.agent.planner", "PlanningAgent", "_rematch", lambda self, now: [], ["E02"]),
    "qa_evidence_off": ("turnaround.agent.qa", "QueryAgent", "_readmission_q", None, ["F04"]),
}


def _unsafe_candidates(orig):
    def f(self, case, now, events=None):
        saved = self.gw._shacl
        self.gw._shacl = lambda *a, **k: []
        try:
            return orig(self, case, now, events)
        finally:
            self.gw._shacl = saved
    return f


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_every_mutant_is_caught_by_some_dev_scenario(monkeypatch, name):
    import importlib
    mod, cls, attr, repl, sids = MUTANTS[name]
    klass = getattr(importlib.import_module(mod), cls)
    if name == "agent_safety_filter_off":
        repl = _unsafe_candidates(getattr(klass, attr))
    if name == "qa_evidence_off":
        from turnaround.agent.qa import Answer
        repl = lambda self, events, case: Answer("answered", "재입원 예정 없음", ("made-up",))  # noqa: E731
    monkeypatch.setattr(klass, attr, repl)
    caught = [s for s in sids if not runners.RUNNERS[s](load_scenario(DEV / f"{s}.yaml")).passed]
    assert caught, f"뮤턴트 {name}를 잡은 시나리오가 없다"


# ---------- 6. 규칙 모드 vs 에이전트 200 병원·일 ----------

def test_rule_mode_vs_agent_200_hospital_days():
    """시드 40개 × 5일 = 200 병원·일. 가짜 LLM 에이전트와 규칙 모드의 제안이 같고, 실행된 배치는 안전하다.
    (실제 모델 비교는 모델 성능점검 하네스에서 한다. 여기서는 대체 경로가 같은 결과를 내는지 본다)"""
    from turnaround.agent import FakeLLM
    from turnaround.ui.demo import build_demo
    diffs = 0
    for seed in range(40):
        a = build_demo(seed=seed, day=4, until=time(18), via_fhir=False, llm=FakeLLM())
        r = build_demo(seed=seed, day=4, until=time(18), via_fhir=False, mode="rule")
        r_props = [(e.payload["case"], e.payload["bed_id"]) for e in r.store.read() if e.type is EventType.PROPOSAL_RECEIVED]
        a_props = [(e.payload["case"], e.payload["bed_id"]) for e in a.store.read() if e.type is EventType.PROPOSAL_RECEIVED]
        diffs += r_props != a_props
        occ = a.gw._occupants(a.store.read())
        assert check_assignment(a.h.rooms, a.h.beds, occ) == [], seed
    assert diffs == 0
