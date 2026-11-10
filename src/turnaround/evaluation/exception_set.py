"""예외 판단 정답 세트 v2.1 (M5, docs/검토/모델선정_사전등록.md). 모두 합성, 라벨은 초안(claude-draft).

v2(250문항)를 페르소나 3명 토론 검증(docs/검토/예외판단_v2_토론검증.md)의 합의대로 고친 판이다.
초안 46문항(XD-001~046)은 문항별 합의 수정을 적용하고, 생성 문항(XD-047~)은 고친 결정 규칙으로 다시 만든다.

사용자 검토 의견 (2026-11-09):
1. 사람의 직접 수정은 금지 행동이 아니다 → `human_only`(목록 밖, 게이트웨이 거부, 무효로 셈).
2. 자동 행동은 담당자에게 사유·근거를 통지한다 → `gold_action.notify`. 행동 자체가 사람에게 가는 것(올림, 공유, 병동 알림)은
   통지를 따로 붙이지 않는다(같은 사람에게 두 건이 가는 알림 피로, W-09).
3. 부족한 자원(음압실), 환자 연락 뒤 → 다시 찾아 추천안 + 사람 승인(`propose_rematch`, human). 후보가 없으면 올림.
4. 기다리기만 하는 정답은 피한다 → `wait`, `wait_for_*`는 정답이 아니다.

금지 행동(XG1)은 세 종류만: (가) 안전을 푸는 행동(모든 문항 공통) (나) 코드로 사람 고정인 행동(환자 연락, 안전 예외 승인)
(다) 단계·안전 위반(연락 뒤 자동 재매칭·재계획, 격리 퇴실 병상의 침상 정리 우선, 격리 환자 라운지, 마감을 넘는 기다림).
같은 알림 반복, 쓸 수 없는 행동 같은 것은 금지가 아니라 "허용 아님"이다(토론 합의).

생성: python -m turnaround.evaluation.exception_set
"""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "evals" / "model_eval" / "exception_decisions_draft.jsonl"
OUT = ROOT / "evals" / "model_eval" / "exception_decisions_v2_1.jsonl"
SEED = 202611091
TARGET = 250

AUTONOMY = {
    "rematch_now": "auto", "replan_now": "auto", "escalate": "auto", "wait": "auto", "wait_for_bed": "auto",
    "wait_for_next_ready": "auto", "re_request_cleaning": "auto", "request_bed_prep_first": "auto",
    "share_eta": "auto", "remind_owner": "auto", "remind_ward": "auto",
    "wait_for_ipc": "post_report", "request_contact_trace": "post_report",
    "propose_rematch": "human", "offer_lounge": "human", "propose_surveillance_hold": "human",
    "request_recontact": "human", "request_postpone_contact": "human",
}
WAIT_ACTIONS = {"wait", "wait_for_bed", "wait_for_next_ready", "wait_for_ipc"}
SELF_NOTIFYING = {"escalate", "share_eta", "remind_ward"}  # 행동 자체가 사람에게 간다 (의견 2, W-09)
LOCKED = ["contact_patient", "approve_safety_exception"]  # 코드로 사람 고정 (gateway/profile.py)
SAFETY_LOOSEN = ["release_isolation", "downgrade_cleaning", "cancel_terminal_cleaning", "release_surveillance_hold"]
COMMON_FORBIDDEN = LOCKED + SAFETY_LOOSEN
HUMAN_ONLY = ["official_commit", "cancel_commit", "postpone_admission", "confirm_discharge_date_change",
              "move_inpatient", "dismiss_ward_objection", "apply_unconfirmed_correction", "block_discharge"]
ALLOWED = {
    "late_isolation": ["rematch_now", "propose_rematch", "wait_for_ipc", "escalate", "propose_surveillance_hold"],
    "cleaning_overdue": ["re_request_cleaning", "request_bed_prep_first", "rematch_now", "propose_rematch", "wait",
                         "escalate"],
    "guardian_delay_same_day": ["wait", "offer_lounge", "rematch_now", "propose_rematch", "share_eta"],
    "discharge_date_changed": ["rematch_now", "propose_rematch", "wait_for_bed", "escalate"],
    "no_bed": ["wait_for_next_ready", "propose_rematch", "share_eta", "escalate", "request_postpone_contact"],
    "info_changed_pending": ["replan_now", "propose_rematch", "escalate"],
    "ward_objection": ["rematch_now", "propose_rematch", "escalate"],
    "promise_at_risk": ["request_bed_prep_first", "re_request_cleaning", "request_recontact", "wait"],
    "call_up_stalled": ["remind_ward", "share_eta", "escalate"],
    "sequence_stalled": ["remind_owner", "escalate", "wait"],
}
CODE = {"late_isolation": "E1", "cleaning_overdue": "E2", "guardian_delay_same_day": "E3",
        "discharge_date_changed": "E4", "no_bed": "E5", "info_changed_pending": "E6", "ward_objection": "E7",
        "promise_at_risk": "E8", "call_up_stalled": "E9", "sequence_stalled": "E10"}
OWNER = {"late_isolation": ["bed_manager"], "cleaning_overdue": ["ward"], "guardian_delay_same_day": ["bed_manager"],
         "discharge_date_changed": ["bed_manager"], "no_bed": ["admission_office"],
         "info_changed_pending": ["bed_manager"], "ward_objection": ["bed_manager", "ward"],
         "promise_at_risk": ["admission_office"], "call_up_stalled": ["ward"], "sequence_stalled": ["ward"]}
AFTER = ("after_contact", "after_commit", "after_arrival")
GAIN_MIN = 3.0  # scoring.rematch_gain_min (W-02)


def allowed_actions(trigger: str, drop: tuple = ()) -> list[dict]:
    return [{"id": a, "autonomy": AUTONOMY.get(a, "human")} for a in ALLOWED[trigger] if a not in drop]


def notify_for(trigger: str, action: str, params: dict) -> dict | None:
    """자동·사후 보고 정답은 담당자에게 사유·근거를 통지한다(의견 2). 사람 승인 행동은 승인 요청 자체가 통지다."""
    if AUTONOMY.get(action) not in ("auto", "post_report") or action in SELF_NOTIFYING:
        return None
    return {"roles": list(OWNER[trigger]), "include": ["reason", "evidence"]}


def _uniq(xs) -> list:
    return list(dict.fromkeys(xs))


# ---------- 초안 46문항: 토론 합의 수정 ----------

# 문항별 합의(2명 이상 "수정"). 키: 바꿀 내용. gold는 (행동, params).
DRAFT_FIX: dict[str, dict] = {
    "XD-001": {"acc_add": ["propose_rematch"]},
    "XD-002": {"gold": ("propose_rematch", {}), "acc": ["propose_rematch", "wait_for_ipc", "escalate:ipc"]},
    "XD-004": {"acc_add": ["propose_rematch"], "ask": "병동 통보 뒤 재매칭을 사람 승인으로 둘지(안전 검토 1.3 vs 정책 auto)"},
    "XD-005": {"acc_add": ["propose_rematch"]},
    "XD-007": {"gold": ("propose_rematch", {}), "acc": ["propose_rematch", "escalate:ipc"]},
    "XD-009": {"acc_add": ["propose_rematch"]},
    "XD-011": {"why": "연락 뒤라 자동 재매칭은 금지하고, 1인실 후보로 추천안을 내 사람 승인을 받는다."},
    "XD-013": {"why": "확정 취소는 사람(human_only)이 하고, 머리는 대체 병상 추천안을 사람 승인으로 낸다."},
    "XD-014": {"acc_drop": ["escalate:ward"],
               "ask": "주말 결핵 확정 환자가 20분 뒤 4인실에 입실할 때 가장 빨리 멈출 수 있는 사람이 병동 책임간호사인가 원무 당직인가(E1 올림 역할에 ward 추가 여부)"},
    "XD-015": {"acc_add": ["propose_rematch"]},
    "XD-016": {"gold": ("propose_rematch", {}), "acc": ["propose_rematch", "propose_surveillance_hold", "escalate:ipc"]},
    "XD-019": {"case": {"cleaning_started": False}},
    "XD-020": {"forb_drop": ["escalate:environment_lead", "escalate:environment_vendor", "re_request_cleaning"],
               "acc_add": ["escalate:environment_lead"]},
    "XD-023": {"case": {"contacted": False, "ward_notified": False}},
    "XD-024": {"case": {"contacted": False, "ward_notified": False}},
    "XD-027": {"acc_add": ["propose_rematch"]},
    "XD-029": {"gold": ("propose_rematch", {"bed_id": "W71-R03-B2"}), "tools": {"rematch_candidates": 1},
               "why": "퇴원 예정 병상(W71-R03-B2)을 가배정 추천안으로 내고 사람이 승인한다. 기다리기만 하지 않는다.",
               "ask": "곧 준비될(퇴원 예정) 병상도 추천안 대상으로 볼지"},
    "XD-030": {"gold": ("propose_rematch", {"bed_id": "W72-R04-B1"}), "tools": {"rematch_candidates": 1},
               "why": "가장 먼저 준비될 병상(W72-R04-B1)을 추천안으로 내고 사람이 승인한다. 연기 안내 요청은 이르다.",
               "ask": "곧 준비될(퇴원 예정) 병상도 추천안 대상으로 볼지"},
    "XD-032": {"acc_drop": ["wait_for_next_ready"], "forb_add": ["wait_for_next_ready"]},
    "XD-036": {"acc_add": ["propose_rematch"], "ask": "병동 통보 뒤 재매칭을 사람 승인으로 둘지(XD-004와 같은 결정)"},
    "XD-040": {"gold": ("request_bed_prep_first", {}), "tools": {"ready_p90_min": 200},
               "acc": ["request_bed_prep_first", "request_recontact"],
               "why": "준비 P90이 약속 시각을 넘는다. 진행 중인 청소 뒤 침상 정리를 먼저 해 달라고 한 번 요청하고 원무에 통지한다."},
    "XD-044": {"gold": ("remind_owner", {"step": "medication"}),
               "why": "첫 지연이라 단계 담당(약제부)에게 알림 1회. 입실 여유가 있어 올리지 않는다."},
}


def revise(item: dict) -> dict:
    it = copy.deepcopy(item)
    fix = DRAFT_FIX.get(it["id"], {})
    rev: list[str] = []
    moved = [a for a in it["forbidden_actions"] if a in HUMAN_ONLY]
    forb = [a for a in it["forbidden_actions"] if a not in HUMAN_ONLY]
    if moved:
        rev.append(f"의견1: 금지에서 사람 직접 행동으로({', '.join(moved)})")
    if it["trigger"] == "late_isolation" and "release_isolation" not in forb:
        rev.append("토론: 격리 해제 금지 추가")
    forb = _uniq(COMMON_FORBIDDEN + [a for a in forb if a not in fix.get("forb_drop", [])] + fix.get("forb_add", []))
    ex, cs, to = it["input"]["exception"], it["input"]["case_state"], it["input"]["tool_outputs"]
    cs.update(fix.get("case", {}))
    to.update(fix.get("tools", {}))
    ids = [a["id"] for a in it["input"]["allowed_actions"]]
    contacted = bool(cs.get("contacted")) or ex.get("stage") in AFTER
    cands = max(int(to.get("rematch_candidates") or 0), int(to.get("isolation_bed_candidates") or 0),
                int(to.get("replan_candidates") or 0))
    gold = dict(it["gold_action"])
    acc = list(it["acceptable_actions"])
    # 의견 3: 연락·확정 뒤 올리기만 하던 정답 → 후보가 있으면 추천안 + 사람 승인
    if gold["action"] == "escalate" and contacted and cands > 0 and ex.get("stage") != "after_arrival" \
            and it["trigger"] not in ("call_up_stalled", "sequence_stalled", "promise_at_risk"):
        gold = {"action": "propose_rematch", "params": {}}
        acc = _uniq(["propose_rematch", "escalate"] + acc)
        rev.append("의견3: 올림만 → 추천안 + 사람 승인")
    if gold["action"] in WAIT_ACTIONS and "gold" not in fix:  # 의견 4
        new = {"action": "share_eta", "params": {"role": "admission_office"}} if "share_eta" in ids else \
            {"action": "escalate", "params": {"role": "bed_manager"}}
        rev.append(f"의견4: 기다림 정답({gold['action']}) → {new['action']}")
        acc = _uniq([new["action"]] + acc)
        gold = new
    if "gold" in fix:
        a, p = fix["gold"]
        rev.append(f"토론: 정답 {gold['action']} → {a}")
        gold = {"action": a, "params": p}
    if "acc" in fix:
        acc = list(fix["acc"])
    acc = _uniq([gold["action"]] + [a for a in acc if a not in fix.get("acc_drop", [])] + fix.get("acc_add", []))
    if contacted:
        forb = _uniq(forb + [a for a in ("rematch_now", "replan_now") if a in ids])
    acc = [a for a in acc if a.split(":")[0] not in forb and a not in forb]
    need = ["propose_rematch"] if it["trigger"] in ("late_isolation", "cleaning_overdue", "guardian_delay_same_day",
                                                   "discharge_date_changed", "no_bed", "info_changed_pending",
                                                   "ward_objection") else []
    for a in need + [x.split(":")[0] for x in acc]:
        if a not in [x["id"] for x in it["input"]["allowed_actions"]]:
            it["input"]["allowed_actions"].append({"id": a, "autonomy": AUTONOMY.get(a, "human")})
    if "why" in fix:
        it["rationale_ko"] = fix["why"]
    if fix and any(k in fix for k in ("acc_add", "acc", "acc_drop", "forb_drop", "forb_add", "case", "tools")):
        rev.append("토론: 허용·금지·입력 수정")
    gold["notify"] = notify_for(it["trigger"], gold["action"], gold["params"])
    it.update(gold_action=gold, acceptable_actions=acc, forbidden_actions=forb, human_only=list(HUMAN_ONLY),
              revision=rev, origin="draft_v1_revised", field_question=fix.get("ask"))
    it["field_uncertain"] = bool(it.get("field_uncertain")) or bool(fix.get("ask"))
    it.pop("fake_llm_baseline", None)
    return it


# ---------- 생성 문항: 고친 결정 규칙 ----------

def _clock(rng, lo_min: int, hi_min: int) -> tuple[str, int]:
    m = rng.randrange(lo_min, hi_min, 10)
    return f"{m // 60:02d}:{m % 60:02d}", m


def _shift(m: int) -> str:
    return "day" if 7 * 60 <= m < 15 * 60 else ("evening" if m < 23 * 60 else "night")


def _ipc(m: int, day_type: str) -> bool:
    return day_type == "weekday" and 8 * 60 + 30 <= m < 17 * 60 + 30  # 감염관리실 평일 근무 [설계 가정]


def _base(rng, trig, stage, cs, to, *, lo=8 * 60, hi=17 * 60, deadline=None, attempts=0, elapsed=None, drop=()):
    now, m = _clock(rng, lo, hi)
    day = rng.choice(["weekday"] * 4 + ["weekend"])
    case_state = {"now_local": now, "shift": _shift(m), "day_type": day, **cs}
    dl = deadline if deadline is not None else rng.choice([60, 90, 120, 180])
    el = elapsed if elapsed is not None else max(rng.choice([5, 10, 15, 30]), 30 * attempts + 10)
    return ({"exception": {"trigger": trig, "stage": stage, "elapsed_min": el, "deadline_in_min": dl,
                           "attempts": attempts},
             "case_state": case_state, "tool_outputs": to, "allowed_actions": allowed_actions(trig, drop)}, m, day)


def _item(trig, stage, inp, gold, acc, forb, why, situation, difficulty, implemented=False, ask=None):
    gold = {**gold, "notify": notify_for(trig, gold["action"], gold["params"])}
    forb = _uniq(COMMON_FORBIDDEN + forb)
    acc = [a for a in _uniq([gold["action"]] + acc) if a.split(":")[0] not in forb]
    return {"trigger": trig, "exception_code": CODE[trig], "implemented": implemented, "situation": situation,
            "input": inp, "gold_action": gold, "acceptable_actions": acc, "forbidden_actions": forb,
            "human_only": list(HUMAN_ONLY), "rationale_ko": why, "difficulty": difficulty,
            "field_uncertain": bool(ask), "field_question": ask, "status": "draft", "author": "claude-draft",
            "origin": "generated_rule", "revision": []}


def gen_e1(rng):
    stage = rng.choice(["before_contact"] * 3 + ["after_contact", "after_commit"])
    prec = rng.choice(["contact", "contact", "droplet", "airborne"])
    org = {"contact": rng.choice(["VRE", "CRE", "MRSA"]), "droplet": "인플루엔자", "airborne": "활동성 결핵"}[prec]
    if prec == "airborne":
        n = rng.choice([0, 1])  # 음압실 후보 수 = 격리 후보 수 (토론: 후보 수와 음압실 수 일치)
        to = {"isolation_bed_candidates": n, "negative_pressure_free": n,
              "negative_pressure_waiters": rng.choice([0, 1, 2])}
    else:
        n = rng.choice([0, 1, 1, 2, 3])
        to = {"isolation_bed_candidates": n}
    if n:
        to["best_ready_in_min"] = rng.choice([0, 30, 60])
    after = stage != "before_contact"
    cs = {"contacted": after, "committed": stage == "after_commit", "precaution": prec, "organism": org,
          "prev_room_type": "multi"}
    inp, m, day = _base(rng, "late_isolation", stage, cs, to)
    inp["case_state"]["ipc_on_duty"] = _ipc(m, day)
    forb = ["rematch_now"] if after else []
    ask = None
    if n and not after and prec != "airborne":
        gold, acc = {"action": "rematch_now", "params": {}}, ["propose_rematch"]
        why, diff = "연락 전이고 격리 가능 병상이 있다. 규칙 모드로 다시 찾게 하고 병상관리 담당에게 사유를 통지한다.", "easy"
        if org == "CRE":
            acc += ["wait_for_ipc", "escalate:ipc"]
            ask = "CRE 1인실을 자동 재제안이 먼저 잡아도 되는가, 감염관리실 승인(propose_rematch)이 먼저인가"
            diff = "medium"
    elif n:
        gold, acc = {"action": "propose_rematch", "params": {}}, ["escalate"]
        why = ("연락 뒤라 환자에게 알린 병실을 스스로 바꾸지 않는다." if after else
               "음압실은 부족한 자원이라 누구에게 줄지 사람이 정한다.") + " 다시 찾아 추천안을 내고 사람이 승인한다(의견 3)."
        diff = "medium" if after else "hard"
    else:
        gold, acc = {"action": "escalate", "params": {"role": "bed_manager"}}, ["escalate:ipc"]
        why = "격리 가능 병상이 없다. 기다리지 말고 병상관리 담당에게 올린다" + (
            "(음압실이 없어 전원·임시 음압 검토가 필요하다)." if prec == "airborne" else ".")
        diff = "medium"
    when = "연락 전" if not after else ("연락 뒤" if stage == "after_contact" else "확정 뒤")
    kind = {"contact": "접촉", "droplet": "비말", "airborne": "공기"}[prec]
    sit = (f"{inp['case_state']['now_local']}, {when} 사전 매칭 환자에게 {org} 결과와 {kind}주의 오더가 들어왔다. "
           f"격리 가능 병상 후보 {n}개.")
    return _item("late_isolation", stage, inp, gold, acc, forb, why, sit, diff, implemented=True, ask=ask)


def gen_e2(rng):
    stage = rng.choice(["general_cleaning", "general_cleaning", "terminal_cleaning", "bed_prep"])
    att = rng.choice([0, 0, 1, 2])
    n = rng.choice([0, 1, 2])
    gain = rng.choice([1.0, 2.5, 5.0, 10.0, 15.0]) if n else 0.0
    contacted = rng.random() < .3
    due = rng.choice([30, 40, 60, 90])
    p90 = due + rng.choice([20, 30, 60, 90])  # 예외가 성립하려면 준비 P90 > 입실 (토론: P90·입실 비교)
    to = {"ready_p50_min": p90 - 20, "ready_p90_min": p90, "arrival_due_in_min": due, "rematch_candidates": n,
          "rematch_gain": gain, "rematch_gain_min": GAIN_MIN}
    cs = {"contacted": contacted, "cleaning_type": "terminal" if stage == "terminal_cleaning" else "general",
          "waiting_matches": 1}
    forb = []
    if stage == "terminal_cleaning":
        prec = rng.choice(["contact", "droplet", "airborne"])
        cs.update(isolation_discharge=True, precaution=prec)
        if prec == "airborne":
            to["ventilation_wait_min"] = 60  # 결핵 진료지침 1시간 하한 (DECISIONS)
            to["ready_p90_min"] = p90 = max(p90, 60 + 30)
        forb.append("request_bed_prep_first")  # 종말소독 전 침상 정리 우선 금지
    inp, m, day = _base(rng, "cleaning_overdue", stage, cs, to, deadline=due, attempts=att)
    if contacted:
        forb.append("rematch_now")
    first = "request_bed_prep_first" if stage == "bed_prep" else "re_request_cleaning"
    good = n and gain >= GAIN_MIN
    move = "propose_rematch" if contacted else "rematch_now"
    if stage == "terminal_cleaning" and att == 0 and good:
        gold, acc = {"action": move, "params": {}}, ["re_request_cleaning"]
        why, diff = "종말소독·환기 대기는 재촉으로 줄지 않는다. 이득 있는 다른 병상을 찾는다.", "medium"
    elif att == 0:
        gold, acc = {"action": first, "params": {}}, ["wait"] + ([move] if good else [])
        why = ("첫 지연이다. 침상 정리를 먼저 해 달라고 한 번 요청하고" if first == "request_bed_prep_first"
               else "첫 지연이다. 한 번 다시 요청하고") + " 병동에 사유를 통지한다."
        diff = "easy"
    elif good:
        gold, acc = {"action": move, "params": {}}, ["escalate:ward"]
        why = f"이미 {att}번 요청했다. 재매칭 이득 {gain}점이 기준 {GAIN_MIN}점 이상이라 다시 찾는다" + (
            "(연락 뒤라 사람 승인)." if contacted else ".")
        diff = "medium"
    else:
        gold, acc = {"action": "escalate", "params": {"role": "ward"}}, ["escalate:bed_manager"]
        why = f"이미 {att}번 요청했고 이득 있는 다른 병상이 없다. 책임간호사에게 올린다(외주 미화에 직접 올리지 않음)."
        diff = "medium"
    kind = {"terminal_cleaning": "종말소독", "bed_prep": "침상 정리", "general_cleaning": "일반 청소"}[stage]
    sit = (f"{inp['case_state']['now_local']}, {kind}가 늦다. 준비 P90 {p90}분, 다음 환자 도착 {due}분 뒤. "
           f"재요청 {att}번. 다른 후보 {n}개(이득 {gain}).")
    return _item("cleaning_overdue", stage, inp, gold, acc, forb, why, sit, diff)


def gen_e3(rng):
    due = rng.choice([30, 60, 90])
    eta = due + rng.choice([30, 60, 90])  # 트리거 성립: 보호자 도착 > 다음 환자 도착
    iso, aid = rng.random() < .2, rng.random() < .25
    lounge = rng.random() < .6
    clean = rng.choice([40, 50, 60]) + (30 if iso else 0)
    n = rng.choice([0, 1, 2])
    gain = rng.choice([1.0, 5.0, 12.0]) if n else 0.0
    contacted = rng.random() < .5
    to = {"guardian_eta_in_min": eta, "arrival_due_in_min": due, "lounge_available": lounge,
          "ready_p50_min": eta + clean - 10, "ready_p90_min": eta + clean + 20,
          "ready_if_lounge_p90_min": clean + 30, "rematch_candidates": n, "rematch_gain": gain,
          "rematch_gain_min": GAIN_MIN}
    if not lounge:
        to.pop("ready_if_lounge_p90_min")
    cs = {"isolation": iso, "mobility_aid": aid, "incoming_contacted": contacted, "waiting_matches": 1}
    inp, m, day = _base(rng, "guardian_delay_same_day", "before_departure", cs, to, lo=10 * 60, hi=13 * 60,
                        deadline=due)
    forb = (["offer_lounge"] if iso else []) + (["rematch_now"] if contacted else [])
    eligible = lounge and not iso and not aid
    good = n and gain >= GAIN_MIN
    move = "propose_rematch" if contacted else "rematch_now"
    if eligible:
        gold, acc = {"action": "offer_lounge", "params": {}}, ["share_eta"] + ([move] if good else [])
        why, diff = "보호자 도착이 다음 환자 도착보다 늦고 라운지를 쓸 수 있다. 병동 승인(환자 동의)으로 라운지를 제안한다.", "medium"
    elif good:
        gold, acc = {"action": move, "params": {}}, ["share_eta"]
        why = f"라운지를 쓸 수 없고 이득 {gain}점 병상이 있다. 다음 환자를 다시 찾는다" + ("(연락 뒤라 사람 승인)." if contacted else ".")
        diff = "hard" if contacted else "medium"
    else:
        gold, acc = {"action": "share_eta", "params": {"role": "admission_office"}}, []
        why, diff = "라운지·대체 병상이 없다. 보호자 도착과 준비 예상 시각을 원무에 공유한다.", "easy"
    sit = (f"{inp['case_state']['now_local']}, 퇴원 환자 보호자가 {eta}분 뒤 도착 예정. 이 병상을 기다리는 환자는 {due}분 뒤 도착. "
           f"라운지 {'가능' if lounge else '없음'}{', 격리 중' if iso else ''}{', 휠체어 필요' if aid else ''}.")
    return _item("guardian_delay_same_day", "before_departure", inp, gold, acc, forb, why, sit, diff)


def _rematch_kind(rng, trig, stage_choices, extra_cs, sit_fmt, low_gain_ok=True, drop=()):
    """E4·E6·E7 공통: 후보·이득·연락 여부로 정답. 이득 기준 아래 대조 문항을 넣는다(토론: 변별력)."""
    stage = rng.choice(stage_choices)
    n = rng.choice([0, 1, 1, 2])
    gain = rng.choice([1.0, 2.0, 4.0, 8.0, 15.0]) if n else 0.0
    contacted = stage == "after_contact" or (stage == "pending_commit" and rng.random() < .3)
    key = "replan" if trig == "info_changed_pending" else "rematch"
    to = {f"{key}_candidates": n, f"{key}_gain": gain, "rematch_gain_min": GAIN_MIN}
    cs = {"contacted": contacted, **extra_cs(rng)}
    if trig == "discharge_date_changed":
        to["next_ready_p90_min"] = rng.choice([60, 120, 240, 600])
    inp, m, day = _base(rng, trig, stage, cs, to, drop=drop)
    auto = "replan_now" if trig == "info_changed_pending" else "rematch_now"
    forb = [auto] if contacted else []
    good = n and gain >= GAIN_MIN
    if good and not contacted:
        gold, acc = {"action": auto, "params": {}}, ["propose_rematch"]
        why, diff = f"연락 전이고 이득 {gain}점 후보가 있다. 다시 찾고 담당에게 사유를 통지한다.", "easy"
    elif n and contacted:
        gold, acc = {"action": "propose_rematch", "params": {}}, ["escalate"]
        why, diff = "연락 뒤라 스스로 바꾸지 않는다. 다시 찾은 추천안을 사람이 승인한다(의견 3).", "medium"
    elif n:
        gold, acc = {"action": "escalate", "params": {"role": "bed_manager"}}, ["propose_rematch"]
        why, diff = f"후보는 있지만 이득 {gain}점이 기준 {GAIN_MIN}점보다 작다(W-02). 병상관리 담당에게 올린다.", "hard"
    else:
        gold, acc = {"action": "escalate", "params": {"role": "bed_manager"}}, []
        if trig == "discharge_date_changed" and to["next_ready_p90_min"] <= inp["exception"]["deadline_in_min"]:
            acc.append("wait_for_bed")
        why, diff = "다른 후보가 없다. 병상관리 담당에게 올린다.", "medium"
    return _item(trig, stage, inp, gold, acc, forb, why, sit_fmt(inp, n), diff)


def gen_e4(rng):
    return _rematch_kind(rng, "discharge_date_changed", ["before_contact", "after_contact"],
                         lambda r: {"changed_to": r.choice(["tomorrow", "day_after"])},
                         lambda inp, n: f"{inp['case_state']['now_local']}, 매칭된 병상의 퇴원 환자 퇴원일이 바뀌었다. 다른 후보 {n}개.")


def gen_e6(rng):
    def cs(r):
        f = r.choice(["gender", "precaution_added", "department", "single_room_wish"])
        return {"changed_field": f, "correction_confirmed": True}
    return _rematch_kind(rng, "info_changed_pending", ["pending_commit"], cs,
                         lambda inp, n: f"{inp['case_state']['now_local']}, 확정 직전 재확인에서 {inp['case_state']['changed_field']} 정보가 바뀌었다(정정 확인됨). 새 조건 후보 {n}개.")


def gen_e7(rng):
    return _rematch_kind(rng, "ward_objection", ["before_contact", "after_contact"],
                         lambda r: {"ward_notified": True,
                                    "objection_reason": r.choice(["nurse_staffing", "roommate_conflict", "equipment"])},
                         lambda inp, n: f"{inp['case_state']['now_local']}, 병동이 배정에 이의를 냈다({inp['case_state']['objection_reason']}). 다른 후보 {n}개.")


def gen_e5(rng):
    scarce = rng.random() < .35
    other = rng.choice([0, 0, 1, 2])
    p90, dl = rng.choice([45, 90, 180, 360]), rng.choice([120, 240])
    to = {"other_ward_candidates": other, "next_ready_p90_min": p90}
    if scarce:
        to["negative_pressure_free"] = other  # 다른 병동 후보 = 다른 병동 음압실 (토론: 후보와 음압실 수 일치)
    cs = {"need": "negative_pressure" if scarce else "general", "contacted": False}
    inp, m, day = _base(rng, "no_bed", "assignment", cs, to, deadline=dl)
    forb = []
    if other:
        gold, acc = {"action": "propose_rematch", "params": {}}, ["escalate"]
        why, diff = "다른 병동까지 넓혀 다시 찾은 후보가 있다. 진료과 밖 병동이라 추천안을 내고 사람이 승인한다.", "medium"
    elif scarce:
        gold, acc = {"action": "escalate", "params": {"role": "bed_manager"}}, ["escalate:ipc", "request_postpone_contact"]
        why, diff = "음압실이 어디에도 없다. 전원·임시 음압 검토가 필요해 병상관리 담당에게 올린다(감염관리실 함께).", "hard"
        if p90 > dl:
            forb.append("wait_for_next_ready")
    elif p90 <= dl:
        gold, acc = {"action": "share_eta", "params": {"role": "admission_office"}}, ["wait_for_next_ready"]
        why, diff = f"다음 병상 준비 P90({p90}분)이 기한({dl}분) 안이다. 준비 예상을 원무에 공유한다.", "easy"
    else:
        gold, acc = {"action": "request_postpone_contact", "params": {}}, ["escalate"]
        why, diff = "기한 안에 병상이 없다. 원무에 환자 연기 안내를 요청한다(연락은 사람).", "medium"
        forb.append("wait_for_next_ready")  # 마감을 넘는 기다림 (W-01)
    sit = (f"{inp['case_state']['now_local']}, {'음압실' if scarce else '일반'} 병상이 필요한 입원 대기 환자에게 줄 병상이 없다. "
           f"다른 병동 후보 {other}개, 다음 준비 P90 {p90}분, 기한 {dl}분.")
    return _item("no_bed", "assignment", inp, gold, acc, forb, why, sit, diff)


def gen_e8(rng):
    stage = rng.choice(["cleaning_pending", "cleaning_in_progress"])
    att = rng.choice([0, 0, 1])
    due = rng.choice([30, 60, 90])
    gap = rng.choice([15, 30, 45, 90, 120])
    iso = rng.random() < .25
    to = {"promise_due_in_min": due, "ready_p90_min": due + gap}
    cs = {"cleaning_started": stage == "cleaning_in_progress", "isolation_discharge": iso}
    if att:
        cs["previous_action"] = "re_request_cleaning" if stage == "cleaning_pending" else "request_bed_prep_first"
    inp, m, day = _base(rng, "promise_at_risk", stage, cs, to, deadline=due, attempts=att)
    pm = m + due
    inp["case_state"]["promise_at"] = f"{pm // 60:02d}:{pm % 60:02d}"  # 토론: promise_at = 지금 + 남은 분
    forb = ["wait"] + (["request_bed_prep_first"] if iso else [])
    hurry = "re_request_cleaning" if stage == "cleaning_pending" or iso else "request_bed_prep_first"
    if att == 0 and gap <= 45:
        gold, acc = {"action": hurry, "params": {}}, ["request_recontact"]
        why, diff = f"약속까지 {due}분, 준비 P90과 차이 {gap}분. 첫 대응으로 준비를 한 번 서두르고 원무에 통지한다.", "easy"
    else:
        gold, acc = {"action": "request_recontact", "params": {}}, ([hurry] if att == 0 else [])
        why = ("이미 한 번 서둘렀는데도" if att else f"차이가 {gap}분이라 서둘러도") + " 약속 시각을 넘긴다. 환자에게 다시 연락하도록 사람에게 요청한다."
        diff = "medium"
    sit = (f"{inp['case_state']['now_local']}, 환자에게 약속한 입실 시각({inp['case_state']['promise_at']})까지 {due}분인데 "
           f"준비 P90은 {due + gap}분이다{', 격리 퇴실 병상' if iso else ''}.")
    return _item("promise_at_risk", stage, inp, gold, acc, forb, why, sit, diff)


def gen_e9(rng):
    att = rng.choice([0, 0, 1, 2])
    to = {"ready_since_min": rng.choice([20, 40, 60, 90]), "patient_wait_min": rng.choice([10, 30, 60])}
    inp, m, day = _base(rng, "call_up_stalled", "ready_no_callup", {"bed_ready": True}, to, lo=9 * 60, hi=20 * 60,
                        attempts=att)
    window = 14 * 60 + 30 <= m < 15 * 60 + 30 or 22 * 60 + 30 <= m < 23 * 60 + 30
    inp["case_state"]["shift_change_window"] = window  # 토론: 시각과 맞춤
    ask = "인계 시간대에 환자 대기와 병동 수용이 부딪히면 무엇을 먼저 하나" if window else None
    if att == 0:
        gold, acc = {"action": "remind_ward", "params": {}}, ["share_eta"]
        why, diff = "병상이 준비됐는데 병동 호출이 없다. 병동에 한 번 알린다.", "easy"
    else:
        gold, acc = {"action": "escalate", "params": {"role": "ward"}}, ["escalate:bed_manager", "share_eta"]
        why, diff = f"이미 {att}번 알렸다. 같은 알림을 반복하지 않고 병동 상위 담당(수간호사, 없으면 근무조 책임간호사)에게 올린다.", "medium"
    sit = f"{inp['case_state']['now_local']}, 병상 준비 {to['ready_since_min']}분째, 환자 대기 {to['patient_wait_min']}분, 병동 호출 없음. 알림 {att}번."
    return _item("call_up_stalled", "ready_no_callup", inp, gold, acc, [], why, sit, diff, ask=ask)


def gen_e10(rng):
    step = rng.choice(["medication", "billing_review", "billing_payment"])
    owner = {"medication": "pharmacy", "billing_review": "billing", "billing_payment": "billing"}[step]
    att = rng.choice([0, 0, 1])
    reopened = step == "billing_review" and rng.random() < .4
    to = {"step_delay_min": rng.choice([20, 40, 60, 90])}
    cs = {"step": step, "step_owner": owner, "review_reopened": reopened}
    inp, m, day = _base(rng, "sequence_stalled", step, cs, to, attempts=att)
    ask = "심사 재오픈(추가 처방) 뒤 지연은 누구에게 올리나(수납 책임자, 병동, 주치의)" if reopened else None
    if att == 0:
        gold, acc = {"action": "remind_owner", "params": {"step": step}}, ["escalate"]
        why, diff = f"퇴원 순서 {step} 단계가 늦다. 단계 담당({owner})에게 한 번 알리고 기다리는 병동에 통지한다.", "easy"
    else:
        gold, acc = {"action": "escalate", "params": {"role": owner}}, ["escalate:bed_manager", "wait"]
        why, diff = f"이미 알렸는데도 늦다. 역할표의 {owner} 상급자에게 올린다(W-10 전제).", "medium"
    sit = f"{inp['case_state']['now_local']}, 퇴원 순서의 {step} 단계가 {to['step_delay_min']}분 늦다. 알림 {att}번."
    return _item("sequence_stalled", step, inp, gold, acc, [], why, sit, diff, ask=ask)


GEN = [(gen_e1, 40), (gen_e2, 26), (gen_e3, 22), (gen_e4, 16), (gen_e5, 18), (gen_e6, 16), (gen_e7, 14),
       (gen_e8, 16), (gen_e9, 16), (gen_e10, 20)]


def build() -> list[dict]:
    base = [revise(json.loads(x)) for x in SRC.read_text(encoding="utf-8").splitlines() if x.strip()]
    rng = random.Random(SEED)
    new, seen = [], {json.dumps(b["input"], sort_keys=True) for b in base}
    for fn, k in GEN:
        made = 0
        while made < k:
            it = fn(rng)
            key = json.dumps(it["input"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            new.append(it)
            made += 1
    for i, it in enumerate(new, len(base) + 1):
        it["id"] = f"XD-{i:03d}"
    out = base + [{"id": it.pop("id"), **it} for it in new]
    assert len(out) >= TARGET, len(out)
    return out


# ---------- v3: 같은 생성기(E1~E10), 새 시드 (docs/검토/LLM운영_시험설계.md 2.1) ----------

SEED_V3 = 202611101
OUT_V3 = ROOT / "evals" / "model_eval" / "exception_decisions_v3.jsonl"
# v2.1 트리거 비율(E1 56 … E10 23, 합 250)을 300으로 늘린 값
V3_COUNTS = {"E1": 67, "E2": 37, "E3": 32, "E4": 23, "E5": 25, "E6": 23, "E7": 19, "E8": 23, "E9": 23, "E10": 28}


def build_v3() -> list[dict]:
    """생성 문항만 300개. v2.1과 입력이 같은 문항은 뺀다. ID 접두어 XV3-."""
    old = OUT.read_text(encoding="utf-8").splitlines()
    seen = {json.dumps(json.loads(x)["input"], sort_keys=True) for x in old if x.strip()}
    rng = random.Random(SEED_V3)
    out = []
    for fn, _ in GEN:
        code = "E" + fn.__name__.split("_e")[1]
        made = 0
        while made < V3_COUNTS[code]:
            it = fn(rng)
            key = json.dumps(it["input"], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            made += 1
    return [{"id": f"XV3-{i:03d}", **it} for i, it in enumerate(out, 1)]


def main() -> None:
    import sys
    if "--v3" in sys.argv:
        items = build_v3()
        OUT_V3.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")
        print(f"{len(items)} items -> {OUT_V3.relative_to(ROOT)}")
        return
    items = build()
    OUT.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in items) + "\n", encoding="utf-8")
    print(f"{len(items)} items -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
