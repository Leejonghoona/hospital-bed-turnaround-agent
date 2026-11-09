"""병원 데이터 매핑 (로드맵 07 M6): 병원 내보내기 표 → 핵심 온톨로지 사건·객체.

매핑은 선언(YAML로 저장 가능한 pydantic 모델)이다. 표마다 종류(병상 표 / 넓은 사례 표 / 사례 마스터 / 사건 기록),
열 → 정규 키, 코드표(원천값 → 정규값), 시각 형식, 목록 구분자, 참거짓 값, 병상 ID 조합 방식을 적는다.
`apply_mapping`은 결정적 변환이다. 결과 사건의 내용(payload)은 핵심 온톨로지 사건 사전(core.ttl)의 `carries`를 따른다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from turnaround.events import Event
from turnaround.events.replay import _patient
from turnaround.sim.models import Bed, Gender, Patient, Room, RoomType, Ward

from .core import event_kinds

_PATIENT_FIELDS = ("patient_id", "name", "gender", "age", "department", "precautions", "organism", "precaution_status",
                   "isolation_ordered_by", "immunocompromised", "open_wound")
_TIME_EXTRAS = {"eta", "requested_at"}
_BOOLS = {"immunocompromised", "open_wound", "from_icu", "wants_single_room", "sporicidal", "near_nurse_station"}
_INTS = {"age", "day", "aerosol_wait_min", "capacity", "air_changes_per_hour"}
_LISTS = {"precautions", "departments"}


class TableMap(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["layout", "cases", "events"]
    columns: dict[str, str] = Field(default_factory=dict)  # 열 이름 → 정규 키
    event_codes: dict[str, str] = Field(default_factory=dict)  # 사건 기록: 원천 코드 → 사건 이름
    event_columns: tuple[str, str, str] = ("", "", "")  # 사건 기록: (사례 열, 코드 열, 시각 열)


class Mapping(BaseModel):
    model_config = ConfigDict(frozen=True)
    hospital: str
    time_format: str
    list_sep: str = ","
    bool_values: tuple[str, str] = ("Y", "N")
    bed_parts: tuple[str, str, str]  # 병동·병실·병상 열 이름
    bed_part_style: Literal["plain", "digits"] = "plain"
    codes: dict[str, dict[str, str]] = Field(default_factory=dict)  # 정규 키 → {원천값: 정규값}
    tables: dict[str, TableMap]


def _bed_id(m: Mapping, row: dict) -> str | None:
    w, r, b = (str(row.get(c, "")).strip() for c in m.bed_parts)
    if not (w and r and b):
        return None
    if m.bed_part_style == "digits":
        return f"W{w}-R{r}-B{b}"
    return f"{w}-{r}-{b}"


def _dec(m: Mapping, key: str, raw):
    if raw is None or raw == "":
        return None
    base = key.split(".")[-1]
    code = m.codes.get(key, {})
    if base in _BOOLS:
        return raw == m.bool_values[0]
    if base in _LISTS:
        return [code.get(x, x) for x in str(raw).split(m.list_sep) if x]
    if key.startswith("t:") or base in _TIME_EXTRAS:
        return datetime.strptime(raw, m.time_format)
    if base in _INTS:
        return int(raw)
    val = code.get(raw, raw)
    if key == "patient.gender" and val in ("other", "unknown"):
        return None  # 남녀 병실 구별에 쓸 수 없다: 성별 입력 누락으로 사람이 확인한다 (2026-11-09)
    return val


def _records(m: Mapping, tables: dict[str, list[dict]]) -> dict[str, dict]:
    recs: dict[str, dict] = {}
    for tname, tm in m.tables.items():
        if tm.kind != "cases":
            continue
        for row in tables.get(tname, []):
            r = {}
            for col, key in tm.columns.items():
                if col in row:
                    v = _dec(m, key, row[col])
                    if v is not None:
                        r[key] = v
            bid = _bed_id(m, row)
            if bid:
                r["bed_id"] = bid
            case = r.get("case")
            recs.setdefault(case, {}).update(r)
    for tname, tm in m.tables.items():
        if tm.kind != "events":
            continue
        cc, kc, tc = tm.event_columns
        for row in tables.get(tname, []):
            key = tm.event_codes.get(row[kc])
            if key is None:
                continue
            recs.setdefault(row[cc], {"case": row[cc]})[f"t:{key}"] = datetime.strptime(row[tc], m.time_format)
    return recs


def _events(recs: dict[str, dict]) -> list[Event]:
    kinds = event_kinds()
    out = []
    for case, r in recs.items():
        patient = None
        if r.get("patient.patient_id"):
            attrs = {k: r.get(f"patient.{k}") for k in _PATIENT_FIELDS}
            attrs["id"] = attrs.pop("patient_id")
            attrs = {k: v for k, v in attrs.items() if v is not None}
            attrs.setdefault("precautions", [])
            attrs.setdefault("gender", None)
            patient = _patient(Patient(**attrs))
        for k, v in r.items():
            if not k.startswith("t:"):
                continue
            key = k[2:]
            kind = kinds[key]
            payload: dict = {}
            for c in kind.carries:
                if c == "patient" and patient is not None:
                    payload.update(patient)
                elif c == "bed_id" and r.get("bed_id"):
                    payload["bed_id"] = r["bed_id"]
                elif c == "day" and r.get("day") is not None:
                    payload["day"] = r["day"]
                elif f"{key}.{c}" in r:
                    val = r[f"{key}.{c}"]
                    payload[c] = val.isoformat() if isinstance(val, datetime) else val
            out.append(Event(event_id=f"{case}:{key}", case_id=case, type=kind.event_type, occurred_at=v,
                             observed_at=v, source=kind.source, payload=payload))
    return sorted(out, key=lambda e: (e.observed_at, e.occurred_at, e.event_id))


def _layout(m: Mapping, tables: dict[str, list[dict]]):
    wards, rooms, beds = {}, {}, []
    for tname, tm in m.tables.items():
        if tm.kind != "layout":
            continue
        for row in tables.get(tname, []):
            v = {key: _dec(m, key, row.get(col, "")) for col, key in tm.columns.items()}
            bid = _bed_id(m, row)
            wid, rid = bid.rsplit("-", 2)[0], bid.rsplit("-", 1)[0]
            if wid not in wards:
                wards[wid] = Ward(id=wid, name=v.get("ward_name") or wid, specialty=v.get("specialty") or "",
                                  departments=tuple(v.get("departments") or ()))
            if rid not in rooms:
                rooms[rid] = Room(id=rid, ward_id=wid, room_type=RoomType(v["room_type"]), capacity=v["capacity"],
                                  gender=Gender(v["gender"]) if v.get("gender") else None,
                                  cohort_key=v.get("cohort_key"), near_nurse_station=bool(v.get("near_nurse_station")),
                                  air_changes_per_hour=v.get("air_changes_per_hour"))
            beds.append(Bed(id=bid, room_id=rid, ward_id=wid))
    return tuple(wards.values()), tuple(rooms.values()), tuple(beds)


class Mapped(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    wards: tuple[Ward, ...]
    rooms: tuple[Room, ...]
    beds: tuple[Bed, ...]
    events: list[Event]


def apply_mapping(m: Mapping, tables: dict[str, list[dict]]) -> Mapped:
    wards, rooms, beds = _layout(m, tables)
    return Mapped(wards=wards, rooms=rooms, beds=beds, events=_events(_records(m, tables)))


__all__ = ["Mapped", "Mapping", "TableMap", "apply_mapping"]
