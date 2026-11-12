"""HL7 v2 ADT 메시지 → 사건 저장소 사건 (DECISIONS 2026-11-12).

A01 입원 → PATIENT_ARRIVED, A02 전동 → ROOM_CHANGE_COMPLETED, A03 퇴원 → PATIENT_DEPARTED, A08 정보 변경 → PATIENT_INFO_UPDATED.
그 밖의 트리거는 변환하지 않고 보류로 센다.

읽는 위치는 HL7 v2.5 표준 위치만 쓴다(생성기 열 이름과 무관):
PID-3(환자 ID, 종류 MR 반복을 우선), PID-8(성별, HL7 표 0001), PV1-3(병동^병실^병상), PV1-6(이전 위치),
PV1-19(내원 번호 = 사례 ID), PV1-44/45(입퇴원 시각), EVN-2/EVN-6(기록·발생 시각), MSH-7(메시지 시각 = 인지 시각),
MSH-10(메시지 제어 ID = 사건 ID의 멱등 키).

기존 매핑 파이프라인에서 다시 쓰는 것:
- 질문 꼴: `mapping_agent.Question`(ID 꼴 `code:키:원천값`, `fmt:...`도 같다). 같은 질문은 한 번만 묻는다.
- 시각 형식 표: `mapping_agent._time_format`(12자리·14자리 꼴). HL7 TS의 소수 초·시간대 꼬리만 여기서 뗀다.
- 허용값: `mapping_agent.allowed_values()`(성별 M/F/other/unknown, 주의 contact/droplet/airborne)와
  "other·unknown 성별은 남녀 병실 구별에 쓸 수 없어 사람 확인" 규칙(`_PATIENT_ONLY_GENDER`, `mapping._dec`와 같은 뜻).
- 사건 사전: 퇴실은 `core.event_kinds()["patient_departed"]`의 타입·원천을 쓴다.
- 시계 차이 검사: `Event` 모델(EVT-6). 사건 ID 중복 방지는 `EventStore`.

다시 쓰지 못한 것과 이유:
- `propose_mapping`/`apply_mapping`: 둘 다 "표의 열" 단위다. HL7은 위치(세그먼트-필드-성분)로 뜻이 정해지는 표준이라
  열 이름 추측(LLM·어휘 사전)이 필요 없고, `apply_mapping`은 사례당 병상 하나·사건 종류당 사건 하나(`case:key`)라
  전동(A02)이 여러 번인 사례와 병상이 바뀌는 사례를 담지 못한다.
- `core.ttl`의 사건 순서(ta:notAfter): 입실(patient_arrived)이 사건 사전에 없어 A01 ≤ A02 ≤ A03을 표현하지 못한다.
  온톨로지 파일은 이번 작업에서 고치지 않기로 해(다른 작업 진행 중) ADT 생애 순서를 여기 `ADT_RANK`로 둔다.
- 격리·원인균: 표준상 정해진 자리가 없다(OBX 관찰값이나 병원별 Z세그먼트). 위치를 추측하지 않고
  `pos:` 질문으로 사람에게 넘긴다. 사람이 위치를 답하면(`positions`) 그 위치에서 읽고 코드표 검사를 다시 거친다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from turnaround.agent.mapping_agent import _PATIENT_ONLY_GENDER, Question, _time_format, allowed_values
from turnaround.events import Event, EventType
from turnaround.rules import unknown_organism

from .core import event_kinds
from .hl7v2 import Message, Segment

# HL7 v2.5 표 0001(Administrative Sex) → 정규값. 표준표라 병원마다 다르지 않다.
HL7_SEX = {"M": "M", "F": "F", "O": "other", "U": "unknown", "A": "other", "N": "unknown"}
# ADT 생애 순서(같은 내원 안에서 앞 단계 사건은 뒤 단계 사건보다 늦지 않다). A08은 순서 없음.
ADT_RANK = {"A01": 0, "A02": 1, "A03": 2}
_TRIGGER_TYPE = {"A01": EventType.PATIENT_ARRIVED, "A02": EventType.ROOM_CHANGE_COMPLETED,
                 "A03": event_kinds()["patient_departed"].event_type, "A08": EventType.PATIENT_INFO_UPDATED}
_TS = re.compile(r"^(\d{4,14})(\.\d{1,4})?([+-]\d{4})?$")


@dataclass
class HL7Config:
    """병원 인터페이스 정의서에 해당하는 값. 병동 코드표만 병원마다 다르다(표준표가 아님)."""
    ward_codes: dict[str, str]  # PV1-3.1 병동(point of care) → 내부 병동 ID
    tz_offset_min: int = 540  # 시간대 꼬리가 있으면 이 시간대 현지 시각으로 바꾼다(한국 +0900)
    # 사람이 답한 위치: 질문 ID의 위치 부분(예: "OBX:ISO", "ZIS") → {"key": 정규 키, "field": "OBX-5.1"}
    positions: dict[str, dict] = field(default_factory=dict)
    codes: dict[str, dict[str, str]] = field(default_factory=dict)  # 정규 키 → {원천값: 정규값} (사람이 답한 코드표)


@dataclass
class Converted:
    events: list[Event] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    held: list[dict] = field(default_factory=list)  # 변환하지 않은 메시지: {"msg", "reason"}
    order_violations: list[dict] = field(default_factory=list)  # {"msg", "case", "kind", "detail"}
    _qids: set = field(default_factory=set, repr=False)

    def ask(self, qid: str, item: str, why: str, proposal=None) -> None:
        if qid not in self._qids:
            self._qids.add(qid)
            self.questions.append(Question(qid, item, why, proposal))


def parse_ts(raw: str, tz_offset_min: int = 540) -> tuple[datetime | None, str]:
    """HL7 TS(YYYYMMDDHHMM[SS[.S..]][+/-ZZZZ]) → (현지 시각, 사유). 분까지 없으면 None(입실·퇴실 시각으로 못 쓴다)."""
    m = _TS.match(raw.strip())
    if not m:
        return None, "HL7 시각 꼴이 아니다"
    digits, _frac, off = m.groups()
    fmt = _time_format([digits])  # 매핑 에이전트의 시각 형식 표(12자리 분, 14자리 초)
    if fmt is None:
        return None, f"시각 정밀도가 분보다 낮다({len(digits)}자리)"
    dt = datetime.strptime(digits, fmt)
    if off:
        sign = 1 if off[0] == "+" else -1
        src = timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[3:5])))
        dt = dt.replace(tzinfo=src).astimezone(timezone(timedelta(minutes=tz_offset_min))).replace(tzinfo=None)
    return dt, ""


def _patient_id(msg: Message) -> str:
    pid = msg.seg("PID")
    for r in range(max(msg.repetitions(pid, 3), 1)):
        if msg.field(pid, 3, 5, rep=r) == "MR":
            return msg.field(pid, 3, 1, rep=r)
    return msg.field(pid, 3, 1)


def _position_id(seg: Segment, msg: Message) -> str | None:
    if seg.name == "OBX":
        return f"OBX:{msg.field(seg, 3, 1) or '?'}"
    if seg.name.startswith("Z"):
        return seg.name
    return None


class _Ctx:
    def __init__(self, cfg: HL7Config, out: Converted):
        self.cfg, self.out = cfg, out
        self.allowed = allowed_values()

    def bed(self, msg: Message, n: int) -> tuple[str | None, str]:
        ward, room, bed = (msg.field("PV1", n, i) for i in (1, 2, 3))
        if not (ward and room and bed):
            return None, f"PV1-{n} 위치가 비었다"
        wid = self.cfg.ward_codes.get(ward)
        if wid is None:
            self.out.ask(f"code:ward:{ward}", f"PV1-{n}.1={ward}", "병동 코드표에 없는 값이다(내부 병동을 정해 주거나 쓰지 않음)")
            return None, f"병동 코드 {ward} 모름"
        return f"{wid}-R{room.zfill(2)}-B{bed}", ""

    def gender(self, msg: Message) -> str | None:
        raw = msg.field("PID", 8)
        val = self.cfg.codes.get("patient.gender", {}).get(raw) or HL7_SEX.get(raw)
        if val is None or val not in self.allowed["gender"]:
            self.out.ask(f"code:patient.gender:{raw or '(빈 값)'}", f"PID-8={raw}", "HL7 표 0001에 없는 성별 코드다")
            return None
        if val in _PATIENT_ONLY_GENDER:
            self.out.ask(f"code:patient.gender:{raw}", f"PID-8={raw}",
                         "남녀 병실 구별에 쓸 수 없는 성별이다(사람이 병실 성별 운영을 확인)", proposal=val)
            return None
        return val

    def safety_fields(self, msg: Message) -> tuple[dict, list[str]]:
        """격리·원인균: 사람이 위치를 답한 것만 읽는다. 모르는 위치는 질문으로 넘기고 이름만 남긴다."""
        got: dict = {}
        unread: list[str] = []
        for seg in msg.segments:
            pos = _position_id(seg, msg)
            if pos is None:
                continue
            ans = self.cfg.positions.get(pos)
            if ans is None:
                self.out.ask(f"pos:{pos}", pos, "격리·원인균 같은 값의 자리는 병원마다 다르다(OBX·Z세그먼트). 어느 필드가 무엇인지 정해 주세요")
                unread.append(pos)
                continue
            key = ans["key"]
            raw = msg.get(ans["field"], seg)
            if not raw:
                continue
            if key == "patient.precautions":
                val = self.cfg.codes.get(key, {}).get(raw)
                if val not in self.allowed["precautions"]:
                    self.out.ask(f"code:{key}:{raw}", f"{ans['field']}={raw}", "주의 코드표에 없는 값이다(안전 속성, 사람 확인)")
                    unread.append(pos)
                    continue
                got.setdefault("precautions", [])
                if val not in got["precautions"]:
                    got["precautions"].append(val)
            elif key == "patient.organism":
                val = self.cfg.codes.get(key, {}).get(raw, raw)
                if unknown_organism(val):  # 정규 코드가 아니면 추측하지 않는다 (주의 코드와 같은 규칙, 2차 검수)
                    self.out.ask(f"code:{key}:{raw}", f"{ans['field']}={raw}", "원인균 코드표에 없는 값이다(안전 속성, 사람 확인)")
                    unread.append(pos)
                    continue
                got["organism"] = val
        return got, unread


def _event_time(msg: Message, trig: str, cfg: HL7Config) -> tuple[datetime | None, str]:
    cands = {"A01": [("PV1", 44), ("EVN", 6), ("EVN", 2)], "A03": [("PV1", 45), ("EVN", 6), ("EVN", 2)]}.get(
        trig, [("EVN", 6), ("EVN", 2)])
    for seg, n in cands:
        raw = msg.field(seg, n, 1)
        if raw:
            dt, why = parse_ts(raw, cfg.tz_offset_min)
            return dt, (f"{seg}-{n}={raw}: {why}" if dt is None else f"{seg}-{n}")
    return None, "사건 시각 필드가 비었다"


def to_events(messages: list[Message], cfg: HL7Config) -> Converted:
    """도착 순서(묶음 순서)대로 변환한다. 발생 시각은 표준 필드에서, 인지 시각은 MSH-7에서 읽는다."""
    out = Converted()
    ctx = _Ctx(cfg, out)
    seen: dict[str, list[tuple[int, datetime, str]]] = {}  # 사례 → 받아들인 (단계, 발생 시각, 메시지 ID)

    def hold(msg, reason):
        out.held.append({"msg": msg.control_id, "reason": reason})

    for msg in sorted(messages, key=lambda m: m.index):
        mid, trig = msg.control_id, msg.trigger
        if msg.message_type != "ADT" or trig not in _TRIGGER_TYPE:
            hold(msg, f"변환하지 않는 메시지 {msg.message_type}^{trig}")
            continue
        case = msg.field("PV1", 19, 1)
        if not case:
            # 방문 번호가 없으면 환자 번호로 대신하지 않는다. 같은 환자의 입원 여러 건이 한 사례로 합쳐진다 (2026-11-12)
            out.ask(f"pos:PV1-19:{mid}", f"{trig} 방문 번호", "PV1-19(방문 번호)가 비어 있다. 병원이 입원 건을 구분하는 필드를 알려 줘야 한다")
            hold(msg, "방문 번호 없음")
            continue
        occ, why = _event_time(msg, trig, cfg)
        if occ is None:
            out.ask(f"fmt:time:{mid}", f"{trig} 시각", f"사건 시각을 읽지 못했다({why})")
            hold(msg, why)
            continue
        obs, _ = parse_ts(msg.field("MSH", 7, 1), cfg.tz_offset_min)
        obs = obs or occ
        payload: dict = {"hl7_msg": mid, "hl7_trigger": trig}
        if trig in ("A01", "A02", "A03"):
            bid, why = ctx.bed(msg, 3)
            if bid is None:
                hold(msg, why)
                continue
            payload["bed_id"] = bid
        if trig == "A02":
            prev, why = ctx.bed(msg, 6)
            if prev is None:
                hold(msg, f"이전 위치: {why}")
                continue
            payload["from_bed_id"] = prev
        if trig in ("A01", "A08"):
            payload["patient_id"] = _patient_id(msg)
            payload["gender"] = ctx.gender(msg)
            got, unread = ctx.safety_fields(msg)
            payload.update(got)
            if unread:
                payload["_unread_positions"] = unread
        # 순서 검사: 같은 내원에서 받아들인 사건과 단계·시각이 어긋나는지
        rank = ADT_RANK.get(trig)
        if rank is not None:
            hist = seen.setdefault(case, [])
            clash = [h for h in hist if (h[0] < rank and h[1] > occ) or (h[0] > rank and h[1] < occ)]
            if clash:
                h = clash[0]
                detail = f"{trig} {occ:%Y-%m-%d %H:%M}가 {h[2]}(단계 {h[0]}) {h[1]:%Y-%m-%d %H:%M}와 순서가 맞지 않는다"
                out.order_violations.append({"msg": mid, "case": case, "kind": "time_contradiction", "detail": detail})
                out.ask(f"order:{mid}", f"{case} {trig}", f"사건 순서 위반: {detail}. 어느 시각이 맞는지 확인")
                hold(msg, "사건 순서 위반")
                continue
            later = [h for h in hist if h[0] > rank]
            if later:
                out.order_violations.append({"msg": mid, "case": case, "kind": "late_arrival",
                                             "detail": f"{trig}가 뒤 단계 {later[0][2]}보다 늦게 도착(시각은 맞아 발생 시각으로 넣음)"})
                payload["_late_arrival"] = True
            hist.append((rank, occ, mid))
        try:
            ev = Event(event_id=f"hl7:{mid}", case_id=case, type=_TRIGGER_TYPE[trig], occurred_at=occ,
                       observed_at=obs, source="his", payload=payload)
        except ValueError as e:  # 시계 차이 허용 범위 밖 (EVT-6)
            out.ask(f"fmt:clock:{mid}", f"{mid} MSH-7", f"메시지 시각이 사건 시각보다 앞선다: {e}")
            hold(msg, "시계 차이")
            continue
        out.events.append(ev)
    _check_locations(out)
    out.events.sort(key=lambda e: (e.observed_at, e.occurred_at, e.event_id))
    return out


def _check_locations(out: Converted) -> None:
    """발생 시각 순으로 이어 볼 때 전동 출발 병상과 퇴원 병상이 직전 위치와 같은지. 다르면 사람에게 묻는다."""
    by_case: dict[str, list[Event]] = {}
    for e in out.events:
        if e.type in (EventType.PATIENT_ARRIVED, EventType.ROOM_CHANGE_COMPLETED, EventType.PATIENT_DEPARTED):
            by_case.setdefault(e.case_id, []).append(e)
    for case, evs in by_case.items():
        cur = None
        for e in sorted(evs, key=lambda e: e.occurred_at):
            here = e.payload.get("from_bed_id") if e.type is EventType.ROOM_CHANGE_COMPLETED else e.payload.get("bed_id")
            if cur is not None and e.type is not EventType.PATIENT_ARRIVED and here != cur:
                out.ask(f"loc:{e.payload['hl7_msg']}", f"{case} 위치", f"직전 위치 {cur}와 메시지 위치 {here}가 다르다")
            cur = e.payload.get("bed_id")


__all__ = ["ADT_RANK", "Converted", "HL7Config", "HL7_SEX", "parse_ts", "to_events"]
