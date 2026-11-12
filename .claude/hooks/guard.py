"""PreToolUse 훅: 계획 승인 게이트, 역할별 권한, 위험 명령, 보호 파일.

차단 규칙
- G1 계획 승인 전 코드·문서 수정 금지 (사람이 승인한 계획이 있어야 구현 단계로 넘어간다)
- G2 승인 상태 파일(plans/STATE.json)은 AI가 쓸 수 없다 (승인은 사람 입력 훅만 기록)
- G3 역할 분리: researcher·reviewer는 파일을 고칠 수 없고, planner는 계획 문서만 쓴다
- G4 평가 정답 세트와 지난 측정 결과는 고칠 수 없다 (평가셋 오염 방지)
- G5 비밀 정보(.env, 키 파일) 읽기·쓰기 금지
- G6 되돌리기 어려운 명령 금지 (강제 푸시, hard reset, 재귀 삭제 등)
"""
from __future__ import annotations

import fnmatch
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, deny, load_state, log, rel, read_input, short_hash  # noqa: E402

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# 계획 승인이 있어야 고칠 수 있는 경로
GATED = ["src/*", "tests/*", "scripts/*", "config/*", "web/*", "evals/*", "docs/*",
         "README.md", "CLAUDE.md", "Dockerfile", "pyproject.toml", ".github/*"]

# 평가 정답·측정 기록 (계획이 승인돼도 AI가 고칠 수 없음)
PROTECTED = ["evals/extraction/holdout.jsonl", "evals/model_eval/*.jsonl", "evals/hl7/answers.json",
             "evals/hl7/*expected*", "evals/scenarios/*", "evals/results/*"]

SECRET = [".env", ".env.*", "*.pem", "*.key", "*secret*", "*credentials*"]

DANGEROUS = [
    (r"\bgit\s+push\b.*(--force|-f\b)", "강제 푸시"),
    (r"\bgit\s+reset\s+--hard\b", "hard reset"),
    (r"\bgit\s+clean\s+-[a-z]*f", "git clean -f"),
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "재귀 강제 삭제"),
    (r"Remove-Item\b.*-Recurse", "재귀 삭제"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sh|bash|python)", "내려받은 스크립트 바로 실행"),
    (r"(cat|type|less|more|head|tail|Get-Content)\s+[^|;&]*\.env\b", ".env 읽기"),
    (r"\b(printenv|env)\b[^|]*\|?\s*.*(KEY|TOKEN|SECRET)", "비밀 환경 변수 출력"),
    (r"STATE\.json", "승인 상태 파일 직접 수정"),
]


def match(path: str, patterns: list[str]) -> bool:
    name = Path(path).name
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)


def block(rule: str, reason: str, data: dict, target: str) -> None:
    log("guard", {"session": data.get("session_id", "")[:8], "agent": data.get("agent_type") or "main",
                  "tool": data.get("tool_name"), "rule": rule, "target": target[:200], "decision": "deny"})
    deny(f"[{rule}] {reason}")


def main() -> None:
    data = read_input()
    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    agent = data.get("agent_type") or "main"

    if tool == "Bash":
        cmd = ti.get("command", "")
        for pat, what in DANGEROUS:
            if re.search(pat, cmd, re.IGNORECASE):
                rule = "G2" if what.startswith("승인") else ("G5" if ".env" in what or "비밀" in what else "G6")
                block(rule, f"{what}은(는) 막혀 있습니다. 필요하면 사람에게 요청하세요.", data, cmd)
        if agent in {"researcher", "reviewer", "planner"} and re.search(r"(>|>>|\bsed\s+-i|\btee\b|\bmv\b|\bcp\b)", cmd):
            block("G3", f"{agent} 역할은 파일을 바꾸는 명령을 쓸 수 없습니다.", data, cmd)
        return

    if tool not in EDIT_TOOLS:
        return

    target = rel(ti.get("file_path") or ti.get("notebook_path"))

    if match(target, SECRET):
        block("G5", "비밀 정보 파일은 수정할 수 없습니다.", data, target)
    if target == "plans/STATE.json":
        block("G2", "승인 상태는 사람이 '승인'을 입력할 때만 기록됩니다.", data, target)
    if agent in {"researcher", "reviewer"}:
        block("G3", f"{agent} 역할은 읽기 전용입니다. 발견한 내용은 보고로만 남기세요.", data, target)
    if agent == "planner" and not fnmatch.fnmatch(target, "plans/*.md"):
        block("G3", "planner는 plans/ 아래 계획 문서만 쓸 수 있습니다.", data, target)
    if match(target, PROTECTED) and (ROOT / target).exists():
        block("G4", "평가 정답 세트와 지난 측정 결과는 고칠 수 없습니다. 새 버전 파일을 만들거나 사람에게 요청하세요.", data, target)
    if match(target, GATED):
        state = load_state()
        if state.get("status") != "approved":
            block("G1", "승인된 계획이 없습니다. planner로 plans/ 에 계획을 쓰고 사람이 '승인'을 입력한 뒤 구현하세요.", data, target)
        plan = ROOT / state.get("plan", "")
        try:
            same = short_hash(plan.read_text(encoding="utf-8")) == state.get("plan_hash")
        except OSError:
            same = False
        if not same:
            block("G1", "승인 뒤 계획 문서가 바뀌었습니다. 바뀐 계획을 다시 승인받으세요.", data, target)


if __name__ == "__main__":
    main()
