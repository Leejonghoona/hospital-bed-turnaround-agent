"""두 검사기(독립 파이썬, SHACL)가 함께 쓰는 LP-16~20 뮤턴트 사례."""

from datetime import datetime, timedelta

from turnaround.evaluation.invariants import BedTurnover
from turnaround.sim import CleaningType, Gender, PrecautionStatus, generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS, Patient

H = generate(seed=0)
T0 = datetime(2026, 3, 2, 12, 0)


def rooms_with_cohort(room_id="W71-R04", key="VRE"):
    """다인실 하나를 같은 병원체 코호트실로 지정한 병실 목록."""
    return tuple(r.model_copy(update={"cohort_key": key}) if r.id == room_id else r for r in H.rooms)


def pp(pid, organism=None, name=None, gender=Gender.FEMALE, status=None, immuno=False, wound=False):
    return Patient(id=f"SYN-Q{pid}", name=name or f"환자{pid}", gender=gender, age=50, department="감염내과",
                   precautions=ORGANISM_PRECAUTIONS[organism] if organism else (), organism=organism,
                   precaution_status=status or (PrecautionStatus.CONFIRMED if organism else None),
                   immunocompromised=immuno, open_wound=wound)


COHORT = rooms_with_cohort()
# (병실 목록, 배치, 기대 위반 코드)
ASSIGNMENT_CASES = [
    ("비말주의 1인실", H.rooms, {"W71-R02-B1": pp(1, "INFLUENZA")}, []),
    ("비말주의 일반 다인실", H.rooms, {"W71-R04-B1": pp(1, "INFLUENZA")}, ["isolation_room"]),
    ("접촉주의 같은 균 코호트", COHORT, {"W71-R04-B1": pp(1, "VRE"), "W71-R04-B2": pp(2, "VRE")}, []),
    ("접촉주의 다른 균 코호트", COHORT, {"W71-R04-B1": pp(1, "MRSA")}, ["isolation_room", "non_cohort_in_cohort_room"]),
    ("비격리 환자가 코호트실", COHORT, {"W71-R04-B1": pp(1)}, ["non_cohort_in_cohort_room"]),
    ("면역저하 환자가 코호트실", COHORT, {"W71-R04-B1": pp(1, "VRE", immuno=True)}, ["protective_in_cohort"]),
    ("개방창상 환자가 코호트실", COHORT, {"W71-R04-B1": pp(1, "VRE", wound=True)}, ["protective_in_cohort"]),
    ("선제 격리가 코호트실", COHORT, {"W71-R04-B1": pp(1, "VRE", status=PrecautionStatus.PREEMPTIVE)},
     ["preemptive_in_cohort"]),
    ("선제 격리 1인실", H.rooms, {"W71-R02-B1": pp(1, "CRE", status=PrecautionStatus.PREEMPTIVE)}, []),
    ("수두(공기+접촉) 1인실", H.rooms, {"W71-R02-B1": pp(1, "VARICELLA")}, ["airborne_needs_negative_pressure"]),
    ("수두 음압실", H.rooms, {"W71-R01-B1": pp(1, "VARICELLA")}, []),
    ("결핵 환자 다인실 공유", H.rooms, {"W71-R04-B1": pp(1, "MTB"), "W71-R04-B2": pp(2)},
     ["airborne_needs_negative_pressure", "tb_shared_room"]),
    ("접촉주의 음압실(감점일 뿐 위반 아님)", H.rooms, {"W71-R01-B1": pp(1, "MRSA")}, []),
]


def to(**kw):
    base = dict(bed_id="W72-R01-B1", previous_isolation=True, cleaning_type=CleaningType.TERMINAL,
                cleaning_done_at=T0, committed_at=None, vacant_at=None)
    base.update(kw)
    return BedTurnover(**base)


DEP = T0 - timedelta(hours=2)
TURNOVER_CASES = [
    ("비말 퇴실 환기 대기 후 청소", to(previous_precautions=("droplet",), departed_at=DEP, aerosol_wait_min=35,
                                    cleaning_started_at=DEP + timedelta(minutes=35)), []),
    ("대기 값 없으면 일반실 4회 104분", to(previous_precautions=("droplet",), departed_at=DEP,
                                    cleaning_started_at=DEP + timedelta(minutes=60)), ["cleaning_before_aerosol_wait"]),
    ("비말 퇴실 환기 대기 전 청소", to(previous_precautions=("droplet",), departed_at=DEP,
                                    cleaning_started_at=DEP + timedelta(minutes=10)), ["cleaning_before_aerosol_wait"]),
    ("접촉 퇴실은 환기 대기 없음", to(previous_precautions=("contact",), departed_at=DEP,
                                   cleaning_started_at=DEP + timedelta(minutes=1)), []),
    ("C. difficile 아포 소독", to(previous_precautions=("contact",), previous_organism="CDIFF", sporicidal_done=True), []),
    ("C. difficile 아포 소독 누락", to(previous_precautions=("contact",), previous_organism="CDIFF", sporicidal_done=False),
     ["sporicidal_missing"]),
]
ASSIGNMENT_CASES.append(
    ("동명이인 쌍 두 개가 한 병실", H.rooms,
     {"W71-R04-B1": pp(1, name="김가온"), "W71-R04-B2": pp(2, name="김가온"),
      "W71-R04-B3": pp(3, name="이나라"), "W71-R04-B4": pp(4, name="이나라")}, ["same_name_room", "same_name_room"]))
