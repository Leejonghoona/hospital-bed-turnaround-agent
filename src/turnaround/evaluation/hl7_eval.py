"""HL7 v2 ADT 외부 형식 평가 지표 (DECISIONS 2026-11-12).

정답(`evals/hl7/sample_adt_expected.json`)은 메시지 제어 ID별로 나와야 할 사건(없으면 null = 사람에게 넘길 메시지)을 적는다.

- 틀린 연결: 만든 사건이 정답과 다름(종류·사례·병상·이전 병상·발생 시각·성별, 읽었다면 주의·원인균), 또는 넘겨야 할 메시지를 사건으로 만듦.
- 놓침: 정답에는 사건이 있는데 만들지 않음.
- 안전 값 미판독: 정답에 주의·원인균이 있는데 사건에 없음(위치를 아직 모름).
- 순서 위반 검출: 어댑터가 잡은 순서 위반 중 정답이 순서 위반으로 표시한 메시지 수(맞게 잡음)와 그 밖(잘못 잡음).
"""

from __future__ import annotations

from datetime import datetime

from turnaround.events import EventStore
from turnaround.ontology.hl7_adapter import Converted

_FIELDS = ("bed_id", "from_bed_id")


def _diff(exp: dict, ev) -> list[str]:
    out = []
    if ev.type.value != exp.get("type"):
        out.append(f"type {ev.type.value} != {exp.get('type')}")
    if ev.case_id != exp.get("case"):
        out.append(f"case {ev.case_id} != {exp.get('case')}")
    if "occurred_at" in exp and ev.occurred_at != datetime.fromisoformat(exp["occurred_at"]):
        out.append(f"occurred_at {ev.occurred_at.isoformat()} != {exp['occurred_at']}")
    for k in _FIELDS:
        if (k in exp or k in ev.payload) and ev.payload.get(k) != exp.get(k):
            out.append(f"{k} {ev.payload.get(k)} != {exp.get(k)}")
    if "gender" in exp and ev.payload.get("gender") != exp["gender"]:
        out.append(f"gender {ev.payload.get('gender')} != {exp['gender']}")
    for k in ("precautions", "organism"):
        if k in ev.payload and ev.payload[k] != exp.get(k):
            out.append(f"{k} {ev.payload[k]} != {exp.get(k)}")
    return out


def score(conv: Converted, expected: dict, n_messages: int, parse_errors: int = 0) -> dict:
    exp = expected["messages"]
    by_msg = {e.payload["hl7_msg"]: e for e in conv.events}
    wrong, missed, unread, traps = [], [], [], []
    for mid, x in exp.items():
        ev = by_msg.get(mid)
        if x.get("type") is None:
            if ev is not None:
                wrong.append({"msg": mid, "why": "넘겨야 할 메시지를 사건으로 만듦"})
        elif ev is None:
            missed.append(mid)
        else:
            d = _diff(x, ev)
            if d:
                wrong.append({"msg": mid, "why": "; ".join(d)})
            for k in ("precautions", "organism"):
                if k in x and k not in ev.payload:
                    unread.append({"msg": mid, "key": k})
        if "trap" in x:
            held = next((h["reason"] for h in conv.held if h["msg"] == mid), None)
            traps.append({"msg": mid, "trap": x["trap"], "outcome": "held: " + held if held else
                          ("converted" if ev is not None else "missing")})
    extra = [mid for mid in by_msg if mid not in exp]
    wrong += [{"msg": mid, "why": "정답에 없는 메시지"} for mid in extra]
    exp_order = {mid for mid, x in exp.items() if x.get("order_violation")}
    got_order = {v["msg"] for v in conv.order_violations}
    store = EventStore()
    first = sum(1 for s in store.extend(conv.events) if s is not None)
    again = sum(1 for s in store.extend(conv.events) if s is not None)
    store.close()
    return {
        "messages": n_messages,
        "parse_errors": parse_errors,
        "events_converted": len(conv.events),
        "wrong_links": len(wrong),
        "missed": len(missed),
        "held_messages": len(conv.held),
        "questions": len(conv.questions),
        "order_violations_detected": len(got_order & exp_order),
        "order_violations_expected": len(exp_order),
        "order_violations_false": len(got_order - exp_order),
        "safety_values_unread": len(unread),
        "store_appended": first,
        "store_appended_on_replay": again,
        "details": {
            "wrong": wrong, "missed": missed, "unread": unread, "traps": traps,
            "questions": [{"id": q.id, "item": q.item, "why": q.why} for q in conv.questions],
            "held": conv.held, "order_violations": conv.order_violations,
        },
    }


__all__ = ["score"]
