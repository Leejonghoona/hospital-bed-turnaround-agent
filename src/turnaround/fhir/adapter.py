"""내부 이벤트 ↔ FHIR R4 Bundle(type=history). 런타임 의존성 없이 dict로 만든다.

사건 종류마다 표준 의미에 맞는 리소스로 내보낸다(대응표: DECISIONS.md "2026-11-12 FHIR 어댑터 표준 의미로 정리").

- 입원·전동·퇴원: Encounter(사례 하나 = Encounter 하나)와 Encounter.location(status planned·reserved·active·completed).
- 병상 상태(오염·청소·사용 가능·폐쇄): Location.operationalStatus (HL7 v2-0116 K·H·U·C, 입실하면 O).
- 격리 오더·선별검사 오더: ServiceRequest. 전파경로 주의: Flag(SNOMED CT 주의 코드). 원인균: Observation(SNOMED CT 생물체).
- 사람 할 일(작업·청소·침상 정리): Task. status는 requested → in-progress → completed 로 실제 단계를 따른다.
- 그 밖의 사건(사가 명령, 게이트웨이 감사 기록 등): 사건마다 Task 하나, payload는 Task.input.
- 환자는 Patient 하나(Patient.identifier, system PATIENT_ID_SYSTEM). 값이 바뀌면 같은 id의 새 판(versionId)을 낸다.

이력 번들이라 같은 리소스(Encounter, 병상 Location, Task, Patient)가 판을 바꿔 여러 번 나온다. 사건 하나는
"주 항목" 하나(Bundle.entry.extension에 사건 봉투: 사건 ID·종류·사례·출처·현실 시각·키 순서)와 그 바로 앞의
"딸린 항목"(Patient 새 판, Flag, ServiceRequest, Observation, 병상 Location 판, 청소 Task)으로 나간다.
들여올 때는 표준 필드에서 payload를 다시 만들고, 표준 필드로 되살릴 수 없는 값만 봉투의 payload-value 확장에서 채운다.
그래서 왕복(이벤트 → 번들 → 이벤트)은 항상 원래 사건 목록과 같다. 사건 순서는 번들 항목 순서가 정본이다.

시각: 합성 병원 현지 시각(한국 표준시)이라 +09:00을 고정으로 붙이고, 들여올 때 표기를 떼어 내부 형식(시간대 없음)으로
되돌린다. 다른 시간대 표기가 붙은 번들은 받지 않는다(실제 병원 연동 때 다시 정할 일).
현실 시각(occurred_at)은 봉투의 occurred-at, 인지 시각(observed_at)은 resource.meta.lastUpdated 다.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime
from typing import Any, Iterable

from turnaround.events.models import Event, EventType
from turnaround.sim.models import Bed, Gender, Room, RoomType, Ward

T = EventType
TZ = "+09:00"  # 합성 병원 현지 시각 (한국 표준시). 고정
BASE = "https://wardflow.example/fhir"
SD = BASE + "/StructureDefinition/"  # 확장 정의 URL
CS = BASE + "/CodeSystem/"  # 내부 코드 체계
SID = BASE + "/sid/"  # 식별자 체계
PATIENT_ID_SYSTEM = SID + "patient-id"
CASE_ID_SYSTEM = SID + "case-id"
BED_ID_SYSTEM = SID + "bed-id"
ROOM_ID_SYSTEM = SID + "room-id"
WARD_ID_SYSTEM = SID + "ward-id"
TASK_ID_SYSTEM = SID + "task-id"
SNOMED = "http://snomed.info/sct"
V2_0116 = "http://terminology.hl7.org/CodeSystem/v2-0116"  # 병상 상태
V3_ACT = "http://terminology.hl7.org/CodeSystem/v3-ActCode"
LOC_PHYS = "http://terminology.hl7.org/CodeSystem/location-physical-type"
DISCH = "http://terminology.hl7.org/CodeSystem/discharge-disposition"
FLAG_CAT = "http://terminology.hl7.org/CodeSystem/flag-category"
OBS_CAT = "http://terminology.hl7.org/CodeSystem/observation-category"

PRECAUTION_SNOMED = {"airborne": ("409524006", "Airborne precautions"),
                     "droplet": ("409525007", "Droplet precautions"),
                     "contact": ("409529001", "Contact precautions")}
SNOMED_PRECAUTION = {v[0]: k for k, v in PRECAUTION_SNOMED.items()}
ISOLATION_SNOMED = ("361235007", "Isolation of infected patient")  # 격리 시행 (docs/환자분류코드_조사.md 4절 확인)
# 원인균 → SNOMED CT 개념. 이 저장소에서 근거를 확인한 코드만 둔다(MTB 113861009, CRE 712662001 조사 문서 4절).
# 나머지 균은 SNOMED 브라우저와 대조하기 전까지 내부 코드만 붙인다(확인 안 된 코드를 쓰지 않는다, DECISIONS 한계).
ORGANISM_SNOMED = {"MTB": ("113861009", "Mycobacterium tuberculosis"),
                   "CRE": ("712662001", None)}
PATIENT_KEYS = ("patient_id", "name", "gender", "age", "department", "isolation_order", "isolation_type",
                "precautions", "organism", "precaution_status", "isolation_ordered_by", "immunocompromised",
                "open_wound")
DEMOGRAPHIC_KEYS = ("patient_id", "name", "gender", "age", "department", "immunocompromised", "open_wound")
_GENDER = {"M": "male", "F": "female"}
_GENDER_BACK = {v: k for k, v in _GENDER.items()}
_PREC_STATUS = {"confirmed": "final", "preemptive": "preliminary"}  # 주의 판정 → Observation.status
_PREC_STATUS_BACK = {v: k for k, v in _PREC_STATUS.items()}
_DISCHARGE = {"ama": "aadvice"}  # 자의 퇴원 → discharge-disposition "Left against advice"
_DISCHARGE_BACK = {v: k for k, v in _DISCHARGE.items()}
_FHIR_ID = re.compile(r"^[A-Za-z0-9\-.]{1,64}$")

# 입원·전동·퇴원: (Encounter.status, 이 사건이 병상에 주는 Encounter.location.status). None = 바꾸지 않음
ENCOUNTER_EVENTS: dict[EventType, tuple[str | None, str | None]] = {
    T.ADMISSION_REQUESTED: ("planned", None),
    T.PREMATCH_CREATED: (None, "planned"),
    T.MATCH_COMMITTED: (None, "reserved"),
    T.PATIENT_ARRIVED: ("in-progress", "active"),
    T.ADMISSION_CANCELLED: ("cancelled", None),
    T.DISCHARGE_DECIDED: ("in-progress", "active"),
    T.DISCHARGE_REGISTERED: ("in-progress", "active"),
    T.DISCHARGE_CANCELLED: ("in-progress", "active"),
    T.DEATH_CONFIRMED: ("in-progress", "active"),
    T.UNPLANNED_TRANSFER_OUT: ("in-progress", "completed"),
    T.END_OF_LIFE_ROOM_TRANSFER: ("in-progress", "completed"),
    T.PATIENT_DEPARTED: ("finished", "completed"),
    T.BODY_TRANSFERRED: ("finished", "completed"),
}
# 병상 상태: v2-0116 코드. K 오염(청소 필요), H 청소 중, U 사용 가능, C 폐쇄, O 점유
BED_STATUS_EVENTS: dict[EventType, str] = {
    T.CLEANING_REQUESTED: "K", T.CLEANING_REQUEST_ISSUED: "K", T.CLEANING_STARTED: "H", T.CLEANING_DONE: "H",
    T.CLEANING_INSPECTED: "H", T.BED_READY: "U", T.BED_MARKED_VACANT: "U",
}
ROOM_STATUS_EVENTS: dict[EventType, tuple[str, str]] = {  # (Location.status, operationalStatus)
    T.ROOM_OUT_OF_SERVICE: ("suspended", "C"), T.ROOM_RETURNED_TO_SERVICE: ("active", "U"),
}
_V2_DISPLAY = {"C": "Closed", "H": "Housekeeping", "K": "Contaminated", "O": "Occupied", "U": "Unoccupied"}
# 오더: (ServiceRequest.status, 종류)
ORDER_EVENTS: dict[EventType, tuple[str, str]] = {
    T.ISOLATION_ORDER_ADDED: ("active", "isolation"), T.ISOLATION_RELEASED: ("completed", "isolation"),
    T.SCREENING_ORDERED: ("active", "screening"),
}
RESULT_EVENTS = {T.SCREENING_RESULT_REPORTED}
# 사람 할 일: 작업 하나를 여러 사건이 판을 바꿔 가며 기록한다. 값 = 이 사건 뒤 Task.status (None = 그대로)
HUMAN_TASK_EVENTS: dict[EventType, str | None] = {
    T.HUMAN_TASK_CREATED: "requested", T.TASK_HANDED_OVER: None, T.TASK_ESCALATED: None,
    T.HUMAN_TASK_DONE: "completed", T.BED_PREP_REQUESTED: "requested", T.BED_PREP_DONE: "completed",
}
CLEANING_TASK_EVENTS = {T.CLEANING_REQUESTED: "requested", T.CLEANING_STARTED: "in-progress",
                        T.CLEANING_DONE: "completed"}


# ---------- 공통 ----------

def _fid(s: str) -> str:
    """FHIR id 규칙([A-Za-z0-9-.], 64자)에 맞춘 결정적 ID."""
    return hashlib.sha1(s.encode()).hexdigest()[:32]


def _loc_id(x: str) -> str:
    return x if _FHIR_ID.match(x) else _fid("loc:" + x)


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + TZ


def _from_ts(s: str) -> datetime:
    if not s.endswith(TZ):
        raise ValueError(f"합성 병원 현지 시각(+09:00)만 받는다: {s}")
    return datetime.fromisoformat(s.removesuffix(TZ))


def _same(a, b) -> bool:
    return type(a) is type(b) and (json.dumps(a, sort_keys=True, ensure_ascii=False)
                                   == json.dumps(b, sort_keys=True, ensure_ascii=False))


def _str(v) -> str | None:
    return v if isinstance(v, str) and v else None


def _ext(key: str, value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"url": SD + key, "valueBoolean": value}
    if isinstance(value, int):
        return {"url": SD + key, "valueInteger": value}
    return {"url": SD + key, "valueString": str(value)}


def _ext_get(res: dict, key: str):
    for e in res.get("extension", []):
        if e["url"] == SD + key:
            return e.get("valueBoolean", e.get("valueInteger", e.get("valueString")))
    return None


def _value(v) -> tuple[dict, bool]:
    """payload 값 하나 → (value[x], JSON 여부). None·목록·사전·실수는 JSON 문자열(표준 값 형식이 없거나 null 불가)."""
    if isinstance(v, bool):
        return {"valueBoolean": v}, False
    if isinstance(v, int):
        return {"valueInteger": v}, False
    if isinstance(v, str):
        return {"valueString": v}, False
    return {"valueString": json.dumps(v, ensure_ascii=False)}, True


def _input(key: str, v) -> dict:
    val, js = _value(v)
    if js:
        return {"type": {"text": key, "coding": [{"system": CS + "value-encoding", "code": "json"}]}, **val}
    return {"type": {"text": key}, **val}


def _input_value(inp: dict):
    if any(c.get("code") == "json" for c in inp["type"].get("coding", [])):
        return json.loads(inp["valueString"])
    for k in ("valueBoolean", "valueInteger", "valueString"):
        if k in inp:
            return inp[k]
    raise ValueError(f"값 없음: {inp}")


def _payload_ext(key: str, v) -> dict:
    val, js = _value(v)
    sub = [{"url": "key", "valueString": key}]
    if js:
        sub.append({"url": "encoding", "valueCode": "json"})
    sub.append({"url": "value", **val})
    return {"url": SD + "payload-value", "extension": sub}


def _payload_ext_value(ext: dict) -> tuple[str, Any]:
    sub = {s["url"]: s for s in ext["extension"]}
    v = sub["value"]
    raw = next(v[k] for k in ("valueBoolean", "valueInteger", "valueString") if k in v)
    return sub["key"]["valueString"], (json.loads(raw) if "encoding" in sub else raw)


def _ref(rt: str, rid: str, ident: tuple[str, str] | None = None, display: str | None = None) -> dict:
    out: dict = {"reference": f"{rt}/{rid}"}
    if ident:
        out["identifier"] = {"system": ident[0], "value": ident[1]}
    if display:
        out["display"] = display
    return out


def _cc(system: str, code: str, display: str | None = None) -> dict:
    c = {"system": system, "code": code}
    if display:
        c["display"] = display
    return {"coding": [c]}


def _code_of(cc: dict | None, system: str) -> str | None:
    return next((c["code"] for c in (cc or {}).get("coding", []) if c.get("system") == system), None)


def _subject(r: dict) -> dict | None:
    return r.get("subject") or r.get("for")


class _Versions:
    """이력 번들 항목. 같은 리소스가 다시 나오면 versionId를 올린다."""

    def __init__(self):
        self.n: dict[str, int] = {}

    def entry(self, resource: dict, observed: datetime, ext: list[dict] | None = None) -> dict:
        key = f"{resource['resourceType']}/{resource['id']}"
        self.n[key] = n = self.n.get(key, 0) + 1
        resource = deepcopy(resource)
        resource["meta"] = {"versionId": str(n), "lastUpdated": _ts(observed)}
        e = {"fullUrl": f"{BASE}/{key}", "resource": resource,
             "request": {"method": "PUT", "url": key},
             "response": {"status": "201 Created" if n == 1 else "200 OK", "etag": f'W/"{n}"',
                          "lastModified": _ts(observed)}}
        if ext:
            e["extension"] = ext
        return e


# ---------- 환자와 임상 정보 ----------

def _patient_rid(ev: Event) -> str:
    pid = _str(ev.payload.get("patient_id"))
    return _fid(f"patient:{pid}") if pid else _fid(f"{ev.event_id}:patient")


def _patient_resource(rid: str, state: dict) -> dict:
    patient: dict = {"resourceType": "Patient", "id": rid}
    if _str(state.get("patient_id")):
        patient["identifier"] = [{"system": PATIENT_ID_SYSTEM, "value": state["patient_id"]}]
    if isinstance(state.get("name"), str):
        patient["name"] = [{"text": state["name"]}]
    if state.get("gender") in _GENDER:
        patient["gender"] = _GENDER[state["gender"]]
    ext = [x for x in (_ext(k, state.get(k)) for k in ("age", "department", "immunocompromised", "open_wound"))
           if x is not None]
    if ext:
        patient["extension"] = ext
    return patient


def _isolation_request(ev: Event, rid: str, pref: dict, status: str) -> dict:
    p = ev.payload
    it = p.get("isolation_type")
    code = _cc(SNOMED, *ISOLATION_SNOMED)
    if it in PRECAUTION_SNOMED:
        code["coding"].append(_cc(SNOMED, *PRECAUTION_SNOMED[it])["coding"][0])
    code["text"] = "격리 오더"
    sr = {"resourceType": "ServiceRequest", "id": rid, "status": status, "intent": "order",
          "category": [_cc(CS + "order-kind", "isolation")], "code": code, "subject": pref,
          "authoredOn": _ts(ev.occurred_at)}
    if _str(p.get("isolation_ordered_by")):
        sr["requester"] = {"display": p["isolation_ordered_by"]}
    return sr


def _clinical(ev: Event, pref: dict, iso_primary: bool) -> list[dict]:
    """환자 필드 중 임상 정보: 주의 Flag, 격리 오더 ServiceRequest, 원인균 Observation."""
    p, out, occ = ev.payload, [], _ts(ev.occurred_at)
    for prec in p.get("precautions") or []:
        if prec not in PRECAUTION_SNOMED:
            continue
        out.append({"resourceType": "Flag", "id": _fid(f"{ev.event_id}:flag:{prec}"), "status": "active",
                    "category": [_cc(FLAG_CAT, "clinical")], "code": _cc(SNOMED, *PRECAUTION_SNOMED[prec]),
                    "subject": pref, "period": {"start": occ}})
    if p.get("isolation_order") is True and not iso_primary:
        out.append(_isolation_request(ev, _fid(f"{ev.event_id}:isolation"), pref, "active"))
    org = _str(p.get("organism"))
    if org:
        value = {"coding": [{"system": CS + "organism", "code": org}], "text": org}
        if org in ORGANISM_SNOMED:
            value["coding"].insert(0, _cc(SNOMED, *ORGANISM_SNOMED[org])["coding"][0])
        out.append({"resourceType": "Observation", "id": _fid(f"{ev.event_id}:organism"),
                    "status": _PREC_STATUS.get(p.get("precaution_status"), "registered"),
                    "category": [_cc(OBS_CAT, "laboratory")],
                    "code": {"coding": [{"system": CS + "observation", "code": "organism"}], "text": "원인균"},
                    "subject": pref, "effectiveDateTime": occ, "valueCodeableConcept": value})
    return out


def _patient_payload(patient: dict | None, related: list[dict], keys: list[str]) -> dict:
    patient = patient or {}
    flags = [r for r in related if r["resourceType"] == "Flag"]
    iso = next((r for r in related if r["resourceType"] == "ServiceRequest"
                and _code_of(r["category"][0], CS + "order-kind") == "isolation"), None)
    org = next((r for r in related if r["resourceType"] == "Observation"
                and _code_of(r["code"], CS + "observation") == "organism"), None)
    out: dict = {}
    for k in keys:
        if k == "patient_id":
            out[k] = next((i["value"] for i in patient.get("identifier", []) if i["system"] == PATIENT_ID_SYSTEM),
                          None)
        elif k == "name":
            out[k] = patient["name"][0]["text"] if patient.get("name") else None
        elif k == "gender":
            out[k] = _GENDER_BACK.get(patient.get("gender"))
        elif k == "precautions":
            out[k] = [SNOMED_PRECAUTION[_code_of(f["code"], SNOMED)] for f in flags]
        elif k == "isolation_order":
            out[k] = iso is not None and iso["status"] == "active"
        elif k == "isolation_type":
            out[k] = next((SNOMED_PRECAUTION[c["code"]] for c in iso["code"]["coding"]
                           if c["system"] == SNOMED and c["code"] in SNOMED_PRECAUTION), None) if iso else None
        elif k == "isolation_ordered_by":
            out[k] = iso.get("requester", {}).get("display") if iso else None
        elif k == "organism":
            out[k] = _code_of(org["valueCodeableConcept"], CS + "organism") if org else None
        elif k == "precaution_status":
            out[k] = _PREC_STATUS_BACK.get(org["status"]) if org else None
        else:
            out[k] = _ext_get(patient, k)
    return out


# ---------- 위치 ----------

def _location(kind: str, ident: str, status: str, op: str | None) -> dict:
    sys = {"bd": BED_ID_SYSTEM, "ro": ROOM_ID_SYSTEM, "wa": WARD_ID_SYSTEM}[kind]
    loc = {"resourceType": "Location", "id": _loc_id(ident), "identifier": [{"system": sys, "value": ident}],
           "status": status, "physicalType": _cc(LOC_PHYS, kind)}
    if op:
        loc["operationalStatus"] = {"system": V2_0116, "code": op, "display": _V2_DISPLAY[op]}
    return loc


def _enc_location(kind: str, ident: str, status: str, when: str) -> dict:
    sys = BED_ID_SYSTEM if kind == "bd" else WARD_ID_SYSTEM
    return {"location": _ref("Location", _loc_id(ident), (sys, ident)), "status": status,
            "physicalType": _cc(LOC_PHYS, kind), "period": {"end": when} if status == "completed" else {"start": when}}


def _kind(x: dict) -> str | None:
    return _code_of(x.get("physicalType"), LOC_PHYS)


# ---------- 내보내기 ----------

class _Exporter:
    def __init__(self):
        self.v = _Versions()
        self.patients: dict[str, dict] = {}  # Patient id → 누적 값
        self.case_patient: dict[str, str] = {}
        self.encounters: dict[str, dict] = {}
        self.tasks: dict[str, dict] = {}
        self.entries: list[dict] = []

    def _patient(self, ev: Event, pkeys: list[str]) -> dict | None:
        """환자당 Patient 하나. 값이 바뀌면 새 판을 내고 참조를 돌려준다."""
        if not pkeys:
            rid = self.case_patient.get(ev.case_id)
            return _ref("Patient", rid) if rid else None
        rid = _patient_rid(ev)
        old = self.patients.get(rid)
        state = {**(old or {}), **{k: ev.payload[k] for k in pkeys if k in DEMOGRAPHIC_KEYS}}
        if old is None or not _same(state, old):
            self.patients[rid] = state
            self.entries.append(self.v.entry(_patient_resource(rid, state), ev.observed_at))
        self.case_patient[ev.case_id] = rid
        return _ref("Patient", rid)

    def add(self, ev: Event) -> None:
        pkeys = [k for k in PATIENT_KEYS if k in ev.payload]
        pref = self._patient(ev, pkeys)
        iso_primary = ev.type in ORDER_EVENTS and ORDER_EVENTS[ev.type][1] == "isolation" and pref is not None
        companions = _clinical(ev, pref, iso_primary) if pkeys else []
        primary = self._primary(ev, pref)
        companions += self._side(ev)
        env = [{"url": SD + "event-id", "valueString": ev.event_id},
               {"url": SD + "event-type", "valueCoding": {"system": CS + "event-type", "code": ev.type.value}},
               {"url": SD + "case-id", "valueString": ev.case_id},
               {"url": SD + "event-source", "valueString": ev.source},
               {"url": SD + "occurred-at", "valueDateTime": _ts(ev.occurred_at)},
               {"url": SD + "payload-key-order", "valueString": json.dumps(list(ev.payload), ensure_ascii=False)}]
        patient = None
        if pkeys:
            env.append({"url": SD + "patient-keys", "valueString": json.dumps(pkeys)})
            if _subject(primary) != pref:
                env.append({"url": SD + "event-subject", "valueReference": pref})
            rid = pref["reference"].split("/", 1)[1]
            patient = _patient_resource(rid, self.patients[rid])
        # 표준 필드로 되살릴 수 없는 값만 봉투에 남긴다
        std = _standard_payload(ev.type, primary, companions, patient, pkeys)
        env += [_payload_ext(k, val) for k, val in ev.payload.items() if k not in std or not _same(std[k], val)]
        comp_ext = [{"url": SD + "companion-of", "valueString": ev.event_id}]
        self.entries += [self.v.entry(c, ev.observed_at, comp_ext) for c in companions]
        self.entries.append(self.v.entry(primary, ev.observed_at, env))

    def _primary(self, ev: Event, pref: dict | None) -> dict:
        p, occ = ev.payload, _ts(ev.occurred_at)
        bed = _str(p.get("bed_id"))
        if ev.type in ENCOUNTER_EVENTS:
            return self._encounter(ev, pref, bed)
        if ev.type in BED_STATUS_EVENTS and bed:
            return _location("bd", bed, "active", BED_STATUS_EVENTS[ev.type])
        if ev.type in ROOM_STATUS_EVENTS:
            st, op = ROOM_STATUS_EVENTS[ev.type]
            return _location("ro", _str(p.get("room_id")) or ev.case_id, st, op)
        if ev.type in ORDER_EVENTS and pref:
            status, kind = ORDER_EVENTS[ev.type]
            rid = _fid(f"{ev.event_id}:order")
            if kind == "isolation":
                return _isolation_request(ev, rid, pref, status)
            return {"resourceType": "ServiceRequest", "id": rid, "status": status, "intent": "order",
                    "category": [_cc(CS + "order-kind", kind)], "code": {"text": "선별검사"}, "subject": pref,
                    "authoredOn": occ}
        if ev.type in RESULT_EVENTS and pref:
            return {"resourceType": "Observation", "id": _fid(f"{ev.event_id}:result"), "status": "final",
                    "category": [_cc(OBS_CAT, "laboratory")],
                    "code": {"coding": [{"system": CS + "observation", "code": "screening"}], "text": "선별검사 결과"},
                    "subject": pref, "effectiveDateTime": occ}
        if ev.type in HUMAN_TASK_EVENTS:
            t = self._human_task(ev, pref, bed)
            if t:
                return t
        return self._record_task(ev, pref)

    def _encounter(self, ev: Event, pref: dict | None, bed: str | None) -> dict:
        p, occ = ev.payload, _ts(ev.occurred_at)
        eid = _fid("encounter:" + ev.case_id)
        enc = deepcopy(self.encounters.get(eid) or {
            "resourceType": "Encounter", "id": eid, "identifier": [{"system": CASE_ID_SYSTEM, "value": ev.case_id}],
            "status": "planned", "class": {"system": V3_ACT, "code": "IMP", "display": "inpatient encounter"}})
        status, loc_status = ENCOUNTER_EVENTS[ev.type]
        if status:
            enc["status"] = status
        if status == "in-progress" and "start" not in enc.get("period", {}):
            enc.setdefault("period", {})["start"] = occ
        if status in ("finished", "cancelled"):
            enc.setdefault("period", {})["end"] = occ
        if pref:
            enc["subject"] = pref
        locs = enc.setdefault("location", [])
        ward = _str(p.get("preferred_ward_id"))
        if ward and not any(_kind(x) == "wa" and x["location"]["identifier"]["value"] == ward for x in locs):
            locs.append(_enc_location("wa", ward, "planned", occ))
        if bed and loc_status:
            old = next((x for x in locs if _kind(x) == "bd" and x["location"]["identifier"]["value"] == bed), None)
            if old:
                locs.remove(old)
            if loc_status == "active":  # 다른 병상으로 옮기면 앞 병상은 끝
                for x in locs:
                    if _kind(x) == "bd" and x["status"] == "active":
                        x["status"], x["period"] = "completed", {**x["period"], "end": occ}
            new = _enc_location("bd", bed, loc_status, occ)
            if old and loc_status == "completed":
                new["period"] = {**old["period"], "end": occ}
            elif old and old["status"] == loc_status:
                new["period"] = old["period"]
            locs.append(new)  # 이 사건이 건드린 병상이 맨 뒤
        dest = _str(p.get("destination"))
        if dest and ev.type is T.UNPLANNED_TRANSFER_OUT:
            locs.append({"location": {"display": dest}, "status": "active", "period": {"start": occ}})
        if not locs:
            del enc["location"]
        dt = _str(p.get("discharge_type"))
        if dt:
            enc["hospitalization"] = {"dischargeDisposition": _cc(DISCH, _DISCHARGE[dt]) if dt in _DISCHARGE
                                      else _cc(CS + "discharge-type", dt)}
        if ev.type is T.DEATH_CONFIRMED:
            enc["hospitalization"] = {"dischargeDisposition": _cc(DISCH, "exp", "Expired")}
        self.encounters[eid] = enc
        return enc

    def _task_version(self, key: str, occ: str, **init) -> dict:
        tid = _fid("task:" + key)
        return deepcopy(self.tasks.get(tid) or {
            "resourceType": "Task", "id": tid, "identifier": [{"system": TASK_ID_SYSTEM, "value": key}],
            "status": "requested", "intent": "order", "authoredOn": occ, **init})

    def _human_task(self, ev: Event, pref: dict | None, bed: str | None) -> dict | None:
        p, occ = ev.payload, _ts(ev.occurred_at)
        code = None
        if ev.type in (T.BED_PREP_REQUESTED, T.BED_PREP_DONE):
            if not bed:
                return None
            key, code = f"bed-prep:{ev.case_id}:{bed}", "bed_prep"
        elif ev.type is T.HUMAN_TASK_CREATED:
            key, code = ev.event_id, _str(p.get("kind"))
        elif _str(p.get("task_id")):
            key = p["task_id"]
        else:
            return None
        task = self._task_version(key, occ)
        status = HUMAN_TASK_EVENTS[ev.type]
        if status:
            task["status"] = status
        if code:
            task["code"] = _cc(CS + "task-kind", code)
        owner = p.get("assignee") if ev.type is T.HUMAN_TASK_CREATED else p.get("to")
        if _str(owner):
            task["owner"] = {"display": owner}
        if ev.type is T.TASK_ESCALATED:
            task["priority"] = "urgent"
        if bed:
            task["focus"] = _ref("Location", _loc_id(bed), (BED_ID_SYSTEM, bed))
        if pref:
            task["for"] = pref
        if status == "completed":
            task["executionPeriod"] = {"end": occ}
        task["lastModified"] = occ
        self.tasks[task["id"]] = task
        return task

    def _record_task(self, ev: Event, pref: dict | None) -> dict:
        """표준 대응이 없는 사건(사가 명령, 감사 기록 등). 일어난 일의 기록이라 대개 completed다."""
        v = ev.type.value
        status = ("requested" if v.endswith(("_requested", "_asked")) else
                  "cancelled" if v.endswith(("_cancelled", "_withdrawn")) else
                  "rejected" if "rejected" in v else "completed")
        task = {"resourceType": "Task", "id": _fid(ev.event_id), "status": status,
                "intent": "proposal" if ev.source == "agent" else "order",
                "code": _cc(CS + "event-type", v), "requester": {"display": ev.source},
                "authoredOn": _ts(ev.occurred_at)}
        if status == "completed":
            task["executionPeriod"] = {"start": _ts(ev.occurred_at)}
        if pref:
            task["for"] = pref
        inputs = [_input(k, val) for k, val in ev.payload.items() if k not in PATIENT_KEYS]
        if inputs:
            task["input"] = inputs
        return task

    def _side(self, ev: Event) -> list[dict]:
        """주 리소스에 딸린 판: 입실·퇴실에 따른 병상 상태, 청소 작업 Task."""
        bed, out = _str(ev.payload.get("bed_id")), []
        if not bed:
            return out
        if ev.type in ENCOUNTER_EVENTS:
            if ENCOUNTER_EVENTS[ev.type][1] == "active":
                out.append(_location("bd", bed, "active", "O"))
            elif ev.type in (T.PATIENT_DEPARTED, T.BODY_TRANSFERRED):
                out.append(_location("bd", bed, "active", "K"))
        if ev.type in CLEANING_TASK_EVENTS:
            occ = _ts(ev.occurred_at)
            task = self._task_version(f"cleaning:{ev.case_id}:{bed}", occ, code=_cc(CS + "task-kind", "cleaning"),
                                      focus=_ref("Location", _loc_id(bed), (BED_ID_SYSTEM, bed)))
            task["status"] = CLEANING_TASK_EVENTS[ev.type]
            if task["status"] == "completed":
                task["executionPeriod"] = {"end": occ}
            task["lastModified"] = occ
            self.tasks[task["id"]] = task
            out.append(task)
        return out


def _standard_payload(et: EventType, primary: dict, related: list[dict], patient: dict | None,
                      pkeys: list[str]) -> dict:
    """표준 필드에서 되살릴 수 있는 payload 값 (내보내기와 들여오기가 같은 함수를 쓴다)."""
    out = _patient_payload(patient, [primary, *related], pkeys) if pkeys else {}
    rt = primary["resourceType"]
    if rt == "Encounter":
        locs = primary.get("location", [])
        beds = [x for x in locs if _kind(x) == "bd"]
        if beds:
            out["bed_id"] = beds[-1]["location"]["identifier"]["value"]
        ward = next((x for x in locs if _kind(x) == "wa"), None)
        if ward:
            out["preferred_ward_id"] = ward["location"]["identifier"]["value"]
        dest = [x for x in locs if "physicalType" not in x]
        if dest and et is T.UNPLANNED_TRANSFER_OUT:
            out["destination"] = dest[-1]["location"]["display"]
        dd = primary.get("hospitalization", {}).get("dischargeDisposition")
        if dd and et is not T.DEATH_CONFIRMED:
            code = _code_of(dd, DISCH)
            out["discharge_type"] = _DISCHARGE_BACK.get(code) if code else _code_of(dd, CS + "discharge-type")
    elif rt == "Location":
        out["bed_id" if _kind(primary) == "bd" else "room_id"] = primary["identifier"][0]["value"]
    elif rt == "Task" and "input" in primary:
        out.update({i["type"]["text"]: _input_value(i) for i in primary["input"]})
    elif rt == "Task" and "identifier" in primary:
        if et in (T.HUMAN_TASK_DONE, T.TASK_HANDED_OVER, T.TASK_ESCALATED):
            out["task_id"] = primary["identifier"][0]["value"]
        if "focus" in primary:
            out["bed_id"] = primary["focus"]["identifier"]["value"]
        if et is T.HUMAN_TASK_CREATED:
            if "owner" in primary:
                out["assignee"] = primary["owner"]["display"]
            if "code" in primary:
                out["kind"] = primary["code"]["coding"][0]["code"]
        if et in (T.TASK_HANDED_OVER, T.TASK_ESCALATED) and "owner" in primary:
            out["to"] = primary["owner"]["display"]
    return out


def events_to_bundle(events: Iterable[Event]) -> dict:
    ex = _Exporter()
    for ev in events:
        ex.add(ev)
    return {"resourceType": "Bundle", "id": _fid("bundle:" + str(len(ex.entries))), "type": "history",
            "total": len(ex.entries), "entry": ex.entries}


# ---------- 들여오기 ----------

def _envelope(entry: dict) -> dict:
    out: dict = {"payload": []}
    for e in entry.get("extension", []):
        if e["url"] == SD + "payload-value":
            out["payload"].append(_payload_ext_value(e))
        elif e["url"].startswith(SD):
            out[e["url"].removeprefix(SD)] = next(e[x] for x in e if x.startswith("value"))
    return out


def bundle_to_events(bundle: dict) -> list[Event]:
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "history":
        raise ValueError("Bundle.type=history만 받는다 (이벤트 의미 보존)")
    patients: dict[str, dict] = {}  # Patient id → 지금까지 본 마지막 판
    pending: list[dict] = []  # 다음 주 항목에 딸린 리소스
    out = []
    for entry in bundle.get("entry", []):
        r, env = entry["resource"], _envelope(entry)
        if "event-id" not in env:
            if r["resourceType"] == "Patient":
                patients[r["id"]] = r
            else:
                pending.append(r)
            continue
        et = EventType(env["event-type"]["code"])
        pkeys = json.loads(env.get("patient-keys", "[]"))
        ref = env.get("event-subject") or _subject(r)
        patient = patients.get(ref["reference"].split("/", 1)[1]) if pkeys and ref else None
        payload = _standard_payload(et, r, pending, patient, pkeys)
        payload.update(env["payload"])
        out.append(Event(event_id=env["event-id"], case_id=env["case-id"], type=et,
                         occurred_at=_from_ts(env["occurred-at"]), observed_at=_from_ts(r["meta"]["lastUpdated"]),
                         source=env["event-source"],
                         payload={k: payload[k] for k in json.loads(env["payload-key-order"])}))
        pending = []
    return out


# ---------- 배치 (병동·병실·병상) ----------

def layout_to_locations(wards: Iterable[Ward], rooms: Iterable[Room], beds: Iterable[Bed]) -> list[dict]:
    pt = lambda code: {"coding": [{"system": LOC_PHYS, "code": code}]}  # noqa: E731
    out = []
    for w in wards:
        out.append({"resourceType": "Location", "id": w.id, "status": "active", "name": w.name, "physicalType": pt("wa"),
                    "extension": [_ext("specialty", w.specialty), _ext("departments", json.dumps(list(w.departments),
                                                                                                    ensure_ascii=False))]})
    for r in rooms:
        ext = [x for x in (_ext("room-type", r.room_type.value), _ext("capacity", r.capacity),
                           _ext("gender", r.gender.value if r.gender else None), _ext("cohort-key", r.cohort_key),
                           _ext("near-nurse-station", r.near_nurse_station),
                           _ext("air-changes-per-hour", r.air_changes_per_hour)) if x is not None]
        out.append({"resourceType": "Location", "id": r.id, "status": "active", "physicalType": pt("ro"),
                    "partOf": {"reference": f"Location/{r.ward_id}"}, "extension": ext})
    for b in beds:
        out.append({"resourceType": "Location", "id": b.id, "status": "active", "physicalType": pt("bd"),
                    "partOf": {"reference": f"Location/{b.room_id}"}})
    return out


def locations_to_layout(locs: list[dict]) -> tuple[tuple[Ward, ...], tuple[Room, ...], tuple[Bed, ...]]:
    kind = lambda x: x["physicalType"]["coding"][0]["code"]  # noqa: E731
    parent = lambda x: x["partOf"]["reference"].split("/", 1)[1]  # noqa: E731
    wards = tuple(Ward(id=x["id"], name=x["name"], specialty=_ext_get(x, "specialty"),
                       departments=tuple(json.loads(_ext_get(x, "departments")))) for x in locs if kind(x) == "wa")
    rooms = tuple(Room(id=x["id"], ward_id=parent(x), room_type=RoomType(_ext_get(x, "room-type")),
                       capacity=_ext_get(x, "capacity"),
                       gender=Gender(_ext_get(x, "gender")) if _ext_get(x, "gender") else None,
                       cohort_key=_ext_get(x, "cohort-key"), near_nurse_station=bool(_ext_get(x, "near-nurse-station")),
                       air_changes_per_hour=_ext_get(x, "air-changes-per-hour")) for x in locs if kind(x) == "ro")
    ward_of = {r.id: r.ward_id for r in rooms}
    beds = tuple(Bed(id=x["id"], room_id=parent(x), ward_id=ward_of[parent(x)]) for x in locs if kind(x) == "bd")
    return wards, rooms, beds
