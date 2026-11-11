"""LLM 판단의 규칙표 반영 (휴먼 인 더 루프, DECISIONS 2026-11-11 "맡기기 대신 규칙표 반영").

LLM 예외 판단은 실행하지 않고 기록만 한다(그림자). 그 기록과 담당자 결정을 맞대어
"규칙표와 다른 답을 LLM이 골랐고, 담당자도 LLM 쪽을 택한" 사례가 같은 유형에서 충분히 쌓이면
규칙표 수정안(우선 규칙)을 만든다. 책임자가 승인한 수정안만 config/rule_overrides.yaml에 들어가고,
규칙표(llm._fake_exception_action)는 승인된 우선 규칙을 먼저 본다. LLM 답을 실행에 쓰지 않는다.

세는 규칙 (기술 검수 반영):
- 담당자가 LLM 답을 본 사례(saw_model)와 규칙표가 갈 곳이 없어 LLM 답을 제안으로 보여 준 사례(shown_to_human)는 세지 않는다.
  LLM 답에 끌려간 일치는 증거가 아니다.
- 같은 유형에서 담당자가 규칙표 답을 고른 반대 사례도 분모에 넣는다.
- 기준: 그 유형의 깨끗한 사례 min_cases 이상, 지지 비율의 윌슨 95% 하한 min_agreement_lower95 이상
  (병상 배정 단계 맡김과 같은 PromotionRule 값, 25건이면 25건 모두 지지해야 넘는다).
- 안전 관련 유형(늦은 격리, 병상 없음)과 연락·확정·입실 뒤 단계는 수정안을 만들지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .autonomy import wilson_lower

ROOT = Path(__file__).resolve().parents[3]
OVERRIDES = ROOT / "config" / "rule_overrides.yaml"

# 수정안을 만들지 않는 곳: 안전·자원 유형과 사람 연락 뒤 단계 (승격 금지 목록과 같은 취지)
NEVER_TRIGGERS = frozenset({"late_isolation", "no_bed"})
LOCKED_STAGES = frozenset({"after_contact", "after_commit", "after_arrival"})
MIN_CASES, MIN_LB = 25, 0.85  # gateway.profile.PromotionRule 기본값과 같다


def features(ctx: dict) -> dict:
    """규칙표가 실제로 보는 조건만 뽑은 유형 키. 같은 키 = 같은 유형."""
    ex = ctx.get("exception") or {}
    st = ctx.get("case_state") or {}
    f = {
        "waiting": bool(st.get("next_admission_waiting") or st.get("waiting_matches")),
        "isolation_discharge": bool(st.get("isolation_discharge")),
        "mobility_aid": bool(st.get("mobility_aid")),
        "scarce": bool(st.get("scarce_room") or st.get("precaution") == "airborne"),
        "cleaning_started": bool(st.get("cleaning_started")),
        "from_icu": bool(st.get("from_icu")),
        "has_candidates": bool((ctx.get("tool_outputs") or {}).get("rematch_candidates")),
        "retried": bool(ex.get("attempts", 0)),
    }
    if st.get("correction_direction"):
        f["correction"] = str(st["correction_direction"])
    return f


def type_key(trigger: str | None, stage: str | None, feats: dict) -> str:
    return json.dumps([trigger, stage, sorted(feats.items())], ensure_ascii=False)


@dataclass(frozen=True)
class Case:
    """한 예외의 그림자 기록 + 담당자 결정."""
    exception_id: str
    trigger: str | None
    stage: str | None
    feats: dict
    rule_action: str
    model_action: str | None
    human_action: str | None
    saw_model: bool = False  # 담당자가 결정 전에 LLM 답을 봤다
    shown_to_human: bool = False  # 규칙표 기본값이라 LLM 답을 제안으로 보여 줬다

    @property
    def clean(self) -> bool:
        return (self.human_action is not None and self.model_action is not None
                and not self.saw_model and not self.shown_to_human)


def _base(a: str | None) -> str | None:
    return a.split(":", 1)[0] if isinstance(a, str) and a else None


def join_cases(shadow_log: list[dict], decisions: list[dict]) -> list[Case]:
    """그림자 기록과 담당자 결정 기록(exception_id, action, saw_model)을 잇는다."""
    dec = {d["exception_id"]: d for d in decisions}
    out = []
    for r in shadow_log:
        if r.get("status") != "ok":
            continue
        d = dec.get(r["exception_id"]) or {}
        out.append(Case(exception_id=r["exception_id"], trigger=r.get("trigger"), stage=r.get("stage"),
                        feats=dict(r.get("features") or {}), rule_action=_base(r["rule_action"]),
                        model_action=_base(r.get("model_action")), human_action=_base(d.get("action")),
                        saw_model=bool(d.get("saw_model")), shown_to_human=bool(r.get("shown_to_human"))))
    return out


@dataclass
class Proposal:
    id: str
    trigger: str | None
    stage: str | None
    features: dict
    from_action: str
    to_action: str
    n: int  # 그 유형의 깨끗한 사례 (분모)
    k: int  # 담당자가 LLM 답(to_action)을 택한 사례
    kept_rule: int  # 담당자가 규칙표 답을 택한 반대 사례
    lower95: float
    excluded_seen: int  # LLM 답을 봐서 뺀 사례
    ready: bool  # 기준을 넘었나 (안 넘은 후보도 보여 주되 승인할 수 없다)
    blocked: str | None = None

    def text(self) -> str:
        cond = ", ".join(k for k, v in sorted(self.features.items()) if v is True) or "조건 없음"
        return (f"[{self.id}] {self.trigger}/{self.stage or '-'} ({cond}): {self.from_action} → {self.to_action} "
                f"| 지지 {self.k}/{self.n}, 반대 {self.kept_rule}, 하한 {self.lower95:.3f}, 본 사례 제외 {self.excluded_seen}"
                + (" | 승인 가능" if self.ready else f" | 보류({self.blocked or '기준 미달'})"))


def propose(cases: list[Case], min_cases: int = MIN_CASES, min_lb: float = MIN_LB) -> list[Proposal]:
    """유형마다 '규칙표와 다르고 담당자도 택한 LLM 답'을 세어 수정안 후보를 만든다."""
    groups: dict[str, list[Case]] = {}
    for c in cases:
        groups.setdefault(type_key(c.trigger, c.stage, c.feats), []).append(c)
    out = []
    for key, cs in groups.items():
        clean = [c for c in cs if c.clean]
        seen = sum(1 for c in cs if c.human_action is not None and not c.clean)
        rule_actions = {c.rule_action for c in clean}
        if not clean or len(rule_actions) != 1:
            continue  # 같은 유형인데 규칙표 답이 갈리면 유형 정의가 부족한 것: 수정안을 만들지 않는다
        rule = rule_actions.pop()
        wins: dict[str, int] = {}
        for c in clean:
            if c.model_action != rule and c.human_action == c.model_action:
                wins[c.model_action] = wins.get(c.model_action, 0) + 1
        if not wins:
            continue
        to, k = max(wins.items(), key=lambda kv: kv[1])
        n = len(clean)
        lb = wilson_lower(k, n)
        c0 = clean[0]
        blocked = None
        if c0.trigger in NEVER_TRIGGERS:
            blocked = "안전 유형"
        elif c0.stage in LOCKED_STAGES:
            blocked = "연락 뒤 단계"
        ready = blocked is None and n >= min_cases and lb >= min_lb
        pid = hashlib.sha1(f"{key}|{to}".encode()).hexdigest()[:8]
        out.append(Proposal(id=pid, trigger=c0.trigger, stage=c0.stage, features=dict(c0.feats), from_action=rule,
                            to_action=to, n=n, k=k, kept_rule=sum(1 for c in clean if c.human_action == rule),
                            lower95=round(lb, 4), excluded_seen=seen, ready=ready, blocked=blocked))
    return sorted(out, key=lambda p: (-p.ready, -p.lower95, -p.k))


# ---- 승인된 우선 규칙 (규칙표가 먼저 본다) ----

def load_overrides(path: Path = OVERRIDES) -> list[dict]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [o for o in data.get("overrides") or [] if o.get("active", True)]


def match_override(ctx: dict, overrides: list[dict]) -> dict | None:
    """승인된 우선 규칙 중 이 사례 유형과 같은 것. 허용 목록 밖 행동이나 잠긴 단계에는 쓰지 않는다."""
    if not overrides:
        return None
    ex = ctx.get("exception") or {}
    trig, stage = ex.get("trigger"), ex.get("stage")
    if trig in NEVER_TRIGGERS or stage in LOCKED_STAGES:
        return None
    allowed = {a["id"] for a in ctx.get("allowed_actions", [])}
    key = type_key(trig, stage, features(ctx))
    for o in overrides:
        if type_key(o.get("trigger"), o.get("stage"), o.get("features") or {}) == key and o["action"] in allowed:
            return {"action": o["action"], "params": dict(o.get("params") or {}),
                    "rationale": f"승인된 규칙 {o['id']}: {o.get('reason') or ''}".strip()}
    return None


def approve(p: Proposal, approver: str, reason: str = "", path: Path = OVERRIDES,
            now: datetime | None = None) -> str:
    """책임자 승인: 기준을 넘은 수정안만 규칙 파일에 넣는다. 바뀐 파일의 sha256을 돌려준다(재인수 시험 표시용)."""
    if not p.ready:
        raise ValueError(f"{p.id}: 기준 미달 또는 금지 유형이라 승인할 수 없다 ({p.blocked or '기준 미달'})")
    if not approver:
        raise ValueError("승인자가 필요하다")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    data = data or {"overrides": []}
    data.setdefault("overrides", [])
    if any(o.get("id") == p.id for o in data["overrides"]):
        raise ValueError(f"{p.id}: 이미 있는 규칙")
    data["overrides"].append({
        "id": p.id, "trigger": p.trigger, "stage": p.stage, "features": p.features, "action": p.to_action,
        "replaces": p.from_action, "reason": reason or f"담당자 {p.k}/{p.n}건이 {p.to_action} 선택",
        "evidence": {"n": p.n, "k": p.k, "kept_rule": p.kept_rule, "lower95": p.lower95,
                     "excluded_seen": p.excluded_seen},
        "approved_by": approver, "approved_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "active": True})
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def revoke(rule_id: str, path: Path = OVERRIDES) -> None:
    """되돌리기: 규칙을 지우지 않고 끈다(기록 보존)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for o in data.get("overrides") or []:
        if o.get("id") == rule_id:
            o["active"] = False
            path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            return
    raise KeyError(rule_id)


__all__ = ["Case", "Proposal", "approve", "features", "join_cases", "load_overrides", "match_override",
           "propose", "revoke", "type_key"]
