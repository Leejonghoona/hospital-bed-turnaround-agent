"""학습 사전 (08 계획 3단계): 사람이 온보딩 질문에 답한 내용을 다음 병원의 매핑 후보로 쓴다.

- 배우는 것: 열 이름 → 정규 키, 코드값 → 정규값, 사건 코드 → 사건, 병상 표의 병동·병실·병상 열 이름, 쓰지 않은 열.
- 공용 사전에 들어가려면 회귀 관문을 통과해야 한다: 알려진 모든 내보내기 형식에서 틀린 열·코드·형식 0, 정답으로 답하면
  정규 사건과 완전히 같음, 그리고 사람에게 내는 제안이 정답과 어긋나지 않음(틀린 제안은 사람이 그대로 승인할 수 있다).
  묶음이 떨어지면 반씩 나눠 통과하는 항목만 받는다.
- 이미 있는 항목과 다른 값은 조용히 덮어쓰지 않는다(충돌로 보고 사람이 정한다).
- 사전은 후보일 뿐이다. 안전 속성 코드는 사전에 있어도 늘 사람이 확인하고, 사건 순서 검사·중복 검사도 그대로 돈다.
학습 사전은 합성 데이터 기준이며, 같은 공급사 병원끼리 열 이름이 같다는 것은 설계 가정이다.
"""

from __future__ import annotations

import copy
import re
from functools import lru_cache
from types import SimpleNamespace

from pydantic import BaseModel, ConfigDict, Field

from .mapping_agent import _CODE_KEYS, IGNORE


class Lexicon(BaseModel):
    model_config = ConfigDict(frozen=True)
    columns: dict[str, str] = Field(default_factory=dict)  # 열 이름 → 정규 키
    values: dict[str, dict[str, str]] = Field(default_factory=dict)  # 값 어휘 키 → {원천 코드: 정규값}
    events: dict[str, str] = Field(default_factory=dict)  # 사건 코드 → 사건 이름
    layouts: list[tuple[str, str, str]] = Field(default_factory=list)  # 병상 표의 (병동, 병실, 병상) 열 이름
    ignore: frozenset[str] = frozenset()  # 쓰지 않은 열 (확인 질문의 제안으로만 쓴다)
    provenance: dict[str, str] = Field(default_factory=dict)  # 항목 → 배운 병원

    def apply(self, cand: dict, ctx: dict) -> dict:
        """후보(가짜 LLM 또는 실제 모델)에 학습 항목을 맨 앞 후보로 넣는다."""
        out = copy.deepcopy(cand)
        for t, info in ctx["tables"].items():
            cols = out["columns"].setdefault(t, {})
            for c, ci in info["columns"].items():
                k = self.columns.get(c)
                if k:
                    cols[c] = [k] + [x for x in (cols.get(c) or []) if x != k]
                for code in ci.get("codes", []):
                    for part in {code, *re.split(r"[|+;,]", code)}:
                        for vkey, m in self.values.items():
                            if part in m:
                                lst = out["values"].setdefault(t, {}).setdefault(c, {}).setdefault(part, [])
                                v = f"{vkey}:{m[part]}"
                                if v in lst:
                                    lst.remove(v)
                                lst.insert(0, v)
        for code in ctx.get("event_codes", []):
            e = self.events.get(code)
            if e:
                out["event_codes"][code] = [e] + [x for x in (out["event_codes"].get(code) or []) if x != e]
        return out

    # 항목 단위 (관문에서 나눠 시험한다)
    def entries(self) -> list[tuple[str, str, object]]:
        out = [("column", c, k) for c, k in self.columns.items()]
        out += [("value", f"{vk}|{code}", canon) for vk, m in self.values.items() for code, canon in m.items()]
        out += [("event", code, e) for code, e in self.events.items()]
        out += [("layout", "|".join(ps), tuple(ps)) for ps in self.layouts]
        out += [("ignore", c, True) for c in sorted(self.ignore)]
        return out

    def plus(self, entries, source: str | None = None) -> "Lexicon":
        cols, vals, evs = dict(self.columns), {k: dict(v) for k, v in self.values.items()}, dict(self.events)
        lays, ign, prov = list(self.layouts), set(self.ignore), dict(self.provenance)
        for kind, key, v in entries:
            if kind == "column":
                cols[key] = v
            elif kind == "value":
                vk, code = key.split("|", 1)
                vals.setdefault(vk, {})[code] = v
            elif kind == "event":
                evs[key] = v
            elif kind == "layout" and tuple(v) not in lays:
                lays.append(tuple(v))
            elif kind == "ignore":
                ign.add(key)
            if source:
                prov[f"{kind}:{key}"] = source
        return Lexicon(columns=cols, values=vals, events=evs, layouts=lays, ignore=frozenset(ign), provenance=prov)

    def lookup(self, kind: str, key: str):
        if kind == "column":
            return self.columns.get(key)
        if kind == "value":
            vk, code = key.split("|", 1)
            return self.values.get(vk, {}).get(code)
        if kind == "event":
            return self.events.get(key)
        if kind == "ignore":
            return True if key in self.ignore else None
        return None


def learn(session) -> Lexicon:
    """끝난 온보딩 세션에서 사람이 답한 항목을 후보 사전으로 뽑는다(아직 공용 아님)."""
    ent = []
    for q in session.asked:
        a = session.answers.get(q.id)
        if q.id.startswith("col:"):
            c = q.id.split(":", 2)[2]
            ent.append(("ignore", c, True) if a in (None, IGNORE) else ("column", c, a))
        elif q.id.startswith("code:"):
            _, k, v = q.id.split(":", 2)
            vk = _CODE_KEYS.get(k)
            if vk and a is not None and a != v:
                ent.append(("value", f"{vk}|{v}", a))
        elif q.id.startswith("evt:") and a:
            ent.append(("event", q.id[4:], a))
    if session.layout_answered and session.mapping is not None and all(session.mapping.bed_parts):
        ent.append(("layout", "|".join(session.mapping.bed_parts), tuple(session.mapping.bed_parts)))
    # 같은 열 이름이 쓰임·안 쓰임으로 갈리면(표마다 다른 열) 배우지 않는다
    kinds: dict[str, set] = {}
    for kind, key, _ in ent:
        if kind in ("column", "ignore"):
            kinds.setdefault(key, set()).add(kind)
    clash = {k for k, ks in kinds.items() if len(ks) > 1}
    seen: dict[tuple[str, str], object] = {}
    for kind, key, v in ent:
        if kind in ("column", "ignore") and key in clash:
            continue
        if (kind, key) in seen and seen[(kind, key)] != v:
            clash.add(key)
            continue
        seen[(kind, key)] = v
    return Lexicon().plus([(k, key, v) for (k, key), v in seen.items() if key not in clash], source=session.hospital)


# ---------------- 회귀 관문 ----------------

GATE_SEEDS = (1, 2)


@lru_cache(maxsize=8)
def _canonical(seed: int):
    from turnaround.events.replay import hospital_to_events
    from turnaround.sim import GeneratorConfig, generate
    h = generate(seed=seed, config=GeneratorConfig.realistic())
    ev = [e.model_copy(update={"observed_at": e.occurred_at}) for e in hospital_to_events(h, seed=seed)]
    return h, ev


def _gate_one(lex: Lexicon, fmt: str, seed: int) -> tuple[list[str], int]:
    from turnaround.evaluation.mapping_eval import gold_answers, score
    from turnaround.sim.exports import export, gold_mapping
    from .mapping_agent import propose_mapping
    h, ev = _canonical(seed)
    tables, gold = export(h, ev, fmt), gold_mapping(fmt)
    r = score(tables, gold, ev, h, lexicon=lex)
    bad = []
    if r["columns"]["wrong"] or r["codes"]["wrong"] or "wrong" in r["format"].values():
        bad.append(f"{fmt}/{seed}: 틀림 {r['wrong_columns'][:2]}{r['wrong_codes'][:2]}")
    if not r["exact_after_answers"]:
        bad.append(f"{fmt}/{seed}: 정답으로 답해도 정규 사건과 다름")
    d = propose_mapping(tables, lexicon=lex)
    want = gold_answers(SimpleNamespace(questions=d.questions), gold)
    for q in d.questions:
        if q.proposal is None or q.id not in want:
            continue
        g = IGNORE if want[q.id] is None and q.id.startswith("col:") else want[q.id]
        if q.proposal != g:
            bad.append(f"{fmt}/{seed}: 틀린 제안 {q.id}={q.proposal} (정답 {g})")
    return bad, r["columns"]["correct"] + r["codes"]["correct"]


def _gate(lex: Lexicon, formats, seeds, base_counts: dict | None = None) -> tuple[list[str], dict]:
    """틀림·틀린 제안이 없고, 어느 형식에서도 스스로 맞힌 열·코드 수가 줄지 않아야 통과한다(배운 항목이 다른 열과 겹쳐
    이미 맞히던 것을 다시 묻게 만들면 그 항목은 틀린 것이다. 예: SEX를 나이로 배우면 나이 열과 겹쳐 둘 다 다시 묻는다)."""
    bad, counts = [], {}
    for f in formats:
        for s in seeds:
            b, n = _gate_one(lex, f, s)
            bad += b
            counts[(f, s)] = n
            if base_counts is not None and n < base_counts[(f, s)]:
                bad.append(f"{f}/{s}: 스스로 맞힌 항목이 줄었다 {base_counts[(f, s)]} → {n}")
    return bad, counts


def promote(shared: Lexicon, candidate: Lexicon, formats=None, seeds=GATE_SEEDS) -> tuple[Lexicon, dict]:
    """후보 항목을 회귀 관문에 통과시켜 공용 사전에 넣는다. (새 공용 사전, 보고)"""
    from turnaround.sim.exports import FORMATS, HOLDOUT_FORMATS
    formats = tuple(formats or FORMATS + HOLDOUT_FORMATS)
    conflicts, new = [], []
    for kind, key, v in candidate.entries():
        cur = shared.lookup(kind, key)
        if kind == "layout" and tuple(v) in shared.layouts:
            continue
        if cur is not None and cur != v:
            conflicts.append(f"{kind}:{key}")
        elif cur is None:
            new.append((kind, key, v))
    accepted, rejected, reasons = [], [], {}
    base = {"counts": _gate(shared, formats, seeds)[1]}

    def split(batch):
        if not batch:
            return
        bad, counts = _gate(shared.plus(accepted + batch), formats, seeds, base["counts"])
        if not bad:
            base["counts"] = counts
            accepted.extend(batch)
            return
        if len(batch) == 1:
            rejected.append(batch[0])
            reasons[f"{batch[0][0]}:{batch[0][1]}"] = bad[:3]
            return
        mid = len(batch) // 2
        split(batch[:mid])
        split(batch[mid:])

    split(new)
    src = {f"{k}:{key}": candidate.provenance.get(f"{k}:{key}") for k, key, _ in accepted}
    out = shared.plus(accepted)
    out = out.model_copy(update={"provenance": {**out.provenance, **{k: v for k, v in src.items() if v}}})
    return out, {"accepted": len(accepted), "rejected": len(rejected), "conflicts": conflicts, "reasons": reasons,
                 "gate": {"formats": list(formats), "seeds": list(seeds)}}


__all__ = ["GATE_SEEDS", "Lexicon", "learn", "promote"]
