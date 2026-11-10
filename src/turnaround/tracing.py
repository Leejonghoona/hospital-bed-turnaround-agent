"""에이전트 추적 (DECISIONS 2026-11-10 MCP 서버와 에이전트 추적).

LLM 호출, 에이전트 판단(계획, 예외 판단 머리), 게이트웨이 판정을 OpenTelemetry span으로 남긴다.
- 기본은 꺼짐이다. 꺼져 있으면 OpenTelemetry를 불러오지 않고 전역 불 값 하나만 본다(측정값과 시험 결과가 그대로).
- 켜는 법: 환경 변수 `TURNAROUND_TRACE=1`(파일 `TURNAROUND_TRACE_FILE`, 기본 traces.jsonl), 또는 `with capture() as rec:`.
- 바깥 서비스는 필요 없다. `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`가 있고 OTLP 내보내기 패키지가 있으면 Langfuse로도 보낸다.
- 환자 이름, 자유 서술, LLM 입력 원문은 남기지 않는다. 사례 ID(합성)와 구조화 값만 남긴다.
"""

from __future__ import annotations

import base64
import functools
import json
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ON = False
_tracer = None
_provider = None
_IN_LLM: ContextVar[bool] = ContextVar("turnaround_in_llm_span", default=False)

# 작업 → 역할 (어느 에이전트 부품이 부른 호출인지)
TASK_ROLE = {"choose_bed": "planner", "explain_choice": "planner", "choose_exception_action": "brain",
             "extract": "extractor", "parse_question": "qa", "propose_mapping": "mapping"}


def env_enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("TURNAROUND_TRACE", "")).lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _ON


# ---------- span 손잡이 ----------

def _attr(v):
    if isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, (list, tuple, set)):
        items = list(v)
        if all(isinstance(x, (bool, int, float, str)) for x in items) and len({type(x) for x in items}) <= 1:
            return items
        return [str(x) for x in items]
    return json.dumps(v, ensure_ascii=False, default=str)


class _Handle:
    def __init__(self, span):
        self.span = span

    def set(self, **attrs) -> None:
        for k, v in attrs.items():
            if v is not None:
                self.span.set_attribute(k, _attr(v))

    def error(self, kind: str) -> None:
        from opentelemetry.trace import Status, StatusCode
        self.span.set_status(Status(StatusCode.ERROR, kind))


class _Noop:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def set(self, **attrs) -> None:
        pass

    def error(self, kind: str) -> None:
        pass


NOOP = _Noop()


@contextmanager
def _live_span(name: str, attrs: dict):
    with _tracer.start_as_current_span(name, record_exception=False, set_status_on_exception=True) as sp:
        h = _Handle(sp)
        h.set(**attrs)
        yield h


def span(name: str, **attrs):
    """추적이 켜져 있으면 span 문맥, 꺼져 있으면 아무것도 안 하는 NOOP."""
    if not _ON:
        return NOOP
    return _live_span(name, attrs)


# ---------- LLM 호출 ----------

def traced_llm(fn):
    """`complete_json(self, task, system, user)`에 붙인다. 관문(GuardedLLM) 안쪽 모델의 호출은 바깥 span 하나로 센다."""

    @functools.wraps(fn)
    def wrapper(self, task, system, user):
        if not _ON or _IN_LLM.get():
            return fn(self, task, system, user)
        usage = getattr(self, "usage", None)
        in0, out0 = (usage.input_tokens, usage.output_tokens) if usage is not None else (0, 0)
        model = getattr(self, "model", None) or getattr(self, "name", type(self).__name__)
        with _live_span("llm.call", {"llm.task": task, "llm.role": TASK_ROLE.get(task, "other"),
                                     "llm.model": str(model), "llm.name": str(getattr(self, "name", "")),
                                     "llm.external": bool(getattr(self, "external", False)),
                                     "llm.input_chars": len(user or "")}) as h:
            tok = _IN_LLM.set(True)
            t0 = time.perf_counter()
            blocked, err = False, None
            try:
                return fn(self, task, system, user)
            except PermissionError:
                blocked, err = True, "PermissionError"
                raise
            except Exception as e:  # noqa: BLE001 - 종류만 남기고 다시 던진다
                err = type(e).__name__
                raise
            finally:
                _IN_LLM.reset(tok)
                h.set(**{"llm.latency_ms": round((time.perf_counter() - t0) * 1000, 3), "llm.external_blocked": blocked})
                if usage is not None:
                    h.set(**{"llm.input_tokens": usage.input_tokens - in0, "llm.output_tokens": usage.output_tokens - out0})
                if err:
                    h.set(**{"llm.error": err})
                    h.error(err)

    return wrapper


# ---------- 게이트웨이 판정 분류 ----------

def gateway_outcome(decision, action: str | None = None, actor: str | None = None) -> str:
    """auto(에이전트 확정·자동 실행) | human(사람 확인으로 보냄·사람에게 올림·사람이 확정) | blocked(거절) | duplicate | other."""
    st = decision.status
    if st == "duplicate":
        return "duplicate"
    if st == "rejected":
        return "blocked"
    if st.startswith("awaiting"):
        return "human"
    if st in ("committed", "executed"):
        return "human" if action == "escalate" or actor == "human" else "auto"
    return "other"


def decision_attrs(decision, action: str | None = None, actor: str | None = None) -> dict:
    return {"gateway.status": decision.status, "gateway.stage": decision.stage, "gateway.tier": decision.tier,
            "gateway.reasons": list(decision.reasons or []),
            "gateway.outcome": gateway_outcome(decision, action, actor)}


# ---------- 켜고 끄기 ----------

class Recorder:
    """capture()가 돌려주는 기록. spans는 끝난 순서의 dict 목록."""

    def __init__(self, exporter):
        self._exp = exporter

    @property
    def spans(self) -> list[dict]:
        return [span_to_dict(s) for s in self._exp.get_finished_spans()]


def span_to_dict(s) -> dict:
    p = s.parent
    return {"name": s.name, "trace_id": f"{s.context.trace_id:032x}", "span_id": f"{s.context.span_id:016x}",
            "parent_id": f"{p.span_id:016x}" if p is not None else None,
            "start_ns": s.start_time, "end_ns": s.end_time,
            "duration_ms": round(((s.end_time or s.start_time) - s.start_time) / 1e6, 3),
            "status": s.status.status_code.name if s.status.status_code.name != "UNSET" else "OK",
            "attrs": {k: list(v) if isinstance(v, tuple) else v for k, v in (s.attributes or {}).items()}}


def _jsonl_exporter(path: str):
    from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

    class JsonlExporter(SpanExporter):
        def __init__(self, p):
            self.path = p

        def export(self, spans):
            with open(self.path, "a", encoding="utf-8") as f:
                for s in spans:
                    f.write(json.dumps(span_to_dict(s), ensure_ascii=False) + "\n")
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

    return JsonlExporter(path)


def langfuse_config(env=None) -> dict | None:
    """Langfuse의 OTLP 수신 주소와 인증 머리글. 키가 없으면 None. 네트워크는 쓰지 않는다."""
    env = os.environ if env is None else env
    pk, sk = env.get("LANGFUSE_PUBLIC_KEY"), env.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        return None
    host = (env.get("LANGFUSE_HOST") or env.get("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com").rstrip("/")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return {"endpoint": f"{host}/api/public/otel/v1/traces", "headers": {"Authorization": f"Basic {auth}"}}


def _install(processors) -> tuple:
    global _ON, _tracer, _provider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    prev = (_ON, _tracer, _provider)
    # 전역 공급자는 건드리지 않는다(다른 라이브러리의 추적과 섞이지 않게). 이 모듈 전용 공급자를 쓴다
    _provider = TracerProvider(resource=Resource.create({"service.name": "wardflow", "data.class": "synthetic"}))
    for p in processors:
        _provider.add_span_processor(p)
    _tracer = _provider.get_tracer("turnaround")
    _ON = True
    return prev


def _restore(prev) -> None:
    global _ON, _tracer, _provider
    if _provider is not None:
        _provider.shutdown()
    _ON, _tracer, _provider = prev


@contextmanager
def capture():
    """문맥 안에서만 추적을 켜고 메모리에 모은다(시험, 보고 스크립트)."""
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    exp = InMemorySpanExporter()
    prev = _install([SimpleSpanProcessor(exp)])
    try:
        yield Recorder(exp)
    finally:
        _restore(prev)


def enable(path: str | None = None, env=None) -> None:
    """JSONL 파일(기본 TURNAROUND_TRACE_FILE 또는 traces.jsonl)로 내보내며 켠다. Langfuse 키가 있으면 함께 보낸다."""
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    env = os.environ if env is None else env
    procs = [SimpleSpanProcessor(_jsonl_exporter(path or env.get("TURNAROUND_TRACE_FILE") or "traces.jsonl"))]
    cfg = langfuse_config(env)
    if cfg is not None:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            procs.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=cfg["endpoint"], headers=cfg["headers"])))
        except ImportError:
            pass  # 내보내기 패키지가 없으면 파일만 쓴다
    _install(procs)


def disable() -> None:
    _restore((False, None, None))


if env_enabled():  # 가져올 때 환경 변수로 켠다
    try:
        enable()
    except ImportError:  # OpenTelemetry가 없으면 끈 채로 둔다
        _ON = False


# ---------- 요약 ----------

def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 3)


def case_trace(spans: list[dict], case: str) -> list[dict]:
    """한 사례의 span(사례 ID가 붙은 것과 그 아래 span)을 시작 순서로."""
    ids = {s["span_id"] for s in spans if s["attrs"].get("case_id") == case}
    changed = True
    while changed:
        more = {s["span_id"] for s in spans if s["parent_id"] in ids} - ids
        ids |= more
        changed = bool(more)
    return sorted((s for s in spans if s["span_id"] in ids), key=lambda s: s["start_ns"])


def summarize(spans: list[dict], top: int = 8) -> dict:
    """판단 수, 자동/사람/막음 비율, 막은 사유, LLM 작업별 호출, span 종류별 지연 p50/p95."""
    subs = [s for s in spans if s["name"] == "gateway.submit"]
    acts = [s for s in spans if s["name"] == "gateway.submit_action"]
    decisions = subs + acts
    n = len(decisions)
    counts: dict[str, int] = {}
    reasons: dict[str, int] = {}
    human_reasons: dict[str, int] = {}
    per_case: dict[str, set] = {}
    for s in decisions:
        o = s["attrs"].get("gateway.outcome", "other")
        counts[o] = counts.get(o, 0) + 1
        bucket = reasons if o == "blocked" else human_reasons if o == "human" else None
        if bucket is not None:
            for r in s["attrs"].get("gateway.reasons", []) or ([s["attrs"].get("gateway.action")] if o == "human" else ["?"]):
                r = r or s["attrs"].get("gateway.tier") or "?"
                bucket[r] = bucket.get(r, 0) + 1
        if s["name"] == "gateway.submit" and s["attrs"].get("case_id"):
            per_case.setdefault(s["attrs"]["case_id"], set()).add(o)
    # 사례마다 한 번: 에이전트가 끝내 확정했는가(자동), 사람에게 갔는가, 막히기만 했는가
    case_final = {"auto": 0, "human": 0, "blocked": 0}
    for outs in per_case.values():
        case_final["auto" if "auto" in outs else "human" if "human" in outs else "blocked"] += 1

    def part(group):
        c: dict[str, int] = {}
        for s in group:
            o = s["attrs"].get("gateway.outcome", "other")
            c[o] = c.get(o, 0) + 1
        return {"n": len(group), "by_outcome": dict(sorted(c.items()))}

    llm = [s for s in spans if s["name"] == "llm.call"]
    by_task: dict[str, dict] = {}
    for s in llm:
        a = s["attrs"]
        t = by_task.setdefault(a.get("llm.task", "?"), {"calls": 0, "errors": 0, "external_blocked": 0,
                                                         "input_tokens": 0, "output_tokens": 0, "role": a.get("llm.role")})
        t["calls"] += 1
        t["errors"] += 1 if a.get("llm.error") else 0
        t["external_blocked"] += 1 if a.get("llm.external_blocked") else 0
        t["input_tokens"] += a.get("llm.input_tokens", 0)
        t["output_tokens"] += a.get("llm.output_tokens", 0)
    lat: dict[str, list[float]] = {}
    for s in spans:
        lat.setdefault(s["name"], []).append(s["duration_ms"])
    return {
        "agent_decisions": n,
        "outcome_counts": dict(sorted(counts.items())),
        "outcome_share": {k: round(v / n, 4) for k, v in sorted(counts.items())} if n else {},
        "proposals": part(subs), "actions": part(acts),
        "top_block_reasons": sorted(reasons.items(), key=lambda x: (-x[1], x[0]))[:top],
        "top_human_reasons": sorted(human_reasons.items(), key=lambda x: (-x[1], x[0]))[:top],
        "proposal_cases": {"n": len(per_case), **case_final},
        "llm_calls_by_task": dict(sorted(by_task.items())), "llm_calls": len(llm),
        "latency_ms": {k: {"n": len(v), "p50": _pct(v, 0.5), "p95": _pct(v, 0.95)} for k, v in sorted(lat.items())},
    }
