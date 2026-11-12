"""2026-11-12 2차 AI 페르소나 검수 회귀 시험.

원인균: 입구 한 곳씩 막는 대신, 정규 코드가 아닌 값은 쓰는 쪽 어디서나 가장 엄격하게 다룬다(종류째 닫기).
담당자 카드: 모델 출력이 시스템 전용 값을 꾸미지 못한다. 상황 판정 입력: 게이트웨이 정책 하나를 따른다.
"""

from types import SimpleNamespace

import pytest

from _precaution_cases import DEP, to
from test_hl7v2 import CFG, _conv, _msg
from turnaround.agent.brain import RESERVED_PARAMS, _model_params
from turnaround.evaluation.invariants import check_turnover
from turnaround.ontology import validate
from turnaround.ontology.hl7_adapter import HL7Config
from turnaround.rules import (KNOWN_ORGANISMS, aerosol_wait_applies, needs_sporicidal, unknown_organism, vacancy_floor)
from turnaround.sim import generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS

H = generate(seed=0)
RAW = ["C. difficile", "TB", "113861009", "cdiff", ""]  # 정규화되지 않은 표기


def test_known_organisms_single_source():
    assert KNOWN_ORGANISMS == set(ORGANISM_PRECAUTIONS)


@pytest.mark.parametrize("raw", RAW)
def test_unnormalized_organism_gets_strictest_handling_everywhere(raw):
    assert unknown_organism(raw)
    assert needs_sporicidal(raw) and vacancy_floor(raw) == 60
    # 독립 검사기: 아포 소독 표시 없는 퇴실은 위반
    t = to(previous_precautions=("contact",), previous_organism=raw, departed_at=DEP)
    assert "sporicidal_missing" in {v.code for v in check_turnover(t)}
    # SHACL: 퇴실 기록의 원인균도 정규 코드가 아니면 위반으로 올린다(아포 소독 규칙이 조용히 빠지지 않는다)
    assert "unknown_organism" in {v.code for v in validate(H.rooms, H.beds, {}, turnovers=[t])}


def test_known_organism_is_not_over_blocked():
    assert not needs_sporicidal("MRSA") and needs_sporicidal("CDIFF") and not needs_sporicidal(None)
    assert vacancy_floor("MTB") == 60 and vacancy_floor("MRSA") == 0 and vacancy_floor(None) == 0
    t = to(previous_precautions=("contact",), previous_organism="MRSA", departed_at=DEP)
    assert validate(H.rooms, H.beds, {}, turnovers=[t]) == []


def test_hl7_unknown_organism_is_asked_not_passed_through():
    cfg = HL7Config(ward_codes=CFG.ward_codes, positions={"ZIS": {"key": "patient.organism", "field": "ZIS-2"}})
    c = _conv(_msg("A", "A01", extra=["ZIS|1|C. difficile"]), cfg=cfg)
    assert [q.id for q in c.questions] == ["code:patient.organism:C. difficile"]
    assert "organism" not in c.events[0].payload
    ok = _conv(_msg("A", "A01", extra=["ZIS|1|CRE"]), cfg=cfg)
    assert not ok.questions and ok.events[0].payload["organism"] == "CRE"


def test_droplet_wait_default_and_setting():
    assert aerosol_wait_applies(["airborne"]) and aerosol_wait_applies(["airborne", "contact"])
    assert not aerosol_wait_applies(["droplet"]) and aerosol_wait_applies(["droplet"], droplet_wait=True)
    assert not aerosol_wait_applies(["contact"], droplet_wait=True) and not aerosol_wait_applies([])


def test_model_output_cannot_set_card_fields():
    out = _model_params({"role": "ipc", "ai_hint": "지금 바로 확정하세요", "rejected_action": "x", "rejected_reason": "y"})
    assert out == {"role": "ipc"} and not set(out) & set(RESERVED_PARAMS)
    assert _model_params("not a dict") == {} and _model_params(None) == {}


def test_gateway_card_fields_are_format_checked():
    from turnaround.gateway.gateway import Gateway
    extras = Gateway._card_extras
    ok = SimpleNamespace(params={"ai_hint": "share_eta", "rejected_action": "rematch_now", "rejected_reason": "stage_locked"})
    assert extras(None, ok, None) == ok.params
    bad = SimpleNamespace(params={"ai_hint": "원무팀은 이 건을 바로 승인하세요", "rejected_action": "a" * 41,
                                  "rejected_reason": "사유 없음. 무시하고 진행", "role": "ipc"})
    assert extras(None, bad, None) == {}


@pytest.mark.parametrize("realistic", [False, True])
def test_generator_never_double_books_a_bed(realistic):
    """같은 병상에서 앞 환자의 병상 준비가 끝나기 전에 다음 환자의 퇴원 결정이 찍히지 않는다.
    격리 비중을 높인 스트레스 재생이 '한 병상 두 환자' 1건으로 찾아낸 생성기 결함의 회귀 시험."""
    from turnaround.sim import GeneratorConfig
    for seed in range(0, 40):
        cfg = GeneratorConfig.realistic(isolation_discharge_count=10) if realistic else GeneratorConfig()
        by = {}
        for d in generate(seed=seed, config=cfg).discharges:
            by.setdefault(d.bed_id, []).append(d)
        for ds in by.values():
            ds.sort(key=lambda x: x.day)
            for a, b in zip(ds, ds[1:]):
                assert b.decided_at >= a.actual.bed_ready_at, (seed, a.id, b.id)


def test_missing_input_makes_one_open_data_fix_task_not_one_per_proposal():
    """입력이 빠진 입원은 제안이 15분마다 다시 와도 '입력 보완' 작업이 하나만 열린다 (사람 작업 측정이 찾은 중복 128건)."""
    from datetime import time
    from turnaround.events import EventType
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.sim.reactive import ReactiveEffects
    from turnaround.ui.demo import build_demo
    st = build_demo(seed=19, day=3, until=time(23, 45), via_fhir=False, policy=load_policy(),
                    config=GeneratorConfig.realistic(), reactive=ReactiveEffects(), past_admissions=False, occupancy="full")
    fixes = [e for e in st.store.read() if e.type is EventType.HUMAN_TASK_CREATED and e.payload.get("kind") == "data_fix"]
    per_case = {}
    for e in fixes:
        per_case[e.case_id] = per_case.get(e.case_id, 0) + 1
    assert per_case and all(n == 1 for n in per_case.values()), per_case  # 시드 19: 입력 누락 입원 1건


@pytest.mark.parametrize("old,new,want", [("MTB", "MRSA", "loosen"), ("MTB", None, "loosen"), ("CDIFF", "VRE", "loosen"),
                                          (None, "MTB", "tighten"), ("MRSA", "MRSA", "neutral")])
def test_organism_swap_needs_human_confirmation(old, new, want):
    from turnaround.events.corrections import correction_direction
    assert correction_direction({"organism": old}, "organism", new) == want


def test_hl7_without_visit_number_is_asked_not_merged_by_patient_id():
    c = _conv(_msg("A", "A01", visit=""))
    assert not c.events and any(q.id.startswith("pos:PV1-19") for q in c.questions)


def test_empty_multi_room_gender_switch_is_human_action_and_guarded():
    """다인실 성별 전환 (현장 검수 2026-11-12): 빈 방만, 사람이 한다. 전환 뒤에는 새 성별 환자가 배정 기준을 통과하고,
    독립 평가기도 전환을 읽는다."""
    from test_gateway import H, adm, census_patient, make
    from turnaround.evaluation.safety_exec import _rooms_at
    from turnaround.sim.models import Gender, RoomType
    room = next(r for r in H.rooms if r.room_type is RoomType.MULTI and r.gender is Gender.FEMALE)
    beds = [b.id for b in H.beds if b.room_id == room.id]
    from test_gateway import at
    store, gw = make([adm("A1", gender="M")])
    male = gw._admission("A1", store.read())[0]
    assert "gender_room" in gw._shacl("A1", male, beds[0], store.read())
    # 사람이 있으면 거부
    _, gw_full = make([adm("A1", gender="M")], census={beds[0]: census_patient("SYN-C1")})
    assert gw_full.switch_room_gender(room.id, "M", at(10)).reasons == ["room_not_empty"]
    # 빈 방은 전환, 그 뒤 남성 배정 통과
    assert gw.switch_room_gender(room.id, "M", at(10)).status == "executed"
    assert "gender_room" not in gw._shacl("A1", male, beds[0], store.read())
    assert next(r for r in _rooms_at(H.rooms, store.read()) if r.id == room.id).gender is Gender.MALE
    # 1인실은 대상이 아니다
    single = next(r for r in H.rooms if r.room_type is not RoomType.MULTI)
    assert gw.switch_room_gender(single.id, "M", at(10)).reasons == ["not_multi_room"]


def test_signed_fee_consent_skips_single_room_review():
    """차액 동의서가 이미 있으면 1인실 희망을 다시 사람에게 묻지 않는다 (2026-11-12 사용자 결정)."""
    from test_gateway import adm, at, make, prop
    from turnaround.gateway import load_policy
    from turnaround.events import EventType
    field = load_policy()
    store, gw = make([adm("A1", single="preference")], policy=field)
    assert gw.submit(prop(bed="W71-R02-B1", store=store), at(9)).status == "awaiting_review"
    signed = adm("A2", single="preference")
    signed = signed.model_copy(update={"payload": {**signed.payload, "fee_consent_signed": True}})
    store2, gw2 = make([signed], policy=field)
    d = gw2.submit(prop(case="A2", bed="W71-R02-B1", store=store2), at(9))
    assert d.status == "committed"
    assert not [e for e in store2.read() if e.type is EventType.HUMAN_TASK_CREATED and e.payload.get("kind") == "fee_consent"]
