"""합성 병원 내보내기 형식 (로드맵 07 M6). 실제 병원 형식이 아니다(CLAUDE.md 1번).

같은 합성 병원을 서로 다른 "병원 전산 내보내기"로 쓴다. 형식마다 표 구조(넓은 표 / 사건 기록), 열 이름(영문 약어 /
한글 / 코드), 코드값(성별 1·2 / 남·여), 시각 형식, ID 조합이 다르다. 함정도 넣는다: 열 이름이 "퇴원일시"·DSCH인데
실제로는 수납 완료 시각이고, 퇴실은 다른 열이다(국내 QI 자료도 기관마다 "퇴원 시각" 정의가 달랐다).

정규 기록(record): 사례 하나를 사건 시각과 속성으로 펼친 것.
  case, 환자 속성(patient.*), bed_id, day, t:<사건 이름>, <사건 이름>.<속성>
형식 명세(FormatSpec)는 정규 기록의 키 → 열 이름·코드표다. 정답 매핑(gold_mapping)은 이 명세에서 나온다.
매핑 에이전트는 명세를 보지 않고 표만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from turnaround.events import Event
from turnaround.events.replay import _patient

PATIENT_KEYS = ("patient_id", "name", "gender", "age", "department", "precautions", "organism", "precaution_status",
                "isolation_ordered_by", "immunocompromised", "open_wound")
_DERIVED = {"isolation_order", "isolation_type"}  # 주의 집합에서 다시 계산된다
TIME_EXTRAS = {"eta", "requested_at"}  # 사건 속성 중 시각 값
BOOL_KEYS = {"immunocompromised", "open_wound", "from_icu", "wants_single_room", "sporicidal"}
DISCHARGE_KEYS = ("discharge_decided", "discharge_registered", "guardian_eta_set", "order_confirmed", "rx_issued",
                  "rx_revised", "rx_dispensed", "medication_ready", "education_done", "billing_done",
                  "guardian_eta_updated", "guardian_arrived", "patient_departed", "cleaning_requested",
                  "cleaning_started", "cleaning_done", "bed_ready", "death_confirmed", "body_transferred",
                  "eol_transfer", "unplanned_transfer_out", "billing_review_done", "billing_review_reopened",
                  "discharge_notice_given",
                  "receipt_checked", "medication_handed_over", "bed_prep_done")
ADMISSION_KEYS = ("admission_requested", "admission_cancelled")


def records(events: list[Event]) -> dict[str, dict]:
    """정규 사건 목록 → 사례별 정규 기록."""
    out: dict[str, dict] = {}
    for e in events:
        case, key = e.event_id.split(":", 1)
        r = out.setdefault(case, {"case": case})
        r[f"t:{key}"] = e.occurred_at
        p = dict(e.payload)
        if "patient_id" in p:
            for k in PATIENT_KEYS:
                r[f"patient.{k}"] = p.get(k)
        if "bed_id" in p:
            r["bed_id"] = p["bed_id"]
        if "day" in p:
            r["day"] = p["day"]
        for k, v in p.items():
            if k in PATIENT_KEYS or k in _DERIVED or k in ("bed_id", "day"):
                continue
            r[f"{key}.{k}"] = datetime.fromisoformat(v) if k in TIME_EXTRAS else v
    return out


# ---------------- 형식 명세 ----------------

@dataclass
class FormatSpec:
    name: str
    style: str  # wide | log
    time_format: str
    columns: dict[str, str]  # 정규 키 → 열 이름 (넓은 표와 사례 마스터)
    codes: dict[str, dict] = field(default_factory=dict)  # 정규 키 → {정규값: 원천값}
    event_codes: dict[str, str] = field(default_factory=dict)  # 사건 기록 형식: 사건 이름 → 코드
    list_sep: str = ","
    bool_values: tuple[str, str] = ("Y", "N")
    bed_parts: tuple[str, str, str] = ("WARD", "ROOM", "BED")  # 병동·병실·병상 열 이름
    bed_part_style: str = "plain"  # plain: W71·R03·B1 / digits: 71·03·1
    layout_columns: dict[str, str] = field(default_factory=dict)
    table_names: dict[str, str] = field(default_factory=dict)
    log_columns: tuple[str, str, str] = ("case_key", "evt_cd", "evt_ts")


_GENDER = {"emr_a": {"M": "1", "F": "2"}, "emr_b": {"M": "남", "F": "여"}, "emr_c": {"M": "M", "F": "F"}}
_DEPT = {"emr_a": {"호흡기내과": "PUL", "소화기내과": "GI", "감염내과": "ID", "일반외과": "GS", "정형외과": "OS",
                   "소아청소년과": "PED", "신경과": "NR"},
         "emr_c": {"호흡기내과": "MD101", "소화기내과": "MD102", "감염내과": "MD103", "일반외과": "SG111", "정형외과": "SG112",
                   "소아청소년과": "PD121", "신경과": "MD104"}}
_PREC = {"emr_a": {"contact": "C", "droplet": "D", "airborne": "A"},
         "emr_b": {"contact": "접촉", "droplet": "비말", "airborne": "공기"},
         "emr_c": {"contact": "CONTACT", "droplet": "DROPLET", "airborne": "AIRBORNE"}}
_ROOMTYPE = {"emr_a": {"negative_pressure": "NP", "single": "SR", "multi": "MR"},
             "emr_b": {"negative_pressure": "음압", "single": "1인", "multi": "다인"},
             "emr_c": {"negative_pressure": "ISO-NP", "single": "PRIV", "multi": "WARD"}}
_CLEAN = {"emr_a": {"general": "G", "terminal": "T"}, "emr_b": {"general": "일반", "terminal": "종말"},
          "emr_c": {"general": "GEN", "terminal": "TERM"}}
_STATUS = {"emr_a": {"confirmed": "CF", "suspected": "SP", "preemptive": "PE"},
           "emr_b": {"confirmed": "확진", "suspected": "의심", "preemptive": "선제"}}
_ORDERED = {"emr_a": {"physician": "MD", "ipc": "ICN"}, "emr_b": {"physician": "주치의", "ipc": "감염관리실"}}
_RXR = {"emr_a": {"discharge_hold": "HOLD", "dose_change": "DOSE", "add_medication": "ADD"},
        "emr_b": {"discharge_hold": "퇴원보류", "dose_change": "용량변경", "add_medication": "약추가"}}
_DTYPE = {"emr_a": {"ama": "AMA"}, "emr_b": {"ama": "자의퇴원"}, "emr_c": {"ama": "AMA"}}
_DEST = {"emr_a": {"icu": "ICU"}, "emr_b": {"icu": "중환자실"}, "emr_c": {"icu": "ICU"}}
_WARDID = {"emr_b": {"W71": "71", "W72": "72"}}

_COLS = {  # 정규 키: (emr_a, emr_b, emr_c 사례 마스터)
    "case": ("CASE_NO", "사례번호", "case_key"),
    "patient.patient_id": ("PT_NO", "등록번호", "pt_key"),
    "patient.name": ("PT_NM", "환자명", "pt_name"),
    "patient.gender": ("SEX", "성별", "sex_cd"),
    "patient.age": ("AGE", "나이", "age_yr"),
    "patient.department": ("DEPT_CD", "진료과", "dept_cd"),
    "patient.precautions": ("ISO_CD", "격리구분", "iso_flags"),
    "patient.organism": ("ORG_CD", "균명", "organism_cd"),
    "patient.precaution_status": ("ISO_STAT", "격리상태", "iso_status"),
    "patient.isolation_ordered_by": ("ISO_BY", "격리지시", "iso_ordered_by"),
    "patient.immunocompromised": ("IMMUNO_YN", "면역저하", "immuno_flag"),
    "patient.open_wound": ("WOUND_YN", "개방창상", "wound_flag"),
    "day": ("DAY_SEQ", "일차", "day_idx"),
    "discharge_decided.discharge_type": ("DSCH_GB", "퇴원구분", "dsch_kind"),
    "guardian_eta_set.eta": ("GRD_ETA", "보호자도착예정", "grd_eta"),
    "guardian_eta_updated.eta": ("GRD_ETA_NEW", "보호자도착예정변경", "grd_eta_new"),
    "rx_revised.reason": ("RX_REV_RSN", "처방수정사유", "rx_rev_reason"),
    "cleaning_requested.cleaning_type": ("CLN_GB", "청소구분", "clean_kind"),
    "cleaning_requested.aerosol_wait_min": ("AEROSOL_MIN", "환기대기분", "aerosol_min"),
    "cleaning_requested.sporicidal": ("SPORI_YN", "아포소독", "sporicidal_flag"),
    "cleaning_done.cleaning_type": ("CLN_DONE_GB", "청소완료구분", "clean_done_kind"),
    "unplanned_transfer_out.destination": ("TRF_DEST", "전동처", "transfer_dest"),
    "admission_requested.requested_at": ("ADM_PLAN_DT", "입원예정일시", "adm_plan_ts"),
    "admission_requested.preferred_ward_id": ("PREF_WARD", "희망병동", "pref_ward"),
    "admission_requested.from_icu": ("ICU_YN", "중환자실전동", "from_icu_flag"),
    "admission_requested.wants_single_room": ("SR_YN", "1인실희망", "single_req_flag"),
    # 사건 시각 (넓은 표만). 함정: billing_done의 열 이름이 "퇴원" 계열
    "t:discharge_decided": ("DSCH_DEC_DT", "퇴원결정일시", None),
    "t:discharge_registered": ("DSCH_RSV_DT", "퇴원예고일시", None),
    "t:guardian_eta_set": ("GRD_INPUT_DT", "보호자예정입력일시", None),
    "t:order_confirmed": ("DSCH_ORD_DT", "퇴원오더일시", None),
    "t:rx_issued": ("RX_DT", "퇴원약처방일시", None),
    "t:rx_revised": ("RX_REV_DT", "처방수정일시", None),
    "t:rx_dispensed": ("RX_DISP_DT", "조제완료일시", None),
    "t:medication_ready": ("MED_ARR_DT", "퇴원약도착일시", None),
    "t:education_done": ("EDU_DT", "퇴원교육일시", None),
    "t:billing_done": ("DSCH_DT", "퇴원일시", None),
    "t:guardian_eta_updated": ("GRD_UPD_DT", "보호자예정변경일시", None),
    "t:guardian_arrived": ("GRD_ARR_DT", "보호자도착일시", None),
    "t:patient_departed": ("OUT_DT", "퇴실일시", None),
    "t:cleaning_requested": ("CLN_REQ_DT", "청소요청일시", None),
    "t:cleaning_started": ("CLN_ST_DT", "청소시작일시", None),
    "t:cleaning_done": ("CLN_END_DT", "청소완료일시", None),
    "t:bed_ready": ("BED_RDY_DT", "입실가능일시", None),
    "t:death_confirmed": ("EXP_DT", "사망일시", None),
    "t:body_transferred": ("MORGUE_DT", "안치실이송일시", None),
    "t:eol_transfer": ("EOL_DT", "임종실전실일시", None),
    "t:unplanned_transfer_out": ("TRF_DT", "급변전동일시", None),
    "t:billing_review_done": ("REV_END_DT", "심사완료일시", None),
    "t:billing_review_reopened": ("REV_REOPEN_DT", "재심사일시", None),
    "t:discharge_notice_given": ("PAY_NOTI_DT", "수납안내일시", None),
    "t:receipt_checked": ("RCPT_CHK_DT", "영수증확인일시", None),
    "t:medication_handed_over": ("MED_GIVE_DT", "퇴원약전달일시", None),
    "t:bed_prep_done": ("BED_PREP_DT", "침상정리완료일시", None),
    "t:admission_requested": ("ADM_RSV_DT", "입원예약일시", None),
    "t:admission_cancelled": ("ADM_CNCL_DT", "입원취소일시", None),
}
_EVENT_CODES_C = {  # emr_c 사건 기록 코드. 함정: DSCH = 수납 완료, WARD_OUT = 퇴실
    "discharge_decided": "DC_DECIDE", "discharge_registered": "DC_NOTICE", "guardian_eta_set": "GRD_ETA_IN",
    "order_confirmed": "DC_ORDER", "rx_issued": "RX_ISSUE", "rx_revised": "RX_MODIFY", "rx_dispensed": "RX_DISP",
    "medication_ready": "RX_WARD", "education_done": "DC_EDU", "billing_done": "DSCH",
    "guardian_eta_updated": "GRD_ETA_CHG", "guardian_arrived": "GRD_ARRIVE", "patient_departed": "WARD_OUT",
    "cleaning_requested": "ENV_REQ", "cleaning_started": "ENV_START", "cleaning_done": "ENV_DONE",
    "bed_ready": "BED_READY", "billing_review_done": "BILL_REVIEW_END", "billing_review_reopened": "BILL_REVIEW_REOPEN", "discharge_notice_given": "PAY_NOTICE",
    "receipt_checked": "RECEIPT_CHK", "medication_handed_over": "MED_HANDOVER", "bed_prep_done": "BED_PREP_END",
    "death_confirmed": "EXPIRE", "body_transferred": "MORGUE",
    "eol_transfer": "EOL_ROOM", "unplanned_transfer_out": "TRF_ICU", "admission_requested": "ADM_RSV",
    "admission_cancelled": "ADM_CANCEL"}
_LAYOUT = {  # 병상 단위 표: 정규 키 → (emr_a, emr_b, emr_c)
    "ward_id": ("WARD_CD", "병동", "ward"), "room_id": ("ROOM_NO", "병실", "room"), "bed_id": ("BED_NO", "병상", "bed"),
    "room_type": ("ROOM_GB", "병실구분", "room_class"), "capacity": ("BED_CNT", "병상수", "beds_in_room"),
    "gender": ("ROOM_SEX", "병실성별", "room_sex"), "air_changes_per_hour": ("ACH", "환기횟수", "ach"),
    "cohort_key": ("COHORT", "코호트", "cohort"), "near_nurse_station": ("NS_NEAR", "간호사실인접", "near_ns"),
    "ward_name": ("WARD_NM", "병동명", "ward_name"), "specialty": ("WARD_SPEC", "병동특성", "ward_spec"),
    "departments": ("WARD_DEPTS", "병동진료과", "ward_depts"),
}


def _spec(name: str) -> FormatSpec:
    i = {"emr_a": 0, "emr_b": 1, "emr_c": 2}[name]
    cols = {k: v[i] for k, v in _COLS.items() if v[i] is not None}
    codes = {"patient.gender": _GENDER[name], "patient.department": _DEPT.get(name, {}),
             "patient.precautions": _PREC[name], "patient.precaution_status": _STATUS.get(name, {}),
             "patient.isolation_ordered_by": _ORDERED.get(name, {}), "rx_revised.reason": _RXR.get(name, {}),
             "cleaning_requested.cleaning_type": _CLEAN[name], "cleaning_done.cleaning_type": _CLEAN[name],
             "discharge_decided.discharge_type": _DTYPE[name], "unplanned_transfer_out.destination": _DEST[name],
             "admission_requested.preferred_ward_id": _WARDID.get(name, {}), "room_type": _ROOMTYPE[name],
             "gender": _GENDER[name]}
    common = dict(codes={k: v for k, v in codes.items() if v},
                  layout_columns={k: v[i] for k, v in _LAYOUT.items()})
    if name == "emr_a":
        return FormatSpec(name, "wide", "%Y%m%d%H%M", cols, list_sep=",", bool_values=("Y", "N"),
                          bed_parts=("WARD_CD", "ROOM_NO", "BED_NO"),
                          table_names={"discharges": "TB_DSCH", "admissions": "TB_ADM_RSV", "layout": "TB_BED"},
                          **common)
    if name == "emr_b":
        return FormatSpec(name, "wide", "%Y-%m-%d %H:%M", cols, list_sep="+", bool_values=("예", "아니오"),
                          bed_parts=("병동", "병실", "병상"), bed_part_style="digits",
                          table_names={"discharges": "퇴원현황", "admissions": "입원예약", "layout": "병상현황"},
                          **common)
    return FormatSpec(name, "log", "%Y-%m-%dT%H:%M:%S", cols, event_codes=_EVENT_CODES_C, list_sep="|",
                      bool_values=("1", "0"), bed_parts=("ward", "room", "bed"),
                      table_names={"cases": "case_master", "events": "event_log", "layout": "bed_master"}, **common)


# 홀드아웃 형식 (2026-11-09, 매핑 어휘 사전을 고정한 뒤 만들었다): 다른 공급사 풍. 영문 전체 단어 열 이름,
# 일/월/년 시각, true/false, 진료과·병실 종류 코드가 사전에 없는 말. 함정: discharge_time = 수납 완료, left_unit_time = 퇴실
_D_COLS = {
    "case": "encounter_id", "patient.patient_id": "mrn", "patient.name": "patient_name", "patient.gender": "sex",
    "patient.age": "age_years", "patient.department": "service_line", "patient.precautions": "isolation_precautions",
    "patient.organism": "pathogen", "patient.precaution_status": "isolation_basis",
    "patient.isolation_ordered_by": "isolation_ordered_by", "patient.immunocompromised": "immunosuppressed",
    "patient.open_wound": "open_wound", "day": "los_day", "discharge_decided.discharge_type": "discharge_disposition",
    "guardian_eta_set.eta": "family_expected_arrival", "guardian_eta_updated.eta": "family_expected_arrival_revised",
    "rx_revised.reason": "rx_change_reason", "cleaning_requested.cleaning_type": "housekeeping_level",
    "cleaning_requested.aerosol_wait_min": "air_clearance_minutes", "cleaning_requested.sporicidal": "sporicidal_agent",
    "cleaning_done.cleaning_type": "housekeeping_level_done", "unplanned_transfer_out.destination": "transfer_destination",
    "admission_requested.requested_at": "scheduled_admission_time", "admission_requested.preferred_ward_id": "preferred_unit",
    "admission_requested.from_icu": "icu_stepdown", "admission_requested.wants_single_room": "private_room_request",
    "t:discharge_decided": "discharge_decision_time", "t:discharge_registered": "discharge_notice_time",
    "t:guardian_eta_set": "family_eta_entered_time", "t:order_confirmed": "discharge_order_time",
    "t:rx_issued": "discharge_rx_time", "t:rx_revised": "rx_change_time", "t:rx_dispensed": "pharmacy_dispensed_time",
    "t:medication_ready": "meds_on_unit_time", "t:education_done": "teaching_completed_time",
    "t:billing_done": "discharge_time", "t:guardian_eta_updated": "family_eta_revised_time",
    "t:guardian_arrived": "family_arrived_time", "t:patient_departed": "left_unit_time",
    "t:cleaning_requested": "clean_requested_time", "t:cleaning_started": "clean_started_time",
    "t:cleaning_done": "clean_completed_time", "t:bed_ready": "bed_available_time", "t:death_confirmed": "expired_time",
    "t:body_transferred": "morgue_time", "t:eol_transfer": "comfort_room_time",
    "t:unplanned_transfer_out": "rapid_transfer_time", "t:admission_requested": "booking_time",
    "t:admission_cancelled": "booking_cancelled_time", "t:billing_review_done": "billing_review_completed_time",
    "t:billing_review_reopened": "billing_review_reopened_time",
    "t:discharge_notice_given": "payment_notice_time", "t:receipt_checked": "receipt_verified_time",
    "t:medication_handed_over": "meds_handed_to_patient_time", "t:bed_prep_done": "bed_stripped_made_time"}
_D_LAYOUT = {"ward_id": "unit", "room_id": "room_number", "bed_id": "bed_number", "room_type": "room_category",
             "capacity": "licensed_beds", "gender": "room_sex", "air_changes_per_hour": "ach",
             "cohort_key": "cohort_group", "near_nurse_station": "near_nurse_station", "ward_name": "unit_name",
             "specialty": "unit_specialty", "departments": "unit_services"}


def _spec_d() -> FormatSpec:
    dept = {"호흡기내과": "Pulmonology", "소화기내과": "Gastroenterology", "감염내과": "Infectious Disease",
            "일반외과": "General Surgery", "정형외과": "Orthopedics", "소아청소년과": "Pediatrics", "신경과": "Neurology"}
    prec = {"contact": "Contact", "droplet": "Droplet", "airborne": "Airborne"}
    clean = {"general": "standard", "terminal": "terminal"}
    codes = {"patient.gender": {"M": "male", "F": "female"}, "gender": {"M": "male", "F": "female"},
             "patient.department": dept, "patient.precautions": prec,
             "room_type": {"negative_pressure": "AIIR", "single": "PRIVATE", "multi": "MULTI-BED"},
             "cleaning_requested.cleaning_type": clean, "cleaning_done.cleaning_type": clean,
             "discharge_decided.discharge_type": {"ama": "AMA"}, "unplanned_transfer_out.destination": {"icu": "ICU"},
             "admission_requested.preferred_ward_id": {"W71": "71", "W72": "72"}}
    return FormatSpec("emr_d", "wide", "%d/%m/%Y %H:%M", dict(_D_COLS), codes=codes, list_sep=";",
                      bool_values=("true", "false"), bed_parts=("unit", "room_number", "bed_number"),
                      bed_part_style="digits", layout_columns=dict(_D_LAYOUT),
                      table_names={"discharges": "discharges", "admissions": "bookings", "layout": "beds"})


# 두 번째 홀드아웃 (2026-11-09, 첫 홀드아웃 보강 커밋 66427ea 뒤에 만들었다. 이 형식을 보고 사전·규칙을 고치지 않고 첫 결과를 기록)
# 사건 기록 + 한글 사건 코드, 목록 구분자 "/", 유/무, 14자리 시각. 함정: 사건 코드 "퇴원" = 수납 완료, "병실퇴실" = 퇴실
_E_COLS = {
    "case": "내원키", "patient.patient_id": "환자ID", "patient.name": "환자성명", "patient.gender": "성별구분",
    "patient.age": "연령", "patient.department": "진료과목", "patient.precautions": "감염주의",
    "patient.organism": "원인균", "patient.precaution_status": "주의판정", "patient.isolation_ordered_by": "주의지시자",
    "patient.immunocompromised": "면역억제", "patient.open_wound": "창상유무", "day": "경과일",
    "discharge_decided.discharge_type": "퇴원형태", "guardian_eta_set.eta": "보호자예정시각",
    "guardian_eta_updated.eta": "보호자예정시각변경", "rx_revised.reason": "약변경사유",
    "cleaning_requested.cleaning_type": "청소등급", "cleaning_requested.aerosol_wait_min": "환기대기시간",
    "cleaning_requested.sporicidal": "아포제사용", "cleaning_done.cleaning_type": "완료청소등급",
    "unplanned_transfer_out.destination": "전동부서", "admission_requested.requested_at": "입원예정시각",
    "admission_requested.preferred_ward_id": "요청병동", "admission_requested.from_icu": "중환자실경유",
    "admission_requested.wants_single_room": "1인실요청"}
_E_EVENTS = {
    "discharge_decided": "퇴원결정", "discharge_registered": "퇴원예고", "guardian_eta_set": "보호자예정입력",
    "order_confirmed": "퇴원오더", "rx_issued": "퇴원약발행", "rx_revised": "퇴원약변경", "rx_dispensed": "조제완료",
    "medication_ready": "퇴원약수령", "education_done": "퇴원교육", "billing_done": "퇴원",
    "guardian_eta_updated": "보호자예정변경", "guardian_arrived": "보호자도착", "patient_departed": "병실퇴실",
    "cleaning_requested": "청소요청", "cleaning_started": "청소시작", "cleaning_done": "청소종료",
    "bed_ready": "침상준비완료", "death_confirmed": "사망", "body_transferred": "안치", "eol_transfer": "임종실",
    "unplanned_transfer_out": "중환자실전동", "admission_requested": "입원예약", "admission_cancelled": "예약취소",
    "billing_review_done": "심사완료", "billing_review_reopened": "재심사", "discharge_notice_given": "수납안내", "receipt_checked": "영수증확인",
    "medication_handed_over": "퇴원약전달", "bed_prep_done": "침상정리완료"}
_E_LAYOUT = {"ward_id": "병동코드", "room_id": "호실", "bed_id": "침상번호", "room_type": "병실형태",
             "capacity": "정원", "gender": "남녀구분", "air_changes_per_hour": "시간당환기", "cohort_key": "코호트균",
             "near_nurse_station": "스테이션인접", "ward_name": "병동이름", "specialty": "병동성격",
             "departments": "담당진료과"}


def _spec_e() -> FormatSpec:
    prec = {"contact": "접촉", "droplet": "비말", "airborne": "공기"}
    clean = {"general": "일반", "terminal": "종말"}
    codes = {"patient.gender": {"M": "남", "F": "여"}, "gender": {"M": "남", "F": "여"},
             "patient.precautions": prec, "room_type": {"negative_pressure": "음압격리", "single": "1인", "multi": "다인"},
             "cleaning_requested.cleaning_type": clean, "cleaning_done.cleaning_type": clean,
             "patient.precaution_status": {"confirmed": "확진", "suspected": "의심", "preemptive": "선제"},
             "patient.isolation_ordered_by": {"physician": "주치의", "ipc": "감염관리실"},
             "rx_revised.reason": {"discharge_hold": "퇴원보류", "dose_change": "용량변경", "add_medication": "약추가"},
             "discharge_decided.discharge_type": {"ama": "자의퇴원"}, "unplanned_transfer_out.destination": {"icu": "중환자실"}}
    return FormatSpec("emr_e", "log", "%Y%m%d%H%M%S", dict(_E_COLS), codes=codes, event_codes=dict(_E_EVENTS),
                      list_sep="/", bool_values=("유", "무"), bed_parts=("병동코드", "호실", "침상번호"),
                      layout_columns=dict(_E_LAYOUT), log_columns=("내원키", "이벤트코드", "발생시각"),
                      table_names={"cases": "환자마스터", "events": "이벤트이력", "layout": "병상마스터"})


# 세 번째 새 형식 (2026-11-10, 3단계 끝난 기준 "새 형식 병원 한 번에 운영까지"). emr_d·e는 관문·학습 곡선에 써서 더는
# 홀드아웃이 아니다. emr_f는 사전·관문에 넣지 않는다. 이 형식을 보고 규칙을 고치지 않고 첫 결과를 기록한다.
# 넓은 표, 약어 대문자 열 이름(PX_ 접두), 점 구분 날짜, T/F, 목록 "+", 숫자 병동, 낯선 코드값
def _f_name(col: str) -> str:
    return "PX_" + "".join(w[:4] for w in col.split("_")).upper()


_F_COLS = {k: _f_name(v) for k, v in _D_COLS.items()}
_F_LAYOUT = {"ward_id": "PX_UNIT", "room_id": "PX_RM", "bed_id": "PX_BD", "room_type": "PX_RMCLS",
             "capacity": "PX_CAPA", "gender": "PX_RMGND", "air_changes_per_hour": "PX_VENT", "cohort_key": "PX_COHT",
             "near_nurse_station": "PX_NSADJ", "ward_name": "PX_UNITNM", "specialty": "PX_UNITTYP",
             "departments": "PX_UNITSVC"}


def _spec_f() -> FormatSpec:
    dept = {"호흡기내과": "SV10", "소화기내과": "SV12", "감염내과": "SV13", "일반외과": "SV31", "정형외과": "SV32",
            "소아청소년과": "SV41", "신경과": "SV14"}
    prec = {"contact": "CT", "droplet": "DR", "airborne": "AB"}
    clean = {"general": "ROUT", "terminal": "DEEP"}
    codes = {"patient.gender": {"M": "1", "F": "2"}, "gender": {"M": "1", "F": "2"},
             "patient.department": dept, "patient.precautions": prec,
             "room_type": {"negative_pressure": "IS", "single": "PV", "multi": "GN"},
             "cleaning_requested.cleaning_type": clean, "cleaning_done.cleaning_type": clean,
             "patient.precaution_status": {"confirmed": "C1", "suspected": "C2", "preemptive": "C3"},
             "patient.isolation_ordered_by": {"physician": "DR", "ipc": "IC"},
             "rx_revised.reason": {"discharge_hold": "R1", "dose_change": "R2", "add_medication": "R3"},
             "discharge_decided.discharge_type": {"ama": "AMA"}, "unplanned_transfer_out.destination": {"icu": "ICU"},
             "admission_requested.preferred_ward_id": {"W71": "71", "W72": "72"}}
    return FormatSpec("emr_f", "wide", "%Y.%m.%d %H:%M", dict(_F_COLS), codes=codes, list_sep="+",
                      bool_values=("T", "F"), bed_parts=("PX_UNIT", "PX_RM", "PX_BD"), bed_part_style="digits",
                      layout_columns=dict(_F_LAYOUT),
                      table_names={"discharges": "PX_DSCH", "admissions": "PX_ADMRSV", "layout": "PX_BEDMST"})


FORMATS = ("emr_a", "emr_b", "emr_c")
HOLDOUT_FORMATS = ("emr_d", "emr_e")
NEW_FORMATS = ("emr_f",)  # 사전·관문에 쓰지 않는 새 형식


def spec(name: str) -> FormatSpec:
    return {"emr_d": _spec_d, "emr_e": _spec_e, "emr_f": _spec_f}.get(name, lambda: _spec(name))()


# ---------------- 쓰기 ----------------

def _bed_parts(bed_id: str, style: str) -> tuple[str, str, str]:
    w, r, b = bed_id.split("-")
    if style == "digits":
        return w[1:], r[1:], b[1:]
    return w, r, b


def _enc(sp: FormatSpec, key: str, v):
    if v is None or v == "" or v == [] or v is False and key not in BOOL_KEYS and not key.endswith(tuple(BOOL_KEYS)):
        return ""
    base = key.split(".")[-1]
    if isinstance(v, datetime):
        return v.strftime(sp.time_format)
    if base in BOOL_KEYS:
        return sp.bool_values[0] if v else sp.bool_values[1]
    code = sp.codes.get(key, {})
    if isinstance(v, (list, tuple)):
        return sp.list_sep.join(code.get(x, x) for x in v)
    return code.get(v, v) if not isinstance(v, (int, float)) else str(v)


def export(h, events: list[Event], name: str) -> dict[str, list[dict]]:
    """합성 병원 하나를 형식 name의 표 묶음으로 쓴다."""
    sp = spec(name)
    recs = records(events)
    out: dict[str, list[dict]] = {}
    lay = []
    wards = {w.id: w for w in h.wards}
    rooms = {r.id: r for r in h.rooms}
    lc = sp.layout_columns
    for b in h.beds:
        r, w = rooms[b.room_id], wards[b.ward_id]
        wp, rp, bp = _bed_parts(b.id, sp.bed_part_style)
        lay.append({lc["ward_id"]: wp, lc["room_id"]: rp, lc["bed_id"]: bp,
                    lc["room_type"]: sp.codes["room_type"][r.room_type.value], lc["capacity"]: str(r.capacity),
                    lc["gender"]: sp.codes["gender"].get(r.gender.value, "") if r.gender else "",
                    lc["air_changes_per_hour"]: str(r.air_changes_per_hour), lc["cohort_key"]: r.cohort_key or "",
                    lc["near_nurse_station"]: sp.bool_values[0] if r.near_nurse_station else sp.bool_values[1],
                    lc["ward_name"]: w.name, lc["specialty"]: w.specialty,
                    lc["departments"]: sp.list_sep.join(sp.codes.get("patient.department", {}).get(d, d) for d in w.departments)})
    out[sp.table_names["layout"]] = lay

    def row(rec: dict, keys) -> dict:
        res = {}
        for k in keys:
            if k == "bed_id":
                parts = _bed_parts(rec["bed_id"], sp.bed_part_style) if rec.get("bed_id") else ("", "", "")
                res.update(dict(zip(sp.bed_parts, parts)))
            elif k in sp.columns:
                res[sp.columns[k]] = _enc(sp, k, rec.get(k))
        return res

    attr_keys = ["case", "bed_id"] + [k for k in sp.columns if not k.startswith("t:") and k != "case"]
    if sp.style == "wide":
        for table, kinds in (("discharges", DISCHARGE_KEYS), ("admissions", ADMISSION_KEYS)):
            keys = attr_keys + [f"t:{k}" for k in kinds]
            out[sp.table_names[table]] = [row(rec, keys) for rec in recs.values()
                                          if any(f"t:{k}" in rec for k in kinds)]
    else:
        out[sp.table_names["cases"]] = [row(rec, attr_keys) for rec in recs.values()]
        log = []
        for rec in recs.values():
            for k, v in rec.items():
                if k.startswith("t:"):
                    cc, kc, tc = sp.log_columns
                    log.append({cc: rec["case"], kc: sp.event_codes[k[2:]], tc: v.strftime(sp.time_format)})
        cc, kc, tc = sp.log_columns
        out[sp.table_names["events"]] = sorted(log, key=lambda x: (x[tc], x[cc], x[kc]))
    return out


def gold_mapping(name: str):
    """형식 명세에서 나오는 정답 매핑 (매핑 에이전트 평가의 기준)."""
    from turnaround.ontology.mapping import Mapping, TableMap
    sp = spec(name)
    inv = {k: {src: canon for canon, src in v.items()} for k, v in sp.codes.items()}
    inv["departments"] = inv.get("patient.department", {})
    case_cols = {col: key for key, col in sp.columns.items() if not key.startswith("t:")}
    lay_cols = {col: key for key, col in sp.layout_columns.items() if key not in ("ward_id", "room_id", "bed_id")}
    tables = {sp.table_names["layout"]: TableMap(kind="layout", columns=lay_cols)}
    if sp.style == "wide":
        for t in ("discharges", "admissions"):
            tables[sp.table_names[t]] = TableMap(kind="cases", columns={**case_cols, **{
                col: key for key, col in sp.columns.items() if key.startswith("t:")}})
    else:
        tables[sp.table_names["cases"]] = TableMap(kind="cases", columns=case_cols)
        tables[sp.table_names["events"]] = TableMap(kind="events", event_columns=sp.log_columns,
                                                    event_codes={c: k for k, c in sp.event_codes.items()})
    return Mapping(hospital=name, time_format=sp.time_format, list_sep=sp.list_sep, bool_values=sp.bool_values,
                   bed_parts=sp.bed_parts, bed_part_style=sp.bed_part_style, codes=inv, tables=tables)


__all__ = ["FORMATS", "HOLDOUT_FORMATS", "NEW_FORMATS", "FormatSpec", "export", "gold_mapping", "records", "spec"]
