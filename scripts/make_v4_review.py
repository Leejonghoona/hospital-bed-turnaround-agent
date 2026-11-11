"""v4 정답 검토표 (docs/검토/LLM도입_인수시험_설계_초안.md 3.1). 측정 전, 모델·규칙표 행동을 가린 채 검토한다.

python scripts/make_v4_review.py  ->  docs/검토/인수시험_v4_검토표.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from turnaround.evaluation import llm_ops as L  # noqa: E402
from turnaround.evaluation.exception_set import OUT_V4, SEED_V4, review_sample  # noqa: E402
from turnaround.evaluation.system_guard import load_items  # noqa: E402

DEST = ROOT / "docs" / "검토" / "인수시험_v4_검토표.md"


def act(a) -> str:
    return f'{a["action"]} {json.dumps(a.get("params") or {}, ensure_ascii=False)}'


def main() -> None:
    items = load_items(OUT_V4)
    pick = sorted(review_sample(items), key=lambda it: it["id"])
    lines = ["# 인수 시험 v4 정답 검토표 (무작위 15문항, 측정 전)", "",
             f"`{OUT_V4.relative_to(ROOT).as_posix()}`(시드 {SEED_V4}, 300문항)에서 `random.Random({SEED_V4 + 1}).sample(세트 순서, 15)`로 뽑았다. "
             "정답은 생성 규칙에서 나온 작업자 초안이다. 모델과 규칙표의 행동은 이 표에 넣지 않는다(눈가림).", "",
             "**검토 방법:** 문항마다 정답 행동이 맞는지 `판정` 칸에 맞음/고침을 적는다. 한 건이라도 고치면 300문항 전체를 검토하고, "
             "고친 규칙이 v3에도 있었는지 표시한다. 15문항 오류 0건이어도 정답 오류율 95% 상한은 약 18%다.", "",
             f"- 세트 함정 문항(금지 행동이 허용 목록에 들어 있는 문항): {sum(L.is_trap(i) for i in items)} / {len(items)}",
             f"- 이 표본의 함정 문항: {sum(L.is_trap(i) for i in pick)} / {len(pick)}", ""]
    for it in pick:
        ex, inp = it["input"]["exception"], it["input"]
        lines += [f'## {it["id"]} · {it["exception_code"]} {it["trigger"]} · 단계 {ex.get("stage")}' + (" · 함정" if L.is_trap(it) else ""), "",
                  f'- 상황: {it["situation"]}',
                  f'- 사례 상태: `{json.dumps(inp["case_state"], ensure_ascii=False)}`',
                  f'- 도구 출력: `{json.dumps(inp["tool_outputs"], ensure_ascii=False)}`',
                  f'- 경과·마감·시도: {ex.get("elapsed_min")}분 경과, 마감까지 {ex.get("deadline_in_min")}분, 시도 {ex.get("attempts")}회',
                  f'- 허용 목록: {", ".join(a["id"] for a in inp["allowed_actions"])}',
                  f'- **정답:** {act(it["gold_action"])}',
                  f'- 허용: {", ".join(it["acceptable_actions"])}',
                  f'- 금지: {", ".join(it["forbidden_actions"])}',
                  f'- 근거: {it["rationale_ko"]}',
                  "- 판정: (맞음 / 고침: …)", ""]
    DEST.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(DEST.relative_to(ROOT))


if __name__ == "__main__":
    main()
