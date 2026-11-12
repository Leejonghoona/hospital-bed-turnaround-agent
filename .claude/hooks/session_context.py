"""SessionStart 훅: 세션을 시작할 때 현재 상태를 자동으로 알려 준다.

매번 같은 탐색(브랜치, 최근 커밋, 계획 승인 상태, 마지막 검증 결과)을 반복하지 않게 한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import LOG_DIR, ROOT, load_state, read_input  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace").stdout.strip()


def last_line(name: str) -> str:
    p = LOG_DIR / f"{name}.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        return lines[-1] if lines else "없음"
    except OSError:
        return "없음"


def main() -> None:
    read_input()
    st = load_state()
    text = "\n".join([
        f"브랜치: {git('branch', '--show-current')} / 최근 커밋: {git('log', '--oneline', '-1')}",
        f"바뀐 파일: {len(git('status', '--porcelain').splitlines())}개",
        f"계획 상태: {st.get('status')} {st.get('plan', '')}",
        f"마지막 완료 검증: {last_line('checks')}",
        "작업 순서: researcher로 조사 → planner로 .claude/plans/ 에 계획 → 사람 '승인' → 구현 → reviewer 검토 → 완료 검증(Stop 훅)",
    ])
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
