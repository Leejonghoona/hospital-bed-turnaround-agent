"""자율권 승격·강등 (로드맵 07 M3, DECISIONS 2026-11-09).

에이전트가 사람 개입 기록을 보고 "이 유형은 사람이 늘 그대로 승인했다"를 증명해 자율권을 넓히자고 제안한다.
판정은 결정적 통계다(CLAUDE.md 2번). LLM은 쓰지 않는다.

- 단위: 담당 검토 사유 하나(그 사유만으로 검토된 사례), 그리고 `official_commit`(검토 사유 없는 사례의 확정).
- 일치: 사람이 에이전트 제안 병상을 그대로 승인. 불일치: 사람이 다른 병상으로 직접 수정, 또는 에이전트 확정 뒤 병동 이의.
- 승격: 사람 결정 사례가 기준 수 이상이고 일치율 윌슨 95% 하한이 기준 이상이며 승격 금지 목록에 없을 때 제안.
  재생 증명(과거 날을 새 정책으로 다시 돌려 실행 안전 위반 0)은 `evaluation.autonomy_curve`가 붙인다. 승인은 사람이 한다.
- 강등: 승격된 유형에서 최근 에이전트 결정 중 불일치가 기준 수에 닿으면 자동으로 내린다(안전을 조이는 쪽은 자동).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from turnaround.events import Event, EventType

COMMIT = "official_commit"


@dataclass(frozen=True)
class Outcome:
    unit: str
    case: str
    proposal_id: str
    agreed: bool
    by: str  # human | agent (누가 결정했나)


def wilson_lower(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / den


def unit_outcomes(events: list[Event], cases: set[str] | None = None) -> list[Outcome]:
    """사례별 결정 결과. cases를 주면 그 사례만 본다(하루 재생에서 그날 입원만)."""
    keep = (lambda c: True) if cases is None else (lambda c: c in cases)  # noqa: E731
    overridden = {e.case_id for e in events if e.type is EventType.MANUAL_OVERRIDE_APPLIED}
    objected = {e.payload.get("proposal_id") for e in events if e.type is EventType.WARD_OBJECTION_RAISED}
    committed = {e.payload.get("proposal_id"): e for e in events
                 if e.type is EventType.MATCH_COMMITTED and not e.payload.get("manual")}
    out: list[Outcome] = []
    seen: set[str] = set()
    for e in events:
        pid = e.payload.get("proposal_id")
        if not keep(e.case_id) or pid in seen:
            continue
        if e.type is EventType.CASE_ROUTED_REVIEW:
            reasons = e.payload.get("reasons") or []
            unit = reasons[0] if len(reasons) == 1 else None
        elif e.type is EventType.MANAGER_CONFIRM_REQUESTED:
            unit = COMMIT
        else:
            continue
        seen.add(pid)
        if unit is None:
            continue
        if e.case_id in overridden:
            out.append(Outcome(unit, e.case_id, pid, False, "human"))
        elif pid in committed:
            out.append(Outcome(unit, e.case_id, pid, True, "human"))
    for e in events:  # 에이전트가 확정한 사례: 승격된 단위의 강등 판정용
        if e.type is not EventType.WARD_ASSIGNMENT_NOTIFIED or e.payload.get("committed_by") != "agent":
            continue
        if not keep(e.case_id):
            continue
        pid = e.payload.get("proposal_id")
        bad = pid in objected or e.case_id in overridden
        for unit in [COMMIT] + list((e.payload.get("basis") or {}).get("waived_reasons") or []):
            out.append(Outcome(unit, e.case_id, pid, not bad, "agent"))
    return out


def current_mode(unit: str, policy) -> str:
    if unit == COMMIT:
        return "auto" if policy.autonomy.get(COMMIT, "human") != "human" else "human"
    return "human" if unit in policy.review_reasons else "auto"


def evaluate(outcomes: list[Outcome], policy, since: dict[str, int] | None = None) -> list[dict]:
    """단위마다 현재 단계, 근거, 권고(promote | demote | hold).

    since: 단위별로 마지막 강등 시점(outcomes 위치). 강등 뒤에는 그 뒤의 기록만 센다.
    옛 근거로 다음 날 다시 승격하면 날마다 오르내린다(2026-11-09 측정에서 발견).
    """
    rule = policy.autonomy_promotion
    since = since or {}
    units = sorted({o.unit for o in outcomes})
    rows = []
    for u in units:
        mode = current_mode(u, policy)
        recent_all = outcomes[since.get(u, 0):]
        human = [o for o in recent_all if o.unit == u and o.by == "human"]
        agent = [o for o in recent_all if o.unit == u and o.by == "agent"]
        k, n = sum(o.agreed for o in human), len(human)
        lb = wilson_lower(k, n)
        recent = agent[-rule.demote_window:]
        bad = sum(not o.agreed for o in recent)
        rec = "hold"
        if mode == "human" and u not in rule.never and n >= rule.min_cases and lb >= rule.min_agreement_lower95:
            rec = "promote"
        elif mode == "auto" and bad >= rule.demote_disagreements:
            rec = "demote"
        rows.append({"unit": u, "mode": mode, "human_n": n, "human_agreed": k, "agreement_lower95": round(lb, 3),
                     "agent_n": len(agent), "agent_recent_disagreements": bad, "never": u in rule.never,
                     "recommendation": rec})
    return rows


def apply(policy, unit: str, to: str):
    """정책 사본을 돌려준다. to = auto(승격) | human(강등)."""
    if unit == COMMIT:
        return policy.model_copy(update={"autonomy": {**policy.autonomy, COMMIT: "auto" if to == "auto" else "human"}})
    reasons = [r for r in policy.review_reasons if r != unit]
    if to == "human":
        reasons.append(unit)
    return policy.model_copy(update={"review_reasons": reasons})


__all__ = ["COMMIT", "Outcome", "apply", "current_mode", "evaluate", "unit_outcomes", "wilson_lower"]
