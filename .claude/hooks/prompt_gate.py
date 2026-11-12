"""UserPromptSubmit 훅: 사람 입력 기록과 계획 승인.

- 모든 입력을 logs/prompts.jsonl 에 남긴다 (원문 앞부분과 해시). 나중에 어떤 지시로 어떤 변경이 나왔는지 추적한다.
- 사람이 '승인' 또는 'approve' 를 입력하면 가장 최근 계획을 승인 상태로 기록한다.
  이 훅은 사람이 입력할 때만 실행되므로, AI가 스스로 자기 계획을 승인할 수 없다.
- '반려 <사유>' 는 계획을 되돌리고 사유를 기록한다.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import PLAN_DIR, load_state, log, now, read_input, rel, save_state, short_hash  # noqa: E402


def latest_plan() -> Path | None:
    plans = [p for p in PLAN_DIR.glob("*.md")] if PLAN_DIR.exists() else []
    return max(plans, key=lambda p: p.stat().st_mtime) if plans else None


def context(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}},
                     ensure_ascii=False))


def main() -> None:
    data = read_input()
    prompt = (data.get("prompt") or "").strip()
    session = data.get("session_id", "")[:8]
    log("prompts", {"session": session, "chars": len(prompt), "hash": short_hash(prompt), "head": prompt[:300]})

    m = re.match(r"^(승인|approve)\b\s*(.*)$", prompt, re.IGNORECASE | re.DOTALL)
    if m:
        plan = latest_plan()
        if plan is None:
            context("승인할 계획이 없습니다. 먼저 planner로 계획을 작성하세요.")
            return
        body = plan.read_text(encoding="utf-8")
        state = {"status": "approved", "plan": rel(str(plan)), "plan_hash": short_hash(body),
                 "approved_at": now(), "approved_by": "human-prompt", "note": m.group(2)[:200]}
        save_state(state)
        log("approvals", {"session": session, **state})
        context(f"사람이 계획 {state['plan']} 을(를) 승인했습니다. 계획 범위 안에서만 구현하고, 범위를 벗어나면 멈추고 다시 계획하세요.")
        return

    m = re.match(r"^(반려|reject)\b\s*(.*)$", prompt, re.IGNORECASE | re.DOTALL)
    if m:
        st = load_state()
        st.update({"status": "rejected", "rejected_at": now(), "reason": m.group(2)[:300]})
        save_state(st)
        log("approvals", {"session": session, **st})
        context("계획이 반려되었습니다. 사유를 반영해 planner로 계획을 다시 쓰세요. 코드 수정은 막혀 있습니다.")


if __name__ == "__main__":
    main()
