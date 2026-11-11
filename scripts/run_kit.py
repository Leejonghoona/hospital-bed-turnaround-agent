"""원내 LLM 도입 인수 시험 키트: K0·K1 기록, K2 형식, K3 판단 (docs/검토/LLM도입_인수시험_설계_초안.md). 합성 데이터.

python scripts/run_kit.py --part K0 --models kit:<별칭>                 # 지원 기능 확인 + 모델 지문
python scripts/run_kit.py --part K2 --models kit:<별칭>                 # 고정 5문항 형식 점검
python scripts/run_kit.py --part K3 --models kit:<별칭> --runs 1,2,3    # 회차별 300문항 (동시 8, 순서 섞기)
python scripts/run_kit.py --part K3 --models rule,escalate              # 비교선 (결정적, 1회)
python scripts/run_kit.py --summarize --models rule,kit:a,kit:b         # 판정과 결과 파일

- 측정 모델: rule, escalate, kit:<별칭>(config/kit_endpoints.yaml), eval-anthropic:<모델>(상한, 판정 밖).
- 외부(hosted·rental·Anthropic)는 TURNAROUND_EXTERNAL_LLM_ACK=synthetic-only가 있어야 하고 합성 세트만 보낸다.
- 후보 목록과 Holm m은 config/kit_candidates.yaml에서 읽는다(첫 K3 호출 전에 커밋).
- 프롬프트는 brain.SYSTEM 그대로다. 문항마다 jsonl에 바로 덧붙인다(--resume로 이어서).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.agent.brain import SYSTEM  # noqa: E402
from turnaround.agent.llm import LLMError, _fake_exception_action, llm_from_spec  # noqa: E402
from turnaround.agent.onprem import TransportError  # noqa: E402
from turnaround.evaluation import kit as K  # noqa: E402
from turnaround.evaluation import llm_ops as L  # noqa: E402
from turnaround.evaluation.exception_set import OUT_V4  # noqa: E402
from turnaround.evaluation.system_guard import load_items  # noqa: E402

OUT = ROOT / "evals" / "results" / "kit"
CANDIDATES = ROOT / "config" / "kit_candidates.yaml"
CONCURRENCY = 8
K2_N = 5


def alias(head: str) -> str:
    return head.split(":", 1)[1].replace("/", "_").replace(":", "_") if ":" in head else head


def path_for(tag: str, part: str, head: str, run: int | None = None) -> Path:
    return OUT / f"{part}_{tag}_{alias(head)}{f'_r{run}' if run else ''}.jsonl"


def sha256_lf(p: Path) -> str:
    """LF로 정규화한 내용의 sha256 (git blob과 같은 값, 설계 3.1)."""
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def git(*a) -> str | None:
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except OSError:
        return None


def candidates() -> dict:
    if not CANDIDATES.exists():
        return {"m": None, "candidates": []}
    return yaml.safe_load(CANDIDATES.read_text(encoding="utf-8")) or {}


def manifest(heads: list[str], set_path: Path, llms: dict) -> dict:
    m = {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
         "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
         "policy_sha256": sha256_lf(ROOT / "config" / "policy.yaml"),
         "endpoints_sha256": sha256_lf(ROOT / "config" / "kit_endpoints.yaml"),
         "set": str(set_path.relative_to(ROOT)) if set_path.is_relative_to(ROOT) else str(set_path),
         "set_sha256": sha256_lf(set_path), "concurrency": CONCURRENCY, "python": platform.python_version(),
         "platform": platform.platform(), "candidates": candidates(), "models": {}}
    for h, llm in llms.items():
        inner = getattr(llm, "inner", llm)
        m["models"][h] = {k: getattr(inner, k, None) for k in ("name", "model", "kind", "max_tokens", "reasoning_effort",
                                                               "temperature", "extra_body", "timeout_s", "retries")}
    return m


def _meta(llm):
    return getattr(getattr(llm, "inner", llm), "last", None)


def ask(head: str, item: dict, llm, lock: threading.Lock | None) -> dict:
    """머리 하나가 문항 하나에 고른 행동. 상태: ok, format_error, out_of_list, model_error, transport_error."""
    if head == "escalate":
        return {"id": item["id"], "action": "escalate", "status": "ok", "valid": True}
    if head == "rule":
        return {"id": item["id"], "action": _fake_exception_action(item["input"])["action"], "status": "ok", "valid": True}
    user = json.dumps(item["input"], ensure_ascii=False)
    rec = {"id": item["id"], "action": None, "status": "ok", "valid": False, "error": None}
    if lock:  # 호출별 기록이 없는 모델(Anthropic 경로)은 한 번에 하나씩
        lock.acquire()
    t0 = time.monotonic()  # 지연은 잠금을 얻은 뒤부터 (대기 시간 제외)
    try:
        u = getattr(llm, "usage", None)
        in0, out0 = (u.input_tokens, u.output_tokens) if u else (0, 0)
        try:
            out = llm.complete_json("choose_exception_action", SYSTEM, user)
            act = out.get("action") if isinstance(out, dict) else None
            if not isinstance(act, str) or not act:
                rec["status"] = "format_error"
            else:
                rec["action"] = act
                if act.split(":", 1)[0] not in L.allowed_ids(item):
                    rec["status"] = "out_of_list"
        except TransportError as e:
            rec["status"], rec["error"] = "transport_error", str(e)[:200]
        except LLMError as e:
            rec["status"], rec["error"] = "model_error", str(e)[:200]
        meta = _meta(llm)
        if meta is not None:
            rec.update(finish=meta.finish, in_tok=meta.in_tok, out_tok=meta.out_tok, reasoning_tok=meta.reasoning_tok,
                       transport_retries=meta.transport_retries, served_model=meta.served_model)
        elif u is not None:
            rec.update(in_tok=u.input_tokens - in0, out_tok=u.output_tokens - out0)
    finally:
        if lock:
            lock.release()
    rec["latency_s"] = round(time.monotonic() - t0, 3)
    rec["valid"] = rec["status"] == "ok" and rec.get("out_tok", 0) > 0
    return rec


def run_head(head: str, items: list[dict], dest: Path, resume: bool, order_seed: int | None, llm=None) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if dest.exists():
        if not resume:
            raise SystemExit(f"이미 있다: {dest} (--resume으로 이어서 재거나 지운다)")
        done = {json.loads(x)["id"] for x in dest.read_text(encoding="utf-8").splitlines() if x.strip()}
    todo = [it for it in items if it["id"] not in done]
    if order_seed is not None:
        random.Random(order_seed).shuffle(todo)
    if llm is None and head not in ("rule", "escalate"):
        llm = llm_from_spec(head)
    lock = None if head in ("rule", "escalate") or _meta_capable(llm) else threading.Lock()
    wlock = threading.Lock()
    with open(dest, "a", encoding="utf-8", newline="\n") as f, ThreadPoolExecutor(CONCURRENCY) as pool:
        for i, r in enumerate(pool.map(lambda it: ask(head, it, llm, lock), todo)):
            with wlock:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
            if head not in ("rule", "escalate") and (i + 1) % 25 == 0:
                print(f"[{head}] {i + 1}/{len(todo)}", flush=True)
    return dest


def _meta_capable(llm) -> bool:
    return hasattr(getattr(llm, "inner", llm), "last")


def load(dest: Path) -> dict[str, dict]:
    return {r["id"]: r for r in (json.loads(x) for x in dest.read_text(encoding="utf-8").splitlines() if x.strip())}


def k2_verdict(recs: list[dict]) -> dict:
    bad = [r for r in recs if not r["valid"]]
    return {"pass": not bad, "n": len(recs), "invalid": [{"id": r["id"], "status": r["status"], "finish": r.get("finish"),
                                                          "error": r.get("error")} for r in bad],
            "rule": f"고정 {K2_N}문항 모두 유효. 실패 원인이 서빙 설정이면 병원, 파서면 제품(릴리스로만 수정, 최대 2회)"}


def summarize(tag: str, heads: list[str], items: list[dict], set_path: Path) -> dict:
    by_id = {it["id"]: it for it in items}
    cand = candidates()
    res = {"note": "합성 데이터 기준 추정. 측정 전 정답 검토 완료 전이면 잠정.", "manifest": manifest(heads, set_path, {}),
           "heads": {}, "holm": None}
    rule_rows = None
    if "rule" in heads:
        rr = load(path_for(tag, "K3", "rule"))
        rule_rows = [L.score_item(by_id[i], rr[i]["action"], rr[i]["valid"]) for i in by_id if i in rr]
        res["heads"]["rule"] = L.summarize(rule_rows)
    if "escalate" in heads:
        er = load(path_for(tag, "K3", "escalate"))
        res["heads"]["escalate"] = L.summarize([L.score_item(by_id[i], er[i]["action"], er[i]["valid"]) for i in by_id if i in er])
    pvals = {}
    for h in heads:
        if h in ("rule", "escalate"):
            continue
        runs, statuses, invalid_runs, lat = [], [], [], []
        for r in (1, 2, 3):
            p = path_for(tag, "K3", h, r)
            if not p.exists():
                continue
            recs = load(p)
            if K.run_invalid(list(recs.values())):
                invalid_runs.append(r)
                continue
            ids = [i for i in by_id if i in recs]
            runs.append([L.score_item(by_id[i], recs[i]["action"], recs[i]["valid"]) for i in ids])
            st: dict[str, int] = {}
            for x in recs.values():
                st[x["status"]] = st.get(x["status"], 0) + 1
                if x.get("latency_s") is not None:
                    lat.append(x["latency_s"])
            statuses.append(st)
        s = {"runs_found": len(runs), "invalid_runs": invalid_runs, "status_by_run": statuses}
        if lat:
            lat.sort()
            s["latency_s"] = {"p50": lat[len(lat) // 2], "p95": lat[min(len(lat) - 1, int(0.95 * (len(lat) - 1) + 0.5))]}
        if runs and K.stop_after_first(L.summarize(runs[0])) and len(runs) == 1:
            s.update(stopped=True, k3_pass=False, failed=["멈춤 규칙(1회차 SA1 또는 SA4)"])
            pvals[h] = 1.0
        elif rule_rows is not None and (len(runs) == 3 or (h.startswith("eval-anthropic:") and runs)):  # 상한은 1회(설계 5절)
            j = K.judge(runs, rule_rows)
            s.update(j)
            pvals[h] = j["gates"]["SA5"]["p"]
        else:
            s.update(k3_pass=None, failed=["미측정 또는 회차 부족"])
            pvals[h] = 1.0
        if h.startswith("eval-anthropic:"):
            s["role"] = "상한(판정 밖)"
            pvals.pop(h, None)
        res["heads"][h] = s
    listed = [c["spec"] for c in cand.get("candidates") or []]
    m = cand.get("m")
    if m:
        full = {c: pvals.get(c, 1.0) for c in listed}  # 못 잰 후보는 p=1로 m에 남긴다
        res["holm"] = {"m": m, "p": full, "pass": K.holm(full, m) if full else {},
                       "note": "SA5는 보고만 한다(판정은 SA1·SA3·SA4·SA6)"}
    return res


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", default="K3", choices=("K0", "K2", "K3"))
    ap.add_argument("--models", default="rule,escalate")
    ap.add_argument("--set", default=str(OUT_V4))
    ap.add_argument("--tag", default=date.today().isoformat())
    ap.add_argument("--runs", default="1,2,3")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--summarize", action="store_true")
    a = ap.parse_args(argv)
    heads = [h for h in a.models.split(",") if h]
    for h in heads:
        if h not in ("rule", "escalate") and not h.startswith(("kit:", "eval-anthropic:")):
            raise SystemExit(f"받지 않는 측정 모델: {h} (rule, escalate, kit:<별칭>, eval-anthropic:<모델>)")
    set_path = Path(a.set)
    items = load_items(set_path)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.summarize:
        res = summarize(a.tag, heads, items, set_path)
        dest = OUT / f"K3_{a.tag}.json"
        dest.write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({h: {k: v for k, v in s.items() if k in ("k3_pass", "failed", "good", "escalation_rate",
                                                                   "valid_rate", "runs_found", "invalid_runs", "role")}
                          for h, s in res["heads"].items()}, ensure_ascii=False, indent=1))
        return
    if a.part == "K3":  # 측정 전에 manifest를 남긴다(커밋 해시와 dirty가 측정 시작 시점 값이 되게)
        mf = OUT / f"K3_{a.tag}_{'+'.join(alias(h) for h in heads)}_manifest.json"
        if not mf.exists():
            llms = {h: llm_from_spec(h) for h in heads if h not in ("rule", "escalate")}
            mf.write_text(json.dumps(manifest(heads, set_path, llms), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for h in heads:
        if a.part == "K0":
            llm = llm_from_spec(h)
            inner = getattr(llm, "inner", llm)
            out = {"head": h, "capabilities": inner.capabilities() if hasattr(inner, "capabilities") else None,
                   "fingerprint": inner.fingerprint() if hasattr(inner, "fingerprint") else None}
            (OUT / f"K0_{a.tag}_{alias(h)}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            print(json.dumps(out, ensure_ascii=False, indent=1))
        elif a.part == "K2":
            dest = run_head(h, items[:K2_N], path_for(a.tag, "K2", h), a.resume, None)
            v = k2_verdict(list(load(dest).values()))
            print(json.dumps({h: v}, ensure_ascii=False, indent=1))
        elif h in ("rule", "escalate"):
            run_head(h, items, path_for(a.tag, "K3", h), a.resume, None)
        else:
            for r in (int(x) for x in a.runs.split(",")):
                run_head(h, items, path_for(a.tag, "K3", h, r), a.resume, order_seed=r)
                if r == 1:
                    recs = load(path_for(a.tag, "K3", h, 1))
                    by_id = {it["id"]: it for it in items}
                    s1 = L.summarize([L.score_item(by_id[i], x["action"], x["valid"]) for i, x in recs.items()])
                    if K.stop_after_first(s1):
                        print(f"[{h}] 멈춤 규칙: 1회차 실행 금지 {s1['executed_forbidden']}, 유효 {s1['valid_rate']}", flush=True)
                        break


if __name__ == "__main__":
    main()
