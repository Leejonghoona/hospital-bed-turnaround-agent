"""합성 병원 데이터를 HIS 이벤트 흐름으로 바꾼다(이벤트 재생 트윈의 입력).

생성기의 하루치 실제 시각은 각 사건이 일어난 뒤에야 인지되는 개별 이벤트로 쪼갠다.
잡음 옵션으로 중복 도착, 늦은 인지(순서 뒤바뀜), 청소 완료 신호 누락을 넣을 수 있다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from turnaround.sim.models import AdmissionRequest, Discharge, Patient, SyntheticHospital

from .models import Event, EventType, Source

_M = timedelta(minutes=1)


@dataclass(frozen=True)
class NoiseConfig:
    duplicates: int = 0  # 같은 이벤트 ID로 한 번 더 도착
    reorders: int = 0  # 25~90분 늦게 인지돼 다른 이벤트보다 뒤에 도착
    drops: int = 0  # 청소 완료 신호 누락


def _patient(p: Patient) -> dict:
    return {"patient_id": p.id, "name": p.name, "gender": p.gender.value if p.gender else None, "age": p.age,
            "department": p.department, "isolation_order": p.isolation_order,
            "isolation_type": p.isolation_type.value if p.isolation_type else None,
            "precautions": [x.value for x in p.precautions], "organism": p.organism,
            "precaution_status": p.precaution_status.value if p.precaution_status else None,
            "isolation_ordered_by": p.isolation_ordered_by,
            "immunocompromised": p.immunocompromised, "open_wound": p.open_wound}


def _ceil_half_hour(dt: datetime) -> datetime:
    m = dt.hour * 60 + dt.minute + (1 if dt.second else 0)
    m = math.ceil(m / 30) * 30
    return datetime.combine(dt.date(), time()) + timedelta(minutes=m)


class _Builder:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.events: list[Event] = []

    def add(self, case: str, key: str, etype: EventType, occurred: datetime, source: Source = "his", **payload):
        self.events.append(Event(
            event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=occurred,
            observed_at=occurred + self.rng.randint(0, 3) * _M, source=source, payload=payload))

    def discharge(self, d: Discharge) -> None:
        t, c, bed = d.actual, d.id, d.bed_id
        if d.discharge_type == "death":
            if d.death_place == "ward":
                # 병동 내 사망: 확인 뒤 면회·사후 처치, 안치실 이송 때 병상이 빈다 (SAG-10). 60분은 합성 가정
                self.add(c, "death_confirmed", EventType.DEATH_CONFIRMED, t.departed_at - 60 * _M, bed_id=bed,
                         **_patient(d.patient))
                self.add(c, "body_transferred", EventType.BODY_TRANSFERRED, t.departed_at, bed_id=bed)
            else:
                # 임종실 전실: 퇴원 결정 없이 병상이 빈다. 이후는 병상 준비 흐름만 (SAG-9)
                self.add(c, "eol_transfer", EventType.END_OF_LIFE_ROOM_TRANSFER, t.departed_at, bed_id=bed,
                         **_patient(d.patient))
            self._cleaning(d)
            return
        if d.discharge_type == "transfer_out":
            # 퇴원 예정이던 환자가 악화돼 중환자실로 급변 전동: 퇴원 결정·등록 뒤 퇴실 대신 전동 (SAG-10)
            self.add(c, "discharge_decided", EventType.DISCHARGE_DECIDED, d.decided_at, bed_id=bed, day=d.day,
                     **_patient(d.patient))
            if d.registered_at is not None:
                self.add(c, "discharge_registered", EventType.DISCHARGE_REGISTERED, d.registered_at, bed_id=bed)
            self.add(c, "unplanned_transfer_out", EventType.UNPLANNED_TRANSFER_OUT, t.departed_at, bed_id=bed,
                     destination="icu")
            self._cleaning(d)
            return
        extra = {"discharge_type": "ama"} if d.discharge_type == "ama" else {}
        self.add(c, "discharge_decided", EventType.DISCHARGE_DECIDED, d.decided_at, bed_id=bed, day=d.day,
                 **_patient(d.patient), **extra)
        if d.registered_at is not None:
            self.add(c, "discharge_registered", EventType.DISCHARGE_REGISTERED, d.registered_at, bed_id=bed)
        eta_set_at = datetime.combine(t.order_confirmed_at.date(), time(8, 0)) + self.rng.randint(0, 30) * _M
        self.add(c, "guardian_eta_set", EventType.GUARDIAN_ETA_SET, eta_set_at, "human",
                 eta=d.guardian_expected_at.isoformat())
        self.add(c, "order_confirmed", EventType.ORDER_CONFIRMED, t.order_confirmed_at)
        if t.rx_issued_at is not None:
            self.add(c, "rx_issued", EventType.DISCHARGE_RX_ISSUED, t.rx_issued_at)
        if t.rx_revised_at is not None:
            self.add(c, "rx_revised", EventType.DISCHARGE_RX_REVISED, t.rx_revised_at, reason=d.rx_revision_reason)
        if t.rx_dispensed_at is not None:
            self.add(c, "rx_dispensed", EventType.DISCHARGE_RX_DISPENSED, t.rx_dispensed_at)
        self.add(c, "medication_ready", EventType.MEDICATION_READY, t.medication_ready_at)
        self.add(c, "education_done", EventType.EDUCATION_DONE, t.education_done_at, "human")
        if t.billing_review_done_at is not None:  # 현장형 순서 (현실 프리셋)
            self.add(c, "billing_review_done", EventType.BILLING_REVIEW_DONE, t.billing_review_done_at)
            self.add(c, "discharge_notice_given", EventType.DISCHARGE_NOTICE_GIVEN, t.notice_given_at, "human")
        self.add(c, "billing_done", EventType.BILLING_DONE, t.billing_done_at)
        if t.receipt_checked_at is not None:
            self.add(c, "receipt_checked", EventType.RECEIPT_CHECKED, t.receipt_checked_at, "human")
            self.add(c, "medication_handed_over", EventType.MEDICATION_HANDED_OVER, t.medication_handed_at, "human")
        if d.guardian_delayed:
            self.add(c, "guardian_eta_updated", EventType.GUARDIAN_ETA_UPDATED, d.guardian_expected_at - 30 * _M,
                     "human", eta=_ceil_half_hour(t.guardian_arrived_at).isoformat())
        self.add(c, "guardian_arrived", EventType.GUARDIAN_ARRIVED, t.guardian_arrived_at, "human")
        self.add(c, "patient_departed", EventType.PATIENT_DEPARTED, t.departed_at, bed_id=bed)
        self._cleaning(d)

    def _cleaning(self, d: Discharge) -> None:
        t, c, bed = d.actual, d.id, d.bed_id
        ctype = d.cleaning_type.value
        req = {"aerosol_wait_min": d.aerosol_wait_min} if d.aerosol_wait_min else {}
        if d.sporicidal:
            req["sporicidal"] = True
        self.add(c, "cleaning_requested", EventType.CLEANING_REQUESTED, t.baseline_cleaning_requested_at, "human",
                 bed_id=bed, cleaning_type=ctype, **req)
        self.add(c, "cleaning_started", EventType.CLEANING_STARTED, t.cleaning_started_at, "human", bed_id=bed)
        self.add(c, "cleaning_done", EventType.CLEANING_DONE, t.cleaning_done_at, "human", bed_id=bed,
                 cleaning_type=ctype)
        if t.bed_prep_done_at is not None:  # 청소 두 갈래: 병원 직원 침상 정리 완료
            self.add(c, "bed_prep_done", EventType.BED_PREP_DONE, t.bed_prep_done_at, "human", bed_id=bed)
        self.add(c, "bed_ready", EventType.BED_READY, t.bed_ready_at, "human", bed_id=bed)

    def admission(self, a: AdmissionRequest) -> None:
        booked = datetime.combine(a.requested_at.date() - timedelta(days=1), time(15, 0)) + self.rng.randint(0, 120) * _M
        self.add(a.id, "admission_requested", EventType.ADMISSION_REQUESTED, booked, day=a.day,
                 requested_at=a.requested_at.isoformat(), preferred_ward_id=a.preferred_ward_id,
                 from_icu=a.from_icu, wants_single_room=a.wants_single_room, **_patient(a.patient))
        if a.cancelled_at is not None:
            self.add(a.id, "admission_cancelled", EventType.ADMISSION_CANCELLED, a.cancelled_at)


_REORDERABLE = {EventType.ORDER_CONFIRMED, EventType.DISCHARGE_RX_ISSUED, EventType.DISCHARGE_RX_DISPENSED,
                EventType.MEDICATION_READY, EventType.BILLING_DONE,
                EventType.PATIENT_DEPARTED, EventType.CLEANING_STARTED, EventType.BED_READY}


def _apply_noise(events: list[Event], noise: NoiseConfig, rng: random.Random) -> list[Event]:
    done = [i for i, e in enumerate(events) if e.type is EventType.CLEANING_DONE]
    if noise.drops > len(done):
        raise ValueError("누락할 청소 완료 신호가 부족하다")
    dropped = set(rng.sample(done, noise.drops))
    kept = [e for i, e in enumerate(events) if i not in dropped]
    reorderable = [i for i, e in enumerate(kept) if e.type in _REORDERABLE]
    for i in rng.sample(reorderable, noise.reorders):
        e = kept[i]
        kept[i] = e.model_copy(update={"observed_at": e.occurred_at + rng.randint(25, 90) * _M})
    dups = [kept[i].model_copy(update={"observed_at": kept[i].observed_at + rng.randint(1, 10) * _M})
            for i in rng.sample(range(len(kept)), noise.duplicates)]
    return kept + dups


def hospital_to_events(h: SyntheticHospital, seed: int = 0, noise: NoiseConfig | None = None) -> list[Event]:
    """인지 시각 순서로 정렬한 이벤트 목록. 같은 시드면 같은 결과."""
    rng = random.Random(seed)
    b = _Builder(rng)
    for d in h.discharges:
        b.discharge(d)
    for a in h.admissions:
        b.admission(a)
    events = b.events if noise is None else _apply_noise(b.events, noise, rng)
    return sorted(events, key=lambda e: (e.observed_at, e.occurred_at, e.event_id))
