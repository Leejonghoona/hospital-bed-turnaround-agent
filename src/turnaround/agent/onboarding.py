"""온보딩: 병원의 과거 기록만으로 병원 프로필 초안을 만든다 (로드맵 07 M4, DECISIONS 2026-11-09).

판정은 결정적 통계다(CLAUDE.md 2번). 항목마다 근거(건수, 비율)를 붙이고, 근거가 부족하거나 애매하면
추측하지 않고 사람에게 묻는 질문으로 남긴다. 기록으로 알 수 없는 항목(병원 규정, 감염관리 기준)은 늘 묻는다.
초안은 사람이 확인한 뒤 정책으로 쓴다(`load_policy(profile=경로)`).
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from turnaround.events import Event, EventType

MIN_N = 20  # 항목 판정에 필요한 최소 건수 (설계 가정)
MIN_DEPT_N = 5
CLEAR = 0.7  # 이 비율 이상이면 분명하다고 본다 (설계 가정)
# 기록으로는 알 수 없어 늘 묻는 항목
ALWAYS_ASK = (
    ("discharge_sequence.rx_due_at", "퇴원 당일 처방 완료 기준 시각은 병원 규정이라 기록만으로 정할 수 없다"),
    ("isolation.risk_conditions", "선제 격리 알림 대상(고위험 조건)은 병원 감염관리 규정이 정한다"),
    ("escalation_roles", "예외를 누구에게 올릴지는 병원 조직이 정한다"),
)


@dataclass
class OnboardingResult:
    values: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    questions: list[dict] = field(default_factory=list)

    def ask(self, item: str, why: str, guess=None) -> None:
        self.questions.append({"item": item, "why": why, "guess": guess})

    def matches(self, rules, item: str) -> bool:
        v = self.values.get(item)
        if item == "dept_ward":
            return v is not None and all(v.get(d) == w for d, w in rules.dept_ward.items())
        if item == "admission_window":
            return v is not None and tuple(v) == tuple(rules.admission_window)
        return v == getattr(rules, item)

    def profile_yaml(self) -> dict:
        v = self.values
        hp: dict = {"id": "onboarded-draft"}
        if "assignment_timing" in v:
            hp["assignment"] = {"timing": v["assignment_timing"]}
        if "ward_confirmation" in v:
            hp["ward_confirmation"] = {"mode": v["ward_confirmation"],
                                       "merge_manager_confirm_into_commit": v["ward_confirmation"] == "notify"}
        if "departure_due_at" in v:
            hp["discharge_sequence"] = {"departure_due_at": v["departure_due_at"]}
        if "lounge_enabled" in v:
            hp["guardian"] = {"lounge_enabled": v["lounge_enabled"]}
        if "cleaning_split" in v:
            hp["cleaning"] = {"split": v["cleaning_split"]}
        if "admission_window" in v:
            hp["admission_window"] = list(v["admission_window"])
        if "dept_ward" in v:
            by_ward: dict[str, list[str]] = defaultdict(list)
            for d, w in sorted(v["dept_ward"].items()):
                by_ward[w].append(d)
            hp["ward_departments"] = dict(by_ward)
        return {"hospital_profile": hp}


def _hhmm(minutes: float) -> str:
    m = int(minutes)
    return f"{m // 60:02d}:{m % 60:02d}"


def infer_profile(events: list[Event]) -> OnboardingResult:
    out = OnboardingResult()
    by_case: dict[str, dict[EventType, Event]] = defaultdict(dict)
    for e in events:
        by_case[e.case_id].setdefault(e.type, e)
    commits = [c for c in by_case.values() if EventType.MATCH_COMMITTED in c and EventType.ADMISSION_REQUESTED in c]
    deps = [c[EventType.PATIENT_DEPARTED] for c in by_case.values() if EventType.PATIENT_DEPARTED in c]

    # 배정 시점: 입원 예정일 전날 확정이 많으면 전날 사전 매칭
    if len(commits) >= MIN_N:
        before = sum(c[EventType.MATCH_COMMITTED].observed_at.date()
                     < datetime.fromisoformat(c[EventType.ADMISSION_REQUESTED].payload["requested_at"]).date()
                     for c in commits)
        share = before / len(commits)
        out.evidence["assignment_timing"] = {"n": len(commits), "prev_day_share": round(share, 3)}
        if share >= CLEAR:
            out.values["assignment_timing"] = "prematch_evening"
        elif share <= 1 - CLEAR:
            out.values["assignment_timing"] = "same_day_morning"
        else:
            out.ask("assignment.timing", f"전날 확정 {share:.0%}로 섞여 있다", "same_day_morning")
        # 병동 확인: 확정 전에 병동 수용 회신이 있으면 회신형
        replied = sum(EventType.WARD_ACCEPTED in c and c[EventType.WARD_ACCEPTED].observed_at
                      <= c[EventType.MATCH_COMMITTED].observed_at for c in commits)
        share = replied / len(commits)
        out.evidence["ward_confirmation"] = {"n": len(commits), "reply_before_commit_share": round(share, 3)}
        if share >= CLEAR:
            out.values["ward_confirmation"] = "reply"
        elif share <= 1 - CLEAR:
            out.values["ward_confirmation"] = "notify"
        else:
            out.ask("ward_confirmation.mode", f"확정 전 병동 회신 {share:.0%}로 섞여 있다", "notify")
        # 입실 시간대: P10~P90을 시 단위로
        arr = sorted(e.observed_at.hour * 60 + e.observed_at.minute
                     for c in commits for t, e in c.items() if t is EventType.PATIENT_ARRIVED)
        if len(arr) >= MIN_N:
            lo, hi = arr[int(0.05 * len(arr))], arr[min(len(arr) - 1, int(0.95 * len(arr)))]
            out.values["admission_window"] = (_hhmm(math.floor(lo / 60) * 60), _hhmm(math.ceil(hi / 60) * 60))
            out.evidence["admission_window"] = {"n": len(arr), "p05": _hhmm(lo), "p95": _hhmm(hi)}
        # 진료과 → 병동: 다수 병동이 분명할 때만
        counts: dict[str, Counter] = defaultdict(Counter)
        for c in commits:
            dept = c[EventType.ADMISSION_REQUESTED].payload.get("department")
            bed = c[EventType.MATCH_COMMITTED].payload.get("bed_id") or ""
            counts[dept][bed.split("-", 1)[0]] += 1
        mapping = {}
        for dept, cnt in sorted(counts.items()):
            ward, k = cnt.most_common(1)[0]
            n = sum(cnt.values())
            if n >= MIN_DEPT_N and k / n >= CLEAR:
                mapping[dept] = ward
            else:
                out.ask(f"ward_departments.{dept}", f"{dept} {n}건, 가장 많은 병동 {ward} {k / n:.0%}", ward)
        out.values["dept_ward"] = mapping
        out.evidence["dept_ward"] = {d: dict(c) for d, c in counts.items()}
    else:
        out.ask("assignment·ward_confirmation·admission_window·ward_departments",
                f"배정 기록 {len(commits)}건으로 적다(최소 {MIN_N})")

    # 퇴실 기준 시각: 퇴실 중앙값을 올림한 정시 (규정 시각 조금 앞에 몰린다)
    if len(deps) >= MIN_N:
        med = statistics.median(e.observed_at.hour * 60 + e.observed_at.minute for e in deps)
        due = _hhmm(math.ceil(med / 60) * 60)
        out.values["departure_due_at"] = due
        out.evidence["departure_due_at"] = {"n": len(deps), "median": _hhmm(med)}
        lounge = sum(1 for c in by_case.values() if EventType.LOUNGE_ACCEPTED in c)
        out.values["lounge_enabled"] = lounge >= 2
        out.evidence["lounge_enabled"] = {"n": len(deps), "lounge_events": lounge}
        prep = sum(1 for c in by_case.values() if EventType.PATIENT_DEPARTED in c and EventType.BED_PREP_DONE in c)
        share = prep / len(deps)
        out.evidence["cleaning_split"] = {"n": len(deps), "bed_prep_share": round(share, 3)}
        if share >= CLEAR or share <= 1 - CLEAR:
            out.values["cleaning_split"] = share >= CLEAR
        else:
            out.ask("cleaning.split", f"침상 정리 기록이 퇴실의 {share:.0%}에만 있다", share >= 0.5)
    else:
        out.ask("departure_due_at·lounge·cleaning.split", f"퇴실 기록 {len(deps)}건으로 적다(최소 {MIN_N})")
    for item, why in ALWAYS_ASK:
        out.ask(item, why)
    return out


__all__ = ["OnboardingResult", "infer_profile"]
