"""질의 응답 (AGT-6). 조회 도구만 쓰고, 답에는 근거 레코드 ID가 있어야 한다. 없으면 "레코드 없음".

LLM은 질문을 정해진 조회 의도로 바꾸는 일만 한다. 답의 사실은 모두 도구가 레코드에서 가져온다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from turnaround.events.models import Event, EventType
from turnaround.events.store import EventStore

from .llm import LLM, LLMError

SYSTEM = (
    "너는 병상관리 질의를 조회 의도로 바꾼다. 답을 하지 말고 조회 의도만 낸다. 사실을 지어내지 않는다. "
    "JSON 객체 하나만 출력한다. 형식(최상위 키만 사용): "
    '{"intent": "discharges_with_attending_surgery", "day": "today|tomorrow", "period": "am|pm|any"} 또는 '
    '{"intent": "readmission_plan", "case": "<사례 ID, 예: D1>"} 또는 '
    '{"intent": "cleaning_status", "case": "<사례 ID>"} 또는 {"intent": "unknown"}'
)
_NORM = {"내일": "tomorrow", "오늘": "today", "오전": "am", "오후": "pm"}


def _normalize(intent: dict) -> dict:
    """모델이 인자를 parameters 아래에 넣거나 한국어 값을 쓰는 경우를 결정적으로 고친다."""
    out = {k: v for k, v in intent.items() if k != "parameters"}
    out.update({k: v for k, v in (intent.get("parameters") or {}).items() if k not in out})
    return {k: _NORM.get(v, v) if isinstance(v, str) else v for k, v in out.items()}
ALLOWED_ROLES = {"bed_manager"}


@dataclass(frozen=True)
class Appointment:
    """합성 일정 레코드 (FHIR Appointment에 대응)."""
    id: str
    practitioner: str
    start: datetime
    kind: str  # surgery | clinic


@dataclass
class Records:
    appointments: list[Appointment] = field(default_factory=list)


@dataclass(frozen=True)
class Answer:
    status: str  # answered | no_record | refused
    text: str
    evidence: tuple[str, ...] = ()


class QueryAgent:
    def __init__(self, store: EventStore, llm: LLM, records: Records | None = None, data_class: str | None = None):
        from .llm import bind_data_class
        # 외부 모델 관문 (기본 병원 데이터, 2026-11-09 개인정보·IT 2회차)
        self.store, self.llm, self.records = store, bind_data_class(llm, data_class), records or Records()

    # ---------- 조회 도구 ----------

    def _discharges(self, events, day) -> list[Event]:
        out = []
        for e in events:
            if e.type is EventType.DISCHARGE_DECIDED:
                planned = e.payload.get("discharge_date")
                d = datetime.fromisoformat(planned).date() if planned else (e.observed_at + timedelta(days=1)).date()
                cancelled = any(x.type is EventType.DISCHARGE_CANCELLED and x.case_id == e.case_id for x in events)
                if d == day and not cancelled:
                    out.append(e)
        return out

    def _surgery_q(self, events, now, day_key, period) -> Answer:
        day = now.date() + timedelta(days=1 if day_key == "tomorrow" else 0)
        hits, ev = [], []
        for d in self._discharges(events, day):
            doc = d.payload.get("attending")
            for a in self.records.appointments:
                if a.practitioner == doc and a.kind == "surgery" and a.start.date() == day and (
                        period != "am" or a.start.hour < 12):
                    hits.append(d.case_id)
                    ev += [d.event_id, a.id]
        if not hits:
            return Answer("no_record", "해당 레코드 없음")
        return Answer("answered", f"{len(hits)}건: {', '.join(sorted(set(hits)))}", tuple(ev))

    def _readmission_q(self, events, case) -> Answer:
        dis = next((e for e in events if e.type is EventType.DISCHARGE_DECIDED and e.case_id == case), None)
        if dis is None:
            return Answer("no_record", f"{case} 퇴원 레코드 없음")
        pid = dis.payload.get("patient_id")
        adm = [e for e in events if e.type is EventType.ADMISSION_REQUESTED and e.payload.get("patient_id") == pid]
        if not adm:
            return Answer("no_record", "재입원 예정 레코드 없음")  # 없다고 단정하지 않는다: 레코드가 없을 뿐
        return Answer("answered", f"입원 예약 {len(adm)}건", tuple(e.event_id for e in adm))

    # ---------- 질의 ----------

    def ask(self, qid: str, role: str, question: str, now: datetime) -> Answer:
        events = self.store.read()
        if role not in ALLOWED_ROLES:
            ans = Answer("refused", "조회 권한 없음")
        else:
            try:
                intent = self.llm.complete_json("parse_question", SYSTEM, json.dumps({"question": question},
                                                                                     ensure_ascii=False))
            except LLMError:
                intent = {"intent": "unknown"}
            intent = _normalize(intent)
            kind = intent.get("intent")
            if kind == "discharges_with_attending_surgery":
                ans = self._surgery_q(events, now, intent.get("day", "today"), intent.get("period", "any"))
            elif kind == "readmission_plan" and intent.get("case"):
                ans = self._readmission_q(events, intent["case"])
            else:
                ans = Answer("no_record", "답할 수 있는 조회가 없음")
            if ans.status == "answered" and not ans.evidence:
                ans = Answer("no_record", "근거 레코드 없음")  # 근거 없는 답은 내보내지 않는다
        etype = {"answered": EventType.QUERY_ANSWERED, "no_record": EventType.QUERY_NO_RECORD,
                 "refused": EventType.QUERY_REFUSED}[ans.status]
        self.store.append(Event(event_id=f"qa:{qid}", case_id=qid, type=etype, occurred_at=now, observed_at=now,
                                source="agent", payload={"role": role, "answer": ans.text,
                                                         "evidence": list(ans.evidence)}))
        return ans
