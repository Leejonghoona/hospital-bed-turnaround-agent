"""인수 시험 키트 KF(장애 시 동작)·KD(데이터 흐름 명세)와 K4용 요청 기록 (docs/검토/LLM도입_인수시험_설계_초안.md 2절·4절).

python scripts/kit_kfkd.py --part KF     # 가짜 OpenAI 호환 서버에 장애를 주입하고 하루 재생 결과를 규칙표와 비교
python scripts/kit_kfkd.py --part KD     # 하루 재생에서 LLM으로 나가는 필드 목록과 식별 정보 검사
python scripts/kit_kfkd.py --part record # K4 재전송용 요청 기록 (15분 틱, 역할, system, user)

모델이 필요 없다(가짜 서버 127.0.0.1). 합성 데이터. 시연 병원(176병상) 하루가 한 번에 약 20초 걸린다.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import fake_openai_server as F  # noqa: E402
import requests  # noqa: E402
import trace_report as T  # noqa: E402

from turnaround.agent.llm import FakeLLM, redact_for_external  # noqa: E402
from turnaround.agent.onprem import OnPremLLM  # noqa: E402
from turnaround.evaluation.safety_exec import executed_safety  # noqa: E402

OUT = ROOT / "evals" / "results" / "kit"
PORT = 8765
MODES = [("normal", {}), ("down", {}), ("length", {}), ("empty", {}), ("broken", {}), ("http429", {}),
         ("http500", {}), ("slow", {"latency_s": 3.0, "every": 5})]


def _sim_now():
    f = inspect.currentframe()
    while f:
        v = f.f_locals.get("now")
        if isinstance(v, datetime):
            return v
        f = f.f_back
    return None


def signature(st) -> list:
    """실행 결과 지문: 사건 종류, 사례, 병상, 행동. 설명 문장 같은 글은 뺀다."""
    out = []
    for e in st.store.read():
        p = e.payload or {}
        out.append((e.type.value, e.case_id, p.get("bed_id"), p.get("action"), p.get("status")))
    return out


def endpoint(port: int, timeout_s: float = 2.0) -> OnPremLLM:
    return OnPremLLM(alias=f"fake-{port}", base_url=f"http://127.0.0.1:{port}/v1", model="fake-rule", kind="onprem",
                     allow_http_loopback=True, timeout_s=timeout_s, retries=0)


def control(mode: str, **kw) -> None:
    requests.post(f"http://127.0.0.1:{PORT}/v1/control", json={"mode": mode, **kw}, timeout=5)


def kf() -> dict:
    base_st, _ = T.run_day(seed=2, llm=FakeLLM(), exception_llm=FakeLLM(), shadow=True)
    base = signature(base_st)
    res = {"note": "합성 데이터. 그림자 모드(운영 시작 조건)에서 LLM 장애를 주입하고 실행 결과를 LLM 없는 규칙표와 비교",
           "baseline_events": len(base), "modes": {}}
    srv = F.serve(PORT)
    try:
        for mode, kw in MODES:
            if mode != "down":
                control("normal" if mode == "normal" else mode, **kw)
            port = 8799 if mode == "down" else PORT  # down: 아무도 듣지 않는 포트
            llm, exc = endpoint(port), endpoint(port)
            err = None
            try:
                st, _ = T.run_day(seed=2, llm=llm, exception_llm=exc, shadow=True)
                sig = signature(st)
                safety = executed_safety(st.gw, st.store.read())
                shadow = T.shadow_summary(st.agent.shadow_log)
            except Exception as e:  # noqa: BLE001  하루가 끝나지 않으면 그것이 결함이다
                err, sig, safety, shadow = f"{type(e).__name__}: {str(e)[:200]}", None, None, None
            res["modes"][mode] = {
                "completed": err is None, "error": err, "same_as_rule": sig == base if sig is not None else False,
                "events": len(sig) if sig else None,
                "safety_violations": {k: v["violations"] for k, v in safety.items() if isinstance(v, dict)} if safety else None,
                "llm_calls": llm.usage.calls + exc.usage.calls, "finish": {**llm.finish, **exc.finish},
                "shadow_status": shadow["by_status"] if shadow else None}
            print(mode, json.dumps(res["modes"][mode], ensure_ascii=False), flush=True)
    finally:
        srv.shutdown()
    res["pass"] = all(m["completed"] and m["same_as_rule"] for m in res["modes"].values())
    res["rule"] = "모든 장애 모드에서 하루가 끝나고, 실행 결과(사건 지문)가 LLM 없는 규칙표와 같아야 통과"
    return res


def record() -> tuple[list[dict], object]:
    log: list[dict] = []

    def wrap(llm, role):
        orig = llm.complete_json

        def w(task, system, user):
            now = _sim_now()
            tick = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0) if now else None
            log.append({"seq": len(log), "tick": tick.isoformat() if tick else None, "role": role, "task": task,
                        "system": system, "user": user})
            return orig(task, system, user)
        llm.complete_json = w
        return llm

    st, _ = T.run_day(seed=2, llm=wrap(FakeLLM(), "planner"), exception_llm=wrap(FakeLLM(), "exception"), shadow=True)
    return log, st


_SENSITIVE_KEYS = re.compile(r"(name|성명|이름|rrn|ssn|주민|phone|tel|연락처|address|주소|email|diagnos|진단|mrn|등록번호|memo|note|메모)",
                             re.I)


def _walk(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v, f"{path}[]")
    else:
        yield path, obj


def kd(log: list[dict]) -> dict:
    fields: dict[str, dict] = {}
    issues = []
    for r in log:
        try:
            obj = json.loads(r["user"])
        except json.JSONDecodeError:
            obj = {"_text": r["user"]}
        for path, v in _walk(obj):
            f = fields.setdefault(r["task"], {}).setdefault(path, {"types": set(), "n": 0, "example": None})
            f["types"].add(type(v).__name__)
            f["n"] += 1
            if f["example"] is None and v not in (None, ""):
                f["example"] = v if not isinstance(v, str) else v[:40]
            leaf = path.split(".")[-1].rstrip("[]")
            if _SENSITIVE_KEYS.search(leaf):
                issues.append({"task": r["task"], "field": path, "why": "민감 정보 이름의 필드", "value": str(v)[:40]})
            if isinstance(v, str) and redact_for_external(v) != v:
                issues.append({"task": r["task"], "field": path, "why": "식별 정보 모양(이름·전화·긴 번호·진단)",
                               "value": v[:60], "masked": redact_for_external(v)[:60]})
    spec = {t: {p: {"types": sorted(d["types"]), "n": d["n"], "example": d["example"]} for p, d in sorted(fs.items())}
            for t, fs in fields.items()}
    uniq = {(i["task"], i["field"], i["why"]): i for i in issues}
    return {"note": "합성 데이터 하루 재생에서 LLM으로 나간 user 입력의 필드 목록. 병원 보안성 검토·개인정보 영향평가 제출용 초안",
            "calls": len(log), "by_task": {t: sum(1 for r in log if r["task"] == t) for t in fields},
            "fields": spec, "issues": list(uniq.values()), "pass": not uniq,
            "rule": "민감 정보 이름의 필드가 없고, 값에 이름·전화·긴 번호·진단 모양이 없어야 통과(자동 검사, 설계 가정)"}


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=("KF", "KD", "record"), required=True)
    ap.add_argument("--tag", default=datetime.now().date().isoformat())
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    if a.part == "KF":
        res = kf()
        (OUT / f"KF_{a.tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(json.dumps({"pass": res["pass"]}, ensure_ascii=False))
        return
    log, _ = record()
    if a.part == "record":
        p = OUT / f"requests_{a.tag}.jsonl"
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in log) + "\n")
        print(json.dumps({"calls": len(log), "out": str(p.relative_to(ROOT))}, ensure_ascii=False))
        return
    res = kd(log)
    (OUT / f"KD_{a.tag}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"pass": res["pass"], "calls": res["calls"], "issues": res["issues"][:10]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
