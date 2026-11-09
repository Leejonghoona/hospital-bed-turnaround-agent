"""교체 가능한 LLM 인터페이스 (AGT-1).

기본은 API 키 없이 도는 결정적 가짜 LLM이다. 실제 모델은 환경 변수가 모두 있을 때만 붙는다.
- TURNAROUND_LLM=openai | anthropic | xai | local | claudesub(구독 계정 Claude Code, 개인 평가용)
- TURNAROUND_LLM_MODEL=<모델 ID>
- TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only  (외부 API에는 합성 데이터만 보낸다는 확인. 운영 정책은 deny)
- OPENAI_API_KEY 또는 ANTHROPIC_API_KEY

LLM이 하는 일은 판단(후보 중 선택), 설명, 자유 서술 추출, 질문 해석뿐이다. 숫자 계산과 배정 규칙은 도구가 한다.
모든 호출은 JSON 하나를 돌려받는다. 형식이 틀리면 호출한 쪽이 규칙 모드로 내려간다.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import requests


class LLMError(RuntimeError):
    """모델 장애, 형식 오류. 호출한 쪽은 규칙 모드로 내려간다 (NFR-4)."""


class QuotaExceeded(LLMError):
    """계정 잔액·한도 소진 (HTTP 402·429 중 잔액 관련). 평가에서는 오답이 아니라 중단으로 센다."""


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0


class LLM(Protocol):
    name: str
    usage: Usage

    def complete_json(self, task: str, system: str, user: str) -> dict: ...


# ---------- 결정적 가짜 LLM ----------

_TIME = re.compile(r"(오전|오후)?\s*(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분|\s*(반))?")


def _first_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise LLMError("JSON 없음")
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise LLMError(f"JSON 형식 오류: {e}") from e


@dataclass
class FakeLLM:
    """테스트용 결정적 LLM. 같은 입력이면 같은 출력 (NFR-2). 규칙은 단순하게 둔다."""

    name: str = "fake"
    usage: Usage = field(default_factory=Usage)
    fail: bool = False  # 장애 주입 (NFR-4 시험)

    def complete_json(self, task: str, system: str, user: str) -> dict:
        self.usage.calls += 1
        if self.fail:
            raise LLMError("가짜 장애")
        data = _first_json(user)
        if task == "choose_bed":
            cands = data.get("candidates") or []
            if not cands:
                return {"bed_id": None, "rationale": "조건에 맞는 병상 없음"}
            best = max(cands, key=lambda c: (c["score"], -cands.index(c)))
            return {"bed_id": best["bed_id"], "rationale": f"점수 1순위 {best['bed_id']} ({best['score']:.1f})"}
        if task == "choose_exception_action":
            return _fake_exception_action(json.loads(user))
        if task == "explain_choice":
            c = next((x for x in data.get("candidates", []) if x["bed_id"] == data.get("chosen_bed_id")), None)
            if c is None:
                raise LLMError("선택 병상 없음")
            parts = ", ".join(f"{k} {v:+.0f}" for k, v in (c.get("parts") or {}).items()) or "감점 없음"
            return {"rationale": f"{c['bed_id']}: 점수 {c['score']:.1f} ({parts})"}
        if task == "extract":
            return _fake_extract(data.get("text", ""), data.get("reference_time"))
        if task == "parse_question":
            return _fake_parse(data.get("question", ""))
        raise LLMError(f"모르는 작업: {task}")


def _fake_exception_action(ctx: dict) -> dict:
    """가짜 LLM의 예외 행동 결정표 (설계 개정 4.5). 같은 입력에 같은 출력. 실제 모델 평가의 비교선."""
    ex = ctx.get("exception", {})
    allowed = [a["id"] for a in ctx.get("allowed_actions", [])]
    st0 = ctx.get("case_state") or {}
    if ex.get("stage") in ("after_contact", "after_commit", "after_arrival"):
        # 목표 함수 2항: 연락·확정·입실 뒤에는 매칭을 스스로 바꾸지 않는다 (트리거와 상관없이).
        # 다시 찾기는 추천안으로 사람 승인을 받는다 (사용자 검토 XD-028). 입실 뒤는 사람에게
        if "propose_rematch" in allowed and ex.get("stage") != "after_arrival":
            return {"action": "propose_rematch", "params": {}, "rationale": "연락 뒤: 다시 찾은 추천안을 사람이 승인, 재연락은 원무"}
        return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "연락 뒤: 사람에게"}
    if st0.get("scarce_room") and "propose_rematch" in allowed:
        # 음압실처럼 부족한 자원: 자동으로 잡지 않고 추천안으로 승인 (사용자 검토 XD-007·012)
        return {"action": "propose_rematch", "params": {}, "rationale": "부족 자원: 추천안을 사람이 승인"}
    trig, st = ex.get("trigger"), ctx.get("case_state") or {}
    if trig == "cleaning_overdue":
        # 1차: 같은 등급으로 다시 요청(독촉 1회). 2차: 기다리는 입원이 있으면 추천안(사람 승인), 없으면 병동에 올림
        if ex.get("stage") != "second" and "re_request_cleaning" in allowed:
            return {"action": "re_request_cleaning", "params": {}, "rationale": "청소 지연: 같은 등급으로 재요청"}
        if st.get("next_admission_waiting") and "propose_rematch" in allowed:
            return {"action": "propose_rematch", "params": {}, "rationale": "재요청 뒤에도 지연: 다른 병상 추천안을 사람 승인"}
        return {"action": "escalate", "params": {"role": "ward"}, "rationale": "재요청 뒤에도 지연: 병동에 올림"}
    if trig == "no_bed":
        if st.get("from_icu"):
            return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "중환자실 하향: 원무에 근거 카드와 함께"}
        if "wait_for_next_ready" in allowed:
            return {"action": "wait_for_next_ready", "params": {}, "rationale": "곧 준비될 병상: 늦은 퇴원 재촉과 함께 기다림"}
    if trig == "discharge_order_late" and "nudge_order" in allowed:
        return {"action": "nudge_order", "params": {}, "rationale": "처방 없음: 병동·주치의에게 재촉"}
    if trig == "guardian_after_ready":
        if st.get("next_admission_waiting") and "offer_lounge" in allowed:
            return {"action": "offer_lounge", "params": {}, "rationale": "다음 입원 대기: 라운지 제안으로 병상 먼저 비움"}
        if "wait_for_guardian" in allowed:
            return {"action": "wait_for_guardian", "params": {}, "rationale": "기다리는 입원 없음: 보호자 도착까지 둠"}
    if "rematch_now" in allowed:
        return {"action": "rematch_now", "params": {}, "rationale": "연락 전: 규칙 모드로 다시 찾음"}
    return {"action": "escalate", "params": {"role": "bed_manager"}, "rationale": "허용 목록 밖: 사람에게"}


def _fake_extract(text: str, ref: str | None) -> dict:
    out: dict = {}
    m = _TIME.search(text)
    if m and ("도착" in text or "온다" in text or "오신다" in text):
        hh = int(m.group(2))
        if m.group(1) == "오후" and hh < 12 or (m.group(1) is None and 1 <= hh <= 7):
            hh += 12  # "2시 도착"은 오후로 본다 (병원 낮 시간 가정)
        mm = 30 if m.group(4) else int(m.group(3) or 0)
        out["guardian_eta"] = f"{hh:02d}:{mm:02d}"
    for code, words in (("guardian", ("보호자가", "출발이 늦")), ("billing", ("수납", "원무과", "정산")),
                        ("medication", ("약",)), ("transport", ("구급차", "이송차", "택시"))):
        if any(w in text for w in words) and ("늦" in text or "지연" in text or "대기" in text or "밀림" in text):
            out.setdefault("delay_reason", code)
    if re.search(r"휠체어|부축", text):
        out["mobility_aid"] = True
    if re.search(r"1인실\s*(희망|원)", text):
        out["single_room_wish"] = True
    return out


def _fake_parse(q: str) -> dict:
    case = re.search(r"(?<![A-Za-z0-9])([A-Z]{1,3}-?[A-Z]?\d{1,4})(?![A-Za-z0-9])", q)  # 한글 조사 앞도 잡는다
    if "수술" in q and "퇴원" in q:
        return {"intent": "discharges_with_attending_surgery", "day": "tomorrow" if "내일" in q else "today",
                "period": "am" if "오전" in q else "any"}
    if "재입원" in q:
        return {"intent": "readmission_plan", "case": case.group(1) if case else None}
    if "청소" in q and case:
        return {"intent": "cleaning_status", "case": case.group(1)}
    return {"intent": "unknown"}


# ---------- 실제 모델 (환경 변수가 있을 때만) ----------

@dataclass
class _HttpLLM:
    model: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    timeout_s: float = 60.0

    def _post(self, url: str, headers: dict, body: dict) -> dict:
        t0 = time.monotonic()
        try:
            r = requests.post(url, headers=headers, json=body, timeout=self.timeout_s)
        except requests.RequestException as e:
            raise LLMError(f"연결 실패: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        if r.status_code in (402, 429) and any(w in r.text.lower() for w in ("quota", "credit", "balance", "billing", "spend")):
            raise QuotaExceeded(f"HTTP {r.status_code}: {r.text[:200]}")
        if r.status_code != 200:
            raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


@dataclass
class OpenAILLM(_HttpLLM):
    base_url: str = "https://api.openai.com/v1"

    def complete_json(self, task: str, system: str, user: str) -> dict:
        body = {"model": self.model, "messages": [{"role": "system", "content": system},
                                                   {"role": "user", "content": user}],
                "response_format": {"type": "json_object"}}
        if not self.model.startswith(("gpt-5", "o1", "o3", "o4")):
            body["temperature"] = 0  # 반복 일관성. 추론 모델은 온도 설정을 받지 않는다
        res = self._post(f"{self.base_url}/chat/completions",
                         {"Authorization": f"Bearer {os.environ.get('OPENAI_API_KEY', '')}"}, body)
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        return _first_json(res["choices"][0]["message"]["content"] or "")


@dataclass
class AnthropicLLM(_HttpLLM):
    base_url: str = "https://api.anthropic.com/v1"

    def body(self, system: str, user: str) -> dict:
        body = {"model": self.model, "max_tokens": 1024, "system": system + "\nJSON 객체 하나만 출력한다.",
                "messages": [{"role": "user", "content": user}]}
        if re.match(r"claude-(3|[a-z]+-4)", self.model):
            body["temperature"] = 0  # 반복 일관성. Claude 5 계열은 temperature를 받지 않는다(HTTP 400)
        return body

    def complete_json(self, task: str, system: str, user: str) -> dict:
        res = self._post(f"{self.base_url}/messages",
                         {"x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"},
                         self.body(system, user))
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("input_tokens", 0)
        self.usage.output_tokens += u.get("output_tokens", 0)
        text = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        return _first_json(text)


@dataclass
class LocalLLM:
    """원내(온프레미스) 모델: llama.cpp로 GGUF 양자화 모델을 CPU·GPU에서 돌린다. 외부 전송 없음."""

    model_path: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    n_ctx: int = 4096
    n_threads: int | None = None
    _llm: object = None

    def __post_init__(self):
        from llama_cpp import Llama  # 선택 의존성

        self.name = self.name or f"local:{Path(self.model_path).name}"
        self._llm = Llama(model_path=self.model_path, n_ctx=self.n_ctx, n_threads=self.n_threads, verbose=False)

    def complete_json(self, task: str, system: str, user: str) -> dict:
        t0 = time.monotonic()
        try:
            res = self._llm.create_chat_completion(
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"}, temperature=0.0, max_tokens=256)
        except Exception as e:  # noqa: BLE001  모델 런타임 오류는 모두 장애로 본다
            raise LLMError(f"로컬 모델 오류: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        u = res.get("usage") or {}
        self.usage.input_tokens += u.get("prompt_tokens", 0)
        self.usage.output_tokens += u.get("completion_tokens", 0)
        return _first_json(res["choices"][0]["message"]["content"] or "")


@dataclass
class ClaudeSubLLM:
    """구독(Pro·Max) 계정으로 로그인한 Claude Code를 claude-agent-sdk로 부른다 (개인 평가용, 대리 측정).

    사용자가 약관에서 개인 평가 용도 허용을 확인함(2026-11-09). 한계: 온도를 고정할 수 없고,
    지연에 Claude Code 기동 시간이 섞여 지연·비용 수치는 쓰지 않는다. 정확도 비교에만 쓴다.
    `ANTHROPIC_API_KEY`가 잡혀 있으면 구독이 아니라 API로 과금되므로 실행하지 않는다.
    """

    model: str
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    api_equiv_usd: float = 0.0  # API 정가 환산(실제 청구 아님)

    def __post_init__(self):
        self.name = self.name or f"claudesub:{self.model}"

    def complete_json(self, task: str, system: str, user: str) -> dict:
        if os.environ.get("ANTHROPIC_API_KEY"):
            raise LLMError("ANTHROPIC_API_KEY가 잡혀 있어 구독 대신 API로 과금된다. 변수를 지우고 다시 실행")
        import asyncio

        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query
        except ImportError as e:  # 선택 의존성
            raise LLMError("claude-agent-sdk가 없다: pip install claude-agent-sdk") from e

        opts = ClaudeAgentOptions(model=self.model, system_prompt=system + "\nJSON 객체 하나만 출력한다.",
                                  tools=[], max_turns=1, setting_sources=[], strict_mcp_config=True,
                                  extra_args={"no-session-persistence": None})

        async def run():
            res = None
            async for m in query(prompt=user, options=opts):
                if isinstance(m, ResultMessage):
                    res = m
            return res

        t0 = time.monotonic()
        try:
            res = asyncio.run(run())
        except Exception as e:  # noqa: BLE001  SDK·CLI 오류는 모두 장애로 본다
            raise LLMError(f"구독 경유 호출 실패: {e}") from e
        self.usage.latency_s += time.monotonic() - t0
        self.usage.calls += 1
        if res is None or res.is_error:
            msg = getattr(res, "subtype", "응답 없음")
            if res is not None and any(w in str(res.result or "").lower() for w in ("limit", "quota")):
                raise QuotaExceeded(f"구독 한도: {res.result}")
            raise LLMError(f"구독 경유 오류: {msg}")
        u = res.usage or {}
        self.usage.input_tokens += sum(u.get(k, 0) or 0 for k in
                                       ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        self.usage.output_tokens += u.get("output_tokens", 0) or 0
        self.api_equiv_usd += res.total_cost_usd or 0.0
        return _first_json(res.result or "")


def get_llm(env: dict | None = None) -> LLM:
    """환경 변수가 모두 갖춰졌을 때만 실제 모델. 아니면 가짜 LLM."""
    env = os.environ if env is None else env
    kind, model = env.get("TURNAROUND_LLM", "fake"), env.get("TURNAROUND_LLM_MODEL")
    if kind == "fake" or not model:
        return FakeLLM()
    if kind == "local":
        return LocalLLM(model_path=model)  # 원내 실행: 외부 전송이 없어 확인 값이 필요 없다
    if env.get("TURNAROUND_EXTERNAL_LLM_ACK") != "synthetic-only":
        raise LLMError("외부 LLM은 합성 데이터 확인(TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only)이 있어야 쓴다")
    if kind == "claudesub":  # 구독 경유도 데이터가 국외 서버로 간다: 합성 데이터 확인 필요
        return ClaudeSubLLM(model=model)
    if kind == "openai" and env.get("OPENAI_API_KEY"):
        return OpenAILLM(model=model, name=f"openai:{model}")
    if kind == "xai":  # OpenAI 호환 API. 키는 환경 프록시가 넣는다
        return OpenAILLM(model=model, name=f"xai:{model}", base_url="https://api.x.ai/v1")
    if kind == "anthropic" and env.get("ANTHROPIC_API_KEY"):
        return AnthropicLLM(model=model, name=f"anthropic:{model}")
    raise LLMError(f"{kind} 키가 없다")
