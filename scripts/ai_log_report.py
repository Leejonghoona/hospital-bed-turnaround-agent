"""AI 개발 기록 집계 (.claude/logs/*.jsonl).

훅이 남긴 기록으로 다음을 센다.
- 세션 수, 사람 입력 수
- 역할별(main, researcher, planner, reviewer) 도구 사용 수
- 규칙별 차단 수 (G1 계획 승인, G2 승인 파일, G3 역할, G4 평가셋, G5 비밀, G6 위험 명령)
- 계획 초안·승인·반려 수
- 완료 검증(Stop 훅) 통과·실패·생략 수

사용: python scripts/ai_log_report.py [--json]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[1] / ".claude" / "logs"


def load(name: str) -> list[dict]:
    p = LOG_DIR / f"{name}.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def summarize() -> dict:
    prompts, tools, guard, approvals, checks = (load(n) for n in ("prompts", "tools", "guard", "approvals", "checks"))
    sessions = {r.get("session") for r in prompts + tools if r.get("session")}
    return {
        "sessions": len(sessions),
        "prompts": len(prompts),
        "tool_calls_by_agent": dict(Counter(r.get("agent", "main") for r in tools)),
        "tool_calls_by_tool": dict(Counter(r.get("tool") for r in tools).most_common(8)),
        "blocked_by_rule": dict(sorted(Counter(r.get("rule") for r in guard).items())),
        "plans": dict(Counter(r.get("status") for r in approvals)),
        "checks": dict(Counter(r.get("result") for r in checks)),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    s = summarize()
    if ap.parse_args().json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return
    print(f"세션 {s['sessions']}개, 사람 입력 {s['prompts']}건")
    for key, title in [("tool_calls_by_agent", "역할별 도구 사용"), ("tool_calls_by_tool", "도구별 사용"),
                       ("blocked_by_rule", "규칙별 차단"), ("plans", "계획 상태 기록"), ("checks", "완료 검증")]:
        items = ", ".join(f"{k} {v}" for k, v in s[key].items()) or "없음"
        print(f"- {title}: {items}")


if __name__ == "__main__":
    main()
