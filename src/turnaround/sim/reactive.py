"""반응형 재생: 에이전트 행동이 그 뒤 퇴원 시각을 바꾼다 (로드맵 07 M2, DECISIONS 2026-11-09).

생성기의 퇴원 시각은 "에이전트 없음" 기준선이다. 에이전트가 행동하면(처방 재촉, 퇴원 라운지, 즉시 청소 요청)
생성기와 같은 의존 관계로 아직 일어나지 않은 단계만 다시 계산한다. 이미 일어난 일은 바꾸지 않고, 늦추지도 않는다.

효과 크기는 근거가 없어 설계 가정이다. 결과는 효과 크기를 바꾼 민감도와 함께 낸다.
- 처방 재촉: 재촉 시각 뒤 남은 지연의 일부(gain)를 줄인다. 응답에 최소 시간이 걸린다.
- 퇴원 라운지: 환자가 동의하면(동의율) 준비 완료 뒤 병상을 먼저 비운다. 보호자는 라운지로 온다(서울성모 안내).
- 즉시 청소 요청: 퇴실 시각에 요청이 나가므로 기존 방식의 "퇴실 → 사람이 요청" 지연이 없어진다.

운영 현실 옵션(GeneratorConfig.ops_realism, DECISIONS 2026-11-10)을 켠 병원에서만 수단 두 가지가 더 있다.
- 퇴실 즉시 침상 정리 요청(bed_prep_request): 다음 입원이 매칭된 병상의 퇴실을 보면 침상 정리 회차를 기다리지 않게 요청한다.
- 청소 예정표 공유(schedule_share): 다음 입원 시각을 예정표에 올려 청소 대기열이 그 병상을 먼저 고른다. 사람 배정 아님.
이 옵션에서는 청소 시작을 병동별 대기열로 다시 계산하고, 순서가 바뀌어 늦어지는 병상은 늦어진 대로 둔다.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from .models import Discharge, DischargeTimeline

_M = timedelta(minutes=1)


class ReactiveEffects(BaseModel):
    model_config = ConfigDict(frozen=True)
    order_nudge_gain: float = Field(default=0.5, ge=0, le=1)  # 재촉 뒤 남은 지연을 줄이는 비율 (설계 가정)
    order_nudge_min_response_min: int = Field(default=15, ge=0)  # 재촉부터 오더까지 최소 (설계 가정)
    lounge_accept_rate: float = Field(default=0.6, ge=0, le=1)  # 라운지 제안 동의율 (설계 가정)
    lounge_move_min: int = Field(default=15, ge=0)  # 제안부터 라운지 이동까지 (설계 가정)
    # 청소 끝부터 정리 시작까지 최소 (ops_realism, 설계 가정). None: 미리 알린 이득 없이 청소 끝 뒤 기존 응답 시간(민감도)
    bed_prep_min_response_min: int | None = Field(default=10, ge=0)
    # bed_prep_request, schedule_share는 ops_realism 병원에서만 작동한다
    enabled: frozenset[str] = frozenset({"order_nudge", "lounge", "cleaning_request", "bed_prep_request", "schedule_share"})

    def scaled(self, k: float) -> "ReactiveEffects":
        """민감도: 효과 크기를 k배로 (비율은 0~1로 자른다)."""
        return self.model_copy(update={"order_nudge_gain": min(1.0, self.order_nudge_gain * k),
                                       "lounge_accept_rate": min(1.0, self.lounge_accept_rate * k)})


@dataclass(frozen=True)
class Levers:
    order_nudge_at: datetime | None = None
    lounge_at: datetime | None = None  # 라운지 이동에 동의한 시각
    cleaning_request_at: datetime | None = None  # 시스템 청소 요청 시각
    # 단계 재촉 (E2 확장): 단계 이름 → 재촉 시각. 처방(discharge_order)은 order_nudge_at을 쓴다
    step_nudges: tuple[tuple[str, datetime], ...] = ()
    # 운영 현실 옵션 (ops_realism)
    bed_prep_request_at: datetime | None = None  # 퇴실 즉시 침상 정리 요청 시각
    schedule_share_at: datetime | None = None  # 청소 예정표에 다음 입원 시각을 공유한 시각
    next_admission_at: datetime | None = None  # 공유한 다음 입원 예정 시각


def retime(d: Discharge, lv: Levers, fx: ReactiveEffects, clean_start: datetime | None = None) -> DischargeTimeline:
    """생성기와 같은 의존 관계로 퇴원 시각을 다시 계산한다. 행동이 없으면 그대로 돌려준다.

    clean_start는 운영 현실 옵션에서 대기열이 정한 청소 시작 시각이다. 없으면 대기열 대기 시간을 원래대로 둔다."""
    return _retime(d, lv, fx, clean_start)[0]


def _is_ops(d: Discharge) -> bool:
    return "bed_prep_direct_response" in d.durations_min


def _retime(d: Discharge, lv: Levers, fx: ReactiveEffects, clean_start: datetime | None = None):
    """(새 시각, 청소 인력이 올 수 있는 시각). 뒤의 값은 운영 현실 옵션에서만 쓴다."""
    from .generator import ops_bed_side, ops_cleaning_arrival
    t, dur = d.actual, d.durations_min
    ops_mode = _is_ops(d)
    orig_arrive = ops_cleaning_arrival(d, t.baseline_cleaning_requested_at, t.departed_at) if ops_mode else None
    if lv == Levers():
        if not ops_mode or clean_start is None or clean_start == t.cleaning_started_at:
            return t, orig_arrive
        new = t.model_copy(update=ops_bed_side(d, clean_start))
        return (t if new == t else new), orig_arrive
    m = lambda k: timedelta(minutes=k)  # noqa: E731
    # 1) 처방 재촉: 오더가 재촉 뒤였으면 남은 지연의 gain만큼 앞당긴다
    shift = timedelta(0)
    if lv.order_nudge_at is not None and "order_nudge" in fx.enabled and t.order_confirmed_at > lv.order_nudge_at:
        remain = t.order_confirmed_at - lv.order_nudge_at
        new = max(lv.order_nudge_at + m(fx.order_nudge_min_response_min),
                  t.order_confirmed_at - remain * fx.order_nudge_gain)
        shift = min(timedelta(0), new - t.order_confirmed_at)
    sh = lambda x: None if x is None else x + shift  # noqa: E731
    order, rx_issued, rx_rev, rx_disp = (t.order_confirmed_at + shift, sh(t.rx_issued_at), sh(t.rx_revised_at),
                                         sh(t.rx_dispensed_at))
    med, edu = t.medication_ready_at + shift, t.education_done_at + shift
    billing = t.billing_done_at + shift

    def pulled(orig: datetime, at: datetime | None) -> datetime:
        """재촉 뒤 남은 지연을 gain만큼 줄인다(처방 재촉과 같은 가정)."""
        if at is None or "order_nudge" not in fx.enabled or orig <= at:
            return orig
        return min(orig, max(at + m(fx.order_nudge_min_response_min), orig - (orig - at) * fx.order_nudge_gain))

    steps = dict(lv.step_nudges)
    field = t.medication_handed_at is not None
    seq_new: dict = {}
    if field:
        # 현장형 순서 (방법론 검수 P4): 심사 → 안내 → 수납 → 영수증, 퇴원약 도착 → 전달. 재촉은 그 단계부터 전파
        review = pulled(t.billing_review_done_at + shift, steps.get("billing_review"))
        d_rev = review - (t.billing_review_done_at + shift)
        notice = t.notice_given_at + shift + d_rev
        billing = pulled(t.billing_done_at + shift + d_rev, steps.get("billing_payment"))
        receipt = billing + (t.receipt_checked_at - t.billing_done_at)
        med = pulled(med, steps.get("medication"))
        handed = max(med, receipt) + (t.medication_handed_at - max(t.medication_ready_at, t.receipt_checked_at))
        edu = handed
        seq_new = dict(billing_review_done_at=review, notice_given_at=notice, receipt_checked_at=receipt,
                       medication_handed_at=handed)
        ready = handed
    else:
        ready = max(billing, edu)
    # 2) 퇴원 라운지: 준비가 끝났으면 보호자를 기다리지 않고 병상을 비운다
    gate = t.guardian_arrived_at
    if lv.lounge_at is not None and "lounge" in fx.enabled:
        gate = min(gate, max(lv.lounge_at, ready))
    departed = max(ready, gate) + m(dur["exit_prep"])
    departed = min(departed, t.departed_at)  # 행동은 늦추지 않는다
    # 3) 청소: 시스템이 요청하면 그 시각, 아니면 기존 방식 지연
    if lv.cleaning_request_at is not None and "cleaning_request" in fx.enabled:
        requested = max(departed, lv.cleaning_request_at)
    else:
        requested = departed + m(dur["baseline_cleaning_request_delay"])
    extra = d.cleaning_extra_delay_min
    if ops_mode:  # 운영 현실 옵션: 대기열이 청소 시작을, 회차·요청이 침상 정리를 정한다. min으로 자르지 않는다
        arrive = ops_cleaning_arrival(d, requested, departed)
        start = clean_start if clean_start is not None else arrive + (t.cleaning_started_at - orig_arrive)
        prep_req = (max(lv.bed_prep_request_at, departed) if lv.bed_prep_request_at is not None
                    and "bed_prep_request" in fx.enabled else None)
        side = ops_bed_side(d, start, prep_req, fx.bed_prep_min_response_min)
        if seq_new:
            side.update({k: min(v, departed) for k, v in seq_new.items()})
        new = t.model_copy(update=dict(
            order_confirmed_at=order, rx_issued_at=rx_issued, rx_revised_at=rx_rev, rx_dispensed_at=rx_disp,
            medication_ready_at=med, education_done_at=edu, billing_done_at=billing, departed_at=departed,
            baseline_cleaning_requested_at=departed + m(dur["baseline_cleaning_request_delay"]), **side))
        return (t if new == t else new), arrive
    clean_start = max(requested + m(dur["cleaning_response"] + extra), departed + m(d.aerosol_wait_min))
    clean_done = clean_start + m(dur["cleaning"])
    ready_at = clean_done + m(dur["bed_make"])
    extra_seq: dict = {}
    if t.bed_prep_done_at is not None:  # 현실 프리셋: 청소 두 갈래 (생성기와 같은 의존 관계)
        clean_done = clean_start + (t.cleaning_done_at - t.cleaning_started_at)  # 범위(침상 단위·병실)는 원래 길이
        prep_start = clean_done + (t.bed_prep_started_at - t.cleaning_done_at)  # 재설치는 청소 뒤 (P1, P11)
        prep_done = prep_start + (t.bed_prep_done_at - t.bed_prep_started_at)
        ready_at = max(clean_done, prep_done)
        extra_seq.update(bed_prep_started_at=prep_start, bed_prep_done_at=prep_done)  # min으로 묶지 않는다 (P11)
    if seq_new:  # 현장형 순서 (퇴실을 넘지 않게: 라운지로 퇴실이 당겨져도 순서는 유지)
        extra_seq.update({k: min(v, departed) for k, v in seq_new.items()})
    new = t.model_copy(update=dict(
        order_confirmed_at=order, rx_issued_at=rx_issued, rx_revised_at=rx_rev, rx_dispensed_at=rx_disp,
        medication_ready_at=med, education_done_at=edu, billing_done_at=billing, departed_at=departed,
        baseline_cleaning_requested_at=departed + m(dur["baseline_cleaning_request_delay"]),
        cleaning_started_at=min(clean_start, t.cleaning_started_at), cleaning_done_at=min(clean_done, t.cleaning_done_at),
        bed_ready_at=min(ready_at, t.bed_ready_at), **extra_seq))
    return (t if new == t else new), None


def lounge_accepts(case: str, fx: ReactiveEffects, seed: int = 0) -> bool:
    """환자(보호자) 동의 여부. 시드·사례마다 결정적 (시드가 빠지면 모든 시드가 같은 동의 패턴, 방법론 검수)."""
    return random.Random(f"lounge-{seed}-{case}").random() < fx.lounge_accept_rate




class ReactiveReplay:
    """하루 재생의 이벤트 공급기. 에이전트 행동을 읽어 아직 오지 않은 퇴원 이벤트의 시각을 다시 잡는다."""

    def __init__(self, h, seed: int, fx: ReactiveEffects):
        from turnaround.events.replay import hospital_to_events
        self.h, self.fx, self.seed = h, fx, seed
        self.discharges = {d.id: d for d in h.discharges if d.discharge_type == "normal"}
        self.levers: dict[str, Levers] = {k: Levers() for k in self.discharges}
        self.pending = sorted(hospital_to_events(h, seed=seed), key=lambda e: (e.observed_at, e.event_id))
        self.delivered: set[str] = set()
        self.applied: list[dict] = []  # 적용한 행동 기록 (보고용)
        self._seq = 0  # 읽은 마지막 순번 (새 이벤트만 본다)
        self._nudge: dict[str, datetime] = {}
        self._lounge: dict[str, datetime] = {}
        self._clean: dict[str, datetime] = {}
        self._steps: dict[str, dict[str, datetime]] = {}
        # 운영 현실 옵션 (ops_realism): 병동별 청소 대기열을 다시 계산하려면 모든 퇴원(사망·전동 포함)이 필요하다
        self.ops = bool(getattr(h.config, "ops_realism", False))
        self.all = {d.id: d for d in h.discharges}
        self.current = {k: d.actual for k, d in self.all.items()}  # 지금 재생에 반영된 시각
        self._ward = {b.id: b.ward_id for b in h.beds}
        self._adm = {a.id: a for a in h.admissions}
        self._awaiting: dict[str, tuple[str, datetime]] = {}  # 입원 요청 → (병상, 매칭을 처음 본 시각)
        self._departed: dict[str, datetime] = {}
        self._accepted: dict[str, datetime | None] = {}

    def deliver(self, store, t: datetime) -> None:
        while self.pending and self.pending[0].observed_at <= t:
            e = self.pending.pop(0)
            self.delivered.add(e.event_id)
            store.append(e)

    def _watch_ops(self, e) -> None:
        """다음 입원이 기다리는 병상과 퇴실을 본다 (운영 현실 옵션 수단의 근거)."""
        from turnaround.events import EventType
        if e.type is EventType.PREMATCH_CREATED or (e.type is EventType.MATCH_COMMITTED and e.source == "gateway"):
            bed = e.payload.get("bed_id")
            cur = self._awaiting.get(e.case_id)
            if bed and (cur is None or cur[0] != bed):
                self._awaiting[e.case_id] = (bed, e.observed_at)
        elif e.type in (EventType.PREMATCH_RELEASED, EventType.ADMISSION_CANCELLED):
            self._awaiting.pop(e.case_id, None)
        elif e.type is EventType.PATIENT_DEPARTED and e.case_id in self.discharges:
            self._departed.setdefault(e.case_id, e.observed_at)

    def _ops_levers(self, d: Discharge) -> dict:
        waits = [(at_, self._adm[a].requested_at) for a, (bed, at_) in self._awaiting.items()
                 if bed == d.bed_id and a in self._adm and self._adm[a].day == d.day]
        if not waits:
            return {}
        seen = min(w[0] for w in waits)
        out = {"schedule_share_at": seen, "next_admission_at": min(w[1] for w in waits)}
        if d.id in self._departed:
            out["bed_prep_request_at"] = max(self._departed[d.id], seen)
        return out

    def react(self, store, t: datetime) -> None:
        from turnaround.events import EventType
        events = store.read(after_seq=self._seq)
        if events:
            self._seq = max(e.seq or 0 for e in events)
        nudge, lounge, clean = self._nudge, self._lounge, self._clean
        for e in events:
            if self.ops:
                self._watch_ops(e)
            if e.case_id not in self.discharges:
                continue
            if e.type is EventType.ACTION_EXECUTED and e.payload.get("action") == "remind_owner":
                step = (e.payload.get("exception_id") or "").split(":")[2:3]
                if step and step[0] in ("billing_review", "medication", "billing_payment"):
                    self._steps.setdefault(e.case_id, {}).setdefault(step[0], e.observed_at)
                else:
                    nudge.setdefault(e.case_id, e.observed_at)
            elif e.type is EventType.LOUNGE_OFFERED:
                lounge.setdefault(e.case_id, e.observed_at)
            elif e.type is EventType.CLEANING_REQUEST_ISSUED and e.source == "saga":
                clean.setdefault(e.case_id, e.observed_at)
        changed = []
        for case, d in self.discharges.items():
            lounge_at = None
            if case in lounge and lounge_accepts(case, self.fx, self.seed):
                lounge_at = lounge[case] + timedelta(minutes=self.fx.lounge_move_min)
            lv = Levers(order_nudge_at=nudge.get(case), lounge_at=lounge_at, cleaning_request_at=clean.get(case),
                        step_nudges=tuple(sorted(self._steps.get(case, {}).items())),
                        **(self._ops_levers(d) if self.ops else {}))
            if lv != self.levers[case]:
                for name in ("bed_prep_request_at", "schedule_share_at"):
                    if getattr(lv, name) is not None and getattr(self.levers[case], name) is None:
                        self.applied.append({"case": case, "at": t.isoformat(), "lever": name[:-3]})
                self.levers[case] = lv
                self._accepted[case] = lounge_at
                changed.append(case)
        if not changed:
            return
        if not self.ops:
            for case in changed:
                self._rebuild(self.discharges[case], retime(self.discharges[case], self.levers[case], self.fx), t,
                              accepted_lounge=self._accepted[case])
            return
        self._replan_ops(t, set(changed))

    def _replan_ops(self, t: datetime, changed: set[str]) -> None:
        """운영 현실 옵션: 병동별 청소 대기열을 지금의 행동으로 다시 계산하고, 시각이 바뀐 퇴원만 다시 짓는다.
        이미 시작한 청소는 고정한다. 순서가 바뀌어 늦어지는 병상도 그대로 반영한다(효과를 부풀리지 않는다)."""
        from .ops import CleanJob, schedule_cleaning
        share = "schedule_share" in self.fx.enabled
        jobs = []
        for k, d in self.all.items():
            lv = self.levers.get(k, Levers())
            _, arrive = _retime(d, lv, self.fx)
            cur = self.current[k]
            clean_len = round((cur.cleaning_done_at - cur.cleaning_started_at).total_seconds() / 60)
            ward = self._ward.get(d.bed_id, d.bed_id.split("-")[0])
            if cur.cleaning_started_at <= t:
                jobs.append(CleanJob(k, ward, arrive, clean_len, fixed_start=cur.cleaning_started_at))
            else:
                jobs.append(CleanJob(k, ward, arrive, clean_len, not_before=t + _M,
                                     share_at=lv.schedule_share_at if share else None,
                                     next_admission_at=lv.next_admission_at if share else None))
        starts = schedule_cleaning(jobs)
        for k, d in self.all.items():
            new_t = _retime(d, self.levers.get(k, Levers()), self.fx, starts[k])[0]
            if new_t != self.current[k] or k in changed:
                self._rebuild(d, new_t, t, accepted_lounge=self._accepted.get(k))

    def _rebuild(self, d: Discharge, new_t: DischargeTimeline, t: datetime, accepted_lounge: datetime | None) -> None:
        from turnaround.events import Event, EventType
        from turnaround.events.replay import _Builder
        self.current[d.id] = new_t
        b = _Builder(random.Random(f"rb-{d.id}"))
        b.discharge(d.model_copy(update={"actual": new_t}))
        fresh = {e.event_id: e for e in b.events}
        if accepted_lounge is not None:
            when = max(accepted_lounge, t)
            fresh[f"{d.id}:lounge_accepted"] = Event(event_id=f"{d.id}:lounge_accepted", case_id=d.id,
                                                      type=EventType.LOUNGE_ACCEPTED, occurred_at=when,
                                                      observed_at=when, source="human", payload={"bed_id": d.bed_id})
        keep = []
        for e in self.pending:
            if e.case_id != d.id:
                keep.append(e)
                continue
            n = fresh.pop(e.event_id, None)
            if n is None or n.occurred_at == e.occurred_at:
                keep.append(e)
            else:
                keep.append(_clamp(n, t))
        keep += [_clamp(n, t) for k, n in fresh.items() if k not in self.delivered and k.endswith("lounge_accepted")]
        self.pending = sorted(keep, key=lambda e: (e.observed_at, e.event_id))
        self.applied.append({"case": d.id, "at": t.isoformat(), "departed_from": d.actual.departed_at.isoformat(),
                             "departed_to": new_t.departed_at.isoformat()})


def _clamp(e, t: datetime):
    """아직 오지 않은 이벤트는 지금보다 앞설 수 없다."""
    if e.observed_at > t:
        return e
    return e.model_copy(update={"occurred_at": max(e.occurred_at, t), "observed_at": t + _M})


__all__ = ["Levers", "ReactiveEffects", "ReactiveReplay", "lounge_accepts", "retime"]
