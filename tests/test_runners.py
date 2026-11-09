"""3단계 시나리오 실행기 테스트. 실행기는 YAML 정답 라벨을 읽어 사가 결과를 채점한다."""

from pathlib import Path

import pytest

from turnaround.evaluation import runners
from turnaround.evaluation.scenario import load_scenario

DEV = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "dev"
STAGE3 = ["C01", "C02", "C03", "C05", "E01", "E03"]
STAGE4 = ["A01", "A02", "A03", "A04", "A05", "A06", "B01", "B02", "B03", "B04", "B05",
          "D01", "D02", "D03", "D04", "D05", "D06", "E02", "E04", "F03"]
STAGE7 = ["C04", "F01", "F02", "F04"]  # 7단계(계획 에이전트, 추출, 질의)


@pytest.mark.parametrize("sid", STAGE3 + STAGE4 + STAGE7)
def test_stage3_scenarios_pass(sid):
    r = runners.RUNNERS[sid](load_scenario(DEV / f"{sid}.yaml"))
    assert r.passed, r.failures


def test_runner_registry_matches_stage_lists():
    assert sorted(runners.RUNNERS) == sorted(STAGE3 + STAGE4 + STAGE7)


@pytest.mark.parametrize("sid", [f"H{i:02d}" for i in range(1, 11)])
def test_holdout_runners_exist(sid):
    from turnaround.evaluation.runners_gateway import HOLDOUT_RUNNERS
    assert sid in HOLDOUT_RUNNERS


@pytest.mark.parametrize("sid,target,replacement", [
    ("B02", "_changed_since", lambda self, prop, events: []),
    ("B05", "location_version", lambda self, bed: 1),
    ("F03", "query", None),
])
def test_gateway_scenarios_fail_when_mechanism_disabled(monkeypatch, sid, target, replacement):
    from turnaround.gateway import gateway as g
    if target == "query":
        def replacement(self, role, fields, case, now):
            self._emit(case, "gw:q:answered", g.EventType.QUERY_ANSWERED, now, role=role, fields=fields)
            return g.Decision("answered", "query")
    monkeypatch.setattr(g.Gateway, target, replacement)
    assert not runners.RUNNERS[sid](load_scenario(DEV / f"{sid}.yaml")).passed


# 평가 민감도(G4): 사가 장치를 끄면 해당 시나리오가 실패해야 한다
@pytest.mark.parametrize("sid,target,replacement", [
    ("C02", "_compensate", lambda *a, **k: None),
    ("C03", "_compensate", lambda *a, **k: None),
])
def test_scenario_fails_when_saga_mechanism_is_disabled(monkeypatch, sid, target, replacement):
    from turnaround.saga import machine
    monkeypatch.setattr(machine, target, replacement)
    r = runners.RUNNERS[sid](load_scenario(DEV / f"{sid}.yaml"))
    assert not r.passed


def test_e03_fails_if_registration_check_is_disabled(monkeypatch):
    from turnaround.saga import machine
    orig = machine._decide_discharge

    def no_check(d, s, now, out, p):
        p2 = p.model_copy(update={"registration_check_at": machine.time(23, 59)})
        return orig(d, s, now, out, p2)
    monkeypatch.setattr(machine, "_decide_discharge", no_check)
    assert not runners.RUNNERS["E03"](load_scenario(DEV / "E03.yaml")).passed


def test_c05_fails_when_saga_command_ids_are_not_deterministic(monkeypatch):
    """명령 ID에 실행 시각이 섞이면(멱등 깨짐) 재계산이 반복돼 C05가 실패해야 한다."""
    from turnaround.saga import machine
    orig = machine._Out.cmd

    def cmd(self, case, key, *a, **k):
        return orig(self, case, f"{key}:{self.now.isoformat()}", *a, **k)
    monkeypatch.setattr(machine._Out, "cmd", cmd)
    assert not runners.RUNNERS["C05"](load_scenario(DEV / "C05.yaml")).passed


def test_ward_acceptance_check_mutant_is_caught_by_holdout_h13(monkeypatch):
    """G4: 회귀 세트가 못 잡던 "병동 수용 회신 확인" 장치를 홀드아웃 H13이 잡는다."""
    from pathlib import Path
    from turnaround.evaluation.runners_gateway import HOLDOUT_RUNNERS
    from turnaround.evaluation.scenario import load_scenario
    from turnaround.gateway.gateway import Gateway
    s = load_scenario(Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "holdout" / "H13.yaml")
    assert HOLDOUT_RUNNERS["H13"](s).passed
    monkeypatch.setattr(Gateway, "_ward_accepted", lambda self, pid, events: True)
    assert not HOLDOUT_RUNNERS["H13"](s).passed
