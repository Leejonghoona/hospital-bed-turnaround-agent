"""2026-11-12 외부 검수(10인) 안전 지적 회귀 시험: A1 원인균 코드, A2 병실 정보 없음."""

from turnaround.agent.mapping_agent import _VALID, propose_mapping
from turnaround.ontology import validate
from turnaround.saga.isolation import _bed_fits
from turnaround.sim import Gender, IsolationType, Patient, generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS


def _rows(orgs):
    return {"pt": [{"PT_NO": f"{i:08d}", "AGE": str(40 + i), "ORG": o} for i, o in enumerate(orgs)]}


def test_organism_vocab_matches_simulator_codes():
    assert _VALID["organism"] == set(ORGANISM_PRECAUTIONS)


def test_organism_codes_always_go_to_a_human_and_unknown_is_asked():
    d = propose_mapping(_rows(["TB", "113861009", "MRSA", "XYZ-77"]), data_class="synthetic")
    qs = {q.id: q for q in d.questions}
    # 알아본 표기도 안전 속성이라 늘 확인을 받는다(제안값은 정규 코드)
    assert qs["code:patient.organism:TB"].proposal == "MTB"
    assert qs["code:patient.organism:113861009"].proposal == "MTB"
    # 모르는 표기는 추측하지 않는다
    assert "code:patient.organism:XYZ-77" in qs and qs["code:patient.organism:XYZ-77"].proposal is None


def test_unnormalized_organism_is_a_shacl_violation():
    h = generate(seed=0)
    p = Patient(id="SYN-T1", name="가나다", gender=Gender.MALE, age=50, department="호흡기내과",
                isolation_order=True, isolation_type=IsolationType.AIRBORNE, organism="TB")
    assert "unknown_organism" in {v.code for v in validate(h.rooms, h.beds, {"W71-R01-B1": p})}
    ok = p.model_copy(update={"organism": "MTB"})
    assert validate(h.rooms, h.beds, {"W71-R01-B1": ok}) == []


def test_missing_room_info_is_fail_closed():
    assert _bed_fits({}, "W99-R01-B1", {"precautions": ["airborne"]}) is False
    assert _bed_fits({}, "W99-R01-B1", {"precautions": ["contact"], "organism": "MRSA"}) is False
    assert _bed_fits({}, "W99-R01-B1", {"precautions": []}) is True  # 격리 주의가 없으면 막을 이유가 없다
    assert _bed_fits({"B": ("negative_pressure", None)}, "B", {"precautions": ["airborne"]}) is True


def test_in_memory_records_are_bounded():
    from turnaround.agent import brain, planner
    from turnaround.agent.llm import FakeLLM
    from types import SimpleNamespace
    b = brain.ExceptionBrain(SimpleNamespace(policy=object()), FakeLLM())
    for i in range(brain.RECORDS_MAX + 500):
        b.records.append(str(i))
    assert len(b.records) == brain.RECORDS_MAX and b.records[0] == "500"
    assert planner.RECORDS_MAX == brain.RECORDS_MAX
