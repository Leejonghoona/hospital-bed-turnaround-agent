"""매핑 에이전트 평가 (로드맵 07 M6-4·5). 합성 데이터 기준 추정.

- 열: 정답 매핑의 (표, 열) → 정규 키와 초안 비교. 맞음 / 틀림(질문 없이 틀린 키) / 질문.
- 코드값: 정답 코드표의 (키, 원천값) → 정규값과 초안 비교. 맞음 / 틀림 / 질문(안전 코드 상시 확인 포함).
- 끝까지: 질문에 정답으로 답한 매핑으로 변환한 결과가 정규 사건·병상 구조와 완전히 같은가.
"""

from __future__ import annotations

from turnaround.agent.mapping_agent import answer, propose_mapping
from turnaround.ontology.mapping import apply_mapping


def gold_answers(draft, gold) -> dict:
    out = {}
    for q in draft.questions:
        if q.id.startswith("col:"):
            _, t, c = q.id.split(":", 2)
            out[q.id] = gold.tables[t].columns.get(c)
        elif q.id.startswith("code:"):
            _, k, v = q.id.split(":", 2)
            out[q.id] = gold.codes.get(k, {}).get(v, v)
        elif q.id.startswith("evt:"):
            code = q.id[4:]
            out[q.id] = next((tm.event_codes.get(code) for tm in gold.tables.values() if tm.kind == "events"), None)
    return out


def score(tables: dict, gold, canonical_events, h) -> dict:
    d = propose_mapping(tables)
    asked = {q.id for q in d.questions}
    col = {"correct": 0, "wrong": 0, "asked": 0, "missed": 0}
    wrong_cols = []
    present = {t: {c for r in rows for c, v in r.items() if v != ""} for t, rows in tables.items()}
    seen_vals: dict[str, set] = {}
    for t, tm in gold.tables.items():
        for r in tables.get(t, []):
            for c, k in tm.columns.items():
                v = r.get(c, "")
                if v != "":
                    seen_vals.setdefault(k, set()).update(v.split(gold.list_sep) if k.endswith(("precautions", "departments"))
                                                          else [v])
    for t, tm in gold.tables.items():
        if tm.kind == "events":
            used = {r[tm.event_columns[1]] for r in tables.get(t, [])}
            items = [(f"evt:{code}", k) for code, k in tm.event_codes.items() if code in used]
        else:
            items = [(c, k) for c, k in tm.columns.items() if c in present.get(t, set())]
        for c, k in items:
            got = d.columns.get((t, c))
            if got == k:
                col["correct"] += 1
            elif f"col:{t}:{c}" in asked or c in asked:
                col["asked"] += 1
            elif got is None:
                col["missed"] += 1
            else:
                col["wrong"] += 1
                wrong_cols.append((t, c, got, k))
    code = {"correct": 0, "wrong": 0, "asked": 0}
    wrong_codes = []
    for k, cm in gold.codes.items():
        if k == "departments":
            continue
        for src, canon in cm.items():
            if src not in seen_vals.get(k, set()):
                continue  # 데이터에 나오지 않는 코드
            got = d.mapping.codes.get(k, {}).get(src, src)
            qid = f"code:{k}:{src}"
            if qid in asked and (got != canon or any(q.id == qid and q.proposal in (None, canon)
                                                     for q in d.questions)):
                code["asked"] += 1
                code["correct_proposal" if any(q.id == qid and q.proposal == canon for q in d.questions)
                     else "asked_no_proposal"] = code.get("correct_proposal" if any(
                         q.id == qid and q.proposal == canon for q in d.questions) else "asked_no_proposal", 0) + 1
            elif got == canon:
                code["correct"] += 1
            else:
                code["wrong"] += 1
                wrong_codes.append((k, src, got, canon))
    final = answer(d, gold_answers(d, gold))
    out = apply_mapping(final, tables)
    key = lambda e: (e.event_id, e.type, e.occurred_at, e.source,  # noqa: E731
                     tuple(sorted((a, str(b)) for a, b in e.payload.items())))
    exact = sorted(map(key, out.events)) == sorted(map(key, canonical_events)) and out.beds == h.beds \
        and out.rooms == h.rooms
    return {"columns": col, "codes": code, "questions": len(d.questions), "exact_after_answers": exact,
            "wrong_columns": wrong_cols, "wrong_codes": wrong_codes,
            "order_checks": d.evidence.get("order_checks", {})}


__all__ = ["gold_answers", "score"]
