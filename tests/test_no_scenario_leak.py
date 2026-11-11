"""평가 누수 방지: 평가 대상 코드는 시나리오 ID나 시나리오 파일을 알면 안 된다 (평가표 원칙 5)."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "turnaround"
SCENARIO_ID = re.compile(r"(?<![A-Za-z0-9-])[A-F]0[1-9](?![0-9])")


def _targets():
    for p in SRC.rglob("*.py"):
        if "evaluation" in p.relative_to(SRC).parts:
            continue  # 평가 패키지는 시나리오를 다루는 쪽이라 제외
        yield p


# HL7 v2 ADT 트리거 코드(A01 입원, A02 전동, A03 퇴원, A08 정보 변경)는 표준 코드라 HL7 모듈에서만 허용한다 (DECISIONS 2026-11-12)
_HL7_FILES = {"hl7v2.py", "hl7_adapter.py"}
_HL7_TRIGGERS = {"A01", "A02", "A03", "A08"}


def test_no_scenario_ids_in_code_under_test():
    hits = [(p.name, m.group()) for p in _targets() for m in SCENARIO_ID.finditer(p.read_text(encoding="utf-8"))
            if not (p.name in _HL7_FILES and m.group() in _HL7_TRIGGERS)]
    assert hits == []


def test_code_under_test_does_not_read_scenario_files():
    hits = [p.name for p in _targets() if re.search(r"evals|scenarios", p.read_text(encoding="utf-8"))]
    assert hits == []
