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
