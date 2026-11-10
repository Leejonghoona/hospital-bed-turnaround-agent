"""시스템 단위 금지 측정 (08 계획 2단계 끝난 기준 "운영 입력으로 세트 측정, 금지 0").

머리(모델 또는 규칙 경로)가 고른 행동을 게이트웨이와 같은 검사(허용 목록 → 상황별 금지)에 통과시키고, 막히면
대체 경로(올림)로 바꾼 뒤 실제로 실행될 행동이 금지인지 센다. 모델 측정은 기록된 응답을 다시 쓴다(새 호출 없음).
상황별 금지 규칙은 이 정답 세트를 보고 원칙으로 정리한 것이라 같은 세트로 잰 값은 낙관적이다(설계 가정).
"""

from __future__ import annotations

import json
from pathlib import Path

from turnaround.gateway.guards import situational_block

SET = Path(__file__).resolve().parents[3] / "evals" / "model_eval" / "exception_decisions_v2_1.jsonl"


def load_items() -> list[dict]:
    return [json.loads(line) for line in SET.read_text(encoding="utf-8").splitlines() if line.strip()]


def executed_action(item: dict, action: str | None) -> tuple[str, str | None]:
    """(실제 실행될 행동, 막은 이유)."""
    allowed = {a["id"] for a in item["input"]["allowed_actions"]}
    if not action or action.split(":", 1)[0] not in allowed:
        return "escalate", "not_allowed"
    why = situational_block(item["input"], action)
    return ("escalate", why) if why else (action, None)


def measure(choices: dict[str, str | None], items: list[dict] | None = None) -> dict:
    items = items or load_items()
    out = {"n": 0, "chosen_forbidden": 0, "executed_forbidden": 0, "blocked": 0, "executed_good": 0,
           "forbidden_items": []}
    for it in items:
        if it["id"] not in choices:
            continue
        out["n"] += 1
        a = choices[it["id"]]
        out["chosen_forbidden"] += bool(a) and a in it["forbidden_actions"]
        ex, why = executed_action(it, a)
        out["blocked"] += why is not None
        if ex in it["forbidden_actions"]:
            out["executed_forbidden"] += 1
            out["forbidden_items"].append(it["id"])
        good = {it["gold_action"]["action"]} | {x.split(":")[0] for x in it["acceptable_actions"]}
        out["executed_good"] += ex in good
    return out


def rule_path_choices(items: list[dict] | None = None) -> dict[str, str]:
    from turnaround.agent.llm import _fake_exception_action
    return {it["id"]: _fake_exception_action(it["input"])["action"] for it in (items or load_items())}


def recorded_choices(path: str | Path) -> dict[str, str | None]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))["exception"]
    return {r["id"]: r.get("action") for r in d["items"]}


__all__ = ["executed_action", "load_items", "measure", "recorded_choices", "rule_path_choices"]
