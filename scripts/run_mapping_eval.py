"""매핑 제안 평가 (로드맵 07 M6 → M5): 가짜 LLM(어휘 사전)과 실제 모델을 같은 형식 5개로 비교한다.

합성 데이터만 쓴다. LLM에는 열 이름·값 모양·종류가 적은 코드값만 간다(원천 값 없음).
쓰는 법: python scripts/run_mapping_eval.py fake openai:gpt-5-mini xai:grok-4-fast ...
결과: evals/results/mapping/<날짜>_<모델>.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

from turnaround.agent.llm import FakeLLM, get_llm
from turnaround.evaluation.mapping_eval import score
from turnaround.events.replay import hospital_to_events
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.exports import FORMATS, HOLDOUT_FORMATS, export, gold_mapping

OUT = Path(__file__).resolve().parents[1] / "evals" / "results" / "mapping"


def _llm(spec: str):
    if spec == "fake":
        return FakeLLM()
    kind, model = spec.split(":", 1)
    env = {**os.environ, "TURNAROUND_LLM": kind, "TURNAROUND_LLM_MODEL": model,
           "TURNAROUND_EXTERNAL_LLM_ACK": "synthetic-only"}
    env.setdefault("OPENAI_API_KEY", "proxy-injected")  # 키는 환경 프록시가 넣는다(값을 코드에 두지 않음)
    os.environ.setdefault("OPENAI_API_KEY", "proxy-injected")
    llm = get_llm(env, data_class="synthetic")  # 합성 내보내기만 쓴다
    llm.timeout_s = 240
    if getattr(llm, "model", "").startswith("gpt-5"):
        llm.reasoning_effort = "low"  # 상류 시간 제한 안에 끝나게 (기록에 남김)
    return llm


def run(spec: str, seeds=(1,)) -> dict:
    llm = _llm(spec)
    rows = []
    for seed in seeds:
        h = generate(seed=seed, config=GeneratorConfig.realistic())
        ev = [e.model_copy(update={"observed_at": e.occurred_at}) for e in hospital_to_events(h, seed=seed)]
        for fmt in FORMATS + HOLDOUT_FORMATS:
            r = score(export(h, ev, fmt), gold_mapping(fmt), ev, h, llm=None if spec == "fake" else llm)
            rows.append({"seed": seed, "fmt": fmt, **{k: r[k] for k in (
                "llm", "columns", "codes", "format", "questions", "followup_questions", "exact_after_answers",
                "error", "wrong_columns", "wrong_codes")}})
            print(spec, seed, fmt, r["llm"], "col", r["columns"], "code_wrong", r["codes"]["wrong"],
                  "Q", r["questions"], "+", r["followup_questions"], "exact", r["exact_after_answers"], flush=True)
    usage = getattr(llm, "usage", None)
    out = {"model": spec, "date": date.today().isoformat(), "rows": rows,
           "usage": usage.__dict__ if usage is not None and hasattr(usage, "__dict__") else None}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{out['date']}_{spec.replace(':', '_')}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1,
                                                                                default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    for spec in sys.argv[1:] or ["fake"]:
        run(spec)
