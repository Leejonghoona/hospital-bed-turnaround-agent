"""개인정보 검토 재현 결함 (2026-11-09 최고점 루프 1회차, `docs/DECISIONS.md`). 모든 값은 합성이다."""

import json

import pytest


# ---------------- 1. 외부 전송 프로필 허용 목록 ----------------

def _repeat_patient_rows():
    return [{"pt_nm": n, "dx": d, "memo": m, "dsch_dt": "202611090930"} for n, d, m in
            [("김민수", "F20.9 조현병", "보호자 010 1234"), ("김민수", "F20.9 조현병", "보호자 010 1234"),
             ("이영희", "B20 HIV", "VIP"), ("이영희", "B20 HIV", "VIP")]]


def test_repeated_patient_values_do_not_reach_llm_profile():
    from turnaround.agent.mapping_agent import llm_profile, profile
    p = llm_profile(profile({"t": _repeat_patient_rows()}))
    blob = json.dumps(p, ensure_ascii=False)
    for raw in ("김민수", "이영희", "F20.9", "B20", "HIV", "조현병", "010", "1234", "VIP"):
        assert raw not in blob, raw


def test_value_shapes_removed_even_under_neutral_column_name():
    from turnaround.agent.mapping_agent import llm_profile, profile
    rows = [{"c1": v} for v in ("김민수", "김민수", "이영희", "이영희")]
    rows2 = [{"c2": v} for v in ("J18.9", "J18.9", "A41", "A41")]
    rows3 = [{"c3": v} for v in ("AB123", "AB123", "CD456", "CD456")]
    rows4 = [{"c4": v} for v in ("늦게 옴", "늦게 옴", "일찍 옴", "일찍 옴")]
    for t in (rows, rows2, rows3, rows4):
        col = next(iter(t[0]))
        assert "codes" not in llm_profile(profile({"t": t}))["t"]["columns"][col]


def test_non_code_like_column_sends_no_values():
    from turnaround.agent.mapping_agent import llm_profile, profile
    # 고유값이 행 수의 절반보다 많으면 코드 열이 아니다
    rows = [{"x": v} for v in ("A", "A", "B", "B", "C", "C", "D", "E")]
    assert "codes" not in llm_profile(profile({"t": rows}))["t"]["columns"]["x"]


def test_code_columns_still_go_including_known_korean_code_words():
    from turnaround.agent.mapping_agent import llm_profile, profile
    rows = [{"SEX": s, "ISO_CD": i} for s, i in zip("MFMFMF", ("접촉", "비말", "접촉", "비말", "공기", "공기"))]
    p = llm_profile(profile({"t": rows}))
    assert p["t"]["columns"]["SEX"]["codes"] == ["F", "M"]
    assert p["t"]["columns"]["ISO_CD"]["codes"] == ["공기", "비말", "접촉"]


def test_ward_name_column_is_not_a_person_name_column():
    from turnaround.agent.mapping_agent import llm_profile, profile
    rows = [{"WARD_NM": w, "WARD_CD": c, "YN": b} for w, c, b in
            (("71병동", "W71", "예"), ("71병동", "W71", "아니오"), ("72병동", "W72", "예"), ("72병동", "W72", "아니오"))]
    cols = llm_profile(profile({"t": rows}))["t"]["columns"]
    assert cols["WARD_NM"]["codes"] == ["71병동", "72병동"]
    assert cols["WARD_CD"]["codes"] == ["W71", "W72"]
    assert cols["YN"]["codes"] == ["아니오", "예"]


# ---------------- 2. 자유 서술 추출도 외부 전송 관문을 지난다 ----------------

class _FakeExternalLLM:
    """외부 모델처럼 표시한 가짜(실제 호출 없음). 받은 입력을 기록한다."""
    name = "openai:fake-external"
    external = True

    def __init__(self):
        self.sent = []

    def complete_json(self, task, system, user):
        self.sent.append(user)
        return {"guardian_eta": "14:00"}


def _note_store(text):
    from test_gateway import at, ev
    from turnaround.events import EventStore, EventType
    store = EventStore(":memory:")
    store.append(ev("A1", "memo", EventType.FREE_TEXT_NOTE, at(8), "human", text=text))
    return store


def test_extract_refuses_hospital_data_to_external_llm_under_deny():
    from test_gateway import at
    from turnaround.agent import Extractor
    llm = _FakeExternalLLM()
    store = _note_store("보호자 김민수님 010-1234-5678 2시 도착")
    with pytest.raises(PermissionError):
        Extractor(store, llm).step(at(8, 1))  # 기본은 병원 데이터
    assert not llm.sent


def test_extract_sends_synthetic_data_with_names_and_phones_masked():
    from test_gateway import at
    from turnaround.agent import Extractor
    from turnaround.events import EventType
    llm = _FakeExternalLLM()
    store = _note_store("보호자 김민수님 010-1234-5678 2시 도착, 등록번호 00012345")
    Extractor(store, llm, data_class="synthetic").step(at(8, 1))
    assert len(llm.sent) == 1
    for raw in ("김민수", "010-1234-5678", "1234", "00012345"):
        assert raw not in llm.sent[0]
    assert "2시 도착" in json.loads(llm.sent[0])["text"]
    out = [e for e in store.read() if e.type is EventType.FIELDS_EXTRACTED][0]
    assert out.payload["fields"] == {"guardian_eta": "14:00"}


def test_guard_lives_in_llm_wrapper_and_ignores_internal_models():
    from turnaround.agent.llm import FakeLLM, guard_external, redact_for_external
    guard_external(FakeLLM(), "hospital")  # 외부가 아니면 통과
    with pytest.raises(PermissionError):
        guard_external(_FakeExternalLLM(), "hospital")
    guard_external(_FakeExternalLLM(), "synthetic")
    assert redact_for_external("이영희 환자, 보호자 010 9876 5432") == "○○○ 환자, 보호자 [전화]"
    assert redact_for_external("보호자가 3시 도착") == "보호자가 3시 도착"


# ---------------- 3. 사건 기록 표는 구조로 가린다 ----------------

def _log_rows(extra: int):
    rows = []
    for i, case in enumerate(("C1", "C2", "C3")):
        for j, code in enumerate(("DSCH_DEC", "BILL", "OUT")):
            r = {"enc": case, "kind": code, "ts": f"2026-11-0{i + 1} {10 + j}:00", "user": f"u{i}"}
            if extra >= 2:
                r["term"] = f"T{i % 2}"
            if extra >= 3:
                r["dept"] = "IM"
            rows.append(r)
    return rows


@pytest.mark.parametrize("extra", [2, 3])
def test_event_log_with_author_and_terminal_columns_is_events(extra):
    from turnaround.agent.mapping_agent import propose_mapping
    rows = _log_rows(extra)
    assert len(rows[0]) in (5, 6)
    d = propose_mapping({"log": rows})
    tm = d.mapping.tables["log"]
    assert tm.kind == "events"
    assert tuple(tm.event_columns) == ("enc", "kind", "ts")


def test_reviewer_two_row_five_column_log_is_events():
    from turnaround.agent.mapping_agent import propose_mapping
    ev = [{"case": "C1", "code": "A", "ts": "2026-11-09 10:00", "user": "u1", "term": "x"},
          {"case": "C1", "code": "B", "ts": "2026-11-09 11:00", "user": "u2", "term": "y"}]
    tm = propose_mapping({"log": ev}).mapping.tables["log"]
    assert tm.kind == "events" and tuple(tm.event_columns) == ("case", "code", "ts")


def test_one_row_per_case_table_with_one_time_column_stays_cases():
    from turnaround.agent.mapping_agent import propose_mapping
    rows = [{"pt_no": f"P{i:02d}", "sex": "MF"[i % 2], "dept": ("GI", "OS", "PUL")[i % 3],
             "plan": f"2026-11-09 {8 + i}:00"} for i in range(9)]
    assert propose_mapping({"adm": rows}).mapping.tables["adm"].kind == "cases"


# ---------------- 4. 진료과 허용값은 병원 코드표에서 ----------------

def _dept_mapping():
    from turnaround.ontology.mapping import Mapping, TableMap
    return Mapping(hospital="H", time_format="%Y-%m-%d %H:%M", list_sep=",", bool_values=("Y", "N"),
                   bed_parts=("", "", ""), bed_part_style="plain",
                   codes={"patient.department": {"URO": "비뇨의학과", "PUL": "호흡기내과"}},
                   tables={"c": TableMap(kind="cases", columns={"dept": "patient.department"})})


def test_hospital_code_master_departments_are_allowed():
    from turnaround.agent.mapping_agent import allowed_values, followup_questions
    m = _dept_mapping()
    tables = {"c": [{"dept": "URO"}, {"dept": "PUL"}]}
    assert [q.id for q in followup_questions(m, tables)] == ["code:patient.department:URO"]  # 기본 7개에 없음
    allowed = allowed_values({"patient.department": {"URO": "비뇨의학과", "PUL": "호흡기내과"}})
    assert allowed["department"] == {"비뇨의학과", "호흡기내과"}
    assert followup_questions(m, tables, allowed=allowed) == []
    # 코드표가 있으면 그 목록만: 코드표에 없는 기본 진료과도 묻는다
    only = allowed_values({"patient.department": {"URO": "비뇨의학과"}})
    assert [q.id for q in followup_questions(m, tables, allowed=only)] == ["code:patient.department:PUL"]
    assert allowed_values({})["department"] == allowed_values(None)["department"]
    assert len(allowed_values(None)["department"]) == 7


def test_onboarding_session_uses_imported_code_master_as_allowed_departments():
    from test_stage3_onboarding import _canonical
    from turnaround.agent.onboarding_flow import OnboardingSession
    from turnaround.sim.exports import export
    h, ev = _canonical(1)
    s = OnboardingSession(export(h, ev, "emr_a"), hospital="H")
    assert len(s.allowed_values()["department"]) == 7
    s.import_code_master({"patient.department": {"URO": "비뇨의학과"}})
    assert s.allowed_values()["department"] == {"비뇨의학과"}


# ---------------- 5. 사전 출처 지문은 솔트를 쓴 HMAC ----------------

def test_source_id_is_salted_hmac(monkeypatch):
    import hashlib
    import hmac

    from turnaround.agent.lexicon import DEV_SALT, source_id
    monkeypatch.delenv("TURNAROUND_LEXICON_SALT", raising=False)
    plain = "src:" + hashlib.sha256("서울가상병원".encode()).hexdigest()[:10]
    dev = source_id("서울가상병원")
    assert dev != plain  # 솔트 없는 해시로 사전 대입이 되지 않는다
    assert dev == "src:" + hmac.new(DEV_SALT.encode(), "서울가상병원".encode(), hashlib.sha256).hexdigest()[:10]
    assert source_id("서울가상병원") == dev  # 결정적
    monkeypatch.setenv("TURNAROUND_LEXICON_SALT", "합성-시험-솔트")
    salted = source_id("서울가상병원")
    assert salted.startswith("src:") and salted not in (dev, plain)
    assert salted == "src:" + hmac.new("합성-시험-솔트".encode(), "서울가상병원".encode(), hashlib.sha256).hexdigest()[:10]


# ---------------- 6. 성별 코드: 3·4 관례, other·unknown ----------------

def test_gender_codes_3_4_are_convention_and_other_unknown_are_allowed():
    from turnaround.agent.mapping_agent import followup_questions, propose_mapping
    from turnaround.ontology.mapping import _dec
    rows = [{"case": f"C{i}", "sex": s, "dsch": f"2026-11-09 1{i % 10}:00"} for i, s in enumerate("12341234UO")]
    d = propose_mapping({"c": rows})
    q = {x.id: x for x in d.questions}
    assert q["code:patient.gender:3"].proposal == "M" and q["code:patient.gender:4"].proposal == "F"
    assert "관례" in q["code:patient.gender:3"].why
    m = d.mapping
    assert m.codes["patient.gender"]["U"] == "unknown" and m.codes["patient.gender"]["O"] == "other"
    assert not [x for x in followup_questions(m, {"c": rows}) if x.id.startswith("code:patient.gender")]
    # 환자 성별을 모름·기타로 받으면 비워 둔다(성별 입력 누락으로 사람 확인)
    assert _dec(m, "patient.gender", "U") is None and _dec(m, "patient.gender", "O") is None
    assert _dec(m, "patient.gender", "3") == "M"


def test_room_gender_does_not_accept_other_or_unknown():
    from turnaround.agent.mapping_agent import followup_questions
    from turnaround.ontology.mapping import Mapping, TableMap
    m = Mapping(hospital="H", time_format="%Y-%m-%d %H:%M", list_sep=",", bool_values=("Y", "N"),
                bed_parts=("", "", ""), bed_part_style="plain", codes={"gender": {"U": "unknown"}},
                tables={"b": TableMap(kind="layout", columns={"room_sex": "gender"})})
    assert [q.id for q in followup_questions(m, {"b": [{"room_sex": "U"}]})] == ["code:gender:U"]
