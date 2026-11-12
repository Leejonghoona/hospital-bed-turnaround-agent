"""Claude Code 훅 공용 함수.

훅은 Claude Code가 매번 강제로 실행한다. 모델이 지시를 잊어도 규칙이 지켜지게,
계획 승인·위험 차단·기록·완료 검사를 프롬프트가 아니라 여기에 둔다.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
CLAUDE_DIR = ROOT / ".claude"
LOG_DIR = CLAUDE_DIR / "logs"
PLAN_DIR = ROOT / "plans"
STATE_FILE = PLAN_DIR / "STATE.json"


def read_input() -> dict:
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def log(name: str, record: dict) -> None:
    """logs/<name>.jsonl 에 한 줄 추가. 기록 실패가 작업을 막지 않게 조용히 넘어간다."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / f"{name}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), **record}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def rel(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    try:
        return p.resolve().relative_to(ROOT.resolve()).as_posix()
    except (ValueError, OSError):
        return p.as_posix()


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "none"}


def save_state(state: dict) -> None:
    PLAN_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def deny(reason: str) -> None:
    """PreToolUse 거부. 사유는 모델에게 돌아가 다른 방법을 찾게 한다."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)
