"""숨은 규칙을 가진 합성 병원의 과거 기록 (로드맵 07 M4, 온보딩 시험용).

실제 병원 데이터가 아니다(CLAUDE.md 1번). 병원마다 다른 절차(배정 시점, 병동 확인 방식, 퇴실 기준 시각,
퇴원 라운지, 청소 두 갈래, 입실 시간대, 진료과-병동 관계)를 무작위로 정하고, 그 규칙대로 사람들이 일한 기록을
이벤트 내보내기 형태로 만든다. 이름·등록번호는 넣지 않는다. 규칙을 벗어난 기록(넘침 배정, 흩어진 시각)도 섞는다.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, ConfigDict

from turnaround.events import Event, EventType

DEPARTMENTS = ("호흡기내과", "소화기내과", "감염내과", "신경과", "일반외과", "정형외과")
WARD_BEDS = {"W71": [f"W71-R{r:02d}-B{b}" for r in range(3, 6) for b in range(1, 5)],
             "W72": [f"W72-R{r:02d}-B{b}" for r in range(3, 6) for b in range(1, 5)]}
_M = timedelta(minutes=1)


class HiddenRules(BaseModel):
    model_config = ConfigDict(frozen=True)
    dept_ward: dict[str, str]
    assignment_timing: str  # same_day_morning | prematch_evening
    ward_confirmation: str  # notify | reply
    departure_due_at: str  # "10:00" | "11:00" | "12:00"
    lounge_enabled: bool
    cleaning_split: bool
    admission_window: tuple[str, str]
    overflow_rate: float = 0.1  # 자리가 없어 다른 병동으로 넘친 배정
    compliance: float = 1.0  # 배정 시점·병동 확인 절차를 규칙대로 한 비율 (나머지는 다른 방식, 지저분한 병원)

    @classmethod
    def random(cls, seed: int) -> "HiddenRules":
        r = random.Random(f"rules-{seed}")
        depts = list(DEPARTMENTS)
        r.shuffle(depts)
        cut = r.randint(2, 4)
        return cls(dept_ward={d: ("W71" if i < cut else "W72") for i, d in enumerate(depts)},
                   assignment_timing=r.choice(["same_day_morning", "prematch_evening"]),
                   ward_confirmation=r.choice(["notify", "reply"]),
                   departure_due_at=r.choice(["10:00", "11:00", "12:00"]),
                   lounge_enabled=r.random() < 0.5, cleaning_split=r.random() < 0.5,
                   admission_window=r.choice([("10:00", "15:00"), ("13:00", "17:00"), ("14:00", "18:00")]),
                   overflow_rate=r.choice([0.05, 0.1, 0.15]))


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def hospital_history(rules: HiddenRules, seed: int, days: int, per_day: int = 3,
                     start: date = date(2026, 1, 5)) -> list[Event]:
    r = random.Random(f"hist-{seed}")
    out: list[Event] = []

    def add(case, key, etype, when, source="his", **p):
        out.append(Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=when, observed_at=when,
                         source=source, payload=p))

    due = _t(rules.departure_due_at)
    w0, w1 = _t(rules.admission_window[0]), _t(rules.admission_window[1])
    span = (w1.hour * 60 + w1.minute) - (w0.hour * 60 + w0.minute)
    for day in range(days):
        d0 = datetime.combine(start + timedelta(days=day), time())
        for k in range(per_day):
            # 퇴원: 퇴실 기준 시각 근처(조금 앞)에 몰리고 흩어진다
            c = f"H-D{day:03d}{k}"
            ward = r.choice(list(WARD_BEDS))
            bed = r.choice(WARD_BEDS[ward])
            dep = datetime.combine(d0.date(), due) + round(r.gauss(-20, 45)) * _M
            if rules.lounge_enabled and r.random() < 0.15:
                add(c, "lounge_accepted", EventType.LOUNGE_ACCEPTED, dep - 5 * _M, "human", bed_id=bed)
            add(c, "patient_departed", EventType.PATIENT_DEPARTED, dep, bed_id=bed)
            if rules.cleaning_split:
                add(c, "bed_prep_done", EventType.BED_PREP_DONE, dep + r.randint(20, 60) * _M, "human", bed_id=bed)
            add(c, "cleaning_done", EventType.CLEANING_DONE, dep + r.randint(40, 90) * _M, "human", bed_id=bed)
            add(c, "bed_ready", EventType.BED_READY, dep + r.randint(95, 120) * _M, "human", bed_id=bed)
            # 입원: 진료과 → 병동 (가끔 넘침), 배정 시점, 병동 확인, 입실 시간대
            a = f"H-A{day:03d}{k}"
            dept = r.choice(DEPARTMENTS)
            home = rules.dept_ward[dept]
            ward = home if r.random() >= rules.overflow_rate else ("W72" if home == "W71" else "W71")
            bed = r.choice(WARD_BEDS[ward])
            arrive = datetime.combine(d0.date(), w0) + r.randint(0, max(0, span)) * _M
            add(a, "admission_requested", EventType.ADMISSION_REQUESTED, d0 - timedelta(hours=9),
                department=dept, gender=r.choice(["M", "F"]), requested_at=arrive.isoformat())
            ok = r.random() < rules.compliance
            timing = rules.assignment_timing if ok else ({"prematch_evening": "same_day_morning"}
                                                         .get(rules.assignment_timing, "prematch_evening"))
            mode = rules.ward_confirmation if r.random() < rules.compliance else (
                "reply" if rules.ward_confirmation == "notify" else "notify")
            if timing == "prematch_evening":
                commit = d0 - timedelta(hours=7) + r.randint(0, 120) * _M  # 전날 17:00~19:00
            else:
                commit = d0 + timedelta(hours=8) + r.randint(0, 210) * _M  # 당일 08:00~11:30
            if mode == "reply":
                add(a, "ward_asked", EventType.WARD_ACCEPTANCE_ASKED, commit - 40 * _M, bed_id=bed)
                add(a, "ward_accepted", EventType.WARD_ACCEPTED, commit - r.randint(5, 30) * _M, "human", bed_id=bed)
                add(a, "committed", EventType.MATCH_COMMITTED, commit, "human", bed_id=bed)
            else:
                add(a, "committed", EventType.MATCH_COMMITTED, commit, "human", bed_id=bed)
                add(a, "ward_notified", EventType.WARD_ASSIGNMENT_NOTIFIED, commit + 1 * _M, bed_id=bed)
            add(a, "arrived", EventType.PATIENT_ARRIVED, max(arrive, commit + 30 * _M), "human", bed_id=bed)
    return sorted(out, key=lambda e: (e.observed_at, e.event_id))


__all__ = ["DEPARTMENTS", "HiddenRules", "hospital_history"]
