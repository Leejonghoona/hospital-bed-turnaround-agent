"""계획 에이전트 (AGT-3, AGT-4, AGT-7).

에이전트는 도구가 거른 후보 중에서 고르고, 재매칭 여부를 판단하고, 검토 카드 설명을 쓴다.
실행 권한은 없다. 결과는 게이트웨이에 제안으로만 낸다.
LLM이 없거나 장애이거나 후보 밖을 고르면 규칙 모드(도구 1순위)로 내려간다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from turnaround.events.models import EventType
from turnaround.gateway import Decision, Gateway, Proposal
from turnaround.saga import fold

from .llm import LLM, LLMError
from .tools import Candidate, PlanningTools

SYSTEM = (
    "너는 병상 배정 계획 에이전트다. 후보 병상은 결정적 도구가 안전 기준으로 이미 걸렀다. "
    "목표 우선순위: 1 안전(도구가 보장) 2 되돌릴 수 없는 일 최소 3 임상 우선 4 병상 공백 최소 "
    "5 선호와 운영 6 변동 최소. 후보 목록 밖의 병상은 고르지 않는다. "
    '출력: {"bed_id": "<후보의 bed_id 또는 null>", "rationale": "<한국어 한두 문장>"}'
)


@dataclass
class PlanRecord:
    case: str
    mode: str  # agent | rule | fallback
    bed_id: str | None
    candidates: list[str] = field(default_factory=list)
    llm_input: str = ""  # 구조 검사용 (자유 서술이 없는지, SEC-3)


class PlanningAgent:
    def __init__(self, gateway: Gateway, wards, llm: LLM | None = None, mode: str = "agent",
                 score_overrides: dict[str, float] | None = None, top_k: int = 5):
        self.gw = gateway
        self.tools = PlanningTools(gateway, wards, score_overrides)
        self.llm, self.mode, self.top_k = llm, mode, top_k
        self.records: list[PlanRecord] = []

    # ---------- 선택 ----------

    def _context(self, case: str, cands: list[Candidate], now: datetime, events) -> dict:
        p, payload = self.tools.patient(case, events)
        room = lambda b: self.gw.room_by_id[self.gw.room_of[b]]  # noqa: E731
        return {
            "case": case,
            # 구조화 필드만 넣는다. 전달사항·메모 원문은 넣지 않는다 (SEC-3)
            "patient": {"gender": p.gender.value if p.gender else None, "age": p.age, "department": p.department,
                        "precautions": [x.value for x in p.precautions], "organism": p.organism,
                        "from_icu": bool(payload.get("from_icu")),
                        "single_room_reason": payload.get("single_room_reason")},
            "candidates": [{"bed_id": c.bed_id, "room_type": room(c.bed_id).room_type.value,
                            "ward": room(c.bed_id).ward_id, "score": c.score,
                            "ready_in_min": int((c.ready_at - now).total_seconds() // 60), "parts": c.parts}
                           for c in cands[: self.top_k]],
        }

    def choose(self, case: str, cands: list[Candidate], now: datetime, events) -> tuple[Candidate | None, str, str]:
        """(선택 후보, 근거, 모드)."""
        rule = cands[0] if cands else None
        rule_text = f"규칙 모드: 점수 1순위 {rule.bed_id} ({rule.score:.1f})" if rule else "조건에 맞는 병상 없음"
        if self.mode == "rule" or self.llm is None or not cands:
            self.records.append(PlanRecord(case, "rule", rule.bed_id if rule else None, [c.bed_id for c in cands]))
            return rule, rule_text, "rule"
        ctx = json.dumps(self._context(case, cands, now, events), ensure_ascii=False)
        try:
            out = self.llm.complete_json("choose_bed", SYSTEM, ctx)
            pick = out.get("bed_id")
            chosen = next((c for c in cands[: self.top_k] if c.bed_id == pick), None)
            if chosen is None:
                raise LLMError(f"후보 밖 선택: {pick}")
            text = str(out.get("rationale") or "")[:300]
            self.records.append(PlanRecord(case, "agent", chosen.bed_id, [c.bed_id for c in cands], ctx))
            return chosen, text, "agent"
        except (LLMError, KeyError, TypeError, ValueError) as e:
            self.records.append(PlanRecord(case, "fallback", rule.bed_id, [c.bed_id for c in cands], ctx))
            return rule, f"{rule_text} (LLM 대체: {str(e)[:80]})", "fallback"

    # ---------- 제안 ----------

    def _pid(self, case: str, events) -> str:
        n = sum(1 for e in events if e.type is EventType.PROPOSAL_RECEIVED and e.payload.get("case") == case
                and str(e.payload.get("proposal_id", "")).startswith("ag:"))
        return f"ag:{case}:{n + 1}"

    def _proposal(self, case: str, now: datetime, events, cands: list[Candidate]) -> Proposal:
        chosen, text, _ = self.choose(case, cands, now, events)
        if chosen is None:
            return Proposal(proposal_id=self._pid(case, events), case=case, bed_id=None,
                            basis_seq=self.gw.store.last_seq(), rationale=text)
        # 같은 방의 같은 조건 병상은 대안이 아니다: 방마다 최고 점수 병상만 비교한다
        per_room: dict[str, Candidate] = {}
        for c in cands:
            per_room.setdefault(self.gw.room_of[c.bed_id], c)
        others = [c for c in per_room.values() if self.gw.room_of[c.bed_id] != self.gw.room_of[chosen.bed_id]]
        alts = others[:2]
        return Proposal(proposal_id=self._pid(case, events), case=case, bed_id=chosen.bed_id,
                        basis_seq=self.gw.store.last_seq(), discharge_id=chosen.discharge_id,
                        scores={c.bed_id: c.score for c in [chosen] + alts},
                        alternatives=[c.bed_id for c in alts], rationale=text)

    def plan(self, case: str, now: datetime) -> Proposal:
        events = self.tools.events()
        return self._proposal(case, now, events, self.tools.candidates(case, now, events))

    def step(self, now: datetime) -> list[Decision]:
        """한 주기: 대기 건마다 제안, 청소 지연 사전 매칭은 재매칭 판단."""
        out = []
        for case in self.tools.pending_admissions(self.tools.events()):
            out.append(self.gw.submit(self.plan(case, now), now))
        out += self._rematch(now)
        return out

    def _rematch(self, now: datetime) -> list[Decision]:
        events = self.tools.events()
        s = fold(events)
        live = {p["case"] for p in self.tools.live_proposals(events).values()}
        overdue = {e.case_id for e in events if e.type is EventType.READY_TIME_RECOMPUTED
                   and e.payload.get("reason") == "cleaning_overdue"}
        out = []
        for m in sorted(s.matches.values(), key=lambda x: x.admission_id):
            if not m.active or m.discharge_id not in overdue or m.admission_id in live:
                continue
            if EventType.PATIENT_CONTACTED in m.facts:
                continue  # 목표 2항: 연락한 매칭은 바꾸지 않는다 (사람에게)
            cands = self.tools.candidates(m.admission_id, now, events)
            cur = next((c for c in cands if c.bed_id == m.bed_id), None)
            best = cands[0] if cands else None
            if best is None or best.bed_id == m.bed_id:
                continue
            if cur is not None and best.score - cur.score < self.gw.policy.scoring.rematch_gain_min:
                continue  # 목표 6항: 이득이 기준값보다 작으면 그대로 둔다
            prop = self._proposal(m.admission_id, now, events, [best] + [c for c in cands if c is not best])
            out.append(self.gw.submit(prop, now))
        return out
