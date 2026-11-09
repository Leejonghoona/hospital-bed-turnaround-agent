"""실행 상태 안전 지표 (DECISIONS "안전 지표 S1·S2를 시스템 실행 상태로 재기", 2026-11-09).

생성기가 만든 기록이 아니라 시스템(사가, 게이트웨이)이 실제로 낸 이벤트를 검사한다.
- S1: 게이트웨이가 실행한 배정(확정, 직접 수정)이 배정 기준 검사기를 통과하는가. 이중 배정이 없는가.
  감염관리 예외 승인으로 실행한 직접 수정(GW-10)은 분모에서 뺀다(사람이 책임지고 넘은 경우).
- S2: 격리 퇴실마다 시스템의 청소 요청이 종말소독이고 환기 대기·아포 소독 표시를 담는가.
  빈 병상 전환과 매칭 확정이 종말소독 완료 뒤인가.
"""

from __future__ import annotations

from datetime import time
from typing import Iterable

from turnaround.events import Event, EventType
from turnaround.rules import vacancy_floor
from turnaround.saga import fold

from .invariants import Violation, check_assignment, zero_event_upper_bound

_AEROSOL = {"droplet", "airborne"}


def _row(violations: list[Violation], n: int) -> dict:
    v = len(violations)
    return {"violations": v, "denominator": n,
            "upper_bound_95": round(zero_event_upper_bound(n), 5) if v == 0 and n else None,
            "codes": sorted({x.code for x in violations})}


def _excepted_cases(events: list[Event]) -> set[str]:
    return {e.case_id for e in events if e.type is EventType.GATEWAY_DECISION and e.payload.get("step") == "override"
            and e.payload.get("decision") == "applied" and e.payload.get("safety_exception")}


def _s1(gw, events: list[Event]) -> dict:
    excepted = _excepted_cases(events)
    commits = [i for i, e in enumerate(events) if e.type is EventType.MATCH_COMMITTED and e.source == "gateway"
               and e.case_id not in excepted]
    found: list[Violation] = []
    for i in commits:
        upto = events[: i + 1]
        assigned = gw._assignments(upto)
        if len(set(assigned.values())) != len(assigned):
            found.append(Violation("double_booking", "S1", f"이중 배정 (이벤트 {events[i].seq})"))
        excepted_beds = {b for c, b in assigned.items() if c in excepted}
        occ = {b: p for b, p in gw._occupants(upto).items() if b not in excepted_beds}
        room = gw.room_of.get(events[i].payload.get("bed_id") or "", "")
        # 이번 확정 병상의 병실이 들어간 위반만 센다 (병상 ID에 병실 ID가 들어 있다. 앞선 확정의 위반을 거듭 세지 않는다)
        found += [v for v in check_assignment(gw.rooms, gw.beds, occ) if room and room in v.detail]
    return _row(found, len(commits))


def _first(events: list[Event], etype: EventType, case: str) -> Event | None:
    return next((e for e in events if e.type is etype and e.case_id == case), None)


def _s2(gw, events: list[Event]) -> dict:
    s = fold(events)
    found: list[Violation] = []
    n = 0
    for d in s.discharges.values():
        departed = d.seen(EventType.PATIENT_DEPARTED)
        if not d.isolation or departed is None or not d.bed_id:
            continue
        n += 1
        # 마지막 청소 요청을 본다: 퇴실 뒤 격리 오더가 오면 종말소독으로 올린 요청이 뒤에 붙는다
        reqs = [e for e in events if e.type is EventType.CLEANING_REQUEST_ISSUED and e.case_id == d.id
                and e.source == "saga"]
        req = reqs[-1] if reqs else None
        if req is None or req.payload.get("cleaning_type") != "terminal":
            found.append(Violation("terminal_cleaning_missing", "S2", f"{d.bed_id}: 격리 퇴실 청소 요청이 종말소독 아님"))
            continue
        if _AEROSOL & set(d.precautions) and not req.payload.get("aerosol_wait_min"):
            found.append(Violation("aerosol_wait_missing", "S2", f"{d.bed_id}: 환기 대기 표시 없음"))
        if (req.payload.get("aerosol_wait_min") or 0) < vacancy_floor(d.organism):
            found.append(Violation("vacancy_floor_short", "S2", f"{d.bed_id}: {d.organism} 퇴실 비움 시간이 지침 하한보다 짧음"))
        if d.organism == "CDIFF" and not req.payload.get("sporicidal"):
            found.append(Violation("sporicidal_missing", "S2", f"{d.bed_id}: 아포 소독 표시 없음"))
        done = next((e for e in events if e.type is EventType.CLEANING_DONE and e.case_id == d.id
                     and e.observed_at >= req.occurred_at
                     and e.payload.get("cleaning_type") != "general"), None)  # 종말소독 요청 뒤, 일반 청소 표시가 아닌 완료만 (P4)
        done_at = done.observed_at if done else None
        # 종말소독 요청 뒤의 빈 병상 전환만 본다 (격리 정보를 모를 때 낸 전환은 되돌림 이벤트로 철회됨)
        vacant = next((e for e in events if e.type is EventType.BED_MARKED_VACANT and e.case_id == d.id
                       and e.occurred_at >= req.occurred_at), None)
        if vacant is not None and (done_at is None or vacant.occurred_at < done_at):
            found.append(Violation("vacant_before_disinfection", "S2", f"{d.bed_id}: 종말소독 완료 전 빈 병상 전환"))
        preps = [e.observed_at for e in events if e.type is EventType.BED_PREP_DONE and e.case_id == d.id]
        if vacant is not None and preps and done_at is not None and \
                not any(done_at <= t <= vacant.occurred_at for t in preps):
            found.append(Violation("bed_prep_before_terminal", "S2", f"{d.bed_id}: 종말소독 뒤 침상 정리 없이 빈 병상 전환(H34)"))
        commit = next((e for e in events if e.type is EventType.MATCH_COMMITTED and e.source == "gateway"
                       and e.payload.get("bed_id") == d.bed_id and e.observed_at >= departed), None)
        if commit is not None and (done_at is None or commit.observed_at < done_at) \
                and commit.case_id not in _excepted_cases(events):
            found.append(Violation("commit_before_disinfection", "S2", f"{d.bed_id}: 종말소독 완료 전 매칭 확정"))
    return _row(found, n)


def _s3(gw, events: list[Event]) -> dict:
    """S3 입실 가능 조건 (청소 두 갈래, 감염관리 검수 P5): 침상 정리 사건이 있는 퇴실에서 빈 병상 전환은
    청소 완료 뒤의 침상 정리(재설치) 완료 뒤여야 한다. 분모 = 침상 정리 요청이 나간 퇴실."""
    found: list[Violation] = []
    cases = {e.case_id for e in events if e.type is EventType.BED_PREP_REQUESTED}
    for c in sorted(cases):
        vacant = [e.occurred_at for e in events if e.type is EventType.BED_MARKED_VACANT and e.case_id == c]
        if not vacant:
            continue
        v = vacant[-1]
        cleans = [e.observed_at for e in events if e.type is EventType.CLEANING_DONE and e.case_id == c and e.observed_at <= v]
        preps = [e.observed_at for e in events if e.type is EventType.BED_PREP_DONE and e.case_id == c and e.observed_at <= v]
        if not cleans or not any(t >= min(cleans) for t in preps):
            found.append(Violation("vacant_without_bed_prep_after_cleaning", "S3", f"{c}: 청소 뒤 침상 정리 없이 빈 병상 전환"))
    return _row(found, len(cases))


def executed_safety(gw, events: list[Event]) -> dict:
    return {"source": "system_executed", "S1": _s1(gw, events), "S2": _s2(gw, events), "S3": _s3(gw, events)}


def executed_safety_sweep(seeds: Iterable[int], day: int = 1, until: time = time(23, 45)) -> dict:
    """데모 하루 재생을 시드마다 돌려 실행 상태 안전 지표를 합친다. 하루 재생이 1분쯤 걸린다."""
    from turnaround.ui.demo import build_demo
    seeds = list(seeds)
    tot = {"S1": [0, 0, set()], "S2": [0, 0, set()]}
    for seed in seeds:
        st = build_demo(seed=seed, day=day, until=until, via_fhir=False)
        r = executed_safety(st.gw, st.store.read())
        for k in tot:
            tot[k][0] += r[k]["violations"]
            tot[k][1] += r[k]["denominator"]
            tot[k][2] |= set(r[k]["codes"])
    out: dict = {"source": "system_executed", "seeds": [seeds[0], seeds[-1]] if seeds else [], "day": day,
                 "until": until.isoformat(timespec="minutes")}
    for k, (v, n, codes) in tot.items():
        out[k] = {"violations": v, "denominator": n,
                  "upper_bound_95": round(zero_event_upper_bound(n), 5) if v == 0 and n else None,
                  "codes": sorted(codes)}
    return out


__all__ = ["executed_safety", "executed_safety_sweep"]
