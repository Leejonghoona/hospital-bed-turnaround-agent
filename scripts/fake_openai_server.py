"""가짜 OpenAI 호환 서버 (인수 시험 키트 드라이런·장애 주입, 설계 11절 5번). 외부 연결 없음, 127.0.0.1만.

python scripts/fake_openai_server.py [--port 8765]

응답은 저장소의 가짜 LLM 규칙(결정적)을 따른다. 예외 판단 입력이면 규칙표 행동, 설명 입력이면 고정 문장.
토큰 사용량은 0이 아닌 값을 돌려준다(가짜 LLM 경로가 valid 0으로 잡히던 함정 방지).

장애 모드는 POST /control {"mode": ..., "latency_s": ..., "every": N} 로 바꾼다.
- normal: 정상
- length: finish_reason=length, 잘린 JSON
- empty: 빈 content
- broken: JSON이 아닌 문장
- http429 / http500: 오류 응답
- slow: latency_s만큼 늦게 응답(시간 초과 시험)
every=N이면 N번째 요청마다만 장애를 낸다(나머지는 정상).
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.agent.llm import _fake_exception_action  # noqa: E402

STATE = {"mode": "normal", "latency_s": 0.0, "every": 1, "n": 0, "served_model": "fake-rule@v1"}
LOCK = threading.Lock()


def answer(user: str) -> dict:
    try:
        obj = json.loads(user)
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(obj, dict) and "allowed_actions" in obj:
        return _fake_exception_action(obj)
    if isinstance(obj, dict) and "chosen_bed_id" in obj:
        return {"rationale": f"가짜 서버 설명: {obj['chosen_bed_id']}"}
    if isinstance(obj, dict) and obj.get("candidates"):
        c = obj["candidates"][0]
        return {"bed_id": c.get("bed_id") if isinstance(c, dict) else c, "rationale": "가짜 서버: 1순위"}
    return {"ok": True}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _send(self, code: int, obj: dict | str):
        data = (obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.endswith("/models"):
            return self._send(200, {"data": [{"id": STATE["served_model"], "max_model_len": 4096}]})
        if self.path.endswith("/version"):
            return self._send(200, {"version": "fake-0.1"})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/control"):
            with LOCK:
                STATE.update({k: v for k, v in body.items() if k in ("mode", "latency_s", "every", "served_model")})
                STATE["n"] = 0
            return self._send(200, dict(STATE))
        if not self.path.endswith("/chat/completions"):
            return self._send(404, {"error": "not found"})
        with LOCK:
            STATE["n"] += 1
            fault = STATE["mode"] != "normal" and STATE["n"] % max(1, int(STATE["every"])) == 0
            mode, lat = STATE["mode"], float(STATE["latency_s"])
        msgs = body.get("messages") or []
        user = next((m.get("content") for m in msgs if m.get("role") == "user"), "")
        content = json.dumps(answer(user), ensure_ascii=False)
        finish = "stop"
        if fault:
            if mode == "http429":
                return self._send(429, {"error": "rate limited"})
            if mode == "http500":
                return self._send(500, {"error": "server error"})
            if mode == "slow":
                time.sleep(lat)
            if mode == "length":
                content, finish = content[: max(1, len(content) // 2)], "length"
            elif mode == "empty":
                content = ""
            elif mode == "broken":
                content = "죄송합니다. 판단할 수 없습니다."
        in_tok = max(1, len(json.dumps(msgs, ensure_ascii=False)) // 3)
        self._send(200, {"model": STATE["served_model"], "choices": [
            {"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": in_tok, "completion_tokens": max(1, len(content) // 3)}})


def serve(port: int = 8765) -> ThreadingHTTPServer:
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H)
    print(f"가짜 OpenAI 호환 서버 http://127.0.0.1:{a.port}/v1", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
