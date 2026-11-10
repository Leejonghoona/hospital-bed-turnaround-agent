"""내부 이벤트 ↔ FHIR R4 Bundle(type=history). 런타임 의존성 없이 dict로 만든다.

- 이벤트 1건 = Task 1개. 현실 시각은 executionPeriod.start, 인지 시각은 meta.lastUpdated.
- 환자 필드는 Patient와 Flag(SNOMED CT 주의 코드)로 내보내고, 들여올 때 다시 payload로 만든다.
- 시각은 합성 병원 현지 시각(+09:00)으로 표기하고, 들여올 때 표기를 떼어 내부 형식(시간대 없음)으로 되돌린다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Iterable

from turnaround.events.models import Event, EventType
from turnaround.sim.models import Bed, Gender, Room, RoomType, Ward

TZ = "+09:00"
SYS_EVENT_TYPE = "urn:turnaround:event-type"
SYS_EVENT_ID = "urn:turnaround:event-id"
SYS_CASE = "urn:turnaround:case"
EXT = "urn:turnaround:fhir:extension:"
SNOMED = "http://snomed.info/sct"
PRECAUTION_SNOMED = {"airborne": ("409524006", "Airborne precautions"),
                     "droplet": ("409525007", "Droplet precautions"),
                     "contact": ("409529001", "Contact precautions")}
SNOMED_PRECAUTION = {v[0]: k for k, v in PRECAUTION_SNOMED.items()}
PATIENT_KEYS = ("patient_id", "name", "gender", "age", "department", "isolation_order", "isolation_type",
                "precautions", "organism", "precaution_status", "isolation_ordered_by", "immunocompromised",
                "open_wound")
_GENDER = {"M": "male", "F": "female"}
_GENDER_BACK = {v: k for k, v in _GENDER.items()}


def _fid(s: str) -> str:
    """FHIR id 규칙([A-Za-z0-9-.], 64자)에 맞춘 결정적 ID."""
    return hashlib.sha1(s.encode()).hexdigest()[:32]


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + TZ


def _from_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.removesuffix(TZ))


def _ext(key: str, value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"url": EXT + key, "valueBoolean": value}
    if isinstance(value, int):
        return {"url": EXT + key, "valueInteger": value}
    return {"url": EXT + key, "valueString": str(value)}


def _ext_get(res: dict, key: str):
    for e in res.get("extension", []):
        if e["url"] == EXT + key:
            return e.get("valueBoolean", e.get("valueInteger", e.get("valueString")))
    return None


def _input(key: str, v) -> dict:
    t = {"text": key}
    if isinstance(v, bool):
        return {"type": t, "valueBoolean": v}
    if isinstance(v, int):
        return {"type": t, "valueInteger": v}
    if isinstance(v, str):
        return {"type": t, "valueString": v}
    # None, 목록, 사전, 실수: JSON 문자열 (표준 값 형식이 없거나 null을 허용하지 않음)
    return {"type": {"text": key, "coding": [{"system": EXT + "value-encoding", "code": "json"}]},
            "valueString": json.dumps(v, ensure_ascii=False)}


def _input_value(inp: dict):
    if any(c.get("code") == "json" for c in inp["type"].get("coding", [])):
        return json.loads(inp["valueString"])
    for k in ("valueBoolean", "valueInteger", "valueString"):
        if k in inp:
            return inp[k]
    raise ValueError(f"값 없음: {inp}")


def _entry(resource: dict, observed: datetime) -> dict:
    rt = resource["resourceType"]
    resource["meta"] = {"lastUpdated": _ts(observed)}
    return {"fullUrl": f"urn:uuid:{resource['id']}", "resource": resource,
            "request": {"method": "PUT", "url": f"{rt}/{resource['id']}"},
            "response": {"status": "200 OK", "lastModified": _ts(observed)}}


# ---------- 환자 ----------

def _patient_resources(ev: Event, keys: list[str]) -> tuple[dict, list[dict]]:
    p = ev.payload
    pid = _fid(f"{ev.event_id}:patient")
    ext = [x for x in (_ext(k, p.get(k)) for k in ("patient_id", "age", "department", "isolation_order",
                                                    "isolation_type", "organism", "precaution_status",
                                                    "isolation_ordered_by", "immunocompromised", "open_wound"))
           if x is not None]
    patient = {"resourceType": "Patient", "id": pid, "extension": ext}
    if p.get("name") is not None:
        patient["name"] = [{"text": p["name"]}]
    if p.get("gender") in _GENDER:
        patient["gender"] = _GENDER[p["gender"]]
    if not ext:
        del patient["extension"]
    flags = []
    for i, prec in enumerate(p.get("precautions") or []):
        code, disp = PRECAUTION_SNOMED[prec]
        flags.append({"resourceType": "Flag", "id": _fid(f"{ev.event_id}:flag:{i}"), "status": "active",
                      "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/flag-category",
                                                "code": "clinical"}]}],
                      "code": {"coding": [{"system": SNOMED, "code": code, "display": disp}]},
                      "subject": {"reference": f"Patient/{pid}"}})
    return patient, flags


def _patient_payload(patient: dict, flags: list[dict], keys: list[str]) -> dict:
    out: dict = {}
    for k in keys:
        if k == "name":
            out[k] = patient["name"][0]["text"] if patient.get("name") else None
        elif k == "gender":
            out[k] = _GENDER_BACK.get(patient.get("gender"))
        elif k == "precautions":
            out[k] = [SNOMED_PRECAUTION[f["code"]["coding"][0]["code"]] for f in flags]
        else:
            out[k] = _ext_get(patient, k)
    return out


# ---------- 이벤트 ↔ 번들 ----------

def events_to_bundle(events: Iterable[Event]) -> dict:
    entries = []
    for ev in events:
        pkeys = [k for k in PATIENT_KEYS if k in ev.payload]
        task = {"resourceType": "Task", "id": _fid(ev.event_id), "status": "completed", "intent": "order",
                "identifier": [{"system": SYS_EVENT_ID, "value": ev.event_id}],
                "groupIdentifier": {"system": SYS_CASE, "value": ev.case_id},
                "code": {"coding": [{"system": SYS_EVENT_TYPE, "code": ev.type.value}]},
                "requester": {"display": ev.source},
                "authoredOn": _ts(ev.occurred_at), "executionPeriod": {"start": _ts(ev.occurred_at)}}
        task["extension"] = [{"url": EXT + "key-order", "valueString": json.dumps(list(ev.payload))}]
        if pkeys:
            patient, flags = _patient_resources(ev, pkeys)
            entries.append(_entry(patient, ev.observed_at))
            entries += [_entry(f, ev.observed_at) for f in flags]
            task["for"] = {"reference": f"Patient/{patient['id']}"}
            task["extension"].append({"url": EXT + "patient-keys", "valueString": json.dumps(pkeys)})
        inputs = [_input(k, v) for k, v in ev.payload.items() if k not in pkeys]
        if inputs:
            task["input"] = inputs
        entries.append(_entry(task, ev.observed_at))
    return {"resourceType": "Bundle", "id": _fid("bundle:" + str(len(entries))), "type": "history",
            "total": len(entries), "entry": entries}


def bundle_to_events(bundle: dict) -> list[Event]:
    if bundle.get("resourceType") != "Bundle" or bundle.get("type") != "history":
        raise ValueError("Bundle.type=history만 받는다 (이벤트 의미 보존)")
    patients: dict[str, dict] = {}
    flags: dict[str, list[dict]] = {}
    out = []
    for entry in bundle["entry"]:
        r = entry["resource"]
        rt = r["resourceType"]
        if rt == "Patient":
            patients[r["id"]] = r
        elif rt == "Flag":
            flags.setdefault(r["subject"]["reference"].split("/", 1)[1], []).append(r)
        elif rt == "Task":
            payload = {}
            pkeys = next((json.loads(e["valueString"]) for e in r.get("extension", [])
                          if e["url"] == EXT + "patient-keys"), [])
            # 원래 payload 키 순서를 되살린다: 환자 키와 입력 키를 이벤트 기록 순서대로
            inputs = {i["type"]["text"]: _input_value(i) for i in r.get("input", [])}
            if pkeys:
                pid = r["for"]["reference"].split("/", 1)[1]
                ppay = _patient_payload(patients[pid], flags.get(pid, []), pkeys)
            else:
                ppay = {}
            order = json.loads(next((e["valueString"] for e in r.get("extension", [])
                                     if e["url"] == EXT + "key-order"), "null") or "null")
            for k in order or list(ppay) + list(inputs):
                payload[k] = ppay[k] if k in ppay else inputs[k]
            out.append(Event(event_id=r["identifier"][0]["value"], case_id=r["groupIdentifier"]["value"],
                             type=EventType(r["code"]["coding"][0]["code"]),
                             occurred_at=_from_ts(r["executionPeriod"]["start"]),
                             observed_at=_from_ts(r["meta"]["lastUpdated"]), source=r["requester"]["display"],
                             payload=payload))
    return out


# ---------- 배치 (병동·병실·병상) ----------

def layout_to_locations(wards: Iterable[Ward], rooms: Iterable[Room], beds: Iterable[Bed]) -> list[dict]:
    pt = lambda code: {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/location-physical-type",  # noqa: E731
                                   "code": code}]}
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
