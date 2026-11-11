"""LLM 판단의 규칙표 반영: 수정안 후보 보기와 승인 (src/turnaround/agent/rule_learning.py).

운영 기록으로:
  python scripts/rule_proposals.py --shadow shadow.jsonl --decisions decisions.jsonl
  python scripts/rule_proposals.py --shadow ... --decisions ... --approve <id> --by "원무팀장"
  (decisions.jsonl 한 줄: {"exception_id": ..., "action": 담당자가 실제로 한 행동, "saw_model": LLM 답을 봤는지})

인수 시험 결과로 미리 보기(담당자 결정 자리에 v4 세트 정답을 넣은 대리 실험, 합성 데이터):
  python scripts/rule_proposals.py --from-kit 2026-11-11-gpu --model rental-gemma-4-26b
승인은 운영 기록에서만 받는다(--from-kit 결과로는 승인할 수 없다).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.agent import rule_learning as RL  # noqa: E402
from turnaround.agent.llm import _fake_exception_action  # noqa: E402

V4 = ROOT / "evals" / "model_eval" / "exception_decisions_v4.jsonl"
KIT = ROOT / "evals" / "results" / "kit"


def _jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def kit_cases(tag: str, model: str) -> list[RL.Case]:
    """v4 문항마다: 규칙표 답(우선 규칙 없이), 모델 답(3회 다수결), 담당자 대리 = 정답."""
    items = {it["id"]: it for it in _jsonl(V4)}
    runs = []
    for r in (1, 2, 3):
        p = KIT / f"K3_{tag}_{model}_r{r}.jsonl"
        if p.exists():
            runs.append({x["id"]: x for x in _jsonl(p)})
    if not runs:
        raise SystemExit(f"결과 파일이 없다: K3_{tag}_{model}_r*.jsonl")
    out = []
    for iid, it in items.items():
        acts = [run[iid].get("action") for run in runs if iid in run and run[iid].get("valid")]
        if not acts:
            continue
        model_act = Counter(acts).most_common(1)[0][0]
        ctx = it["input"]
        rule = _fake_exception_action(ctx, overrides=[])
        gold = it["gold_action"]["action"] if isinstance(it["gold_action"], dict) else it["gold_action"]
        ex = ctx.get("exception") or {}
        out.append(RL.Case(exception_id=iid, trigger=ex.get("trigger"), stage=ex.get("stage"),
                           feats=RL.features(ctx), rule_action=RL._base(rule["action"]),
                           model_action=RL._base(model_act), human_action=RL._base(gold),
                           shown_to_human=bool(rule.get("default"))))
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow")
    ap.add_argument("--decisions")
    ap.add_argument("--from-kit")
    ap.add_argument("--model", default="rental-gemma-4-26b")
    ap.add_argument("--min-cases", type=int, default=RL.MIN_CASES)
    ap.add_argument("--approve")
    ap.add_argument("--by", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.from_kit:
        cases = kit_cases(a.from_kit, a.model)
        if a.approve:
            raise SystemExit("대리 실험 결과로는 승인할 수 없다. 운영 기록(--shadow, --decisions)으로만 승인한다")
    elif a.shadow and a.decisions:
        cases = RL.join_cases(_jsonl(Path(a.shadow)), _jsonl(Path(a.decisions)))
    else:
        raise SystemExit("--shadow와 --decisions, 또는 --from-kit이 필요하다")
    props = RL.propose(cases, min_cases=a.min_cases)
    if a.approve:
        p = next((x for x in props if x.id == a.approve), None)
        if p is None:
            raise SystemExit(f"후보에 없다: {a.approve}")
        sha = RL.approve(p, a.by, a.reason)
        print(f"승인: {p.text()}\n규칙 파일 sha256 {sha} (규칙표가 바뀌었으므로 인수 시험 판정은 만료, 재인수 필요)")
        return
    clean = sum(1 for c in cases if c.clean)
    summary = {"cases": len(cases), "clean": clean, "excluded_seen_or_shown": len(cases) - clean,
               "proposals": [p.__dict__ for p in props]}
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return
    print(f"사례 {len(cases)}건, 셀 수 있는 사례 {clean}건 (LLM 답을 봤거나 제안으로 보여 준 {len(cases) - clean}건 제외)")
    for p in props:
        print(p.text())
    if not props:
        print("후보 없음")


if __name__ == "__main__":
    main()
