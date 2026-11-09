"""실행 상태 안전 지표 (DECISIONS "안전 지표 S1·S2를 시스템 실행 상태로 재기")."""

from datetime import timedelta

from test_gateway import adm, at, ev, make
from turnaround.events import EventType
from turnaround.evaluation.safety_exec import executed_safety


def _iso_discharge(case="D1", bed="W71-R02-B1", organism="MRSA"):
    from turnaround.sim.models import ORGANISM_PRECAUTIONS
    prec = [p.value for p in ORGANISM_PRECAUTIONS[organism]]
    return [ev(case, "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id=bed,
               isolation_order=True, precautions=prec, organism=organism),
            ev(case, "patient_departed", EventType.PATIENT_DEPARTED, at(10), bed_id=bed)]


def test_clean_run_has_no_violations_and_counts_denominators():
    store, gw = make(_iso_discharge())
    store.append(ev("D1", "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga",
                    bed_id="W71-R02-B1", cleaning_type="terminal"))
    store.append(ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(11, 30), "human", bed_id="W71-R02-B1"))
    store.append(ev("D1", "bed_vacant", EventType.BED_MARKED_VACANT, at(11, 40), "saga", bed_id="W71-R02-B1"))
    r = executed_safety(gw, store.read())
    assert r["S2"]["violations"] == 0 and r["S2"]["denominator"] == 1


def test_general_cleaning_after_isolation_discharge_is_a_violation():
    store, gw = make(_iso_discharge())
    store.append(ev("D1", "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga",
                    bed_id="W71-R02-B1", cleaning_type="general"))
    r = executed_safety(gw, store.read())
    assert r["S2"]["violations"] == 1 and r["S2"]["codes"] == ["terminal_cleaning_missing"]


def test_vacant_before_terminal_done_is_a_violation():
    store, gw = make(_iso_discharge())
    store.append(ev("D1", "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga",
                    bed_id="W71-R02-B1", cleaning_type="terminal"))
    store.append(ev("D1", "bed_vacant", EventType.BED_MARKED_VACANT, at(10, 30), "saga", bed_id="W71-R02-B1"))
    assert executed_safety(gw, store.read())["S2"]["violations"] == 1


def test_executed_assignment_breaking_rules_is_counted_in_s1():
    # 게이트웨이를 거치지 않은 확정 이벤트를 직접 넣어 검사기가 잡는지 본다 (남성 환자를 여성 병실에)
    store, gw = make([adm("A1", gender="M")])
    store.append(ev("A1", "gw:x:committed", EventType.MATCH_COMMITTED, at(9), "gateway", bed_id="W72-R04-B1",
                    proposal_id="x"))
    r = executed_safety(gw, store.read())
    assert r["S1"]["denominator"] == 1 and r["S1"]["violations"] >= 1


def test_no_assignment_means_zero_denominator():
    store, gw = make([adm("A1")])
    r = executed_safety(gw, store.read())
    assert r["S1"] == {"violations": 0, "denominator": 0, "upper_bound_95": None, "codes": []}


def test_tb_cleaning_request_shorter_than_60_min_is_a_violation():
    store, gw = make(_iso_discharge(bed="W71-R01-B1", organism="MTB"))
    store.append(ev("D1", "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga",
                    bed_id="W71-R01-B1", cleaning_type="terminal", aerosol_wait_min=35))
    assert "vacancy_floor_short" in executed_safety(gw, store.read())["S2"]["codes"]


def test_late_isolation_upgrade_counts_as_terminal_and_requires_later_done():
    evs = [ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W72-R01-B1",
              isolation_order=False),
           ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(10), bed_id="W72-R01-B1"),
           ev("D1", "cleaning_request", EventType.CLEANING_REQUEST_ISSUED, at(10), "saga", bed_id="W72-R01-B1",
              cleaning_type="general"),
           ev("D1", "iso_late", EventType.ISOLATION_ORDER_ADDED, at(10, 40), "his", bed_id="W72-R01-B1",
              precautions=["contact"], organism="VRE"),
           ev("D1", "cleaning_request:terminal_upgrade", EventType.CLEANING_REQUEST_ISSUED, at(10, 40), "saga",
              bed_id="W72-R01-B1", cleaning_type="terminal"),
           ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(10, 30), "human", bed_id="W72-R01-B1"),
           ev("D1", "bed_vacant", EventType.BED_MARKED_VACANT, at(10, 50), "saga", bed_id="W72-R01-B1")]
    store, gw = make(evs)
    r = executed_safety(gw, store.read())
    assert r["S2"]["denominator"] == 1 and r["S2"]["codes"] == ["vacant_before_disinfection"]
