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

from turnaround import tracing
from turnaround.events import Event, EventType

from .llm import LLM, LLMError

# v1: M5 측정(2026-11-09~10)에 쓴 프롬프트. 재현용으로 남긴다
SYSTEM_V1 = ("너는 병상 운영 예외 하나에 대해 허용 목록 중 행동 하나를 고른다. 숫자와 병상은 고르지 않는다. "
             "되돌릴 수 없는 일(환자 연락, 공식 확정, 안전 예외 승인)은 사람에게 올린다. "
             "우선순위: 1 안전 2 되돌릴 수 없는 일 최소 3 임상 우선순위 4 병상 공백 최소 5 선호 6 변동 최소. "
             'JSON 하나로 답한다: {"action": "...", "params": {}, "rationale": "..."}')
# v2 (사후 변경, M5 결과를 본 뒤 고침. 측정 뒤 고친 것이라 v1 결과와 견주는 재측정은 사후 결과로 표시한다):
# M5에서 세 모델 공통 금지가 연락 뒤 자동 재매칭·재계획이었고, 통지 근거 실패가 기준을 넘었다.
# 단계 규칙과 올림 역할, 근거 인용을 명시한다. 같은 내용을 게이트웨이가 코드로도 막는다(단계 잠금).
SYSTEM = ("너는 병상 운영 예외 하나에 대해 허용 목록 중 행동 하나를 고른다. 숫자와 병상은 고르지 않는다. "
          "허용 목록 밖의 행동은 고르지 않는다. 환자 연락과 안전 예외 승인은 사람만 한다. "
          "단계 규칙: case_state의 contacted나 committed가 참이면(연락·확정 뒤) 매칭을 스스로 바꾸는 행동"
          "(rematch_now, replan_now)을 고르지 않는다. 다시 찾아야 하면 propose_rematch(사람 승인)나 escalate를 고른다. "
          "입실 뒤에는 escalate만 고른다. 같은 알림을 이미 한 번 했으면(attempts가 1 이상) 같은 알림을 되풀이하지 말고 "
          "다음 단계(재연락, 추천안, 올림)를 고른다. 마감(deadline_in_min)이 0 이하이면 기다리지 않는다. "
          "격리 퇴실 환자나 이동 보조가 필요한 환자에게 라운지를 제안하지 않는다. "
          "escalate를 고르면 params.role에 받는 역할(bed_manager, ward, ipc 중 하나)을 반드시 넣는다. "
          "rationale에는 판단 근거가 된 case_state·tool_outputs의 사실을 한두 개 그대로 적는다. "
          "우선순위: 1 안전 2 되돌릴 수 없는 일 최소 3 임상 우선순위 4 병상 공백 최소 5 선호 6 변동 최소. "
          'JSON 하나로 답한다: {"action": "...", "params": {}, "rationale": "..."}')

# 제한된 도구 호출 루프 (DECISIONS 2026-11-12). 기본값은 위의 단일 호출이고, ExceptionBrain(tool_loop=True)일 때만 쓴다.
# 도구는 읽기 전용이며 결과는 `_tool_outputs`가 이미 계산한 값을 나눠 보여 준다(새 계산은 사례 최근 사건뿐).
MAX_TOOL_STEPS = 3
TOOLS = {
    "rematch_candidates": "다른 후보 병상 수와 첫 후보(병실 종류, 준비까지 분), 남은 음압 병상 수",
    "ready_eta": "병상 준비 예상(P50·P90 분), 오늘 퇴원 중 가장 빨리 준비되는 병상, 보호자 도착 예상, 라운지 여부",
    "case_history": "이 환자 사례의 최근 사건 종류와 몇 분 전인지 (최근 8건, 자유 서술 없음)",
}
_TOOL_KEYS = {
    "rematch_candidates": ("rematch_candidates", "best_candidate_room_type", "best_ready_in_min",
                           "negative_pressure_free"),
    "ready_eta": ("ready_p50_min", "ready_p90_min", "earliest_ready_bed", "earliest_ready_p50_min",
                  "earliest_ready_p90_min", "guardian_eta_in_min", "lounge_available"),
}
SYSTEM_LOOP = SYSTEM.replace(
    'JSON 하나로 답한다: {"action": "...", "params": {}, "rationale": "..."}',
    "tool_outputs는 처음에 주지 않는다. 필요하면 tools 목록의 도구를 불러 값을 받는다(읽기 전용). "
    f"도구 호출과 최종 답을 합쳐 최대 {MAX_TOOL_STEPS}단계이고, 마지막 단계에서는 반드시 행동을 고른다. "
    "받은 결과는 tool_results에 쌓인다. 단계마다 JSON 하나로 답한다: "
    '도구를 부르면 {"tool": "...", "args": {}}, 행동을 고르면 {"action": "...", "params": {}, "rationale": "..."}')


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
    basis_input: dict[str, Any] = Field(default_factory=dict)  # 머리에 준 입력 (게이트웨이 상황별 금지 검사에 같은 값을 쓴다)
    # 도구 호출 루프에서 부른 도구와 결과 요약 [{step, tool, args, result}]. 단일 호출이면 비어 있다
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    loop_status: str | None = None  # 루프일 때만: ok | step_limit | unknown_tool | format_error | llm_error


class ExceptionBrain:
    def __init__(self, gw, llm: LLM, policy=None, wards=(), data_class: str | None = None,
                 shadow: bool = False, shadow_log: list | None = None, tool_loop: bool = False):
        from .llm import bind_data_class
        self.gw, self.llm = gw, bind_data_class(llm, data_class)  # 외부 모델 관문 (기본 병원 데이터)
        # 그림자 모드 (docs/검토/LLM운영_시험설계.md 3절): 모델 제안은 기록만 하고, 실행은 규칙표가 한다
        self.shadow = shadow
        self.shadow_log = shadow_log if shadow_log is not None else []
        self.wards = tuple(wards)
        # 제한된 도구 호출 루프 (DECISIONS 2026-11-12). 기본 False = 기존 단일 호출 (인수 시험 키트와 같은 방식)
        self.tool_loop = tool_loop
        self.policy = policy or gw.policy
        self.records: list[str] = []  # LLM에 넣은 입력 (감사·시험용)
        # 게이트웨이가 상황별 금지를 판정할 때 머리가 낸 입력을 믿지 않고 이 함수로 이벤트에서 다시 계산한다 (최고점 루프 1회차)
        gw._input_builder = self

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
            precs = set(payload.get("precautions") or []) | {
                p for e in events if e.type is EventType.ISOLATION_ORDER_ADDED and e.case_id == adm
                for p in (e.payload.get("precautions") or [])}
            if "airborne" in precs:
                st.setdefault("need", "negative_pressure")
        # 감염관리실 근무 여부는 운영 달력으로 계산한다(근무 외에는 감염내과 당직 대행, operations.yaml)
        st["ipc_on_duty"] = bool(self.gw.calendar.in_office(now))
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
        if trig == "guardian_delay_same_day":  # 라운지는 이 예외에서만 쓰는 값 (정답 세트 어휘와 같게)
            out["lounge_available"] = bool(self.policy.hospital_profile.guardian.lounge_enabled)
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
                out["earliest_ready_p90_min"] = mins(tools.ready_at(ready[0][1], events, now, q="p90")[0]) if ready else None
                out["negative_pressure_free"] = free_np
        if "guardian_wait_min" in ctx:
            out["guardian_eta_in_min"] = ctx["guardian_wait_min"]
        if trig in ("cleaning_overdue", "guardian_delay_same_day", "sequence_stalled") and ex.payload.get("bed_id"):
            out["ready_p50_min"] = mins(tools.ready_at(ex.payload["bed_id"], events, now)[0])
            out["ready_p90_min"] = mins(tools.ready_at(ex.payload["bed_id"], events, now, q="p90")[0])
        return out

    # ---- 제한된 도구 호출 루프 (DECISIONS 2026-11-12) ----

    def _loop_active(self) -> bool:
        # 결정적 규칙표(가짜 LLM)는 모든 도구 값을 이미 본 결정표라 도구를 부르지 않는 1단계로 둔다: 입력·결과가 단일 호출과 같다
        return self.tool_loop and not (self.llm.name == "fake" and not _scripted(self.llm))

    def _tool_results(self, ctx: dict, ex: Event, events: list[Event], now: datetime) -> dict:
        """도구 이름 → 결과. `_tool_outputs`가 계산한 값을 나눠 쓴다 (읽기 전용, 이벤트를 쓰지 않음)."""
        to = ctx.get("tool_outputs") or {}
        res = {name: {k: to[k] for k in keys if k in to} for name, keys in _TOOL_KEYS.items()}
        hist = [e for e in events if e.case_id == ex.case_id and e.observed_at <= now][-8:]
        res["case_history"] = {"recent_events": [
            {"type": e.type.value, "min_ago": max(0, int((now - e.observed_at).total_seconds() // 60))} for e in hist]}
        return res

    def _run_loop(self, ctx: dict, ex: Event, events: list[Event], now: datetime) -> tuple[dict | None, list, str]:
        """최대 MAX_TOOL_STEPS 단계. 행동 답(dict), 도구 호출 기록, 상태를 돌려준다. 행동이 없으면 부르는 쪽이 대체 경로로 간다.
        LLMError는 그대로 올린다(부르는 쪽이 대체 경로·그림자 기록으로 처리)."""
        results = self._tool_results(ctx, ex, events, now)
        shown = {k: ctx[k] for k in ("exception", "case_state", "allowed_actions")}
        shown["tools"] = [{"name": n, "description": d} for n, d in TOOLS.items()]
        calls: list[dict] = []
        for step in range(1, MAX_TOOL_STEPS + 1):
            user = json.dumps({**shown, "tool_results": [{k: c[k] for k in ("tool", "args", "result")} for c in calls],
                               "step": step, "max_steps": MAX_TOOL_STEPS}, ensure_ascii=False)
            self.records.append(user)
            out = self.llm.complete_json("choose_exception_step", SYSTEM_LOOP, user)
            if not isinstance(out, dict) or ("tool" in out) == ("action" in out):
                return None, calls, "format_error"
            if "action" in out:
                if not isinstance(out["action"], str) or not out["action"]:
                    return None, calls, "format_error"
                return out, calls, "ok"
            name, args = out.get("tool"), out.get("args", {})
            if not isinstance(name, str) or name not in TOOLS:
                return None, calls, "unknown_tool"
            if not isinstance(args, dict):
                return None, calls, "format_error"
            with tracing.span("agent.brain.tool", case_id=ex.case_id, **{
                    "agent.exception_id": ex.payload["exception_id"], "agent.tool": name, "agent.tool.step": step}):
                calls.append({"step": step, "tool": name, "args": dict(args), "result": results[name]})
        return None, calls, "step_limit"

    def _choose_loop(self, base: dict, ctx: dict, ex: Event, events: list[Event], now: datetime) -> ActionChoice:
        try:
            out, calls, status = self._run_loop(ctx, ex, events, now)
        except LLMError:
            out, calls, status = None, [], "llm_error"
        if out is not None:
            try:
                return ActionChoice(**base, action=str(out["action"]), params=dict(out.get("params") or {}),
                                    rationale=str(out.get("rationale") or "")[:300], basis_input=ctx, mode="llm",
                                    tool_calls=calls, loop_status=status)
            except (KeyError, TypeError, ValueError):
                status = "format_error"
        return self.fallback(base).model_copy(update={"tool_calls": calls, "loop_status": status})

    def choose(self, ex: Event, events: list[Event], now: datetime) -> ActionChoice:
        ctx = self._input(ex, events, now)
        user = json.dumps(ctx, ensure_ascii=False)
        self.records.append(user)
        base = dict(exception_id=ex.payload["exception_id"], trigger=ex.payload["trigger"], case=ex.case_id,
                    basis_seq=max((e.seq or 0) for e in events))
        if self.shadow:
            return self._shadow_choose(base, ctx, user, now, ex, events)
        if self._loop_active():
            return self._choose_loop(base, ctx, ex, events, now)
        try:
            out = self.llm.complete_json("choose_exception_action", SYSTEM, user)
            return ActionChoice(**base, action=str(out["action"]), params=dict(out.get("params") or {}),
                                rationale=str(out.get("rationale") or "")[:300], basis_input=ctx,
                                mode="fake" if self.llm.name == "fake" and not _scripted(self.llm) else "llm")
        except (LLMError, KeyError, TypeError, ValueError):
            return self.fallback(base)

    def _shadow_choose(self, base: dict, ctx: dict, user: str, now: datetime,
                       ex: Event | None = None, events: list[Event] | None = None) -> ActionChoice:
        """규칙표(가짜 LLM 결정표)로 실행할 행동을 정하고, 모델 제안은 따로 받아 기록만 한다."""
        import time

        from turnaround.gateway.guards import situational_block

        from .llm import _fake_exception_action
        rule = _fake_exception_action(ctx)
        allowed = {a["id"] for a in ctx.get("allowed_actions", [])}
        from .rule_learning import features
        rec = {"exception_id": base["exception_id"], "trigger": base["trigger"], "case": base["case"],
               "stage": (ctx.get("exception") or {}).get("stage"), "at": now.isoformat(),
               # 규칙표 반영(rule_learning)용: 유형 키, 규칙표 기본값이라 LLM 답을 제안으로 보여 주는 사례 표시
               "features": features(ctx), "shown_to_human": bool(rule.get("default")),
               "rule_action": rule["action"], "model_action": None, "status": "ok", "agree": False,
               "would_block": None, "latency_s": None, "error": None}
        loop = self._loop_active() and ex is not None
        if loop:
            rec["tool_calls"], rec["loop_status"] = [], None
        t0 = time.monotonic()
        try:
            if loop:  # 그림자에서도 루프는 모델 답을 얻는 방식만 바꾼다. 실행은 아래 규칙표 그대로
                out, rec["tool_calls"], rec["loop_status"] = self._run_loop(ctx, ex, events, now)
            else:
                out = self.llm.complete_json("choose_exception_action", SYSTEM, user)
            act = out.get("action") if isinstance(out, dict) else None
            if loop and rec["loop_status"] != "ok":
                rec["status"] = rec["loop_status"]
            elif not isinstance(act, str) or not act:
                rec["status"] = "format_error"
            else:
                rec["model_action"] = act
                if act.split(":", 1)[0] not in allowed:
                    rec["status"] = "out_of_list"
                    rec["would_block"] = "not_allowed"
                else:
                    rec["would_block"] = situational_block(ctx, act)
                    rec["agree"] = act.split(":", 1)[0] == rule["action"]
        except LLMError as e:
            rec["status"], rec["error"] = "llm_error", str(e)[:200]
            if loop:
                rec["loop_status"] = "llm_error"
        rec["latency_s"] = round(time.monotonic() - t0, 3)
        self.shadow_log.append(rec)
        params = dict(rule.get("params") or {})
        if rule.get("default") and rule["action"] == "escalate" and rec["status"] == "ok" and not rec["would_block"]:
            # 규칙표가 정하지 못한 새 상황: LLM 답을 담당자 카드에 참고 의견으로만 붙인다 (실행하지 않음, 규칙표 반영 집계에서 제외)
            params["ai_hint"] = rec["model_action"].split(":", 1)[0]
        return ActionChoice(**base, action=rule["action"], params=params,
                            rationale=str(rule.get("rationale") or "")[:300], basis_input=ctx, mode="fake")

    def fallback(self, base: dict, rejected: tuple[str, str] | None = None) -> ActionChoice:
        # 늦은 격리는 감염관리실(근무 외 감염내과 당직, 워크리스트가 시간대로 바꿈)에 올린다 (2회차 안전 검토)
        params = {"role": "ipc" if base.get("trigger") == "late_isolation" else "bed_manager"}
        if rejected:  # 근거 카드에 거부된 행동과 사유를 남긴다 (2단계 안전 검수 M7)
            params.update(rejected_action=rejected[0], rejected_reason=rejected[1])
        return ActionChoice(**{**base, "action": "escalate", "params": params,
                               "rationale": "대체 경로: 사람에게 올림", "mode": "fallback"})

    def step(self, now: datetime) -> list:
        out = []
        events = self.gw.store.read()
        waiting: dict = {}
        for e in events:  # 마지막으로 잡은 다시 볼 시각
            if e.type is EventType.RECHECK_SCHEDULED:
                waiting[e.payload.get("exception_id")] = datetime.fromisoformat(e.payload["at"])
        decided = {e.payload.get("exception_id") for e in events
                   if e.type in (EventType.ACTION_APPROVED, EventType.ACTION_REJECTED) and e.payload.get("actor")}
        pending = {e.payload.get("exception_id") for e in events if e.type is EventType.ACTION_APPROVAL_REQUESTED}
        for ex in self.open_exceptions(events):
            if waiting.get(ex.payload["exception_id"], now) > now:
                continue  # 기다리기로 한 예외는 다시 볼 시각까지 건너뛴다
            if ex.payload["exception_id"] in pending - decided:
                continue  # 사람 승인을 기다리는 예외는 다시 고르지 않는다 (2단계 안전 검수 M6)
            with tracing.span("agent.brain.choose", case_id=ex.case_id, **{
                    "agent.exception_id": ex.payload["exception_id"], "agent.trigger": ex.payload.get("trigger")}) as h:
                choice = self.choose(ex, events, now)
                h.set(**{"agent.action": choice.action, "agent.mode": choice.mode})
                if choice.loop_status is not None:  # 도구 호출 루프 감사 기록
                    h.set(**{"agent.loop.status": choice.loop_status,
                             "agent.loop.tools": [c["tool"] for c in choice.tool_calls]})
                if self.shadow and self.shadow_log and self.shadow_log[-1]["exception_id"] == choice.exception_id:
                    r = self.shadow_log[-1]
                    h.set(**{"agent.shadow.model_action": r["model_action"] or "", "agent.shadow.status": r["status"],
                             "agent.shadow.agree": r["agree"], "agent.shadow.would_block": r["would_block"] or ""})
                    if r.get("loop_status") is not None:
                        h.set(**{"agent.shadow.loop_status": r["loop_status"],
                                 "agent.shadow.tools": [c["tool"] for c in r["tool_calls"]]})
            d = self.gw.submit_action(choice, now)
            if d.status == "rejected" and d.reasons != ["stale_basis"] and choice.mode != "fallback":
                events = self.gw.store.read()
                d = self.gw.submit_action(self.fallback(choice.model_dump(include={
                    "exception_id", "trigger", "case", "basis_seq"}) | {"basis_seq": max((e.seq or 0) for e in events)},
                    rejected=(choice.action, (d.reasons or ["?"])[0])), now)
            out.append(d)
            events = self.gw.store.read()
        return out


def _scripted(llm) -> bool:
    return type(llm).complete_json is not getattr(__import__("turnaround.agent.llm", fromlist=["FakeLLM"]),
                                                  "FakeLLM").complete_json
