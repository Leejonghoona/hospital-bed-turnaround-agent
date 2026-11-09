"""화면 목업용 하루 재생 (9단계). Streamlit 없이도 돌아가서 테스트할 수 있다.

합성 병원 하나를 FHIR 번들로 내보냈다가 다시 들여와(경계 어댑터 시연) 이벤트를 인지 시각 순서로 흘려 넣고,
사가·계획 에이전트·게이트웨이를 15분마다 돌린다. 사람 응답은 흉내 낸다:
담당 확인은 10분 뒤 승인, 담당 검토는 입원 창구 운영 시간(평일 09–18시)에만 15분에 1건씩 처리,
병동 회신은 20분 뒤 수용, 원무 확정은 회신 10분 뒤.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from turnaround.agent import FakeLLM, PlanningAgent
from turnaround.events import EventStore, EventType, hospital_to_events
from turnaround.fhir import bundle_to_events, events_to_bundle
from turnaround.gateway import Gateway, load_policy
from turnaround.saga import SagaPolicy, SagaRunner, fold
from turnaround.saga.worklist import WorklistRunner, load_operations, open_tasks
from turnaround.sim import generate
from turnaround.sim.models import SyntheticHospital

_M = timedelta(minutes=1)


@dataclass
class DemoState:
    h: SyntheticHospital
    store: EventStore
    gw: Gateway
    agent: PlanningAgent
    now: datetime
    fhir_entries: int = 0
    feed: object = None  # 반응형 재생 공급기 (있을 때만)
    reviewer: object = None  # 사람 응답 모형 (없으면 늘 승인, 로드맵 07 M3)
    log: list[str] = field(default_factory=list)


def resident_census(h, seed: int) -> dict:
    """병상마다 재원 환자(합성). 병실 성별 운영을 따르고 격리 없음. 이름은 병상 ID로 겹치지 않게."""
    import random as _r

    from turnaround.sim.models import Gender, Patient
    rng = _r.Random(f"census-{seed}")
    rooms = {r.id: r for r in h.rooms}
    wards = {w.id: w for w in h.wards}
    out = {}
    for b in h.beds:
        room = rooms[b.room_id]
        g = room.gender or rng.choice(list(Gender))
        out[b.id] = Patient(id=f"SYN-R{b.id}", name=f"재원{b.id[-6:]}", gender=g, age=rng.randint(30, 85),
                            department=rng.choice(wards[b.ward_id].departments))
    return out


def build_demo(seed: int = 42, day: int = 2, until: time = time(9, 30), via_fhir: bool = True, llm=None,
               policy=None, step_min: int = 15, mode: str = "explain", config=None, reactive=None,
               reviewer=None, past_admissions: bool = True, mapped=None, occupancy: str = "sparse") -> DemoState:
    """reactive(ReactiveEffects)를 주면 에이전트 행동이 그 뒤 퇴원 시각을 바꾸는 반응형 재생 (로드맵 07 M2)."""
    h = generate(seed=seed, config=config)
    if mapped is not None:  # 병원 내보내기를 매핑한 결과(병상 구조·사건)로 재생 (로드맵 07 M6)
        h = h.model_copy(update={"wards": mapped.wards, "rooms": mapped.rooms, "beds": mapped.beds})
    if not past_admissions:  # 지난 날의 입원은 이미 입실했다고 본다 (그날과 그 뒤 입원만 재생)
        h = h.model_copy(update={"admissions": tuple(a for a in h.admissions if a.day >= day)})
    census = {}
    if occupancy == "full":
        # 현실 가동률 (병동 검수 1번, 2026-11-10): 병상은 재원 환자로 차 있고 그날·그 뒤 퇴원 병상만 비게 된다.
        # 지난 날 퇴원 병상은 이미 다른 환자가 채웠다고 본다(지난 날 퇴원 사례는 재생에서 뺀다). 상급종합 가동률 93%
        # (국민보건의료실태조사 보도자료 사본) 수준이 되며, 입원은 그날 퇴원 병상으로 들어가야 한다
        h = h.model_copy(update={"discharges": tuple(x for x in h.discharges if x.day >= day)})
        census = resident_census(h, seed)
    events = hospital_to_events(h, seed=seed) if mapped is None else list(mapped.events)
    entries = 0
    if via_fhir:
        bundle = events_to_bundle(events)
        entries = len(bundle["entry"])
        events = bundle_to_events(bundle)
    events = sorted(events, key=lambda e: e.observed_at)
    store = EventStore(":memory:")
    gw = Gateway(store, h.rooms, h.beds, policy or load_policy(), census=census)
    saga = SagaRunner(store, SagaPolicy.for_layout(h.rooms, h.beds) if policy is None
                      else SagaPolicy.from_policy(policy, h.rooms, h.beds))
    agent = PlanningAgent(gw, h.wards, llm=llm or FakeLLM(), mode=mode)
    work = WorklistRunner(store, load_operations())
    base = datetime.combine(h.config.base_date + timedelta(days=day), time())
    t = base - timedelta(hours=8)  # 전날 16:00부터
    end = datetime.combine(base.date(), until)
    i = 0
    st = DemoState(h, store, gw, agent, end, entries)
    st.reviewer = reviewer
    feed = None
    if reactive is not None:
        from turnaround.sim.reactive import ReactiveReplay
        feed = st.feed = ReactiveReplay(h, seed, reactive)
    while t <= end:
        if feed is not None:
            feed.react(store, t)
            feed.deliver(store, t)
        while feed is None and i < len(events) and events[i].observed_at <= t:
            store.append(events[i])
            i += 1
        saga.step(t)
        agent.step(t)
        _humans(st, t)
        work.step(t)
        t += step_min * _M
    return st


_CAL = load_operations().calendar


# 입실 구간의 사람 응답 (08 계획 1단계). 시간은 설계 가정: 원무 연락은 확정 뒤 20분(운영 시간), 병동 준비는 통보 뒤
# 30분, "올려주세요" 뒤 입실 15분(이송 인력 배정은 범위 밖이라 입실 신호만 흉내 낸다)
_CONTACT_AFTER, _WARD_PREP_AFTER, _ARRIVE_AFTER = 20, 30, 15


def _entry_humans(st: DemoState, now: datetime, ev) -> None:
    gw = st.gw
    seen = {(e.case_id, e.type) for e in ev}
    for e in ev:
        case = e.case_id
        if e.type is EventType.MATCH_COMMITTED and e.observed_at + _CONTACT_AFTER * _M <= now and \
                (case, EventType.PATIENT_CONTACTED) not in seen and 7 <= now.hour < 20:  # 주말·휴일은 당직 원무(설계 가정)
            st.store.append(_human_event(case, "patient_contacted", EventType.PATIENT_CONTACTED, now,
                                         bed_id=e.payload.get("bed_id"), by="admission_office",
                                         promised_at=_announce(st, case, now, ev).isoformat()))
            seen.add((case, EventType.PATIENT_CONTACTED))
        if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.observed_at + _WARD_PREP_AFTER * _M <= now and \
                (case, EventType.WARD_PREP_DONE) not in seen:
            st.store.append(_human_event(case, "ward_prep_done", EventType.WARD_PREP_DONE, now, bed_id=e.payload.get("bed_id")))
            seen.add((case, EventType.WARD_PREP_DONE))
        if e.type is EventType.PATIENT_CALL_UP and e.observed_at + _ARRIVE_AFTER * _M <= now and \
                (case, EventType.PATIENT_ARRIVED) not in seen:
            st.store.append(_human_event(case, "patient_arrived", EventType.PATIENT_ARRIVED, now, bed_id=e.payload.get("bed_id")))
            seen.add((case, EventType.PATIENT_ARRIVED))


def _announce(st: DemoState, case: str, now: datetime, ev) -> datetime:
    """원무가 연락 때 환자에게 안내하는 수속 시각 (2단계 현장 검수 H4). 설계 가정: 입원 예정 시각과 연락 1시간 뒤 중
    늦은 쪽, 병상이 아직 비지 않았으면 병원 입원 수속 창 시작보다 이르게 부르지 않는다."""
    req = next((datetime.fromisoformat(e.payload["requested_at"]) for e in ev
                if e.type is EventType.ADMISSION_REQUESTED and e.case_id == case and e.payload.get("requested_at")), now)
    t = max(req, now + timedelta(hours=1))
    from turnaround.saga import fold
    m = fold(ev).matches.get(case)
    ready = False
    if m is not None:
        from turnaround.saga.machine import current_discharge
        d = current_discharge(fold(ev), m)
        ready = d is None or bool(d.seen(EventType.BED_MARKED_VACANT) or d.seen(EventType.BED_READY))
    if not ready:
        h, mi = map(int, st.gw.policy.hospital_profile.admission_window[0].split(":"))
        t = max(t, now.replace(hour=h, minute=mi, second=0, microsecond=0))
    return t.replace(minute=(t.minute // 30) * 30, second=0, microsecond=0) + (timedelta(minutes=30) if t.minute % 30 else
                                                                               timedelta())


def _human_event(case, key, etype, now, **payload):
    from turnaround.events import Event
    return Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=now, observed_at=now, source="human",
                 payload=payload)


def _humans(st: DemoState, now: datetime) -> None:
    ev = st.store.read()
    by = lambda t: {e.payload.get("proposal_id"): e for e in ev if e.type is t}  # noqa: E731
    approved, rejected = by(EventType.MANAGER_APPROVED), by(EventType.REJECTED_AT_PROPOSAL) | by(EventType.REJECTED_AT_COMMIT)
    reviews_left = 1 if _CAL.in_office(now) else 0  # 검토는 운영 시간에 15분에 1건
    overridden = {e.case_id for e in ev if e.type is EventType.MANUAL_OVERRIDE_APPLIED}
    rv = st.reviewer
    for t, wait in ((EventType.MANAGER_CONFIRM_REQUESTED, 10), (EventType.CASE_ROUTED_REVIEW, 30)):
        for pid, e in sorted(by(t).items(), key=lambda kv: kv[1].observed_at):
            if pid in approved or pid in rejected or e.observed_at + wait * _M > now:
                continue
            if e.case_id in overridden:
                continue
            prop = st.gw._proposal(pid, ev)
            if prop is None or prop.bed_id is None:
                continue
            if t is EventType.CASE_ROUTED_REVIEW:
                if reviews_left == 0:
                    continue
                reviews_left -= 1
            if rv is not None:  # 사람 응답 모형: 일부는 그대로 승인하지 않고 다른 병상으로 직접 수정
                reasons = e.payload.get("reasons") or []
                unit = "official_commit" if t is EventType.MANAGER_CONFIRM_REQUESTED else (
                    reasons[0] if len(reasons) == 1 else None)
                alt = next((b for b in prop.alternatives if b != prop.bed_id), None)
                if alt and rv.overrides(pid, unit):
                    d = st.gw.manual_override(prop.case, alt, "bed_manager", "bed_manager", "operational", now)
                    if d.status == "applied":
                        overridden.add(prop.case)
                        continue
            st.gw.manager_approve(pid, now)
    ev = st.store.read()
    if rv is not None:  # 에이전트 확정 뒤 병동 이의 (통보형)
        objected = by(EventType.WARD_OBJECTION_RAISED)
        for pid, e in by(EventType.WARD_ASSIGNMENT_NOTIFIED).items():
            if e.payload.get("committed_by") == "agent" and pid not in objected and \
                    e.observed_at + 10 * _M <= now and rv.objects(pid):
                st.gw.ward_objection(pid, "ward_judgement", now)
        ev = st.store.read()
    _entry_humans(st, now, ev)
    ev = st.store.read()
    replied = by(EventType.WARD_ACCEPTED) | by(EventType.WARD_REJECTED)
    for pid, e in by(EventType.WARD_ACCEPTANCE_ASKED).items():
        if pid not in replied and e.observed_at + 20 * _M <= now:
            st.gw.ward_reply(pid, True, now)
    ev = st.store.read()
    committed = by(EventType.MATCH_COMMITTED)
    for pid, e in by(EventType.WARD_ACCEPTED).items():
        if pid in committed or pid in rejected or e.observed_at + 10 * _M > now:
            continue
        prop = st.gw._proposal(pid, ev)
        if prop is not None:
            st.gw.commit(pid, st.gw.location_version(prop.bed_id), now)


# ---------- 역할별 화면 데이터 ----------

def review_queue(st: DemoState) -> list[dict]:
    ev = st.store.read()
    out = []
    ops = load_operations()
    for tid, t in open_tasks(ev, st.now).items():
        if t.type is not EventType.CASE_ROUTED_REVIEW:
            continue
        prop = st.gw._proposal(t.payload.get("proposal_id"), ev)
        out.append({"case": t.case_id, "bed": t.payload.get("bed_id"), "reasons": t.payload.get("reasons", []),
                    "alternatives": t.payload.get("alternatives", []), "rationale": prop.rationale if prop else "",
                    "scores": prop.scores if prop else {}, "due": ops.due(t).strftime("%H:%M"),
                    "since": t.observed_at.strftime("%H:%M")})
    return out


def ward_requests(st: DemoState) -> list[dict]:
    ev = st.store.read()
    replied = {e.payload.get("proposal_id") for e in ev if e.type in (EventType.WARD_ACCEPTED, EventType.WARD_REJECTED)}
    return [{"case": e.case_id, "bed": e.payload.get("bed_id"), "asked": e.observed_at.strftime("%H:%M")}
            for e in ev if e.type is EventType.WARD_ACCEPTANCE_ASKED and e.payload.get("proposal_id") not in replied]


def vendor_view(st: DemoState) -> list[dict]:
    """청소 협력업체: 병상 번호, 시작 가능 시각, 청소 유형만 (SEC-2). 환자 정보·사망 여부 없음."""
    ev = st.store.read()
    done = {e.case_id for e in ev if e.type is EventType.CLEANING_DONE}
    out = []
    for e in ev:
        if e.type is EventType.CLEANING_REQUEST_ISSUED and e.case_id not in done:
            start = e.occurred_at + e.payload.get("aerosol_wait_min", 0) * _M
            kind = {"terminal": "종말소독", "general": "일반 청소"}[e.payload["cleaning_type"]]
            if e.payload.get("sporicidal"):
                kind += "(아포 대응)"
            out.append({"bed_id": e.payload["bed_id"], "eta": start.strftime("%H:%M"), "cleaning_type": kind})
    return out


def bed_board(st: DemoState) -> list[dict]:
    ev = st.store.read()
    s = fold(ev)
    assigned = st.gw._assignments(ev)
    holder = {b: c for c, b in assigned.items()}
    tags = st.gw.placement_tags("bed_manager")
    room = {r.id: r for r in st.h.rooms}
    rows = []
    for b in st.h.beds:
        d = next((x for x in s.discharges.values() if x.bed_id == b.id and EventType.DISCHARGE_CANCELLED not in x.facts), None)
        status = "빈 병상"
        if d is not None:
            f = d.facts
            status = ("준비 완료" if EventType.BED_READY in f else "청소 중" if EventType.CLEANING_STARTED in f
                      else "청소 대기" if EventType.PATIENT_DEPARTED in f else "퇴원 예정")
        if b.id in holder:
            status += f" → {holder[b.id]} 확정"
        rows.append({"bed": b.id, "ward": b.ward_id, "room_type": room[b.room_id].room_type.value,
                     "status": status, "tag": "직접 수정" if b.id in tags else ""})
    return rows
