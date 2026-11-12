"""PostToolUse·SubagentStart·SubagentStop 훅: 도구 사용 기록.

어떤 역할(agent_type)이 어떤 도구로 무엇을 했는지 logs/tools.jsonl 에 남긴다.
문제가 생기면 이 기록으로 작업 순서를 다시 따라가 원인을 찾는다.
계획 문서가 새로 쓰이면 이전 승인을 무효로 돌린다 (승인 뒤 계획을 몰래 바꾸는 것 방지).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import ROOT, load_state, log, now, read_input, rel, save_state, short_hash  # noqa: E402


def summarize(tool: str, ti: dict) -> str:
    if tool == "Bash":
        return (ti.get("command") or "")[:200]
    if tool in {"Edit", "Write", "MultiEdit", "Read", "NotebookEdit"}:
        return rel(ti.get("file_path") or ti.get("notebook_path"))
    if tool in {"Grep", "Glob"}:
        return f"{ti.get('pattern', '')} @ {ti.get('path', '')}"[:200]
    if tool in {"Agent", "Task"}:
        return f"{ti.get('subagent_type', '')}: {(ti.get('description') or '')[:120]}"
    return ""


def main() -> None:
    data = read_input()
    event = data.get("hook_event_name", "")
    session = data.get("session_id", "")[:8]
    agent = data.get("agent_type") or "main"

    if event in {"SubagentStart", "SubagentStop"}:
        log("agents", {"session": session, "event": event, "agent": agent, "agent_id": data.get("agent_id", "")})
        return

    tool = data.get("tool_name", "")
    ti = data.get("tool_input") or {}
    resp = data.get("tool_response")
    ok = not (isinstance(resp, dict) and (resp.get("is_error") or resp.get("error")))
    log("tools", {"session": session, "agent": agent, "tool": tool, "target": summarize(tool, ti),
                  "ok": ok, "duration_ms": data.get("duration_ms")})

    target = rel(ti.get("file_path"))
    if tool in {"Write", "Edit", "MultiEdit"} and target.startswith("plans/") and target.endswith(".md"):
        st = load_state()
        body = ROOT / target
        try:
            h = short_hash(body.read_text(encoding="utf-8"))
        except OSError:
            h = ""
        if not (st.get("status") == "approved" and st.get("plan") == target and st.get("plan_hash") == h):
            save_state({"status": "draft", "plan": target, "plan_hash": h, "drafted_at": now(), "drafted_by": agent})
            log("approvals", {"session": session, "status": "draft", "plan": target, "by": agent})


if __name__ == "__main__":
    main()
