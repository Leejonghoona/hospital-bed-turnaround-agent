"""인수 시험 키트 K4 용량: 기록한 하루 요청을 압축 재생으로 다시 보내 용량 명세를 낸다 (설계 4절). 합성 데이터.

python scripts/kit_kfkd.py --part record --tag <날짜>                      # 먼저 요청 기록
python scripts/replay_requests.py --models kit:<별칭> --requests evals/results/kit/requests_<날짜>.jsonl \
    [--scale 5] [--background-rps 2] [--tick-timeout 60] [--tag <날짜>] [--label shared-nocache]

- 복제본마다 사례·병상 ID에 접미어를 붙이고 user 맨 앞에 replica를 넣어 입력을 다르게 한다(접두어 캐시 착시 방지).
- 틱 하나의 요청을 한꺼번에 보내고, 모두 끝나거나 tick-timeout이 지나면 다음 틱으로 간다(실시간 재생 아님).
- 배경 부하(공유 조건): 초당 N건, 입력 약 1,000토큰, 출력 300토큰 상한 (설계 가정).
- 접두어 캐시 켬·끔은 서버 설정이다. --label로 조건 이름을 남긴다(판정은 공유·캐시 끔 조건).
- 지연은 클라이언트 쪽(요청 전송부터 응답 완료까지)이다. 서버와 다른 호스트에서 돌리고 ping 왕복을 따로 적는다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.agent.llm import LLMError, llm_from_spec  # noqa: E402
from turnaround.agent.onprem import TransportError  # noqa: E402

OUT = ROOT / "evals" / "results" / "kit"
TICK_DONE_MIN = 0.99  # 관문: 틱 완료율 (설계 가정)
TIMEOUT_MAX = 0.01  # 관문: 시간 초과 비율 (설계 가정)
_ID_KEY = re.compile(r"(^|_)(id|case|bed_id|exception_id)$")


def _suffix(obj, k: int):
    if isinstance(obj, dict):
        return {key: (f"{v}~r{k}" if isinstance(v, str) and _ID_KEY.search(key) else _suffix(v, k)) for key, v in obj.items()}
    if isinstance(obj, list):
        return [_suffix(v, k) for v in obj]
    return obj


def replicate(log: list[dict], scale: int) -> list[dict]:
    out = []
    for r in log:
        for k in range(scale):
            try:
                obj = json.loads(r["user"])
                user = json.dumps({"replica": k, **_suffix(obj, k)}, ensure_ascii=False) if k else r["user"]
            except json.JSONDecodeError:
                user = f"[replica {k}] {r['user']}" if k else r["user"]
            out.append({**r, "user": user, "replica": k})
    return out


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))], 3)


def one(llm, r: dict) -> dict:
    t0 = time.monotonic()
    rec = {"tick": r["tick"], "task": r["task"], "replica": r["replica"], "status": "ok"}
    try:
        llm.complete_json(r["task"], r["system"], r["user"])
    except TransportError as e:
        rec["status"] = "timeout" if "timed out" in str(e).lower() or "timeout" in str(e).lower() else "transport_error"
    except LLMError:
        rec["status"] = "model_error"
    meta = getattr(getattr(llm, "inner", llm), "last", None)
    rec["latency_s"] = round(time.monotonic() - t0, 3)
    if meta is not None:
        rec.update(in_tok=meta.in_tok, out_tok=meta.out_tok)
    return rec


def background(llm, rps: float, stop: threading.Event, stats: dict) -> None:
    filler = "병원 업무 문서 요약 요청입니다. " * 120  # 입력 약 1,000토큰 (설계 가정)
    pool = ThreadPoolExecutor(16)
    while not stop.is_set():
        pool.submit(lambda: _bg_call(llm, filler, stats))
        stop.wait(1.0 / rps)
    pool.shutdown(wait=False, cancel_futures=True)


def _bg_call(llm, filler, stats):
    try:
        llm.complete_json("background", "요약을 JSON {\"summary\": ...}으로만 답한다.", filler)
        stats["ok"] += 1
    except LLMError:
        stats["err"] += 1


def replay(llm, reqs: list[dict], tick_timeout: float) -> dict:
    ticks: dict[str, list[dict]] = {}
    for r in reqs:
        ticks.setdefault(r["tick"], []).append(r)
    rows, tick_rows = [], []
    for tick in sorted(ticks):
        batch = ticks[tick]
        with ThreadPoolExecutor(max(1, len(batch))) as pool:
            t0 = time.monotonic()
            futs = [pool.submit(one, llm, r) for r in batch]
            done, notdone = wait(futs, timeout=tick_timeout)
            res = [f.result() for f in done]
            for f in notdone:
                f.cancel()
            took = time.monotonic() - t0
        rows += res
        tick_rows.append({"tick": tick, "n": len(batch), "done_in_time": len(notdone) == 0 and all(
            x["status"] == "ok" for x in res), "late": len(notdone), "took_s": round(took, 2)})
    return {"rows": rows, "ticks": tick_rows}


def capacity(rep: dict, reqs: list[dict]) -> dict:
    rows, ticks = rep["rows"], rep["ticks"]
    exc = [r["latency_s"] for r in rows if r["task"] == "choose_exception_action" and r["status"] == "ok"]
    n = len(reqs)
    timeouts = sum(r["status"] == "timeout" for r in rows) + sum(t["late"] for t in ticks)
    day_ticks = [t for t in ticks if t["tick"] and datetime.fromisoformat(t["tick"]).hour >= 7]
    peak = max((t["n"] for t in day_ticks), default=0)
    mid = sum(t["n"] for t in ticks if t["tick"] and 10 <= datetime.fromisoformat(t["tick"]).hour < 14)
    done_rate = sum(t["done_in_time"] for t in ticks) / len(ticks) if ticks else None
    spec = {"requests": n, "input_tokens": sum(r.get("in_tok", 0) for r in rows),
            "output_tokens": sum(r.get("out_tok", 0) for r in rows), "peak_tick_concurrency_after_07": peak,
            "share_10_14": round(mid / n, 4) if n else None,
            "exception_latency_s": {"p50": pct(exc, 0.5), "p95": pct(exc, 0.95)},
            "tick_completion_rate": round(done_rate, 4) if done_rate is not None else None,
            "timeout_rate": round(timeouts / n, 4) if n else None,
            "by_status": {s: sum(r["status"] == s for r in rows) for s in sorted({r["status"] for r in rows})}}
    spec["pass"] = (spec["tick_completion_rate"] or 0) >= TICK_DONE_MIN and (spec["timeout_rate"] or 0) <= TIMEOUT_MAX
    spec["rule"] = f"틱 완료율 ≥ {TICK_DONE_MIN:.0%}, 시간 초과 ≤ {TIMEOUT_MAX:.0%} (07:00 이전 재생 시작 몰림 포함, 따로 표시)"
    return spec


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True)
    ap.add_argument("--requests", required=True)
    ap.add_argument("--scale", type=int, default=5)
    ap.add_argument("--background-rps", type=float, default=0.0)
    ap.add_argument("--tick-timeout", type=float, default=60.0)
    ap.add_argument("--label", default="dedicated")
    ap.add_argument("--tag", default=date.today().isoformat())
    a = ap.parse_args(argv)
    log = [json.loads(x) for x in Path(a.requests).read_text(encoding="utf-8").splitlines() if x.strip()]
    reqs = replicate(log, a.scale)
    llm = llm_from_spec(a.models)
    stop, bg_stats = threading.Event(), {"ok": 0, "err": 0}
    bg = None
    if a.background_rps > 0:
        bg = threading.Thread(target=background, args=(llm_from_spec(a.models), a.background_rps, stop, bg_stats), daemon=True)
        bg.start()
    t0 = time.monotonic()
    rep = replay(llm, reqs, a.tick_timeout)
    stop.set()
    spec = capacity(rep, reqs)
    out = {"note": "합성 데이터 요청 기록의 압축 재생. 용량 명세(병원 운영팀 자원 신청서 초안)", "label": a.label, "model": a.models,
           "scale": a.scale, "background_rps": a.background_rps, "background": bg_stats, "wall_s": round(time.monotonic() - t0, 1),
           "capacity": spec, "ticks": rep["ticks"]}
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / f"K4_{a.tag}_{a.models.split(':', 1)[-1]}_{a.label}.json"
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(spec, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
