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
    def __init__(self, gw, llm: LLM, policy=None):
        self.gw, self.llm = gw, llm
        self.policy = policy or gw.policy
        self.records: list[str] = []  # LLM에 넣은 입력 (감사·시험용)

    def open_exceptions(self, events: list[Event]) -> list[Event]:
        done = {e.payload.get("exception_id") for e in events if e.type is EventType.EXCEPTION_RESOLVED}
        return [e for e in events if e.type is EventType.EXCEPTION_RAISED and e.payload["exception_id"] not in done]

    def _input(self, ex: Event, events: list[Event], now: datetime) -> dict:
        rule = self.policy.exception_actions.get(ex.payload["trigger"])
        allowed = list(rule.allowed) if rule else []
        attempts = sum(1 for e in events if e.type is EventType.ACTION_PROPOSED
                       and e.payload.get("exception_id") == ex.payload["exception_id"])
        deadline = datetime.fromisoformat(ex.payload["deadline"])
        return {"exception": {"trigger": ex.payload["trigger"], "stage": ex.payload.get("stage"),
                              "elapsed_min": int((now - ex.observed_at).total_seconds() // 60),
                              "deadline_in_min": int((deadline - now).total_seconds() // 60), "attempts": attempts},
                "allowed_actions": [{"id": a, "autonomy": self.policy.autonomy.get(a, "human")} for a in allowed]}

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
        waiting = {e.payload.get("exception_id"): datetime.fromisoformat(e.payload["at"]) for e in events
                   if e.type is EventType.RECHECK_SCHEDULED}
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
