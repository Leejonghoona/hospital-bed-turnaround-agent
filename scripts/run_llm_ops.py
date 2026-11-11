"""LLM 운영 시험 A (docs/검토/LLM운영_시험설계.md 2절): 예외 판단 시스템 단위 채점. 합성 데이터.

python scripts/run_llm_ops.py --part A --models escalate,rule,local:models/qwen2.5-3b-instruct-q4_k_m.gguf [--tag 2026-11-10]
python scripts/run_llm_ops.py --part A --models local:<경로> --dry-run          # 앞 5문항 (경로 확인)
python scripts/run_llm_ops.py --part A --models local:<경로> --rerun-first 30   # 결정성 확인용 재실행
python scripts/run_llm_ops.py --part A --models local:<경로> --resume           # 끊긴 곳부터
python scripts/run_llm_ops.py --part A --summarize --models escalate,rule,local:<경로>  # 관문 판정과 결과 파일
python scripts/run_llm_ops.py --estimate --models local:<경로>                  # dry-run 지연으로 시간 계산

- 외부 모델은 받지 않는다(fake·rule·escalate·local만). 프롬프트는 brain.SYSTEM 그대로다.
- 문항마다 jsonl에 바로 덧붙인다. 관문은 측정 전에 등록한 SA1~SA7을 기계적으로 적용한다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.agent.brain import SYSTEM  # noqa: E402
from turnaround.agent.llm import LLMError, _fake_exception_action, llm_from_spec, llm_threads  # noqa: E402
from turnaround.evaluation import llm_ops as L  # noqa: E402
from turnaround.evaluation.exception_set import OUT_V3  # noqa: E402
from turnaround.evaluation.system_guard import load_items  # noqa: E402

OUT = ROOT / "evals" / "results" / "llm_ops"
B_CALLS_PER_DAY = 88  # 시연 병원 하루 LLM 호출(예외 49 + 설명 39, trace_report 기록) — 시간 계산용


def safe(head: str) -> str:
    return Path(head.split(":", 1)[1]).stem if head.startswith("local:") else head


def path_for(tag: str, head: str, kind: str = "") -> Path:
    return OUT / f"A_{tag}_{safe(head)}{('_' + kind) if kind else ''}.jsonl"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest(heads: list[str], set_path: Path) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except OSError:
            return None
    m = {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
         "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(), "set": str(set_path.relative_to(ROOT)) if set_path.is_relative_to(ROOT) else str(set_path),
         "set_sha256": sha256(set_path), "cpu_count": os.cpu_count(), "threads": llm_threads(),
         "python": platform.python_version(), "platform": platform.platform(), "models": {}}
    try:
        import llama_cpp
        m["llama_cpp"] = llama_cpp.__version__
    except ImportError:
        m["llama_cpp"] = None
    for h in heads:
        if h.startswith("local:"):
            p = ROOT / h.split(":", 1)[1] if not Path(h.split(":", 1)[1]).is_absolute() else Path(h.split(":", 1)[1])
            m["models"][h] = {"file": p.name, "sha256": sha256(p) if p.exists() else None,
                              "bytes": p.stat().st_size if p.exists() else None}
    return m


def ask(head: str, item: dict, llm) -> dict:
    """머리 하나가 문항 하나에 고른 행동. 무효(형식 오류·목록 밖·모델 오류·토큰 0)는 valid=False."""
    if head == "escalate":
        return {"id": item["id"], "action": "escalate", "status": "ok", "valid": True}
    if head == "rule":
        return {"id": item["id"], "action": _fake_exception_action(item["input"])["action"], "status": "ok", "valid": True}
    user = json.dumps(item["input"], ensure_ascii=False)  # brain.choose와 같은 입력 직렬화
    u = llm.usage
    in0, out0, fin0 = u.input_tokens, u.output_tokens, dict(getattr(llm, "finish", {}) or {})
    rec = {"id": item["id"], "action": None, "status": "ok", "valid": False, "error": None}
    t0 = time.monotonic()
    try:
        out = llm.complete_json("choose_exception_action", SYSTEM, user)
        act = out.get("action") if isinstance(out, dict) else None
        if not isinstance(act, str) or not act:
            rec["status"] = "format_error"
        else:
            rec["action"] = act
            if act.split(":", 1)[0] not in L.allowed_ids(item):
                rec["status"] = "out_of_list"
    except LLMError as e:
        rec["status"], rec["error"] = "llm_error", str(e)[:200]
    rec["latency_s"] = round(time.monotonic() - t0, 3)
    rec["in_tok"], rec["out_tok"] = u.input_tokens - in0, u.output_tokens - out0
    fin = getattr(llm, "finish", {}) or {}
    rec["finish"] = [k for k in fin if fin.get(k, 0) > fin0.get(k, 0)]
    rec["valid"] = rec["status"] == "ok" and rec["in_tok"] > 0 and rec["out_tok"] > 0
    return rec


def run_head(head: str, items: list[dict], dest: Path, resume: bool, llm=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if dest.exists():
        if not resume:
            raise SystemExit(f"이미 있다: {dest} (--resume으로 이어서 재거나 지운다)")
        done = {json.loads(x)["id"] for x in dest.read_text(encoding="utf-8").splitlines() if x.strip()}
    if head.startswith("local:") and llm is None:
        llm = llm_from_spec(head)
    with open(dest, "a", encoding="utf-8") as f:
        for i, it in enumerate(items):
            if it["id"] in done:
                continue
            r = ask(head, it, llm)
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            if head.startswith("local:"):
                print(f"[{head}] {i + 1}/{len(items)} {r['id']} {r['status']} {r['action']} {r.get('latency_s')}s", flush=True)
    return dest


def load(dest: Path) -> dict[str, dict]:
    return {r["id"]: r for r in (json.loads(x) for x in dest.read_text(encoding="utf-8").splitlines() if x.strip())}


def summarize(tag: str, heads: list[str], items: list[dict], set_path: Path) -> dict:
    by_id = {it["id"]: it for it in items}
    res = {"note": "합성 데이터 기준 추정. 결과는 사용자 정답 검토(docs/검토/LLM운영_v3_검토표.md) 전까지 잠정.",
           "status": "잠정", "manifest": manifest(heads, set_path), "heads": {}}
    rows: dict[str, list[dict]] = {}
    for h in heads:
        recs = load(path_for(tag, h))
        ids = [it["id"] for it in items if it["id"] in recs]
        rows[h] = [L.score_item(by_id[i], recs[i]["action"], recs[i]["valid"]) for i in ids]
        s = L.summarize(rows[h])
        st: dict[str, int] = {}
        for r in recs.values():
            st[r["status"]] = st.get(r["status"], 0) + 1
        s["status"] = dict(sorted(st.items()))
        lat = sorted(r["latency_s"] for r in recs.values() if r.get("latency_s") is not None)
        if lat:
            s["latency_s"] = {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1) + 0.5))],
                              "mean": round(sum(lat) / len(lat), 3)}
        s["complete"] = len(ids) == len(items)
        res["heads"][h] = s
    if "rule" in heads:
        for h in heads:
            if not h.startswith("local:") or not res["heads"][h]["complete"]:
                continue
            ids = {r["id"] for r in rows[h]}
            rule_rows = [r for r in rows["rule"] if r["id"] in ids]
            g = L.gates(res["heads"][h], L.summarize(rule_rows), rows[h], rule_rows)
            res["heads"][h]["gates"] = g
            res["heads"][h]["decision"] = L.decide(g)
            rr = path_for(tag, h, "rerun30")
            if rr.exists():
                first = load(path_for(tag, h))
                again = load(rr)
                same = L.same_actions({k: first[k]["action"] for k in again}, {k: v["action"] for k, v in again.items()})
                res["heads"][h]["determinism"] = {"n": len(again), "same": same,
                                                  "rule": "30문항이 모두 같으면 1회 = 3회, 다르면 3회"}
    return res


def estimate(tag: str, head: str, n_items: int) -> dict:
    dry = path_for(tag, head, "dry")
    recs = list(load(dry).values())
    mean = sum(r["latency_s"] for r in recs) / len(recs)
    a_calls = n_items + 30
    return {"dry_mean_s": round(mean, 2), "A_calls": a_calls, "A_hours": round(mean * a_calls / 3600, 2),
            "B_calls_per_day": B_CALLS_PER_DAY, "B_hours_per_day": round(mean * B_CALLS_PER_DAY / 3600, 2),
            "rule": "A가 4시간을 넘으면 A를 빼고 B만. 전체 상한 8시간"}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="A")
    ap.add_argument("--models", default="escalate,rule")
    ap.add_argument("--set", default=str(OUT_V3))
    ap.add_argument("--tag", default=date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rerun-first", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    a = ap.parse_args(argv)
    if a.part != "A":
        raise SystemExit("시험 B는 scripts/trace_report.py --shadow 로 돈다")
    heads = [h for h in a.models.split(",") if h]
    for h in heads:
        if h not in ("escalate", "rule") and not h.startswith("local:"):
            raise SystemExit(f"받지 않는 머리: {h} (외부 모델은 이 세션에서 쓰지 않는다)")
    set_path = Path(a.set)
    items = load_items(set_path)
    if a.estimate:
        print(json.dumps({h: estimate(a.tag, h, len(items)) for h in heads if h.startswith("local:")},
                         ensure_ascii=False, indent=1))
        return
    if a.summarize:
        res = summarize(a.tag, heads, items, set_path)
        dest = OUT / f"A_{a.tag}.json"
        dest.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({h: {k: v for k, v in s.items() if k in ("n", "valid_rate", "executed_forbidden",
                                                                   "general_chosen_forbidden", "good", "escalation_rate",
                                                                   "decision", "determinism")}
                          for h, s in res["heads"].items()}, ensure_ascii=False, indent=1))
        return
    for h in heads:
        if a.dry_run:
            run_head(h, items[:5], path_for(a.tag, h, "dry"), a.resume)
        elif a.rerun_first:
            run_head(h, items[:a.rerun_first], path_for(a.tag, h, f"rerun{a.rerun_first}"), a.resume)
        else:
            run_head(h, items, path_for(a.tag, h), a.resume)


if __name__ == "__main__":
    main()
