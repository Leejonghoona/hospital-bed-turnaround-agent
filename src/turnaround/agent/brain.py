"""예외 판단 머리 (DECISIONS "U8 확장", 설계 개정 4절).

- 감지는 사가가 한다(EXCEPTION_RAISED). 머리는 상태를 갖지 않고 매 주기 열린 예외를 읽는다.
- 머리는 정책의 허용 목록 중 행동 하나를 고른다(LLM, 테스트는 FakeLLM 결정표). 병상과 숫자는 고르지 않는다.
- 검사와 실행은 게이트웨이(`Gateway.submit_action`)가 한다. 실패·거부면 결정적 대체(사람에게 올림).
- LLM 입력은 구조화 값만 넣는다(이름, 등록번호, 자유 서술 원문 없음, SEC-3).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from turnaround.events import Event, EventType

from .llm import LLM, LLMError

SYSTEM = ("너는 병상 운영 예외 하나에 대해 허용 목록 중 행동 하나를 고른다. 숫자와 병상은 고르지 않는다. "
          "되돌릴 수 없는 일(환자 연락, 공식 확정, 안전 예외 승인)은 사람에게 올린다. "
          "우선순위: 1 안전 2 되돌릴 수 없는 일 최소 3 임상 우선순위 4 병상 공백 최소 5 선호 6 변동 최소. "
          'JSON 하나로 답한다: {"action": "...", "params": {}, "rationale": "..."}')


class ActionChoice(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    exception_id: str
    trigger: str
    case: str
    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field(default="", max_length=300)
    basis_seq: int
    mode: str  # llm | fake | fallback


class ExceptionBrain:
    def __init__(self, gw, llm: LLM, policy=None, wards=()):
        self.gw, self.llm = gw, llm
        self.wards = tuple(wards)
        self.policy = policy or gw.policy
        self.records: list[str] = []  # LLM에 넣은 입력 (감사·시험용)

    def open_exceptions(self, events: list[Event]) -> list[Event]:
        done = {e.payload.get("exception_id") for e in events if e.type is EventType.EXCEPTION_RESOLVED}
        return [e for e in events if e.type is EventType.EXCEPTION_RAISED and e.payload["exception_id"] not in done]

    def _input(self, ex: Event, events: list[Event], now: datetime) -> dict:
        rule = self.policy.exception_actions.get(ex.payload["trigger"])
        contacted = bool((ex.payload.get("context") or {}).get("contacted"))
        allowed = rule.allowed_for(ex.payload.get("stage"), contacted) if rule else []  # 단계 잠금 행동은 보여 주지 않는다
        attempts = sum(1 for e in events if e.type is EventType.ACTION_PROPOSED
                       and e.payload.get("exception_id") == ex.payload["exception_id"])
        deadline = datetime.fromisoformat(ex.payload["deadline"])
        return {"exception": {"trigger": ex.payload["trigger"], "stage": ex.payload.get("stage"),
                              "elapsed_min": int((now - ex.observed_at).total_seconds() // 60),
                              "deadline_in_min": int((deadline - now).total_seconds() // 60), "attempts": attempts},
                "case_state": self._case_state(ex, events, now),
                "tool_outputs": self._tool_outputs(ex, events, now),
                "allowed_actions": [{"id": a, "autonomy": self.policy.autonomy.get(a, "human")} for a in allowed]}

    # ---- 입력: 정답 세트와 같은 구조 (측정 조건 = 운영 조건, 2026-11-09). 숫자는 결정적 도구가 계산한다 ----

    def _case_state(self, ex: Event, events: list[Event], now: datetime) -> dict:
        from turnaround.saga import fold
        raw = dict(ex.payload.get("context") or {})
        # 사가 맥락 → 정답 세트 이름 (세트에 없는 내부 키는 카드에만 남기고 LLM 입력에는 넣지 않는다)
        st = {k: v for k, v in raw.items() if k not in (
            "waiting_admissions", "next_admission_waiting", "pending_discharges_today", "scarce_room", "guardian_wait_min")}
        if "waiting_admissions" in raw or "next_admission_waiting" in raw:
            st["waiting_matches"] = len(raw.get("waiting_admissions") or []) or int(bool(raw.get("next_admission_waiting")))
        if "scarce_room" in raw:
            st["precaution"] = "airborne" if raw["scarce_room"] else st.get("precaution", "contact")
        h = now.hour
        st.update(now_local=now.strftime("%H:%M"), shift="day" if 7 <= h < 15 else "evening" if 15 <= h < 23 else "night",
                  day_type="weekend" if now.weekday() >= 5 else "weekday")
        adm = ex.payload.get("admission_id") or (ex.case_id if ex.payload.get("trigger") == "no_bed" else None)
        if adm:
            m = fold(events).matches.get(adm)
            facts = m.facts if m is not None else {}
            st.update(contacted=EventType.PATIENT_CONTACTED in facts, committed=EventType.MATCH_COMMITTED in facts)
            payload = next((e.payload for e in events if e.type is EventType.ADMISSION_REQUESTED
                            and e.case_id == adm), {})
            if payload.get("organism"):
                st.setdefault("organism", payload.get("organism"))
        return st

    def _tool_outputs(self, ex: Event, events: list[Event], now: datetime) -> dict:
        from turnaround.agent.tools import PlanningTools
        from turnaround.sim.models import RoomType
        trig, ctx = ex.payload.get("trigger"), ex.payload.get("context") or {}
        tools = PlanningTools(self.gw, self.wards)
        room_type = lambda b: self.gw.room_by_id[self.gw.room_of[b]].room_type  # noqa: E731
        mins = lambda t: max(0, int((t - now).total_seconds() // 60))  # noqa: E731
        free_np = sum(1 for b in self.gw.beds if room_type(b.id) is RoomType.NEGATIVE_PRESSURE
                      and b.id not in self.gw._occupants(events) and not self.gw._out_of_service(b.id, events))
        out: dict = {}
        adm = ex.payload.get("admission_id") or (ex.case_id if trig == "no_bed" else None)
        waiting = list(ctx.get("waiting_admissions") or [])
        target = adm or (waiting[0] if waiting else None)
        if target:
            cands = tools.candidates(target, now, events)
            out["rematch_candidates"] = len(cands)
            if trig == "late_isolation":
                out["best_candidate_room_type"] = room_type(cands[0].bed_id).value if cands else None
                out["best_ready_in_min"] = mins(cands[0].ready_at) if cands else None
                out["negative_pressure_free"] = free_np
            if trig == "no_bed":
                ready = sorted((tools.ready_at(d["bed_id"], events, now)[0], d["bed_id"])
                               for d in ctx.get("pending_discharges_today") or [])
                out["earliest_ready_bed"] = ready[0][1] if ready else None
                out["earliest_ready_p50_min"] = mins(ready[0][0]) if ready else None
                out["negative_pressure_free"] = free_np
        if "guardian_wait_min" in ctx:
            out["guardian_eta_in_min"] = ctx["guardian_wait_min"]
        if trig in ("cleaning_overdue", "guardian_delay_same_day", "sequence_stalled") and ex.payload.get("bed_id"):
            out["ready_p50_min"] = mins(tools.ready_at(ex.payload["bed_id"], events, now)[0])
        return out

    def choose(self, ex: Event, events: list[Event], now: datetime) -> ActionChoice:
        ctx = self._input(ex, events, now)
        user = json.dumps(ctx, ensure_ascii=False)
        self.records.append(user)
        base = dict(exception_id=ex.payload["exception_id"], trigger=ex.payload["trigger"], case=ex.case_id,
                    basis_seq=max((e.seq or 0) for e in events))
        try:
            out = self.llm.complete_json("choose_exception_action", SYSTEM, user)
            return ActionChoice(**base, action=str(out["action"]), params=dict(out.get("params") or {}),
                                rationale=str(out.get("rationale") or "")[:300],
                                mode="fake" if self.llm.name == "fake" and not _scripted(self.llm) else "llm")
        except (LLMError, KeyError, TypeError, ValueError):
            return self.fallback(base)

    def fallback(self, base: dict) -> ActionChoice:
        return ActionChoice(**{**base, "action": "escalate", "params": {"role": "bed_manager"},
                               "rationale": "대체 경로: 사람에게 올림", "mode": "fallback"})

    def step(self, now: datetime) -> list:
        out = []
        events = self.gw.store.read()
        waiting: dict = {}
        for e in events:  # 마지막으로 잡은 다시 볼 시각
            if e.type is EventType.RECHECK_SCHEDULED:
                waiting[e.payload.get("exception_id")] = datetime.fromisoformat(e.payload["at"])
        for ex in self.open_exceptions(events):
            if waiting.get(ex.payload["exception_id"], now) > now:
                continue  # 기다리기로 한 예외는 다시 볼 시각까지 건너뛴다
            choice = self.choose(ex, events, now)
            d = self.gw.submit_action(choice, now)
            if d.status == "rejected" and d.reasons != ["stale_basis"] and choice.mode != "fallback":
                events = self.gw.store.read()
                d = self.gw.submit_action(self.fallback(choice.model_dump(include={
                    "exception_id", "trigger", "case", "basis_seq"}) | {"basis_seq": max((e.seq or 0) for e in events)}),
                    now)
            out.append(d)
            events = self.gw.store.read()
        return out


def _scripted(llm) -> bool:
    return type(llm).complete_json is not getattr(__import__("turnaround.agent.llm", fromlist=["FakeLLM"]),
                                                  "FakeLLM").complete_json
