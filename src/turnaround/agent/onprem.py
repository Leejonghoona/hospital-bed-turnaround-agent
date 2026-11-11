"""원내 LLM 도입 인수 시험 키트용 연결 (docs/검토/LLM도입_인수시험_설계_초안.md 2절 K0·K1, 11절 1번).

병원이 운영하는 OpenAI 호환 엔드포인트(또는 같은 방식의 호스팅·임대 서버)에 붙는다. 엔드포인트는
`config/kit_endpoints.yaml`에 별칭으로 등록한 것만 쓴다. 이 파일은 측정 세트를 만들기 전에 정하고 동결한다.

- kind: onprem(병원 원내) | hosted(호스팅 대리 측정, 외부) | rental(임대 GPU, 외부). 외부는 합성 데이터만 받는다.
- 프록시·리디렉트를 따르지 않는다(trust_env=False, allow_redirects=False). TLS 검증은 끄지 않는다.
- 키는 별칭마다 정한 환경 변수에서만 읽는다(다른 서비스 키가 새지 않게). 값은 파일에 쓰지 않는다.
- 호출마다 종료 사유, 입력·출력·추론 토큰, 지연, 서버가 알려 준 모델 ID를 남긴다(모델 지문, K1).
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

from .llm import LLMError, QuotaExceeded, Usage, _first_json

ROOT = Path(__file__).resolve().parents[3]
ENDPOINTS = ROOT / "config" / "kit_endpoints.yaml"
KINDS = ("onprem", "hosted", "rental")


class TransportError(LLMError):
    """연결·429·5xx. 모델 탓 실패가 아니라 따로 센다(설계 3.2: 재전송 뒤에도 남으면 회차 무효 판단에 씀)."""


def load_endpoints(path: Path = ENDPOINTS) -> dict:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    eps = data.get("endpoints") or {}
    for alias, e in eps.items():
        if e.get("kind") not in KINDS:
            raise ValueError(f"{alias}: kind는 {KINDS} 중 하나")
        if not str(e.get("base_url", "")).startswith("https://") and not e.get("allow_http_loopback"):
            raise ValueError(f"{alias}: https 주소만 받는다(로컬 시험 서버는 allow_http_loopback: true)")
    return eps


@dataclass
class CallMeta:
    finish: str | None = None
    in_tok: int = 0
    out_tok: int = 0
    reasoning_tok: int = 0
    latency_s: float = 0.0
    served_model: str | None = None
    transport_retries: int = 0
    error: str | None = None
    status_code: int | None = None


@dataclass
class OnPremLLM:
    """OpenAI 호환 chat/completions 엔드포인트. 스레드에서 동시에 불러도 호출별 기록이 섞이지 않는다."""

    alias: str
    base_url: str
    model: str
    kind: str = "onprem"
    key_env: str | None = None
    ca_bundle: str | None = None  # 내부 CA. None이면 시스템 기본 검증
    max_tokens: int = 2048  # 추론 포함 총 생성 상한 (설계 3.2)
    reasoning_effort: str | None = None  # 추론 모델만
    temperature: float | None = 0.0  # None이면 보내지 않는다(엔드포인트가 받지 않을 때)
    extra_body: dict = field(default_factory=dict)  # 사고 모드 끄기 등 모델별 고정값
    timeout_s: float = 60.0
    retries: int = 3  # 전송 오류만 재전송 (지수 백오프)
    allow_http_loopback: bool = False
    name: str = ""
    usage: Usage = field(default_factory=Usage)
    finish: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _local: threading.local = field(default_factory=threading.local, repr=False)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind는 {KINDS} 중 하나")
        if not self.base_url.startswith("https://"):
            if not (self.allow_http_loopback and self.base_url.startswith(("http://127.0.0.1", "http://localhost"))):
                raise ValueError("https 주소만 받는다(로컬 시험 서버만 예외)")
        self.name = self.name or f"onprem:{self.alias}"
        self._session = requests.Session()
        self._session.trust_env = False  # HTTP(S)_PROXY를 따르지 않는다

    @property
    def external(self) -> bool:  # 원내가 아니면 외부: GuardedLLM과 정책(external_llm)이 적용된다
        return self.kind != "onprem"

    @property
    def last(self) -> CallMeta | None:
        return getattr(self._local, "meta", None)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key_env:
            key = os.environ.get(self.key_env)
            if key:
                h["Authorization"] = f"Bearer {key}"
        return h

    def body(self, system: str, user: str) -> dict:
        b = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
             "response_format": {"type": "json_object"}, "max_tokens": self.max_tokens}
        if self.temperature is not None:
            b["temperature"] = self.temperature
        if self.reasoning_effort:
            b["reasoning_effort"] = self.reasoning_effort
        b.update(self.extra_body)
        return b

    def _post(self, body: dict, meta: CallMeta) -> dict:
        url = self.base_url.rstrip("/") + "/chat/completions"
        delay = 1.0
        for attempt in range(self.retries + 1):
            try:
                r = self._session.post(url, headers=self._headers(), json=body, timeout=self.timeout_s,
                                       allow_redirects=False, verify=self.ca_bundle or True)
            except requests.RequestException as e:
                err = TransportError(f"연결 실패: {e}")
            else:
                meta.status_code = r.status_code
                if r.status_code == 200:
                    return r.json()
                if 300 <= r.status_code < 400:
                    raise LLMError(f"리디렉트 거부 HTTP {r.status_code}")
                if r.status_code in (402, 429) and any(w in r.text.lower() for w in ("quota", "credit", "balance", "billing")):
                    raise QuotaExceeded(f"HTTP {r.status_code}: {r.text[:200]}")
                if r.status_code == 429 or r.status_code >= 500:
                    err = TransportError(f"HTTP {r.status_code}: {r.text[:200]}")
                else:
                    raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
            if attempt < self.retries:
                meta.transport_retries += 1
                time.sleep(delay)
                delay *= 2
        raise err

    def complete_json(self, task: str, system: str, user: str) -> dict:
        meta = CallMeta()
        self._local.meta = meta
        t0 = time.monotonic()
        try:
            res = self._post(self.body(system, user), meta)
        except LLMError as e:
            meta.latency_s = round(time.monotonic() - t0, 3)
            meta.error = type(e).__name__
            raise
        meta.latency_s = round(time.monotonic() - t0, 3)
        u = res.get("usage") or {}
        meta.in_tok = int(u.get("prompt_tokens") or 0)
        meta.out_tok = int(u.get("completion_tokens") or 0)
        meta.reasoning_tok = int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        meta.served_model = res.get("model")
        ch = (res.get("choices") or [{}])[0]
        meta.finish = str(ch.get("finish_reason"))
        with self._lock:
            self.usage.calls += 1
            self.usage.input_tokens += meta.in_tok
            self.usage.output_tokens += meta.out_tok
            self.usage.latency_s += meta.latency_s
            self.finish[meta.finish] = self.finish.get(meta.finish, 0) + 1
        if meta.finish == "length":
            raise LLMError("출력 상한에서 잘림(finish_reason=length)")
        content = (ch.get("message") or {}).get("content")
        if not content:
            raise LLMError("빈 응답")
        return _first_json(content)

    def fingerprint(self) -> dict:
        """모델 지문(K1): 서버가 알려 주는 모델 목록과 버전. 가중치 해시·템플릿 해시는 서버 쪽 server_manifest.json으로 보탠다."""
        out = {"alias": self.alias, "kind": self.kind, "base_url_sha256": hashlib.sha256(self.base_url.encode()).hexdigest()[:16],
               "model": self.model}
        for path in ("/models", "/../version"):
            try:
                r = self._session.get(self.base_url.rstrip("/") + path, headers=self._headers(), timeout=10,
                                      allow_redirects=False, verify=self.ca_bundle or True)
                if r.status_code == 200:
                    out[path.strip("/.")] = r.json()
            except (requests.RequestException, ValueError):
                out[path.strip("/.")] = None
        return out

    def capabilities(self) -> dict:
        """K0 지원 기능 확인: temperature, json_object, max_tokens, reasoning_effort를 실제 호출로 본다."""
        probe_sys = "JSON 객체 하나만 출력한다."
        probe_user = json.dumps({"q": "ping", "answer_with": {"ok": True}})
        checks = {}
        for key, extra in (("json_object", {}), ("temperature", {"temperature": 0.0}), ("max_tokens", {"max_tokens": 512}),
                           ("reasoning_effort", {"reasoning_effort": "low"} if self.reasoning_effort else None)):
            if extra is None:
                checks[key] = "해당 없음"
                continue
            body = {"model": self.model, "messages": [{"role": "system", "content": probe_sys},
                                                     {"role": "user", "content": probe_user}],
                    "response_format": {"type": "json_object"}, **extra}
            meta = CallMeta()
            try:
                res = self._post(body, meta)
                _first_json((res.get("choices") or [{}])[0].get("message", {}).get("content") or "")
                checks[key] = "ok"
            except LLMError as e:
                checks[key] = f"실패: {str(e)[:80]}"
        return checks


def from_alias(alias: str, path: Path = ENDPOINTS) -> OnPremLLM:
    eps = load_endpoints(path)
    if alias not in eps:
        raise LLMError(f"등록되지 않은 엔드포인트 별칭: {alias} ({path.name})")
    e = dict(eps[alias])
    return OnPremLLM(alias=alias, base_url=e["base_url"], model=e["model"], kind=e["kind"], key_env=e.get("key_env"),
                     ca_bundle=e.get("ca_bundle"), max_tokens=int(e.get("max_tokens", 2048)),
                     reasoning_effort=e.get("reasoning_effort"),
                     temperature=e.get("temperature", 0.0) if "temperature" in e else 0.0,
                     extra_body=dict(e.get("extra_body") or {}), timeout_s=float(e.get("timeout_s", 60)),
                     allow_http_loopback=bool(e.get("allow_http_loopback")))


__all__ = ["CallMeta", "ENDPOINTS", "OnPremLLM", "TransportError", "from_alias", "load_endpoints"]
