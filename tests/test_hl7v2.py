"""HL7 v2 ADT 외부 형식 평가 경로 (DECISIONS 2026-11-12): 파서, 함정별 처리, 지표, 순서 뒤바뀜 검출."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest

from turnaround.events import EventType
from turnaround.evaluation.hl7_eval import score
from turnaround.ontology.hl7_adapter import HL7Config, parse_ts, to_events
from turnaround.ontology.hl7v2 import HL7ParseError, parse_batch, parse_message, split_batch

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "evals" / "hl7"
CFG = HL7Config(ward_codes={"71W": "W71", "72W": "W72"})
PAD = "|" * 25


def _msg(mid, trig, loc="71W^01^1", sex="M", visit="V1", admit="202611030900", disch="", prior="", evn6="",
         msh7="202611070000", extra=(), sep="|", enc="^~\\&"):
    lines = [f"MSH{sep}{enc}{sep}H{sep}F{sep}W{sep}W{sep}{msh7}{sep}{sep}ADT^{trig}^ADT_A01{sep}{mid}{sep}P{sep}2.5",
             f"EVN|{trig}|{msh7}||||{evn6}",
             f"PID|1||PX1^^^SYN^MR||SYN^ONE||19600101|{sex}",
             f"PV1|1|I|{loc}|||{prior}||||MED|||||||||{visit}{PAD}{admit}|{disch}", *extra]
    if sep != "|":
        lines = [lines[0]] + [ln.replace("|", sep) for ln in lines[1:]]
    return "\r".join(lines)


def _conv(*texts, cfg=CFG):
    return to_events([parse_message(t, index=i) for i, t in enumerate(texts)], cfg)


# ---------------- 파서 ----------------

def test_msh_numbering_and_components():
    m = parse_message(_msg("M1", "A01"))
    assert m.field("MSH", 1) == "|" and m.field("MSH", 2) == "^~\\&"
    assert m.trigger == "A01" and m.message_type == "ADT" and m.control_id == "M1"
    assert m.get("PV1-3.1") == "71W" and m.get("PV1-3.2") == "01" and m.get("PV1-3.3") == "1"
    assert m.get("PV1-19") == "V1" and m.get("PV1-44") == "202611030900" and m.get("PID-8") == "M"


def test_separators_read_from_msh():
    """구분자를 고정하지 않는다: MSH-1이 '#', MSH-2가 '*~\\&'여도 같은 값을 읽는다."""
    txt = _msg("M2", "A01", sep="#", enc="*~\\&").replace("^", "*")
    m = parse_message(txt)
    assert m.enc.field == "#" and m.enc.component == "*"
    assert m.get("PV1-3.1") == "71W" and m.trigger == "A01" and m.get("PID-3.1") == "PX1"


def test_escape_sequences_and_line_endings():
    txt = _msg("M3", "A01", extra=["OBX|1|ST|NOTE^Note^L||A\\F\\B\\S\\C\\E\\D"])
    for eol in ("\r", "\n", "\r\n"):
        m = parse_message(txt.replace("\r", eol))
        assert m.field("OBX", 5) == "A|B^C\\D"


def test_pid3_prefers_mr_repetition():
    txt = _msg("M4", "A01").replace("PX1^^^SYN^MR", "999^^^GOV^NI~PX7^^^SYN^MR")
    c = _conv(txt)
    assert c.events[0].payload["patient_id"] == "PX7"


def test_batch_split_and_broken_message():
    good = _msg("M5", "A01")
    text = "\r\n".join([good, "MSH|", "EVN|A01", good.replace("M5", "M6")])
    assert len(split_batch(text)) == 3
    msgs, errors = parse_batch(text)
    assert [m.control_id for m in msgs] == ["M5", "M6"] and len(errors) == 1
    with pytest.raises(HL7ParseError):
        parse_message("PID|1||X")


# ---------------- 함정별 처리 ----------------

@pytest.mark.parametrize("raw,want", [
    ("202611030910", datetime(2026, 11, 3, 9, 10)),
    ("20261103093015", datetime(2026, 11, 3, 9, 30, 15)),
    ("20261103093015.25+0900", datetime(2026, 11, 3, 9, 30, 15)),
    ("202611030010+0000", datetime(2026, 11, 3, 9, 10)),  # UTC → 한국 시각
])
def test_ts_formats(raw, want):
    assert parse_ts(raw) == (want, "")


def test_ts_date_only_is_held_and_asked():
    assert parse_ts("20261104")[0] is None
    c = _conv(_msg("M7", "A01", admit="20261104"))
    assert not c.events and c.held[0]["msg"] == "M7"
    assert any(q.id == "fmt:time:M7" for q in c.questions)


@pytest.mark.parametrize("sex,gender,asked", [("M", "M", False), ("F", "F", False), ("U", None, True),
                                              ("O", None, True), ("A", None, True), ("X", None, True)])
def test_gender_codes(sex, gender, asked):
    c = _conv(_msg("M8", "A01", sex=sex))
    assert c.events[0].payload["gender"] == gender
    assert any(q.id == f"code:patient.gender:{sex}" for q in c.questions) is asked


def test_unknown_ward_held_and_question_deduped():
    c = _conv(_msg("M9", "A01", loc="ICU2^01^1"), _msg("M10", "A03", loc="ICU2^01^1", disch="202611050900"))
    assert not c.events and [h["msg"] for h in c.held] == ["M9", "M10"]
    assert [q.id for q in c.questions] == ["code:ward:ICU2"]


def test_transfer_then_discharge():
    c = _conv(_msg("A", "A01", loc="71W^03^1"),
              _msg("B", "A02", loc="72W^05^2", prior="71W^03^1", evn6="202611041400"),
              _msg("C", "A03", loc="72W^05^2", disch="202611061000"))
    types = [e.type for e in sorted(c.events, key=lambda e: e.occurred_at)]
    assert types == [EventType.PATIENT_ARRIVED, EventType.ROOM_CHANGE_COMPLETED, EventType.PATIENT_DEPARTED]
    tr = next(e for e in c.events if e.type is EventType.ROOM_CHANGE_COMPLETED)
    assert tr.payload["from_bed_id"] == "W71-R03-B1" and tr.payload["bed_id"] == "W72-R05-B2"
    assert not c.questions and not c.order_violations


def test_discharge_from_wrong_bed_after_transfer_is_asked():
    c = _conv(_msg("A", "A01", loc="71W^03^1"),
              _msg("B", "A02", loc="72W^05^2", prior="71W^03^1", evn6="202611041400"),
              _msg("C", "A03", loc="71W^03^1", disch="202611061000"))
    assert [q.id for q in c.questions] == ["loc:C"]


def test_isolation_unknown_position_goes_to_human_then_answer_reads_it():
    obx = "OBX|1|CE|ISO^Isolation^L||CONTACT^Contact^L||||||F"
    c = _conv(_msg("A", "A01", extra=[obx]), _msg("B", "A01", visit="V2", extra=[obx]))
    assert [q.id for q in c.questions] == ["pos:OBX:ISO"]  # 같은 위치는 한 번만 묻는다
    assert all(e.payload["_unread_positions"] == ["OBX:ISO"] and "precautions" not in e.payload for e in c.events)
    answered = HL7Config(ward_codes=CFG.ward_codes,
                         positions={"OBX:ISO": {"key": "patient.precautions", "field": "OBX-5.1"}},
                         codes={"patient.precautions": {"CONTACT": "contact"}})
    c2 = _conv(_msg("A", "A01", extra=[obx]), cfg=answered)
    assert c2.events[0].payload["precautions"] == ["contact"] and not c2.questions


def test_answered_position_with_unknown_code_still_asks():
    cfg = HL7Config(ward_codes=CFG.ward_codes, positions={"OBX:ISO": {"key": "patient.precautions", "field": "OBX-5.1"}})
    c = _conv(_msg("A", "A01", extra=["OBX|1|CE|ISO^Isolation^L||KPC^?^L"]), cfg=cfg)
    assert [q.id for q in c.questions] == ["code:patient.precautions:KPC"]
    assert "precautions" not in c.events[0].payload


def test_z_segment_is_unknown_position():
    c = _conv(_msg("A", "A01", extra=["ZIS|1|CRE"]))
    assert [q.id for q in c.questions] == ["pos:ZIS"]


def test_clock_skew_beyond_tolerance_is_asked():
    c = _conv(_msg("A", "A01", admit="202611031000", msh7="202611030900"))
    assert not c.events and any(q.id == "fmt:clock:A" for q in c.questions)


# ---------------- 순서 ----------------

def test_late_arriving_transfer_is_detected_and_kept():
    """A03 뒤에 도착한 A02: 발생 시각이 맞으면 넣되 순서 위반(늦은 도착)으로 센다. 인지 시각은 MSH-7."""
    c = _conv(_msg("A", "A01", loc="72W^03^1", msh7="202611031201", admit="202611031200"),
              _msg("C", "A03", loc="72W^04^1", disch="202611051100", msh7="202611051106"),
              _msg("B", "A02", loc="72W^04^1", prior="72W^03^1", evn6="202611041000", msh7="202611051130"))
    assert [(v["msg"], v["kind"]) for v in c.order_violations] == [("B", "late_arrival")]
    b = next(e for e in c.events if e.event_id == "hl7:B")
    assert b.occurred_at == datetime(2026, 11, 4, 10) and b.observed_at == datetime(2026, 11, 5, 11, 30)
    assert not c.questions  # 발생 시각 순으로 이으면 위치가 맞다


def test_discharge_before_admission_is_held():
    c = _conv(_msg("A", "A01", admit="202611041300"), _msg("C", "A03", disch="202611031300"))
    assert [(v["msg"], v["kind"]) for v in c.order_violations] == [("C", "time_contradiction")]
    assert [e.event_id for e in c.events] == ["hl7:A"] and "order:C" in {q.id for q in c.questions}


# ---------------- 지표 ----------------

def _sample():
    msgs, errors = parse_batch((DATA / "sample_adt.hl7").read_text(encoding="utf-8"))
    return msgs, errors, json.loads((DATA / "sample_adt_expected.json").read_text(encoding="utf-8"))


def test_metrics_count_wrong_links_and_misses():
    msgs, _, exp = _sample()
    conv = to_events(msgs, CFG)
    base = score(conv, exp, len(msgs))
    bad = json.loads(json.dumps(exp))
    bad["messages"]["MSG0001"]["bed_id"] = "W71-R09-B9"  # 정답을 바꾸면 틀린 연결로 센다
    bad["messages"]["MSG0006"]["type"] = "patient_arrived"  # 넘긴 메시지를 사건으로 기대하면 놓침
    bad["messages"]["MSG0002"]["type"] = None  # 넘겨야 할 메시지를 사건으로 만들면 틀린 연결
    r = score(conv, bad, len(msgs))
    assert r["wrong_links"] == base["wrong_links"] + 2 and r["missed"] == base["missed"] + 1


def test_sample_covers_requested_traps():
    msgs, errors, exp = _sample()
    assert not errors and 10 <= len({m.field("PID", 3, 1) for m in msgs}) <= 20
    sexes = {m.field("PID", 8) for m in msgs}
    assert {"M", "F", "U", "O"} <= sexes
    times = {len(m.get("PV1-44")) for m in msgs if m.get("PV1-44")}
    assert {12, 14} <= times
    assert any(m.get("PV1-3.1") not in CFG.ward_codes for m in msgs)
    assert sum(1 for x in exp["messages"].values() if x.get("order_violation")) >= 1
    assert {m.trigger for m in msgs} >= {"A01", "A02", "A03", "A08"}


def test_sample_not_built_from_generator_vocabulary():
    """순환 구조를 끊는 평가다: 표본에 생성기 내보내기 열 이름이 없어야 한다."""
    from turnaround.sim.exports import FORMATS, HOLDOUT_FORMATS, gold_mapping
    cols = set()
    for f in FORMATS + HOLDOUT_FORMATS:
        m = gold_mapping(f)
        cols |= set(m.bed_parts) | {c for t in m.tables.values() for c in (*t.columns, *t.event_columns) if c}
    toks = set(re.split(r"[|^~&\\\r\n]+", (DATA / "sample_adt.hl7").read_text(encoding="utf-8")))
    assert not cols & toks


def test_saved_result_matches_rerun():
    from hl7_eval import OUT, run
    res = run()
    saved = json.loads(OUT.read_text(encoding="utf-8"))
    for stage in ("before_answers", "after_answers"):
        strip = lambda r: {k: v for k, v in r.items() if k != "details"}
        assert strip(saved[stage]) == strip(res[stage])
    r = res["before_answers"]
    assert r["order_violations_detected"] == r["order_violations_expected"] and r["order_violations_false"] == 0
    assert r["store_appended"] == r["events_converted"] and r["store_appended_on_replay"] == 0
    assert res["after_answers"]["safety_values_unread"] == 0 and res["after_answers"]["questions"] < r["questions"]
