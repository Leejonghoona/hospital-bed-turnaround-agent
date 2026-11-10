"""시나리오 실행기. 상황을 이벤트 고정 데이터로 만들어 시스템을 돌리고, YAML 정답 라벨로 채점한다.

실행기는 평가 쪽 코드라 시나리오 ID를 알아도 된다. 평가 대상 코드(saga, events)는 모른다.
지금은 3단계(사가)에서 채점할 수 있는 시나리오만 있다. 정답은 실행 시점에 YAML에서 읽으므로
라벨이 바뀌면 채점도 따라 바뀐다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from turnaround.events import Event, EventStore, EventType, hospital_to_events
from turnaround.saga import SagaPolicy, SagaRunner, fold
from turnaround.sim import generate

from .scenario import Scenario
from .scoring import Produced, ScoreResult, actions_from_events, score

STAGE = 7  # 채점할 수 있는 마지막 단계 (7단계 계획 에이전트까지)
DAY0 = datetime(2026, 3, 2)
_STEP = timedelta(minutes=5)


def at(day: int, hh: int, mm: int = 0) -> datetime:
    return DAY0 + timedelta(days=day, hours=hh, minutes=mm)


def _ev(case: str, key: str, etype: EventType, occurred: datetime, source="his", observed=None, **payload) -> Event:
    return Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=occurred,
                 observed_at=observed or occurred, source=source, payload=payload)


def _decided(case, bed, day=0, iso=False):
    return _ev(case, "discharge_decided", EventType.DISCHARGE_DECIDED, at(day - 1, 17), bed_id=bed, day=day,
               patient_id=f"{case}-P", name="합성환자", gender="M", age=60, department="일반외과",
               isolation_order=iso, isolation_type="contact" if iso else None)


def _registered(case, bed, day=0, mm=30):
    return _ev(case, "discharge_registered", EventType.DISCHARGE_REGISTERED, at(day - 1, 17, mm), bed_id=bed)


def _prematch(adm, disc, bed):
    return _ev(adm, f"prematch_created:{bed}", EventType.PREMATCH_CREATED, at(-1, 17, 40), "agent",
               bed_id=bed, discharge_id=disc)


_L = generate(seed=0)  # 병원 배치는 시드와 무관하게 같다
SAGA_POLICY = SagaPolicy.for_layout(_L.rooms, _L.beds)


class _Run:
    """저장소와 사가를 묶어 돌리고, 중복 도착(ignore_duplicate)도 행동으로 기록한다."""

    def __init__(self):
        self.store = EventStore(":memory:")
        self.saga = SagaRunner(self.store, SAGA_POLICY)
        self.extra: list[Produced] = []

    def feed(self, events, alias):
        for e in events:
            if self.store.append(e) is None:
                self.extra.append(Produced("ignore_duplicate", alias.get(e.case_id, e.case_id),
                                           e.payload.get("bed_id"), at=self.store.last_seq() + 0.5))

    def until(self, start: datetime, end: datetime):
        t = start
        while t <= end:
            self.saga.step(t)
            t += _STEP

    def score(self, s: Scenario, alias) -> ScoreResult:
        produced = actions_from_events(self.store.read(), alias) + self.extra
        return score(s.gold, produced, upto_stage=STAGE)


def _with_state(r: ScoreResult, checks: list[tuple[bool, str]]) -> ScoreResult:
    fails = r.failures + [msg for ok, msg in checks if not ok]
    return ScoreResult(passed=not fails, failures=fails)


# ---------- C01 강제 종료 후 재개 ----------

def _saga_signature(store: EventStore):
    return sorted((e.event_id, e.type.value, tuple(sorted(e.payload.items())))
                  for e in store.read() if e.source == "saga" and e.type is not EventType.CASE_RESUMED)


def run_c01(s: Scenario) -> ScoreResult:
    kill, restart, end = at(0, 10, 40), at(0, 11, 30), at(0, 20)
    for seed in range(42, 142):  # 상황 조건을 만족하는 합성 하루를 고른다
        h = generate(seed=seed)
        events = hospital_to_events(h, seed=seed)
        day0 = sorted((d for d in h.discharges if d.day == 0), key=lambda d: d.id)
        dep = {e.case_id: e.observed_at for e in events if e.type is EventType.PATIENT_DEPARTED}
        gap = [d for d in day0 if d.id in dep and kill < dep[d.id] <= restart]  # 사망·급변 전동은 퇴실 이벤트가 없다
        in_progress = all(d.actual.bed_ready_at > restart for d in day0)
        if gap and in_progress:
            break
    else:
        return ScoreResult(False, ["상황을 만족하는 합성 데이터를 찾지 못함"])
    order = gap[:1] + [d for d in day0 if d is not gap[0]]
    alias = {d.id: f"D{i + 1}" for i, d in enumerate(order)}
    start = at(-1, 16)

    ref = _Run()
    ref.feed(events, alias)
    ref.until(start, end)

    run = _Run()
    run.feed(events, alias)
    run.until(start, kill)
    run.saga = SagaRunner(run.store, SAGA_POLICY)  # 재시작: 새 실행기, 같은 저장소
    run.saga.resume(restart)
    run.until(restart, end)
    r = run.score(s, alias)
    return _with_state(r, [(_saga_signature(run.store) == _saga_signature(ref.store),
                            "재개 후 사가 명령 집합이 무중단 실행과 다름")])


# ---------- C02, C03 퇴원 취소 보상 ----------

def _cancel_fixture(bed: str, contacted: bool):
    evs = [_decided("FX-D1", bed), _registered("FX-D1", bed), _prematch("FX-P1", "FX-D1", bed)]
    evs.append(_ev("FX-P1", f"ward_accepted:{bed}", EventType.WARD_ACCEPTED, at(-1, 18), "human", bed_id=bed))
    if contacted:
        evs.append(_ev("FX-P1", "patient_contacted", EventType.PATIENT_CONTACTED, at(0, 9), "human"))
    evs.append(_ev("FX-D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(0, 11)))
    return evs


def run_c02(s: Scenario) -> ScoreResult:
    alias = {"FX-D1": "D1", "FX-P1": "P1"}
    run = _Run()
    run.feed(_cancel_fixture("W72-R03-B1", contacted=False), alias)
    run.until(at(-1, 17), at(0, 11, 30))
    st = fold(run.store.read())
    return _with_state(run.score(s, alias), [
        (st.discharges["FX-D1"].stage.value == s.gold.final_state.get("D1"), "D1 최종 상태 불일치"),
        (st.matches["FX-P1"].stage.value == s.gold.final_state.get("P1"), "P1 최종 상태 불일치"),
    ])


def run_c03(s: Scenario) -> ScoreResult:
    alias = {"FX-D1": "D1", "FX-P1": "P1"}
    run = _Run()
    run.feed(_cancel_fixture("W71-R04-B4", contacted=True), alias)
    run.until(at(-1, 17), at(0, 11, 30))
    st = fold(run.store.read())
    return _with_state(run.score(s, alias), [
        (st.matches["FX-P1"].stage.value == s.gold.final_state.get("P1"), "P1 최종 상태 불일치"),
    ])


# ---------- C05 이벤트 중복과 순서 뒤바뀜 ----------

def run_c05(s: Scenario) -> ScoreResult:
    bed, c = "W72-R04-B3", "FX-D1"
    alias = {c: "D1"}
    done = _ev(c, "cleaning_done", EventType.CLEANING_DONE, at(0, 12, 10), "human", bed_id=bed, cleaning_type="general")
    events = [
        _decided(c, bed), _registered(c, bed),
        _ev(c, "cleaning_started", EventType.CLEANING_STARTED, at(0, 11, 30), "human", observed=at(0, 11, 31), bed_id=bed),
        _ev(c, "patient_departed", EventType.PATIENT_DEPARTED, at(0, 11), observed=at(0, 11, 40), bed_id=bed),  # 늦게 인지
        done,
        done.model_copy(update={"observed_at": at(0, 12, 15)}),  # 같은 이벤트 ID로 다시 도착
        _ev(c, "bed_ready", EventType.BED_READY, at(0, 12, 20), "human", bed_id=bed),
    ]
    run = _Run()
    run.feed(events, alias)
    run.until(at(-1, 17), at(0, 14))
    return run.score(s, alias)


# ---------- E01 보호자 지연 (사가 부분) ----------

def run_e01(s: Scenario) -> ScoreResult:
    bed, c = "W72-R04-B3", "FX-D1"
    alias = {c: "D1", "FX-P1": "P1"}
    events = [
        _decided(c, bed), _registered(c, bed), _prematch("FX-P1", c, bed),
        _ev(c, "guardian_eta_set", EventType.GUARDIAN_ETA_SET, at(0, 8, 10), "human", eta=at(0, 11).isoformat()),
        _ev(c, "guardian_eta_updated", EventType.GUARDIAN_ETA_UPDATED, at(0, 10, 5), "human", eta=at(0, 13).isoformat()),
    ]
    run = _Run()
    run.feed(events, alias)
    run.until(at(-1, 17), at(0, 10, 30))
    return run.score(s, alias)


# ---------- E03 퇴원 예정 미등록 ----------

def run_e03(s: Scenario) -> ScoreResult:
    alias = {"FX-D1": "D1", "FX-D2": "D2", "FX-D3": "D3"}
    events = [_decided("FX-D1", "W71-R03-B1", day=1), _decided("FX-D2", "W72-R04-B1", day=1),
              _decided("FX-D3", "W72-R03-B2", day=1)]
    events = [e.model_copy(update={"occurred_at": at(0, 17), "observed_at": at(0, 17)}) for e in events]
    events.append(_ev("FX-D3", "discharge_registered", EventType.DISCHARGE_REGISTERED, at(0, 17, 30), bed_id="W72-R03-B2"))
    run = _Run()
    run.feed(events, alias)
    run.until(at(0, 17), at(0, 19, 30))
    return run.score(s, alias)


RUNNERS: dict[str, Callable[[Scenario], ScoreResult]] = {
    "C01": run_c01, "C02": run_c02, "C03": run_c03, "C05": run_c05, "E01": run_e01, "E03": run_e03,
}

from .runners_gateway import GATEWAY_RUNNERS, HOLDOUT_RUNNERS  # noqa: E402  4단계 실행기

RUNNERS.update(GATEWAY_RUNNERS)
