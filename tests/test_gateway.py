"""4단계: 실행 게이트웨이 테스트.

설계 요구: 판단 정보 두 번 재확인(제안 시점, 확정 직전), SHACL 재검사, 정책 파일의 확인 등급,
멱등 키, 감사 로그, 종말소독 강제, 조회 권한. 사용자 질문으로 추가: 관리자 직접 수정.
"""

from datetime import datetime, timedelta

import pytest

from turnaround.events import Event, EventStore, EventType
from turnaround.gateway import Gateway, Policy, Proposal, load_policy
from turnaround.sim import Gender, generate
from turnaround.sim.models import ORGANISM_PRECAUTIONS, Patient

H = generate(seed=0)
DAY0 = datetime(2026, 3, 2)


def at(hh, mm=0, day=0):
    return DAY0 + timedelta(days=day, hours=hh, minutes=mm)


def adm(case="A1", gender="F", age=50, dept="소화기내과", organism=None, status=None, icu=False,
        single=None, integrated=False, consent=None, name=None, when=None):
    prec = [p.value for p in ORGANISM_PRECAUTIONS[organism]] if organism else []
    return Event(event_id=f"{case}:admission_requested", case_id=case, type=EventType.ADMISSION_REQUESTED,
                 occurred_at=when or at(15, day=-1), observed_at=when or at(15, day=-1), source="his",
                 payload={"patient_id": f"SYN-{case}", "name": name or f"환자{case}", "gender": gender, "age": age,
                          "department": dept, "isolation_order": bool(organism), "precautions": prec,
                          "organism": organism, "precaution_status": status or ("confirmed" if organism else None),
                          "from_icu": icu, "wants_single_room": single == "preference",
                          "single_room_reason": single, "integrated_care_requested": integrated,
                          "integrated_care_consent": consent, "immunocompromised": False, "open_wound": False})


def ev(case, key, etype, when, source="his", **payload):
    return Event(event_id=f"{case}:{key}", case_id=case, type=etype, occurred_at=when, observed_at=when,
                 source=source, payload=payload)


def census_patient(pid, name="재원환자", gender=Gender.FEMALE):
    return Patient(id=pid, name=name, gender=gender, age=60, department="소화기내과")


def make(events=(), census=None, policy=None):
    store = EventStore(":memory:")
    store.extend(events)
    gw = Gateway(store, H.rooms, H.beds, policy or load_policy(), census=census or {})
    return store, gw


def types(store, source="gateway"):
    return [e.type for e in store.read() if e.source == source]


def prop(pid="p1", case="A1", bed="W72-R04-B2", basis=None, store=None, **kw):
    return Proposal(proposal_id=pid, kind="match", case=case, bed_id=bed,
                    basis_seq=basis if basis is not None else (store.last_seq() if store else 0), **kw)


# ---------- 정책 파일 ----------

def test_policy_file_loads():
    p = load_policy()
    assert p.version.startswith("policy-")
    assert p.autonomy_stage == 1
    assert "isolation" in p.review_reasons
    assert p.query_fields["environment_vendor"] == ["bed_id", "eta", "cleaning_type"]


# ---------- 제안 시점 ----------

def test_plain_case_stage1_goes_to_manager_confirm_not_review():
    store, gw = make([adm()])
    d = gw.submit(prop(store=store), at(9))
    assert d.tier == "ward_confirm" and d.status == "awaiting_manager"
    assert EventType.CASE_ROUTED_REVIEW not in types(store)


def test_promoted_type_stage2_asks_ward_directly():
    pol = load_policy().model_copy(update={"autonomy_stage": 2, "promoted_departments": ["소화기내과"]})
    store, gw = make([adm()], policy=pol)
    d = gw.submit(prop(store=store), at(9))
    assert d.status == "awaiting_ward"
    assert EventType.WARD_ACCEPTANCE_ASKED in types(store)


@pytest.mark.parametrize("kw,reason", [
    ({"organism": "VRE"}, "isolation"),
    ({"age": 6}, "pediatric"),
    ({"icu": True}, "icu_stepdown"),
    ({"single": "preference"}, "single_room_preference"),
    ({"integrated": True, "consent": None}, "integrated_care_consent_missing"),
    ({"gender": None}, "missing_input"),
])
def test_review_reasons_route_to_review(kw, reason):
    bed = "W72-R01-B1" if kw.get("organism") or kw.get("single") else "W72-R04-B2"
    if kw.get("gender", "F") is None:
        bed = "W72-R01-B1"  # 성별 누락은 1인실 제안으로 SHACL을 피하고 등급만 본다
    store, gw = make([adm(**kw)])
    d = gw.submit(prop(store=store, bed=bed), at(9))
    assert d.tier == "review" and reason in d.reasons
    assert EventType.CASE_ROUTED_REVIEW in types(store)


def test_missing_gender_creates_data_fix_task():
    store, gw = make([adm(gender=None)])
    gw.submit(prop(store=store, bed="W72-R01-B1"), at(9))
    tasks = [e for e in store.read() if e.type is EventType.HUMAN_TASK_CREATED]
    assert [t.payload["kind"] for t in tasks] == ["data_fix"]


def test_single_room_preference_creates_fee_consent_but_isolation_does_not():
    store, gw = make([adm("A1", single="preference"), adm("A2", organism="MRSA", single="isolation", gender="M")])
    gw.submit(prop("p1", "A1", "W72-R01-B1", store=store), at(9))
    gw.submit(prop("p2", "A2", "W72-R02-B1", store=store), at(9))
    kinds = {(e.case_id, e.payload.get("kind")) for e in store.read() if e.type is EventType.HUMAN_TASK_CREATED}
    assert ("A1", "fee_consent") in kinds and ("A2", "fee_consent") not in kinds  # LP-22


def test_close_scores_route_to_review():
    store, gw = make([adm(gender="M")])
    d = gw.submit(prop(store=store, bed="W71-R03-B1", scores={"W71-R03-B1": 80.0, "W72-R03-B2": 77.0}), at(9))
    assert "close_scores" in d.reasons


def test_same_name_same_ward_routes_review_and_same_room_is_rejected():
    census = {"W71-R04-B1": census_patient("SYN-C1", name="김가온")}
    store, gw = make([adm(name="김가온")], census=census)
    d1 = gw.submit(prop("p1", bed="W71-R04-B3", store=store), at(9))
    assert d1.status == "rejected" and "same_name_room" in d1.reasons
    d2 = gw.submit(prop("p2", bed="W71-R05-B1", store=store), at(9))
    assert d2.tier == "review" and "same_name_same_ward" in d2.reasons


def test_shacl_rejects_airborne_in_multi_room_at_proposal():
    store, gw = make([adm(organism="MTB", gender="M")])
    d = gw.submit(prop(store=store, bed="W71-R03-B2"), at(9))
    assert d.status == "rejected" and d.stage == "proposal"
    assert EventType.REJECTED_AT_PROPOSAL in types(store)


def test_info_change_after_basis_rejects_at_proposal():
    store, gw = make([adm(), ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1),
                                bed_id="W72-R04-B1", isolation_order=False)])
    basis = store.last_seq()
    store.append(ev("D1", "discharge_cancelled", EventType.DISCHARGE_CANCELLED, at(8)))
    d = gw.submit(prop(store=store, bed="W72-R04-B1", basis=basis, discharge_id="D1"), at(9))
    assert d.status == "rejected" and "info_changed" in d.reasons


def test_no_bed_proposal_reports_and_routes_review():
    store, gw = make([adm(organism="MTB", gender="M")])
    d = gw.submit(prop(store=store, bed=None), at(9))
    assert EventType.NO_BED_REPORTED in types(store) and EventType.CASE_ROUTED_REVIEW in types(store)
    assert d.tier == "review"


# ---------- 확정 직전 ----------

def _through_to_ward_acceptance(store, gw, pid="p1", case="A1", bed="W72-R04-B2", **kw):
    d = gw.submit(prop(pid, case, bed, store=store, **kw), at(9))
    if d.status in ("awaiting_manager", "awaiting_review"):
        gw.manager_approve(pid, at(9, 10))
    gw.ward_reply(pid, accepted=True, now=at(9, 20))
    return d


def test_full_flow_commits_with_version():
    store, gw = make([adm()])
    _through_to_ward_acceptance(store, gw)
    d = gw.commit("p1", approved_version=gw.location_version("W72-R04-B2"), now=at(10))
    assert d.status == "committed"
    assert EventType.MATCH_COMMITTED in types(store)


def test_commit_without_ward_acceptance_is_rejected():
    store, gw = make([adm()])
    gw.submit(prop(store=store), at(9))
    gw.manager_approve("p1", at(9, 10))
    d = gw.commit("p1", approved_version=gw.location_version("W72-R04-B2"), now=at(10))
    assert d.status == "rejected" and "ward_acceptance_missing" in d.reasons


def test_isolation_added_before_commit_rejects_reclassifies_and_notifies_ward():
    store, gw = make([adm(gender="M")])
    _through_to_ward_acceptance(store, gw, bed="W71-R03-B2")
    store.append(ev("A1", "isolation_order_added", EventType.ISOLATION_ORDER_ADDED, at(9, 30), "his",
                    organism="MTB", precautions=["airborne"], precaution_status="confirmed", isolation_ordered_by="physician"))
    d = gw.commit("p1", approved_version=gw.location_version("W71-R03-B2"), now=at(10))
    t = types(store)
    assert d.status == "rejected" and d.stage == "commit"
    assert EventType.REJECTED_AT_COMMIT in t and EventType.CASE_RECLASSIFIED_REVIEW in t
    assert EventType.WARD_NOTIFIED in t
    assert EventType.MATCH_COMMITTED not in t


def test_version_conflict_rejects_and_asks_reapproval():
    store, gw = make([adm()])
    _through_to_ward_acceptance(store, gw)
    v = gw.location_version("W72-R04-B2")
    store.append(ev("W72-R04-B2", "location_updated:1", EventType.LOCATION_UPDATED, at(9, 40), "human", field="nurse"))
    d = gw.commit("p1", approved_version=v, now=at(10))
    assert d.status == "rejected" and "version_conflict" in d.reasons
    kinds = [e.payload.get("kind") for e in store.read() if e.type is EventType.HUMAN_TASK_CREATED]
    assert kinds == ["reapproval"]
    assert EventType.CASE_RECLASSIFIED_REVIEW not in types(store)


def test_concurrent_commits_only_first_wins():
    store, gw = make([adm("A1"), adm("A2")])
    _through_to_ward_acceptance(store, gw, "p1", "A1", "W71-R04-B2")
    _through_to_ward_acceptance(store, gw, "p2", "A2", "W71-R04-B2")
    v = gw.location_version("W71-R04-B2")
    d1 = gw.commit("p1", v, at(10))
    d2 = gw.commit("p2", v, at(10))
    assert d1.status == "committed" and d2.status == "rejected"
    loser = [e.type for e in store.read() if e.source == "gateway" and e.case_id == "A2"]
    assert EventType.NO_BED_REPORTED in loser and EventType.CASE_ROUTED_REVIEW in loser
    assert EventType.WARD_NOTIFIED in loser


def test_isolation_discharge_bed_requires_terminal_cleaning_done_before_commit():
    evs = [adm(), ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W71-R02-B1",
                     isolation_order=True, precautions=["contact"], organism="MRSA"),
           ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(8), bed_id="W71-R02-B1")]
    store, gw = make(evs)
    d0 = _through_to_ward_acceptance(store, gw, bed="W71-R02-B1")
    assert "isolation_discharge_bed" in d0.reasons
    d = gw.commit("p1", gw.location_version("W71-R02-B1"), at(10))
    assert d.status == "rejected" and "precondition_unmet" in d.reasons
    assert EventType.CASE_RECLASSIFIED_REVIEW not in types(store)  # LP-04: 재분류 아님
    store.append(ev("D1", "cleaning_done", EventType.CLEANING_DONE, at(10, 30), "human", bed_id="W71-R02-B1",
                    cleaning_type="terminal"))
    d2 = gw.commit("p1", gw.location_version("W71-R02-B1"), at(11), attempt=2)
    assert d2.status == "committed"


# ---------- 멱등, 감사 ----------

def test_same_submit_is_processed_once():
    store, gw = make([adm()])
    gw.submit(prop(store=store), at(9))
    n = store.last_seq()
    d = gw.submit(prop(store=store, basis=0), at(9, 1))
    assert d.status == "duplicate" and store.last_seq() == n


def test_every_gateway_action_has_an_audit_record():
    store, gw = make([adm(), adm("A2", organism="MTB", gender="M")])
    _through_to_ward_acceptance(store, gw)
    gw.commit("p1", gw.location_version("W72-R04-B2"), at(10))
    gw.submit(prop("p9", "A2", "W71-R03-B2", store=store), at(10))
    audits = [e for e in store.read() if e.type is EventType.GATEWAY_DECISION]
    assert audits and all({"policy_version", "decision", "reasons", "step"} <= set(a.payload) for a in audits)
    steps = {(a.payload["proposal_id"], a.payload["step"]) for a in audits}
    assert {("p1", "submit"), ("p1", "commit"), ("p9", "submit")} <= steps


# ---------- 청소 요청 강제 (LP-03, LP-20) ----------

def test_general_cleaning_for_isolation_discharge_is_corrected_not_rejected():
    store, gw = make([ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W72-R01-B1",
                         isolation_order=True, precautions=["droplet"], organism="INFLUENZA")])
    d = gw.request_cleaning("D1", "general", at(11))
    assert d.status == "executed"
    req = [e for e in store.read() if e.type is EventType.CLEANING_REQUEST_ISSUED]
    assert len(req) == 1
    assert req[0].payload == {"bed_id": "W72-R01-B1", "cleaning_type": "terminal", "aerosol_wait_min": 30,
                              "overridden_from": "general"}
    assert gw.request_cleaning("D1", "general", at(11, 5)).status == "duplicate"


# ---------- 조회 권한 ----------

def test_vendor_query_for_patient_fields_is_refused():
    store, gw = make()
    d = gw.query("environment_vendor", ["name", "diagnosis"], case="D1", now=at(11))
    assert d.status == "refused" and EventType.QUERY_REFUSED in types(store)
    d2 = gw.query("environment_vendor", ["bed_id", "eta"], case="D1", now=at(11))
    assert d2.status == "answered"


# ---------- 관리자 직접 수정 (사용자 질문) ----------

def test_manual_override_requires_reason_and_role():
    store, gw = make([adm()])
    assert gw.manual_override("A1", "W72-R04-B2", actor="u1", role="environment_vendor", reason="vip",
                              now=at(12)).status == "refused"
    assert gw.manual_override("A1", "W72-R04-B2", actor="u1", role="bed_manager", reason="",
                              now=at(12)).status == "refused"


def test_manual_override_applies_releases_old_match_and_pins_case():
    store, gw = make([adm()])
    _through_to_ward_acceptance(store, gw)
    gw.commit("p1", gw.location_version("W72-R04-B2"), at(10))
    d = gw.manual_override("A1", "W72-R04-B3", actor="u1", role="bed_manager", reason="vip", now=at(12))
    assert d.status == "applied"
    t = types(store)
    assert EventType.MANUAL_OVERRIDE_APPLIED in t and EventType.PREMATCH_RELEASED in t and EventType.WARD_NOTIFIED in t
    later = gw.submit(prop("p5", "A1", "W72-R04-B4", store=store), at(13))
    assert later.status == "rejected" and "pinned_by_human" in later.reasons


def test_manual_override_cannot_break_safety_rule_without_ipc_exception():
    store, gw = make([adm(organism="MTB", gender="M")])
    d = gw.manual_override("A1", "W71-R03-B2", actor="u1", role="bed_manager", reason="patient_request", now=at(12))
    assert d.status == "refused" and "airborne_needs_negative_pressure" in d.reasons
    d2 = gw.manual_override("A1", "W71-R03-B2", actor="u1", role="bed_manager", reason="clinical", now=at(12, 5),
                            safety_exception_by=("ipc-1", "ipc"))
    assert d2.status == "applied"
    audit = [e for e in store.read() if e.type is EventType.GATEWAY_DECISION and e.payload["step"] == "override"][-1]
    assert audit.payload["safety_exception"] == {"by": "ipc-1", "role": "ipc",
                                                 "violations": ["airborne_needs_negative_pressure"]}


def test_vip_reason_is_not_exposed_to_vendor_query():
    store, gw = make([adm()])
    gw.manual_override("A1", "W72-R04-B3", actor="u1", role="bed_manager", reason="vip", now=at(12))
    d = gw.query("environment_vendor", ["reason"], case="A1", now=at(12, 5))
    assert d.status == "refused"


# ---------- 직접 수정도 물리 사실은 넘지 못한다 (조사에서 찾은 빈틈) ----------

def test_manual_override_cannot_use_occupied_bed_even_with_exception():
    store, gw = make([adm("A1"), adm("A2")])
    _through_to_ward_acceptance(store, gw, "p1", "A1", "W72-R04-B2")
    gw.commit("p1", gw.location_version("W72-R04-B2"), at(10))
    d = gw.manual_override("A2", "W72-R04-B2", actor="u", role="bed_manager", reason="vip", now=at(12),
                           safety_exception_by=("ipc-1", "ipc"))
    assert d.status == "refused" and "bed_occupied" in d.reasons


def test_manual_override_cannot_use_bed_with_current_inpatient():
    store, gw = make([adm("A1")], census={"W72-R04-B2": census_patient("SYN-C")})
    d = gw.manual_override("A1", "W72-R04-B2", actor="u", role="bed_manager", reason="vip", now=at(12))
    assert d.status == "refused" and "bed_occupied" in d.reasons


def test_manual_override_cannot_skip_terminal_cleaning_even_with_exception():
    evs = [adm(), ev("D1", "discharge_decided", EventType.DISCHARGE_DECIDED, at(17, day=-1), bed_id="W71-R02-B1",
                     isolation_order=True, precautions=["contact"], organism="MRSA"),
           ev("D1", "patient_departed", EventType.PATIENT_DEPARTED, at(8), bed_id="W71-R02-B1")]
    store, gw = make(evs)
    d = gw.manual_override("A1", "W71-R02-B1", actor="u", role="bed_manager", reason="vip", now=at(9),
                           safety_exception_by=("ipc-1", "ipc"))
    assert d.status == "refused" and "precondition_unmet" in d.reasons


# ---------- EVT-4 정정과 격리 해제 ----------

def _corr(case, n, when, field, value, **kw):
    return ev(case, f"correction:{n}", EventType.PATIENT_INFO_CORRECTED, when, "his", field=field, value=value, **kw)


def test_isolation_release_by_ipc_allows_multi_room():
    store, gw = make([adm(gender="M", organism="CRE", status="preemptive")])
    assert gw.submit(prop("p1", bed="W71-R03-B2", store=store), at(9)).status == "rejected"
    store.append(ev("A1", "isolation_released", EventType.ISOLATION_RELEASED, at(10, day=1), "his",
                    ordered_by="ipc", reason="screening_negative_x2"))
    d = gw.submit(prop("p2", bed="W71-R03-B2", store=store), at(10, 5, day=1))
    assert d.status != "rejected" and "preemptive_isolation" not in d.reasons


def test_loosening_correction_waits_for_human_confirmation():
    store, gw = make([adm(gender="M", organism="MRSA")])
    store.append(_corr("A1", 1, at(8), "precautions", []))  # 격리 오입력이라며 삭제하는 정정
    d = gw.submit(prop("p1", bed="W71-R03-B2", store=store), at(9))
    assert d.status == "rejected" and "isolation_room" in d.reasons  # 확인 전에는 반영 안 함
    store.append(ev("A1", "correction_confirmed:1", EventType.CORRECTION_CONFIRMED, at(9, 30), "human",
                    correction_id="A1:correction:1", actor="ipc-1"))
    d2 = gw.submit(prop("p2", bed="W71-R03-B2", store=store), at(9, 40))
    assert d2.status != "rejected"


def test_tightening_correction_applies_immediately_and_blocks_pending_commit():
    store, gw = make([adm(gender="M")])
    _through_to_ward_acceptance(store, gw, bed="W71-R03-B2")
    store.append(_corr("A1", 1, at(9, 30), "precautions", ["contact"], organism="VRE"))
    d = gw.commit("p1", gw.location_version("W71-R03-B2"), at(10))
    assert d.status == "rejected" and "info_changed" in d.reasons


def test_gender_correction_applies_and_is_material():
    store, gw = make([adm(gender="F")])
    _through_to_ward_acceptance(store, gw, bed="W71-R04-B2")
    store.append(_corr("A1", 1, at(9, 30), "gender", "M"))
    d = gw.commit("p1", gw.location_version("W71-R04-B2"), at(10))
    assert d.status == "rejected" and "info_changed" in d.reasons


# ---------- GW-9 동실 환자 재확인 ----------

def test_roommate_isolation_order_after_basis_rejects_commit():
    census = {"W71-R03-B1": census_patient("SYN-C1", gender=Gender.MALE)}
    store, gw = make([adm(gender="M")], census=census)
    _through_to_ward_acceptance(store, gw, bed="W71-R03-B2")
    store.append(ev("SYN-C1", "isolation_order_added", EventType.ISOLATION_ORDER_ADDED, at(9, 30), "his",
                    organism="VRE", precautions=["contact"], precaution_status="confirmed"))
    d = gw.commit("p1", gw.location_version("W71-R03-B2"), at(10))
    assert d.status == "rejected" and "info_changed" in d.reasons


def test_roommate_change_in_other_room_does_not_block():
    census = {"W71-R04-B1": census_patient("SYN-C2")}
    store, gw = make([adm(gender="M")], census=census)
    _through_to_ward_acceptance(store, gw, bed="W71-R03-B2")
    store.append(ev("SYN-C2", "isolation_order_added", EventType.ISOLATION_ORDER_ADDED, at(9, 30), "his",
                    organism="VRE", precautions=["contact"], precaution_status="confirmed"))
    assert gw.commit("p1", gw.location_version("W71-R03-B2"), at(10)).status == "committed"


def test_roommate_state_is_updated_from_events_for_shacl():
    census = {"W71-R04-B1": census_patient("SYN-C3", name="박하늘")}
    store, gw = make([adm(name="김가온")], census=census)
    store.append(ev("SYN-C3", "correction:1", EventType.PATIENT_INFO_CORRECTED, at(8), "his", field="name", value="김가온"))
    d = gw.submit(prop("p1", bed="W71-R04-B3", store=store), at(9))
    assert d.status == "rejected" and "same_name_room" in d.reasons


# ---------- GW-11 직접 수정 태그, 고정 해제 ----------

def test_manual_placement_tag_shows_who_when_and_masks_vip_for_others():
    store, gw = make([adm()])
    gw.manual_override("A1", "W72-R04-B3", actor="u1", role="bed_manager", reason="vip", now=at(12))
    mgr = gw.placement_tags(role="bed_manager")["W72-R04-B3"]
    assert mgr == {"manual": True, "by": "u1", "at": at(12).isoformat(), "reason": "vip"}
    nurse = gw.placement_tags(role="ward_nurse")["W72-R04-B3"]
    assert nurse["manual"] is True and nurse["reason"] == "요청 배치" and "by" not in nurse


def test_release_pin_returns_case_to_agent():
    store, gw = make([adm()])
    gw.manual_override("A1", "W72-R04-B3", actor="u1", role="bed_manager", reason="clinical", now=at(12))
    assert gw.release_pin("A1", actor="u1", role="ward_nurse", now=at(13)).status == "refused"
    assert gw.release_pin("A1", actor="u1", role="bed_manager", now=at(13)).status == "released"
    assert "W72-R04-B3" not in gw.placement_tags(role="bed_manager")
    d = gw.submit(prop("p9", "A1", "W72-R04-B4", store=store), at(14))
    assert "pinned_by_human" not in d.reasons
