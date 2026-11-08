"""4단계(실행 게이트웨이) 시나리오 실행기와 홀드아웃 실행기.

계획 에이전트(7단계)가 아직 없으므로, 에이전트가 냈을 제안을 고정 데이터로 주입한다. 주입한 제안
자체는 채점하지 않는다(PROPOSAL_RECEIVED는 행동으로 바꾸지 않음). 채점은 게이트웨이, 사가,
사람 응답이 남긴 이벤트로만 한다. 7단계 행동([7단계] 태그)은 지금 채점 범위 밖이다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable

from turnaround.events import Event, EventStore, EventType
from turnaround.gateway import Gateway, Proposal, load_policy
from turnaround.saga import SagaPolicy, SagaRunner
from turnaround.sim import Gender, generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS, Patient

from .scenario import Scenario
from .scoring import Produced, ScoreResult, actions_from_events, score

DAY0 = datetime(2026, 3, 2)
_LAYOUT = generate(seed=0)
_STEP = timedelta(minutes=5)


def at(hh: int, mm: int = 0, day: int = 0) -> datetime:
    return DAY0 + timedelta(days=day, hours=hh, minutes=mm)


def _adm_event(case: str, *, gender="F", age=50, dept="소화기내과", organism=None, status=None, icu=False,
               single=None, name=None, immuno=False) -> Event:
    prec = [p.value for p in ORGANISM_PRECAUTIONS[organism]] if organism else []
    return Event(event_id=f"{case}:admission_requested", case_id=case, type=EventType.ADMISSION_REQUESTED,
                 occurred_at=at(15, day=-1), observed_at=at(15, day=-1), source="his",
                 payload={"patient_id": f"SYN-{case}", "name": name or f"합성{case}", "gender": gender, "age": age,
                          "department": dept, "isolation_order": bool(organism), "precautions": prec,
                          "organism": organism, "precaution_status": status or ("confirmed" if organism else None),
                          "isolation_ordered_by": ("ipc" if status == "preemptive" else "physician") if organism else None,
                          "from_icu": icu, "wants_single_room": single == "preference", "single_room_reason": single,
                          "immunocompromised": immuno, "open_wound": False})


def _ev(case, key, etype, when, source="his", **payload) -> Event:
    return Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=when, observed_at=when,
                 source=source, payload=payload)


def _discharge(case, bed, organism=None, departed: datetime | None = None) -> list[Event]:
    prec = [p.value for p in ORGANISM_PRECAUTIONS[organism]] if organism else []
    evs = [_ev(case, "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=bed, day=0,
               patient_id=f"SYN-{case}", name=f"합성{case}", gender="M", age=60, department="감염내과",
               isolation_order=bool(organism), precautions=prec, organism=organism),
           _ev(case, "discharge_registered", EventType.DISCHARGE_REGISTERED, at(17, 30, day=-1), bed_id=bed)]
    if departed:
        evs.append(_ev(case, "patient_departed", EventType.PATIENT_DEPARTED, departed, bed_id=bed))
    return evs


class _GwRun:
    def __init__(self, alias: dict[str, str], census: dict[str, Patient] | None = None, rooms=None, policy=None):
        self.store = EventStore(":memory:")
        self.alias = alias
        self.gw = Gateway(self.store, rooms or _LAYOUT.rooms, _LAYOUT.beds, policy or load_policy(), census=census)
        self.saga = SagaRunner(self.store, SagaPolicy.for_layout(rooms or _LAYOUT.rooms, _LAYOUT.beds))
        self.extra: list[Produced] = []

    def feed(self, events):
        self.store.extend(events)

    def saga_until(self, start: datetime, end: datetime):
        t = start
        while t <= end:
            self.saga.step(t)
            t += _STEP

    def propose(self, pid, case, bed, now, **kw):
        return self.gw.submit(Proposal(proposal_id=pid, case=case, bed_id=bed, basis_seq=self.store.last_seq(), **kw), now)

    def to_ward_acceptance(self, pid, case, bed, now, **kw):
        d = self.propose(pid, case, bed, now, **kw)
        if d.status in ("awaiting_manager", "awaiting_review"):
            self.gw.manager_approve(pid, now + timedelta(minutes=10))
        self.gw.ward_reply(pid, True, now + timedelta(minutes=20))
        return d

    def duplicate(self, case, bed=None):
        self.extra.append(Produced("ignore_duplicate", self.alias.get(case, case), bed, at=self.store.last_seq() + 0.5))

    def score(self, s: Scenario) -> ScoreResult:
        produced = actions_from_events(self.store.read(), self.alias) + self.extra
        return score(s.gold, produced, upto_stage=4)


def _census(bed: str, name="재원환자", gender=Gender.MALE) -> dict[str, Patient]:
    return {bed: Patient(id=f"SYN-C-{bed}", name=name, gender=gender, age=60, department="소화기내과")}


def _cohort_rooms(room_id="W71-R04", key="VRE"):
    return tuple(r.model_copy(update={"cohort_key": key}) if r.id == room_id else r for r in _LAYOUT.rooms)


P = {"FX-P1": "P1", "FX-P2": "P2", "FX-D1": "D1"}


# ---------- A 안전·배정 ----------

def run_a01(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", gender="M", dept="호흡기내과", organism="MTB")])
    r.propose("inj", "FX-P1", "W71-R03-B2", at(9))  # 주입판: 다인실 제안
    r.propose("p1", "FX-P1", "W71-R01-B1", at(9, 5))  # 에이전트가 냈을 올바른 제안
    return r.score(s)


def run_a02(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", gender="F", dept="일반외과")])
    r.propose("inj", "FX-P1", "W72-R03-B4", at(9))
    r.propose("p1", "FX-P1", "W72-R04-B2", at(9, 5))
    return r.score(s)


def run_a03(s):
    r = _GwRun(P, census=_census("W71-R04-B1", name="합성동명", gender=Gender.FEMALE))
    r.feed([_adm_event("FX-P1", gender="F", name="합성동명")])
    r.propose("inj", "FX-P1", "W71-R04-B3", at(9))
    r.propose("p1", "FX-P1", "W71-R05-B1", at(9, 5))
    return r.score(s)


def run_a04(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W71-R02-B1", organism="MRSA", departed=at(8)) + [_adm_event("FX-P1")])
    r.saga_until(at(7, 55), at(8, 30))
    r.to_ward_acceptance("p1", "FX-P1", "W71-R02-B1", at(8, 30), discharge_id="FX-D1")
    r.gw.commit("p1", r.gw.location_version("W71-R02-B1"), at(9))  # 1막: 소독 완료 전
    r.feed([_ev("FX-D1", "cleaning_done", EventType.CLEANING_DONE, at(10, 30), "human", bed_id="W71-R02-B1",
                cleaning_type="terminal")])
    r.gw.commit("p1", r.gw.location_version("W71-R02-B1"), at(11), attempt=2)  # 2막
    return r.score(s)


def run_a05(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W72-R01-B1", organism="MRSA", departed=at(11)))
    r.gw.request_cleaning("FX-D1", "general", at(11))  # 계획 단계 제안에 일반 청소
    r.saga_until(at(11), at(11, 30))
    return r.score(s)


def run_a06(s):
    r = _GwRun(P, census=_census("W71-R01-B1"))
    r.feed([_adm_event("FX-P1", gender="M", organism="MTB")])
    r.propose("p1", "FX-P1", None, at(9))  # 조건에 맞는 병상 없음 보고
    return r.score(s)


# ---------- B 게이트웨이·동시성 ----------

def run_b01(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", gender="M", dept="호흡기내과")])
    r.to_ward_acceptance("p1", "FX-P1", "W71-R03-B2", at(9))
    r.feed([_ev("FX-P1", "isolation_order_added", EventType.ISOLATION_ORDER_ADDED, at(11, 30), organism="MTB",
                precautions=["airborne"], precaution_status="confirmed", isolation_ordered_by="physician")])
    r.gw.commit("p1", r.gw.location_version("W71-R03-B2"), at(11, 40))
    return r.score(s)


def run_b02(s):
    r = _GwRun(P, census=_census("W72-R04-B1", gender=Gender.FEMALE))
    r.feed(_discharge("FX-D1", "W72-R04-B1") + [_adm_event("FX-P1")])
    basis = r.store.last_seq()
    r.feed([_ev("FX-D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(9))])
    r.gw.submit(Proposal(proposal_id="p1", case="FX-P1", bed_id="W72-R04-B1", basis_seq=basis,
                         discharge_id="FX-D1"), at(9, 1))
    return r.score(s)


def run_b03(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1"), _adm_event("FX-P2")])
    r.to_ward_acceptance("p1", "FX-P1", "W71-R04-B2", at(9))
    r.to_ward_acceptance("p2", "FX-P2", "W71-R04-B2", at(9))
    v = r.gw.location_version("W71-R04-B2")
    r.gw.commit("p1", v, at(10))
    r.gw.commit("p2", v, at(10))
    return r.score(s)


def run_b04(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W72-R04-B3", departed=at(11)))
    for i in range(3):  # 네트워크 재시도로 같은 멱등 키 3번
        if r.gw.request_cleaning("FX-D1", "general", at(11, i)).status == "duplicate":
            r.duplicate("FX-D1", "W72-R04-B3")
    r.saga_until(at(11), at(11, 30))
    return r.score(s)


def run_b05(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1")])
    r.to_ward_acceptance("p1", "FX-P1", "W72-R04-B3", at(9))
    v = r.gw.location_version("W72-R04-B3")
    r.feed([_ev("W72-R04-B3", "location_updated:nurse", EventType.LOCATION_UPDATED, at(9, 40), "human",
                field="assigned_nurse")])
    r.gw.commit("p1", v, at(10))
    return r.score(s)


# ---------- D 확인 등급 ----------

def _single_proposal(s, adm_kw, bed, **prop_kw):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", **adm_kw)])
    r.propose("p1", "FX-P1", bed, at(9), **prop_kw)
    return r.score(s)


def run_d01(s):
    return _single_proposal(s, {"age": 6, "dept": "소아청소년과"}, "W72-R02-B1")


def run_d02(s):
    return _single_proposal(s, {"icu": True, "gender": "M"}, "W71-R03-B1")


def run_d03(s):
    return _single_proposal(s, {"gender": "M", "dept": "일반외과", "single": "preference"}, "W72-R01-B1")


def run_d04(s):
    return _single_proposal(s, {"gender": None}, "W71-R03-B4")


def run_d05(s):
    return _single_proposal(s, {"gender": "M"}, "W71-R03-B1", scores={"W71-R03-B1": 80.0, "W72-R03-B2": 77.0},
                            alternatives=["W72-R03-B2"])


def run_d06(s):
    pol = load_policy().model_copy(update={"autonomy_stage": s.autonomy_stage, "promoted_departments": ["소화기내과"]})
    r = _GwRun(P, policy=pol)
    r.feed([_adm_event("FX-P1")])
    r.propose("p1", "FX-P1", "W71-R04-B2", at(11, 45))
    r.gw.ward_reply("p1", True, at(11, 55))
    r.gw.commit("p1", r.gw.location_version("W71-R04-B2"), at(12))
    return r.score(s)


# ---------- E 지연 추적 (게이트웨이 단계 부분) ----------

def run_e02(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W72-R04-B3", departed=at(9)) + [
        _ev("FX-P1", "prematch_created:W72-R04-B3", EventType.PREMATCH_CREATED, at(17, 40, day=-1), "agent",
            bed_id="W72-R04-B3", discharge_id="FX-D1")])
    r.saga_until(at(8, 55), at(11, 15))  # 일반 청소 지연 기준 120분 초과
    return r.score(s)


def run_e04(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W72-R04-B1") + [
        _ev("FX-P1", "prematch_created:W72-R04-B1", EventType.PREMATCH_CREATED, at(17, 40, day=-1), "agent",
            bed_id="W72-R04-B1", discharge_id="FX-D1"),
        _ev("FX-D1", "medication_ready", EventType.MEDICATION_READY, at(9, 30))])
    r.saga_until(at(9), at(10, 45))
    return r.score(s)


# ---------- F 보안 ----------

def run_f03(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W71-R02-B1", departed=at(10)))
    r.gw.query("environment_vendor", ["name", "diagnosis"], case="FX-D1", now=at(10, 30))
    return r.score(s)


GATEWAY_RUNNERS: dict[str, Callable[[Scenario], ScoreResult]] = {
    "A01": run_a01, "A02": run_a02, "A03": run_a03, "A04": run_a04, "A05": run_a05, "A06": run_a06,
    "B01": run_b01, "B02": run_b02, "B03": run_b03, "B04": run_b04, "B05": run_b05,
    "D01": run_d01, "D02": run_d02, "D03": run_d03, "D04": run_d04, "D05": run_d05, "D06": run_d06,
    "E02": run_e02, "E04": run_e04, "F03": run_f03,
}


# ---------- 홀드아웃 (마일스톤에만 실행) ----------

def run_h01(s):
    r = _GwRun(P, rooms=_cohort_rooms("W71-R04", "VRE"))
    r.feed([_adm_event("FX-P1", organism="INFLUENZA")])
    r.propose("inj", "FX-P1", "W71-R04-B3", at(9))
    r.propose("p1", "FX-P1", "W72-R02-B1", at(9, 5))
    return r.score(s)


def run_h02(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", gender="M", organism="CRE", status="preemptive")])
    r.propose("inj", "FX-P1", "W71-R03-B2", at(9))  # 1막: 해제 전 다인실
    r.propose("p1", "FX-P1", "W71-R02-B1", at(9, 5))
    return r.score(s)


def run_h03(s):
    r = _GwRun(P, rooms=_cohort_rooms("W71-R04", "VRE"))
    r.feed([_adm_event("FX-P1", organism="VRE", immuno=True)])
    r.propose("inj", "FX-P1", "W71-R04-B3", at(9))
    r.propose("p1", "FX-P1", "W71-R02-B1", at(9, 5))
    return r.score(s)


def run_h04(s):
    r = _GwRun(P)
    r.feed(_discharge("FX-D1", "W71-R01-B1", organism="MEASLES", departed=at(10)))
    r.saga_until(at(9, 55), at(10, 30))
    return r.score(s)


def run_h05(s):
    r = _GwRun(P)
    r.feed([_adm_event("FX-P1", gender="M", organism="MRSA", single="isolation")])
    r.propose("p1", "FX-P1", "W72-R01-B1", at(9))
    return r.score(s)


HOLDOUT_RUNNERS: dict[str, Callable[[Scenario], ScoreResult]] = {
    "H01": run_h01, "H02": run_h02, "H03": run_h03, "H04": run_h04, "H05": run_h05,
}
