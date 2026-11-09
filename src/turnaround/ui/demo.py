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


def build_demo(seed: int = 42, day: int = 2, until: time = time(9, 30), via_fhir: bool = True, llm=None,
               policy=None, step_min: int = 15, mode: str = "explain", config=None, reactive=None,
               reviewer=None, past_admissions: bool = True, mapped=None) -> DemoState:
    """reactive(ReactiveEffects)를 주면 에이전트 행동이 그 뒤 퇴원 시각을 바꾸는 반응형 재생 (로드맵 07 M2)."""
    h = generate(seed=seed, config=config)
    if mapped is not None:  # 병원 내보내기를 매핑한 결과(병상 구조·사건)로 재생 (로드맵 07 M6)
        h = h.model_copy(update={"wards": mapped.wards, "rooms": mapped.rooms, "beds": mapped.beds})
    if not past_admissions:  # 지난 날의 입원은 이미 입실했다고 본다 (그날과 그 뒤 입원만 재생)
        h = h.model_copy(update={"admissions": tuple(a for a in h.admissions if a.day >= day)})
    events = hospital_to_events(h, seed=seed) if mapped is None else list(mapped.events)
    entries = 0
    if via_fhir:
        bundle = events_to_bundle(events)
        entries = len(bundle["entry"])
        events = bundle_to_events(bundle)
    events = sorted(events, key=lambda e: e.observed_at)
    store = EventStore(":memory:")
    gw = Gateway(store, h.rooms, h.beds, policy or load_policy())
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
