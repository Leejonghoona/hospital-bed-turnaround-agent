"""매핑 에이전트 (로드맵 07 M6-4): 병원 내보내기 표를 보고 핵심 온톨로지 매핑 초안을 만든다.

역할 분담 (CLAUDE.md 2번, 관문 원칙):
- LLM(`complete_json("propose_mapping")`): 열·코드의 뜻을 추측해 후보를 낸다. 시험은 가짜 LLM(어휘 사전)이 한다.
  LLM에는 원천 값을 보내지 않는다. 열 이름, 값의 모양(시각·숫자·목록·코드), 종류가 적은 코드값만 보낸다(이름·번호 없음).
- 결정적 코드: 시각 형식·참거짓·목록 구분자·병상 ID 조합을 값에서 찾고, 후보를 값 모양으로 거르고,
  이름이 애매한 시각 열(예: "퇴원일시")은 핵심 온톨로지의 사건 순서(ta:notAfter)로 가려낸다.
- 사람: 애매한 것, 사전에 없는 코드, 관례로 추정한 코드, 안전 속성 코드표(늘)를 질문으로 받아 답한다.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime

from rdflib import Graph

from turnaround.ontology.core import _core_ttl
from turnaround.ontology.graph import TA
from turnaround.ontology.mapping import Mapping, TableMap

_TIME_FORMATS = ((re.compile(r"^\d{12}$"), "%Y%m%d%H%M"), (re.compile(r"^\d{14}$"), "%Y%m%d%H%M%S"),
                 (re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$"), "%Y-%m-%d %H:%M"),
                 (re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$"), "%Y-%m-%d %H:%M:%S"),
                 (re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$"), "%Y-%m-%dT%H:%M:%S"),
                 (re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$"), "%Y-%m-%dT%H:%M"),
                 (re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$"), "%Y/%m/%d %H:%M"),
                 (re.compile(r"^\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}$"), "%Y.%m.%d %H:%M"),
                 (re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$"), "DMY_OR_MDY"))
_BOOL_PAIRS = (("Y", "N"), ("예", "아니오"), ("1", "0"), ("T", "F"), ("true", "false"), ("TRUE", "FALSE"),
               ("True", "False"), ("yes", "no"), ("YES", "NO"), ("유", "무"))
_SEPS = ("|", "+", ";", ",")
_MAX_CODES = 15


@dataclass
class Question:
    id: str
    item: str
    why: str
    proposal: object = None
    options: list = field(default_factory=list)


@dataclass
class MappingDraft:
    mapping: Mapping
    questions: list[Question]
    evidence: dict
    columns: dict[tuple[str, str], str | None]  # (표, 열) → 정규 키 (평가용)


# ---------------- 표 모양 (LLM 입력) ----------------

def _shape(values: list[str]) -> str:
    vs = [v for v in values if v != ""]
    if not vs:
        return "empty"
    if all(any(p.match(v) for p, _ in _TIME_FORMATS) for v in vs):
        return "time"
    if all(v.isdigit() for v in vs):
        return "int"
    return "text"


def profile(tables: dict[str, list[dict]]) -> dict:
    """표마다 열 이름과 값 모양. 종류가 적은 열만 값 목록을 넣는다(이름·번호·시각 값은 넣지 않는다)."""
    out = {}
    for t, rows in tables.items():
        cols = {}
        for c in (rows[0].keys() if rows else []):
            vals = [str(r.get(c, "")) for r in rows]
            distinct = sorted({v for v in vals if v})
            shape = _shape(vals)
            info = {"shape": shape, "fill": round(sum(1 for v in vals if v) / max(1, len(vals)), 3),
                    "distinct": len(distinct)}
            if shape != "time" and 0 < len(distinct) <= _MAX_CODES:
                info["codes"] = distinct
            cols[c] = info
        out[t] = {"rows": len(rows), "columns": cols}
    return out


# ---------------- 가짜 LLM: 어휘 사전 ----------------

_COL_LEX: dict[str, tuple[str, ...]] = {
    "case": ("case", "사례", "encounter", "내원번호"),
    "patient.patient_id": ("pt_no", "pt_key", "patient_id", "등록번호", "환자번호", "mrn"),
    "patient.name": ("pt_nm", "pt_name", "환자명", "성명", "patient_name"),
    "patient.gender": ("sex", "gender", "성별"),
    "patient.age": ("age", "나이", "연령"),
    "patient.department": ("dept", "진료과", "department"),
    "patient.precautions": ("iso_cd", "iso_flag", "격리구분", "격리유형", "precaution"),
    "patient.organism": ("org", "균명", "organism", "pathogen"),
    "patient.precaution_status": ("iso_stat", "격리상태", "isolation_status"),
    "patient.isolation_ordered_by": ("iso_by", "격리지시", "iso_ordered", "ordered_by"),
    "patient.immunocompromised": ("immuno", "면역"),
    "patient.open_wound": ("wound", "창상"),
    "day": ("day", "일차"),
    "discharge_decided.discharge_type": ("dsch_gb", "퇴원구분", "dsch_kind", "discharge_type"),
    "guardian_eta_set.eta": ("grd_eta", "보호자도착예정", "guardian_eta"),
    "guardian_eta_updated.eta": ("eta_new", "grd_eta_new", "도착예정변경", "eta_changed"),
    "rx_revised.reason": ("rsn", "reason", "사유"),
    "cleaning_requested.cleaning_type": ("cln_gb", "청소구분", "clean_kind", "cleaning_type"),
    "cleaning_requested.aerosol_wait_min": ("aerosol", "환기대기"),
    "cleaning_requested.sporicidal": ("spori", "아포"),
    "cleaning_done.cleaning_type": ("cln_done_gb", "청소완료구분", "clean_done_kind"),
    "unplanned_transfer_out.destination": ("dest", "전동처"),
    "admission_requested.requested_at": ("plan", "입원예정", "scheduled_admission"),
    "admission_requested.preferred_ward_id": ("pref", "희망병동"),
    "admission_requested.from_icu": ("icu_yn", "from_icu", "중환자실전동", "icu_flag"),
    "admission_requested.wants_single_room": ("sr_yn", "1인실", "single_req", "single_room"),
    # 사건 시각
    "t:discharge_decided": ("dec", "결정", "decide"),
    "t:discharge_registered": ("dsch_rsv", "예고", "notice", "register"),
    "t:guardian_eta_set": ("grd_input", "예정입력", "eta_in"),
    "t:order_confirmed": ("ord", "오더", "order"),
    "t:rx_issued": ("rx_dt", "약처방", "rx_issue"),
    "t:rx_revised": ("rx_rev", "처방수정", "rx_modify"),
    "t:rx_dispensed": ("disp", "조제"),
    "t:medication_ready": ("med_arr", "약도착", "rx_ward", "med_ready"),
    "t:education_done": ("edu", "교육"),
    "t:billing_done": ("bill", "수납", "정산", "payment"),
    "t:guardian_eta_updated": ("grd_upd", "예정변경일시", "eta_chg"),
    "t:guardian_arrived": ("grd_arr", "보호자도착일시", "grd_arrive", "guardian_arrived"),
    "t:patient_departed": ("out", "퇴실", "depart"),
    "t:cleaning_requested": ("cln_req", "청소요청", "env_req"),
    "t:cleaning_started": ("cln_st", "청소시작", "env_start"),
    "t:cleaning_done": ("cln_end", "청소완료일시", "env_done"),
    "t:bed_ready": ("rdy", "입실가능", "bed_ready"),
    "t:death_confirmed": ("exp", "사망", "expire"),
    "t:body_transferred": ("morgue", "안치"),
    "t:eol_transfer": ("eol", "임종"),
    "t:unplanned_transfer_out": ("trf", "급변전동", "transfer"),
    "t:admission_requested": ("adm_rsv", "입원예약"),
    "t:admission_cancelled": ("cncl", "취소", "cancel"),
    # 병상 표
    "ward_name": ("ward_nm", "병동명", "ward_name"),
    "specialty": ("spec", "병동특성"),
    "departments": ("depts", "병동진료과"),
    "room_type": ("room_gb", "병실구분", "room_class", "room_type"),
    "capacity": ("bed_cnt", "병상수", "beds_in_room", "capacity"),
    "gender": ("room_sex", "병실성별"),
    "air_changes_per_hour": ("ach", "환기횟수", "air_change"),
    "cohort_key": ("cohort", "코호트"),
    "near_nurse_station": ("ns_near", "간호사실", "near_ns"),
}
# 이 말이 들어 있으면 그 키가 아니다 (변경·새 값은 원래 값이 아님)
_COL_NOT = {"guardian_eta_set.eta": ("변경", "new", "chg", "upd"), "t:guardian_eta_set": ("변경", "chg", "upd"),
            "cleaning_requested.cleaning_type": ("done", "완료"), "patient.department": ("ward", "병동")}
# 이름만으로 사건을 가를 수 없는 말: 후보를 여럿 낸다 (순서로 가린다)
_AMBIGUOUS_TIME = {("dsch", "퇴원", "discharge"): ("t:billing_done", "t:patient_departed")}

_VALUE_LEX: dict[str, dict[str, tuple[str, ...]]] = {
    "gender": {"M": ("m", "남", "male"), "F": ("f", "여", "female")},
    "precautions": {"contact": ("c", "접촉", "contact"), "droplet": ("d", "비말", "droplet"),
                    "airborne": ("a", "공기", "airborne")},
    "room_type": {"negative_pressure": ("np", "음압", "iso-np", "negative"), "single": ("sr", "1인", "priv", "single"),
                  "multi": ("mr", "다인", "ward", "multi")},
    "cleaning_type": {"general": ("g", "일반", "gen", "general"), "terminal": ("t", "종말", "term", "terminal")},
    "precaution_status": {"confirmed": ("cf", "확진", "confirmed"), "suspected": ("sp", "의심", "suspected"),
                          "preemptive": ("pe", "선제", "preemptive")},
    "isolation_ordered_by": {"physician": ("md", "주치의", "physician"), "ipc": ("icn", "감염관리실", "ipc")},
    "reason": {"discharge_hold": ("hold", "퇴원보류", "discharge_hold"), "dose_change": ("dose", "용량변경", "dose_change"),
               "add_medication": ("add", "약추가", "add_medication")},
    "discharge_type": {"ama": ("ama", "자의퇴원")},
    "destination": {"icu": ("icu", "중환자실")},
    "department": {"호흡기내과": ("pul", "호흡기내과"), "소화기내과": ("gi", "소화기내과"), "감염내과": ("id", "감염내과"),
                   "일반외과": ("gs", "일반외과"), "정형외과": ("os", "정형외과"), "소아청소년과": ("ped", "소아청소년과"),
                   "신경과": ("nr", "neuro", "신경과")},
}
_CONVENTION = {"gender": {"1": "M", "2": "F"}}  # 관례 추정(주민등록번호 뒷자리 관례): 늘 묻는다
_CODE_KEYS = {"patient.gender": "gender", "gender": "gender", "patient.precautions": "precautions",
              "room_type": "room_type", "cleaning_requested.cleaning_type": "cleaning_type",
              "cleaning_done.cleaning_type": "cleaning_type", "patient.precaution_status": "precaution_status",
              "patient.isolation_ordered_by": "isolation_ordered_by", "rx_revised.reason": "reason",
              "discharge_decided.discharge_type": "discharge_type", "unplanned_transfer_out.destination": "destination",
              "patient.department": "department", "departments": "department"}
_EVENT_LEX = {k[2:]: v for k, v in _COL_LEX.items() if k.startswith("t:")}
_EVENT_LEX.update({"discharge_decided": ("decide", "dc_dec"), "discharge_registered": ("notice", "dc_rsv"),
                   "order_confirmed": ("dc_order", "order"), "medication_ready": ("rx_ward", "med_arr"),
                   "education_done": ("dc_edu", "edu"), "guardian_arrived": ("grd_arrive",),
                   "patient_departed": ("ward_out", "out"), "admission_requested": ("adm_rsv",),
                   "admission_cancelled": ("adm_cancel", "cancel")})


def _norm(s: str) -> str:
    return s.lower()


def _has(tok: str, name: str) -> bool:
    """짧은 영문 말(4자 이하)은 낱말 경계에서만 맞춘다(morgue 안의 org, without 안의 out 같은 오인 방지)."""
    if len(tok) > 4 or not tok.isascii():
        return tok in name
    return re.search(rf"(?<![a-z]){re.escape(tok)}(?![a-z])", name) is not None


def fake_propose(ctx: dict) -> dict:
    """가짜 LLM: 열 이름·코드값을 어휘 사전으로 읽어 후보를 낸다. 결정적."""
    out: dict = {"columns": {}, "values": {}, "event_codes": {}}
    for t, info in ctx["tables"].items():
        out["columns"][t] = {}
        for c, ci in info["columns"].items():
            name = _norm(c)
            hits = [(max(len(tok) for tok in toks if _has(tok, name)), key) for key, toks in _COL_LEX.items()
                    if any(_has(tok, name) for tok in toks) and not any(n in name for n in _COL_NOT.get(key, ()))]
            cands = [k for _, k in sorted(hits, key=lambda x: -x[0])]
            for toks, keys in _AMBIGUOUS_TIME.items():
                if any(_has(tok, name) for tok in toks) and not any(k.startswith("t:") and k not in keys for k in cands[:1]):
                    cands = list(keys) + [k for k in cands if k not in keys]
            out["columns"][t][c] = cands[:4]
            for code in ci.get("codes", []):
                vals = []
                for vkey, lex in _VALUE_LEX.items():
                    for canon, toks in lex.items():
                        if any(p == _norm(code) for p in toks):
                            vals.append(f"{vkey}:{canon}")
                if vals:
                    out["values"].setdefault(t, {}).setdefault(c, {})[code] = vals
    for code in ctx.get("event_codes", []):
        name = _norm(code)
        hits = [(max(len(tok) for tok in toks if _has(tok, name)), k) for k, toks in _EVENT_LEX.items()
                if any(_has(tok, name) for tok in toks)]
        cands = [k for _, k in sorted(hits, key=lambda x: -x[0])]
        if any(tok == name or name.startswith(tok) for tok in ("dsch", "discharge", "퇴원")) and not cands:
            cands = ["billing_done", "patient_departed"]
        out["event_codes"][code] = cands[:3]
    return out


# ---------------- 결정적 해석 ----------------

def _time_format(values: list[str]) -> str | None:
    """값에서 시각 형식을 찾는다. 일/월 순서가 섞인 꼴(02/03/2026)은 13 이상인 앞자리가 있으면 일/월, 뒷자리면 월/일.
    끝내 모르면 None(추측하지 않고 묻는다)."""
    for p, fmt in _TIME_FORMATS:
        if values and all(p.match(v) for v in values):
            if fmt != "DMY_OR_MDY":
                return fmt
            first = max(int(v[:2]) for v in values)
            second = max(int(v[3:5]) for v in values)
            if first > 12 and second <= 12:
                return "%d/%m/%Y %H:%M"
            if second > 12 and first <= 12:
                return "%m/%d/%Y %H:%M"
            return None
    return None


def _order_rel() -> list[tuple[str, str]]:
    g = Graph().parse(data=_core_ttl(), format="turtle")
    return [(str(s).split(":ev_")[1], str(o).split(":ev_")[1]) for s, o in g.subject_objects(TA.notAfter)]


def _confirm_always() -> set[str]:
    g = Graph().parse(data=_core_ttl(), format="turtle")
    return {str(o) for o in g.objects(TA.mapping_confirm_always, TA.codeKey)}


def _violations(times: dict[str, dict[str, datetime]], key: str, col_times: dict[str, datetime]) -> int:
    """key 사건으로 본 열 값이 이미 정한 사건들과 순서가 어긋나는 사례 수."""
    bad = 0
    for a, b in _order_rel():
        for case, t in col_times.items():
            other = times.get(case, {})
            if a == key and b in other and t > other[b]:
                bad += 1
            if b == key and a in other and other[a] > t:
                bad += 1
    return bad


_PART_TOKS = (("ward", "병동", "unit"), ("room", "병실"), ("bed", "병상"))
_PART_SUFFIX = ("", "_cd", "_no", "_number", "_id", "번호", "코드")


def _part_col(cols: list[str], toks) -> str | None:
    """병동·병실·병상 ID 열: 이름이 그 말 하나이거나 그 말 + 번호류 꼬리."""
    return next((c for c in cols if any(_norm(c) == tok + suf for tok in toks for suf in _PART_SUFFIX)), None)


def propose_mapping(tables: dict[str, list[dict]], llm=None, hospital: str = "draft") -> MappingDraft:
    prof = profile(tables)
    qs: list[Question] = []
    ev: dict = {"profile": prof}
    # 표 종류: 사건 기록(코드 열 + 시각 열, 시각 열이 하나뿐), 병상 표, 사례 표
    kinds, log_cols = {}, {}
    for t, info in prof.items():
        cols = info["columns"]
        times = [c for c, ci in cols.items() if ci["shape"] == "time"]
        if len(times) == 1 and len(cols) <= 4:
            code_col = next(c for c, ci in cols.items() if c not in times and 0 < ci["distinct"] <= 40
                            and ci["shape"] == "text")
            case_col = next(c for c in cols if c not in times and c != code_col)
            kinds[t], log_cols[t] = "events", (case_col, code_col, times[0])
        elif not times and all(_part_col(list(cols), toks) for toks in _PART_TOKS):
            kinds[t] = "layout"  # 시각 열이 없고 병동·병실·병상 열이 있으면 병상 표 (구조로 판정)
        else:
            kinds[t] = "cases"
    event_codes = sorted({r[log_cols[t][1]] for t in log_cols for r in tables[t]})
    ctx = {"tables": {t: prof[t] for t in prof if kinds[t] != "events"}, "event_codes": event_codes,
           "vocabulary": sorted(_COL_LEX)}
    if llm is None or llm.name == "fake":
        cand = fake_propose(ctx)
    else:
        try:
            cand = llm.complete_json("propose_mapping", "병원 내보내기 열을 핵심 온톨로지 키에 대응시킨다. JSON만.",
                                     json.dumps(ctx, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - 실패하면 사전으로
            cand = fake_propose(ctx)
    # 형식: 시각, 참거짓, 목록 구분자, 병상 ID
    all_times = [str(r[c]) for t in tables for r in tables[t] for c in r
                 if prof[t]["columns"][c]["shape"] == "time" and r[c]]
    time_fmt = _time_format(sorted(set(all_times))[:500])
    fmt_known = time_fmt is not None
    if time_fmt is None:
        qs.append(Question("fmt:time", "시각 형식", "값으로 시각 형식(또는 일/월 순서)을 정할 수 없다",
                           None, sorted(set(all_times))[:3]))
        time_fmt = "%Y-%m-%dT%H:%M:%S"  # 답을 받기 전 임시 (변환 전에 답이 필요하다)
    # 참거짓: 값이 알려진 짝과 정확히 같거나 그 한쪽만인 열에서 찾는다. 글자 짝(Y/N, true/false 등)은 이름 힌트가 필요 없고,
    # 숫자 짝(1/0)은 이름에 여부·flag 힌트가 있을 때만 (병상 수 같은 숫자 열과 헷갈리지 않게). 짝이 여럿이면 묻는다
    found = Counter()
    for t in prof:
        for c, ci in prof[t]["columns"].items():
            codes = set(ci.get("codes", []))
            for pair in _BOOL_PAIRS:
                hint = "yn" in _norm(c) or "flag" in _norm(c) or "여부" in c
                if codes and codes <= set(pair) and (not pair[0].isdigit() or hint) and (len(codes) == 2 or hint):
                    found[pair] += 1
    bool_values = found.most_common(1)[0][0] if found else ("Y", "N")
    if len(found) > 1:
        qs.append(Question("fmt:bool", "참거짓 표기", f"여러 표기가 섞여 있다: {sorted(found)}", bool_values))
    elif not found:
        qs.append(Question("fmt:bool", "참거짓 표기", "참거짓 열을 찾지 못했다", None))
    list_sep, sep_seen = ",", False
    for t in prof:
        for c, ci in prof[t]["columns"].items():
            if {"patient.precautions", "departments"} & set(cand["columns"].get(t, {}).get(c) or []):
                vals = [str(r.get(c, "")) for r in tables[t]]
                for sep in _SEPS:
                    if any(sep in v for v in vals):
                        list_sep, sep_seen = sep, True
                        break
    if not sep_seen:  # 여러 값이 든 칸이 없어 구분자를 볼 수 없다: 추측하지 않고 묻는다(답 전까지 쉼표)
        qs.append(Question("fmt:sep", "목록 구분자", "여러 값이 든 칸이 없어 구분자를 확인할 수 없다", ","))
    # 병상 ID 조합: 병상 표에서 병동·병실·병상 열
    bed_parts, style = ("", "", ""), "plain"
    lay = next((t for t, k in kinds.items() if k == "layout"), None)
    if lay:
        cols = list(prof[lay]["columns"])
        w, r, b = (_part_col(cols, toks) for toks in _PART_TOKS)
        bed_parts = (w or "", r or "", b or "")
        sample = str(tables[lay][0].get(w, "")) if w else ""
        style = "digits" if sample.isdigit() else "plain"
        if not all(bed_parts):
            qs.append(Question("bed_parts", "병상 ID", "병동·병실·병상 열을 찾지 못했다", bed_parts))
    # 열 → 키
    chosen: dict[tuple[str, str], str | None] = {}
    tmaps: dict[str, TableMap] = {}
    confirm = _confirm_always()
    codes_out: dict[str, dict[str, str]] = defaultdict(dict)
    for t, kind in kinds.items():
        if kind == "events":
            continue
        cols = prof[t]["columns"]
        used = set(bed_parts)
        cmap = {}
        pending = []
        for c, ci in cols.items():
            if c in used:
                continue
            cs = list(cand["columns"].get(t, {}).get(c) or [])
            # 값 모양으로 거름
            if ci["shape"] == "time":
                cs = [k for k in cs if k.startswith("t:") or k.split(".")[-1] in ("eta", "requested_at")]
            else:
                cs = [k for k in cs if not k.startswith("t:")]
            if kind == "layout":
                cs = [k for k in cs if "." not in k and not k.startswith("t:") and k not in ("case", "day")]
            else:
                cs = [k for k in cs if k not in ("ward_name", "specialty", "departments", "room_type", "capacity",
                                                 "gender", "air_changes_per_hour", "cohort_key", "near_nurse_station")]
            if not cs:
                chosen[(t, c)] = None
                if ci["fill"] > 0:
                    qs.append(Question(f"col:{t}:{c}", f"{t}.{c}", "어느 온톨로지 속성인지 모른다"))
                continue
            if len(cs) > 1 and ci["shape"] == "time" and all(k.startswith("t:") for k in cs[:2]) and \
                    {cs[0], cs[1]} <= set(sum(_AMBIGUOUS_TIME.values(), ())):
                pending.append((c, cs[:2]))
                continue
            cmap[c] = cs[0]
            chosen[(t, c)] = cs[0]
        if pending and not fmt_known:  # 시각 형식을 모르면 순서 검사를 못 한다: 묻는다
            for c, opts in pending:
                chosen[(t, c)] = None
                qs.append(Question(f"col:{t}:{c}", f"{t}.{c}", "시각 형식을 몰라 사건 순서로 가릴 수 없다", None, opts))
            pending = []
        # 애매한 시각 열: 사건 순서로 가림
        if pending:
            case_col = next((c for c, k in cmap.items() if k == "case"), None)
            times: dict[str, dict[str, datetime]] = defaultdict(dict)
            for row in tables[t]:
                for c, k in cmap.items():
                    if k.startswith("t:") and row.get(c):
                        times[row[case_col]][k[2:]] = datetime.strptime(row[c], time_fmt)
            taken = set(cmap.values())
            for c, opts in pending:
                col_times = {row[case_col]: datetime.strptime(row[c], time_fmt) for row in tables[t] if row.get(c)}
                ok = [k for k in opts if k not in taken and _violations(times, k[2:], col_times) == 0]
                ev.setdefault("order_checks", {})[f"{t}.{c}"] = {k: _violations(times, k[2:], col_times) for k in opts}
                if len(ok) == 1:
                    cmap[c], chosen[(t, c)] = ok[0], ok[0]
                    taken.add(ok[0])
                else:
                    chosen[(t, c)] = None
                    qs.append(Question(f"col:{t}:{c}", f"{t}.{c}", "이름으로도 사건 순서로도 어느 사건인지 가릴 수 없다",
                                       None, opts))
        # 코드값
        for c, k in cmap.items():
            vkey = _CODE_KEYS.get(k)
            if vkey is None:
                continue
            for code in cols[c].get("codes", []):
                parts = code.split(list_sep) if k in ("patient.precautions", "departments") else [code]
                for part in parts:
                    vals = [v.split(":", 1)[1] for v in cand["values"].get(t, {}).get(c, {}).get(part, [])
                            if v.startswith(vkey + ":")]
                    if not vals:
                        for v in (cand["values"].get(t, {}).get(c, {}).get(code, [])):
                            if v.startswith(vkey + ":"):
                                vals.append(v.split(":", 1)[1])
                    if not vals and vkey == "department":
                        vals = [d for d, toks in _VALUE_LEX["department"].items() if _norm(part) in toks]
                    if not vals and part in _CONVENTION.get(vkey, {}):
                        codes_out[k][part] = _CONVENTION[vkey][part]
                        qs.append(Question(f"code:{k}:{part}", f"{k}={part}", "관례로 추정했다(확인 필요)",
                                           _CONVENTION[vkey][part]))
                        continue
                    if not vals:
                        qs.append(Question(f"code:{k}:{part}", f"{k}={part}", "코드표에 없는 값이다(병원 코드표 필요)"))
                        continue
                    if vals[0] != part:
                        codes_out[k][part] = vals[0]
                    if k in confirm:
                        qs.append(Question(f"code:{k}:{part}", f"{k}={part}", "안전 속성 코드는 늘 사람이 확인한다",
                                           vals[0]))
        # 희망 병동: 병상 표의 병동 ID로 맞춘다 (숫자만 쓰는 병원)
        for c, k in cmap.items():
            if k == "admission_requested.preferred_ward_id" and lay:
                wards = {str(r.get(bed_parts[0], "")) for r in tables[lay]}
                for code in cols[c].get("codes", []):
                    if style == "digits" and code in wards:  # 병상 ID와 같은 규칙으로 병동 ID를 만든다
                        codes_out[k][code] = f"W{code}"
        if kind == "layout":
            tmaps[t] = TableMap(kind="layout", columns=cmap)
        else:
            tmaps[t] = TableMap(kind="cases", columns=cmap)
    # 사건 기록
    for t, (cc, kc, tc) in log_cols.items():
        emap, pend = {}, []
        for code in event_codes:
            cs = cand["event_codes"].get(code) or []
            if len(cs) >= 2 and set(cs[:2]) == {"billing_done", "patient_departed"}:
                pend.append(code)
            elif cs:
                emap[code] = cs[0]
            else:
                qs.append(Question(f"evt:{code}", f"사건 코드 {code}", "어느 사건인지 모른다"))
        if not fmt_known:
            for code in pend:
                qs.append(Question(f"evt:{code}", f"사건 코드 {code}", "시각 형식을 몰라 사건 순서로 가릴 수 없다", None,
                                   ["billing_done", "patient_departed"]))
            pend = []
        times: dict[str, dict[str, datetime]] = defaultdict(dict)
        for row in tables[t]:
            k = emap.get(row[kc])
            if k:
                times[row[cc]][k] = datetime.strptime(row[tc], time_fmt)
        for code in pend:
            col_times = {row[cc]: datetime.strptime(row[tc], time_fmt) for row in tables[t] if row[kc] == code}
            taken = set(emap.values())
            ok = [k for k in ("billing_done", "patient_departed") if k not in taken
                  and _violations(times, k, col_times) == 0]
            if len(ok) == 1:
                emap[code] = ok[0]
            else:
                qs.append(Question(f"evt:{code}", f"사건 코드 {code}", "사건 순서로도 가릴 수 없다", None,
                                   ["billing_done", "patient_departed"]))
        tmaps[t] = TableMap(kind="events", event_codes=emap, event_columns=(cc, kc, tc))
        for code, k in emap.items():
            chosen[(t, f"evt:{code}")] = k
    if "departments" in codes_out or "patient.department" in codes_out:
        codes_out["departments"] = {**codes_out.get("patient.department", {}), **codes_out.get("departments", {})}
    m = Mapping(hospital=hospital, time_format=time_fmt, list_sep=list_sep, bool_values=bool_values,
                bed_parts=bed_parts, bed_part_style=style, codes=dict(codes_out), tables=tmaps)
    return MappingDraft(mapping=m, questions=qs, evidence=ev, columns=chosen)


def answer(draft: MappingDraft, answers: dict[str, object]) -> Mapping:
    """사람의 답(질문 ID → 값)을 넣어 매핑을 완성한다. 열 질문의 답은 정규 키, 코드 질문의 답은 정규값."""
    m = draft.mapping
    tables = {k: v for k, v in m.tables.items()}
    codes = {k: dict(v) for k, v in m.codes.items()}
    for q in draft.questions:
        if q.id not in answers:
            continue
        a = answers[q.id]
        if q.id.startswith("col:"):
            _, t, c = q.id.split(":", 2)
            if a:
                tables[t] = tables[t].model_copy(update={"columns": {**tables[t].columns, c: a}})
        elif q.id.startswith("code:"):
            _, k, v = q.id.split(":", 2)
            if a is not None and a != v:
                codes.setdefault(k, {})[v] = a
                if k == "patient.department":
                    codes.setdefault("departments", {})[v] = a
        elif q.id == "fmt:sep" and a:
            m = m.model_copy(update={"list_sep": a})
        elif q.id == "fmt:bool" and a:
            m = m.model_copy(update={"bool_values": tuple(a)})
        elif q.id == "fmt:time" and a:
            m = m.model_copy(update={"time_format": a})
        elif q.id.startswith("evt:"):
            code = q.id[4:]
            for t, tm in tables.items():
                if tm.kind == "events" and a:
                    tables[t] = tm.model_copy(update={"event_codes": {**tm.event_codes, code: a}})
    return m.model_copy(update={"tables": tables, "codes": codes})


_VALID = {"gender": {"M", "F"}, "precautions": {"contact", "droplet", "airborne"},
          "room_type": {"negative_pressure", "single", "multi"}, "cleaning_type": {"general", "terminal"},
          "precaution_status": {"confirmed", "suspected", "preemptive"}, "isolation_ordered_by": {"physician", "ipc"},
          "reason": {"discharge_hold", "dose_change", "add_medication"}, "discharge_type": {"ama"},
          "destination": {"icu"}, "department": set(_VALUE_LEX["department"])}


def followup_questions(m: Mapping, tables: dict[str, list[dict]]) -> list[Question]:
    """답을 반영한 매핑으로 값을 풀어 보고, 온톨로지가 허용하지 않는 값이 나오면 묻는다(결정적 검사)."""
    out, seen = [], set()
    wards = None
    for t, tm in m.tables.items():
        if tm.kind == "layout":
            wards = {f"W{r.get(m.bed_parts[0])}" if m.bed_part_style == "digits" else r.get(m.bed_parts[0])
                     for r in tables.get(t, [])}
    for t, tm in m.tables.items():
        if tm.kind == "events":
            continue
        for col, key in tm.columns.items():
            vkey = _CODE_KEYS.get(key)
            if vkey is None and key != "admission_requested.preferred_ward_id":
                continue
            for r in tables.get(t, []):
                raw = r.get(col, "")
                if raw == "":
                    continue
                parts = raw.split(m.list_sep) if key in ("patient.precautions", "departments") else [raw]
                for part in parts:
                    val = m.codes.get(key, {}).get(part, part)
                    ok = val in wards if key == "admission_requested.preferred_ward_id" and wards else \
                        val in _VALID.get(vkey, {val})
                    if not ok and (key, part) not in seen:
                        seen.add((key, part))
                        out.append(Question(f"code:{key}:{part}", f"{key}={part}", "온톨로지가 허용하지 않는 값이다(코드표 필요)"))
    return out


__all__ = ["MappingDraft", "followup_questions", "Question", "answer", "fake_propose", "profile", "propose_mapping"]
