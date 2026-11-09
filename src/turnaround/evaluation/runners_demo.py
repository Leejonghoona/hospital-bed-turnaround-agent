"""데모 장면 시나리오 실행기 (설계 개정 S6, 데모 D2 "늦게 온 격리 정보를 에이전트가 스스로 되돌림").

현장 프로필(`config/profiles/field_default.yaml`)로 돈다: 예외 판단 머리가 켜져 있다.
회귀 30·홀드아웃과 섞지 않는 개발용 별도 세트다(인터뷰 Q3 추천안). 라벨은 초안이다.
"""

from __future__ import annotations

from typing import Callable

from turnaround.events import EventType
from turnaround.gateway import load_policy
from turnaround.saga import SagaPolicy, SagaRunner

from .runners_gateway import P, _GwRun, _LAYOUT, _adm_event, _discharge, _ev, _fill_except, at
from .scenario import Scenario
from .scoring import ScoreResult

FIELD = load_policy(profile="field_default")


def _field_run(census=None) -> _GwRun:
    r = _GwRun(P, census=census, policy=FIELD)
    r.saga = SagaRunner(r.store, SagaPolicy.from_policy(FIELD, _LAYOUT.rooms, _LAYOUT.beds))
    return r


def _iso(case, when, **kw):
    return _ev(case, "iso_late", EventType.ISOLATION_ORDER_ADDED, when, precautions=["contact"], organism="VRE", **kw)


def run_x01(s: Scenario) -> ScoreResult:
    """연락 전: 격리 오더가 늦게 옴 → 다인실 매칭 자동 해제 → 머리가 재매칭 선택 → 규칙 모드가 1인실 제안."""
    r = _field_run(census=_fill_except("W72-R04-B2", "W72-R01-B1"))
    r.feed([_adm_event("FX-P1", gender="F", dept="일반외과"),
            _ev("FX-P1", "prematch_created:W72-R04-B2", EventType.PREMATCH_CREATED, at(8, 40), "agent",
                bed_id="W72-R04-B2"),
            _iso("FX-P1", at(10))])
    r.saga_until(at(10), at(10, 5))
    r.agent_cycle(at(10, 5), commit=False, rounds=2)
    return r.score(s)


def run_x02(s: Scenario) -> ScoreResult:
    """연락 후: 매칭을 자동으로 바꾸지 않는다 → 검토·재연락 작업, 머리는 사람에게 올림."""
    r = _field_run(census=_fill_except("W72-R04-B2", "W72-R01-B1"))
    r.feed([_adm_event("FX-P1", gender="F", dept="일반외과"),
            _ev("FX-P1", "prematch_created:W72-R04-B2", EventType.PREMATCH_CREATED, at(8, 40), "agent",
                bed_id="W72-R04-B2"),
            _ev("FX-P1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9, 30), "human"),
            _iso("FX-P1", at(10))])
    r.saga_until(at(10), at(10, 5))
    r.agent_cycle(at(10, 5), commit=False, rounds=1)
    return r.score(s)


def run_x03(s: Scenario) -> ScoreResult:
    """퇴실 뒤 격리 오더: 입실 가능을 되돌리고 종말소독으로 올림, 이미 확정된 다음 환자는 사람에게."""
    r = _field_run()
    bed = "W72-R04-B1"
    r.feed(_discharge("FX-D1", bed, departed=at(11)) + [
        _ev("FX-D1", "cleaning_done", EventType.CLEANING_DONE, at(11, 50), "human", bed_id=bed, cleaning_type="general"),
        _ev("FX-D1", "bed_ready", EventType.BED_READY, at(12), "human", bed_id=bed),
        _adm_event("FX-P1", gender="F", dept="일반외과"),
        _ev("FX-P1", "prematch_created:" + bed, EventType.PREMATCH_CREATED, at(9), "agent", bed_id=bed,
            discharge_id="FX-D1"),
        _ev("FX-P1", "patient_contacted", EventType.PATIENT_CONTACTED, at(9, 30), "human"),
        _ev("FX-P1", "gw:manual:committed", EventType.MATCH_COMMITTED, at(12, 30), "gateway", bed_id=bed),
        _iso("FX-D1", at(13), bed_id=bed)])
    r.saga_until(at(10, 55), at(13, 10))
    return r.score(s)


DEMO_RUNNERS: dict[str, Callable[[Scenario], ScoreResult]] = {"X01": run_x01, "X02": run_x02, "X03": run_x03}
