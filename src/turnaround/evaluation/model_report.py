"""모델 성능점검 결과표를 JSON 결과에서 만든다 (손으로 숫자를 옮기지 않는다).

사용: python -m turnaround.evaluation.model_report  → docs/모델_성능점검표.md의 결과 구간을 바꾼다.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model_check import mcnemar_exact

ROOT = Path(__file__).resolve().parents[3]
RES = ROOT / "evals" / "results" / "model_check"
DOC = ROOT / "docs" / "모델_성능점검표.md"
START, END = "<!-- 결과 시작 (model_report가 만든다) -->", "<!-- 결과 끝 -->"


def _load() -> dict[tuple[str, str], dict]:
    out = {}
    for p in sorted(RES.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        split = (d.get("extraction") or {}).get("split", "dev")
        out[(d["model"], split)] = d
    return out


def _name(m: str) -> str:
    return m.replace("local:models/", "원내:").replace(".gguf", "").replace("openai:", "")


def _f(x, pct=False):
    if x is None:
        return "-"
    return f"{x:.1%}" if pct else str(x)


def render() -> str:
    data = _load()
    models = sorted({m for m, _ in data}, key=lambda m: (m.startswith("local"), m))
    lines = ["### 추출 (E1·E2·I1)", "",
             "| 모델 | 분리 | E1 필드 정확 [95% CI] | 문항 전부 정확 | E2 틀린 값 | I1 인젝션 짝 변화 / 무작위 바닥 | 지연 P50·P95(초) | 100문항 비용(USD) |",
             "|---|---|---|---|---|---|---|---|"]
    for m in models:
        for split in ("dev", "holdout"):
            e = (data.get((m, split)) or {}).get("extraction")
            if not e:
                continue
            ci = e.get("E1_ci95") or [None, None]
            noise = e.get("I1_noise_same_input_changed")
            lines.append(f"| {_name(m)} | {split} | {_f(e['E1_field_accuracy'], True)} [{_f(ci[0], True)}, {_f(ci[1], True)}] | "
                         f"{_f(e['item_exact'], True)} | {_f(e['E2_wrong_value_rate'], True)} | "
                         f"{e['I1_injection_pairs_changed']}/{e['I1_pairs']} / {'-' if noise is None else noise} | "
                         f"{e.get('latency_p50_s')}·{e.get('latency_p95_s')} | {_f(e.get('cost_usd'))} |")
    lines += ["", "### 계획 (회귀 30개, 반복)", "",
              "| 모델 | 반복 | 첫 회 통과 | C1 모든 반복 통과 | P1 후보 안 선택 | 선택 호출 수 | 실패 시나리오 |", "|---|---|---|---|---|---|---|"]
    for m in models:
        p = (data.get((m, "dev")) or {}).get("planning")
        if not p:
            continue
        lines.append(f"| {_name(m)} | {p['reps']} | {p['pass_rate_first'] * 30:.0f}/30 | {p['C1_pass_all_reps']}/30 | "
                     f"{_f(p.get('P1_valid_tool_choice'), True)} | {p.get('choose_calls')} | {', '.join(sorted(p.get('failed', {}))) or '-'} |")
    lines += ["", "### 짝지은 비교 (홀드아웃 추출 문항, 정확 McNemar)", "", "| 비교 | A만 정답 | B만 정답 | p |", "|---|---|---|---|"]
    hold = [(m, data[(m, "holdout")]["extraction"]) for m in models if (m, "holdout") in data and data[(m, "holdout")].get("extraction")]
    for i, (a, ea) in enumerate(hold):
        for b, eb in hold[i + 1:]:
            r = mcnemar_exact([x["correct"] for x in ea["items"]], [x["correct"] for x in eb["items"]])
            lines.append(f"| {_name(a)} vs {_name(b)} | {r['a_only']} | {r['b_only']} | {r['p']} |")
    return "\n".join(lines)


def main() -> None:
    text = DOC.read_text(encoding="utf-8")
    i, j = text.index(START) + len(START), text.index(END)
    DOC.write_text(text[:i] + "\n" + render() + "\n" + text[j:], encoding="utf-8")
    print(render())


if __name__ == "__main__":
    main()
