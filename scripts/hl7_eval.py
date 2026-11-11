"""HL7 v2 ADT 외부 형식 평가 (DECISIONS 2026-11-12): 생성기(sim)와 무관한 손으로 적은 메시지 묶음으로 온보딩 변환을 잰다.

입력: evals/hl7/sample_adt.hl7(메시지), interface.json(병동 코드표), answers.json(질문에 대한 가상 답),
      sample_adt_expected.json(정답). 질문 답 전(before_answers)과 후(after_answers) 두 번 돌린다.
쓰는 법: python scripts/hl7_eval.py [--out 경로]
결과: evals/results/hl7/2026-11-12.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from turnaround.evaluation.hl7_eval import score
from turnaround.ontology.hl7_adapter import HL7Config, to_events
from turnaround.ontology.hl7v2 import parse_batch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "hl7"
OUT = ROOT / "evals" / "results" / "hl7" / "2026-11-12.json"


def _load(name: str) -> dict:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def run(data: Path = DATA) -> dict:
    text = (data / "sample_adt.hl7").read_text(encoding="utf-8")
    msgs, errors = parse_batch(text)
    iface, answers, expected = _load("interface.json"), _load("answers.json"), _load("sample_adt_expected.json")
    base = HL7Config(ward_codes=iface["ward_codes"], tz_offset_min=iface.get("tz_offset_min", 540))
    answered = HL7Config(ward_codes=iface["ward_codes"], tz_offset_min=iface.get("tz_offset_min", 540),
                         positions=answers["positions"], codes=answers["codes"])
    out = {"input": "evals/hl7/sample_adt.hl7", "standard": "HL7 v2.5 ADT (A01, A02, A03, A08)",
           "patients": len({m.field("PID", 3, 1) for m in msgs})}
    for stage, cfg in (("before_answers", base), ("after_answers", answered)):
        out[stage] = score(to_events(msgs, cfg), expected, len(msgs), len(errors))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    res = run()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for stage in ("before_answers", "after_answers"):
        r = res[stage]
        print(stage, {k: v for k, v in r.items() if k != "details"})
    print("저장:", a.out)


if __name__ == "__main__":
    main()
