"""문서가 코드·결과와 어긋나지 않는지 CI에서 검사한다 (2026-11-12, 2차 AI 페르소나 검수 뒤).

검수 때마다 "끊긴 참조"와 "문서마다 다른 숫자"가 반복해서 나왔다. 사람이 그때그때 고치는 대신 시험으로 막는다.
1) 살아 있는 문서가 가리키는 저장소 경로는 실제로 있어야 한다. DECISIONS·RESULTS는 날짜순 기록이라 옛 경로가
   남는 것이 맞으므로 검사하지 않는다.
2) README 핵심 결과의 숫자는 결과 파일에서 다시 계산한 값과 같아야 한다.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVING = ["README.md", "CLAUDE.md", "docs/AI-DD.md", "docs/2단계_온보딩_스펙.md",
          *sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "docs/기획").glob("*.md"))]
_PATH = re.compile(r"`((?:docs|src|scripts|tests|evals|config|web)/[^`\s*…]+?)`")
FINAL = ROOT / "evals/results/final_1112"


@pytest.mark.parametrize("doc", LIVING)
def test_living_docs_point_to_existing_paths(doc):
    text = (ROOT / doc).read_text(encoding="utf-8")
    missing = []
    for raw in _PATH.findall(text):
        path = raw.split(":")[0].split("#")[0].rstrip(".,)/")
        if any(c in path for c in "<>{}|") or path.endswith(("_", "-")):
            continue  # 자리표시자(예: `evals/results/<날짜>.json`)
        if "*" in path:
            if not list(ROOT.glob(path)):
                missing.append(raw)
        elif not (ROOT / path).exists():
            missing.append(raw)
    assert not missing, f"{doc}: 없는 경로 {sorted(set(missing))}"


def _load(name):
    return json.loads((FINAL / name).read_text(encoding="utf-8"))


def headline() -> dict:
    """README 핵심 결과 숫자를 결과 파일에서 계산한다 ."""
    on, off = _load("cycle_50_on.json"), _load("cycle_50_off.json")
    hold = _load("cycle_holdout_50_99_on.json")
    stress = _load("safety_stress_1000_1399.json")
    ci = [json.loads(x) for x in (FINAL / "effect_ci.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()][0]
    m1 = _load("m1.json")
    safety = {}
    for k in ("S1", "S2", "S3", "S4"):
        n = on["safety"][k + "_n"] + hold["safety"][k + "_n"]
        v = on["safety"][k] + hold["safety"][k]
        ns, vs = stress["safety"][k + "_n"], stress["safety"][k]
        safety[k] = {"violations": v, "n": n, "stress_violations": vs, "stress_n": ns,
                     # 위반 0건일 때 95% 상한: 1 - 0.05^(1/n) (3의 규칙의 정확한 꼴)
                     "upper95_pct": round((1 - 0.05 ** (1 / (n + ns))) * 100, 2) if v + vs == 0 else None}
    return {"safety": safety, "safety_total": sum(s["n"] for s in safety.values()),
            "safety_violations": sum(s["violations"] + s["stress_violations"] for s in safety.values()),
            "wait_off": ci["mean_off"], "wait_on": ci["mean_on"], "wait_ci": ci["ci95_min"], "pairs": ci["pairs"],
            "m1": m1}


def test_readme_headline_numbers_match_result_files():
    h = headline()
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert h["safety_violations"] == 0
    assert f"0 / {h['safety_total']}" in text
    for k, s in h["safety"].items():
        assert f"0/{s['n'] + s['stress_n']}" in text, (k, s)
    assert f"{h['wait_off']} → {h['wait_on']}분" in text
    lo, hi = h["wait_ci"]
    assert f"{lo:+.1f} ~ {hi:+.1f}분".replace("+", "−").replace("-", "−") in text.replace("+", "−")
