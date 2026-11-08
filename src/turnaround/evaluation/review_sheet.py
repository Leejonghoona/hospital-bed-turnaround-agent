"""라벨 검토표(마크다운)를 만든다. 사용자가 이 표를 보고 라벨을 승인한다.

    python -m turnaround.evaluation.review_sheet > docs/라벨_검토표.md
"""

from pathlib import Path

from .scenario import load_scenarios

TIER_KO = {"auto": "자동", "ward_confirm": "병동 확인", "review": "담당 검토"}


def _act(a) -> str:
    parts = [a.type]
    if a.case:
        parts.append(a.case)
    if a.bed:
        parts.append(a.bed)
    parts += [f"{k}={v}" for k, v in a.params.items()]
    s = " ".join(parts)
    return f"{s} ({a.note})" if a.note else s


def render(directory: Path) -> str:
    rows = [
        "# 회귀 시나리오 라벨 검토표",
        "",
        "`evals/scenarios/dev/*.yaml`에서 자동 생성한 표다. 손으로 고치지 않는다.",
        "라벨이 맞으면 해당 YAML의 `status`를 `approved`로, `provenance.reviewed_by`를 검토자로 바꾼다.",
        "",
        "| ID | 제목 | 상황 | 확인 등급 | 해야 할 행동 | 하면 안 되는 행동 | 허용 병상 | 지표 | 상태 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in load_scenarios(directory):
        g = s.gold
        tier = ", ".join(f"{k}: {TIER_KO[v.value]}" for k, v in g.tier_by_case.items()) or "-"
        beds = "; ".join(f"{k}: {', '.join(v) or '없음'}" for k, v in g.acceptable_beds.items()) or "-"
        cells = [s.id, s.title, s.situation, tier, "<br>".join(map(_act, g.must_actions)) or "-",
                 "<br>".join(map(_act, g.must_not)) or "-", beds, ", ".join(s.metrics), s.status]
        rows.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    return "\n".join(rows) + "\n"


if __name__ == "__main__":
    print(render(Path(__file__).resolve().parents[3] / "evals" / "scenarios" / "dev"), end="")
