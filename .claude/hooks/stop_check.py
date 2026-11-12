"""Stop 훅: 완료 전 자동 검증.

에이전트가 '끝났다'고 할 때, 바뀐 코드와 관련된 테스트를 돌려 실패하면 종료를 막고 실패 내용을 돌려준다.
전체 테스트(1,300개 이상)는 CI가 맡고, 여기서는 바뀐 파일과 이름이 이어지는 테스트만 빠르게 돌린다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, log, read_input  # noqa: E402


def changed_files() -> list[str]:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stdout
    files = []
    for line in out.splitlines():
        path = line[3:].strip().strip('"')
        if " -> " in path:
            path = path.split(" -> ")[-1]
        files.append(path)
    return files


def related_tests(files: list[str]) -> list[str]:
    tests = {f for f in files if re.match(r"tests/test_.*\.py$", f) and (ROOT / f).exists()}
    stems = {Path(f).stem for f in files if f.startswith(("src/", "scripts/")) and f.endswith(".py")}
    for t in (ROOT / "tests").glob("test_*.py"):
        if any(s and s != "__init__" and s in t.stem for s in stems):
            tests.add(t.relative_to(ROOT).as_posix())
    if (ROOT / ".claude/hooks").exists() and any(f.startswith(".claude/hooks/") for f in files):
        tests.add("tests/test_ai_hooks.py")
    return sorted(t for t in tests if (ROOT / t).exists())


def python_bin() -> str:
    for cand in (ROOT / ".venv/Scripts/python.exe", ROOT / ".venv/bin/python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def main() -> None:
    data = read_input()
    if data.get("stop_hook_active"):
        return
    files = changed_files()
    code = [f for f in files if f.endswith(".py")]
    if not code:
        log("checks", {"session": data.get("session_id", "")[:8], "result": "skip", "reason": "코드 변경 없음"})
        return
    tests = related_tests(code)
    if not tests:
        log("checks", {"session": data.get("session_id", "")[:8], "result": "skip", "reason": "관련 테스트 없음",
                       "files": code[:20]})
        return
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    r = subprocess.run([python_bin(), "-m", "pytest", "-q", "-x", *tests], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600, env=env)
    tail = (r.stdout + r.stderr).strip().splitlines()[-15:]
    passed = r.returncode == 0
    log("checks", {"session": data.get("session_id", "")[:8], "result": "pass" if passed else "fail",
                   "tests": tests, "summary": tail[-1] if tail else ""})
    if not passed:
        print(json.dumps({"decision": "block",
                          "reason": "관련 테스트가 실패해 완료로 처리하지 않았습니다. 원인을 고친 뒤 다시 끝내세요.\n"
                                    + "\n".join(tail)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
