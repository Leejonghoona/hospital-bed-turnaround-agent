"""3단계 시나리오 실행기 테스트. 실행기는 YAML 정답 라벨을 읽어 사가 결과를 채점한다."""

from pathlib import Path

import pytest

from turnaround.evaluation import runners
from turnaround.evaluation.scenario import load_scenario

DEV = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "dev"
STAGE3 = ["C01", "C02", "C03", "C05", "E01", "E03"]


@pytest.mark.parametrize("sid", STAGE3)
def test_stage3_scenarios_pass(sid):
    r = runners.RUNNERS[sid](load_scenario(DEV / f"{sid}.yaml"))
    assert r.passed, r.failures


def test_runner_registry_matches_stage3_list():
    assert sorted(runners.RUNNERS) == sorted(STAGE3)


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
