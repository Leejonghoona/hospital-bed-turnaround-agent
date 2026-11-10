"""MCP 서버 (DECISIONS 2026-11-10 MCP 서버와 에이전트 추적). 읽기 전용, 합성 데이터만, 이름은 가린다."""

from __future__ import annotations

import json

import pytest

from turnaround import mcp_server as ms
from turnaround.ui import views

EXPECTED = {"bed_board", "gateway_precheck", "case_timeline", "admissions_today", "blocked_decisions", "autonomy_table"}


@pytest.fixture(scope="module")
def st():
    return ms.get_state()


@pytest.fixture(scope="module")
def case(st):
    return ms.admissions_today(st)["rows"][0]["case"]


def _names(st) -> set[str]:
    return {str(p["name"]) for p in views._people(st.store.read()).values() if len(str(p.get("name") or "")) >= 2}


def test_tool_catalog_read_only():
    assert {t.name for t in ms.TOOLS} == EXPECTED
    for t in ms.TOOLS:
        assert t.read_only
        assert any("가" <= ch <= "힣" for ch in t.description)  # 한국어 설명
        assert "English:" in t.description  # 영어 설명
    # 상태를 바꾸는 이름은 없다 (확정, 직접 배정, 승인은 게이트웨이와 화면의 사람 확인 등급으로만)
    assert not any(w in t.name for t in ms.TOOLS for w in ("commit", "override", "approve", "assign", "set_"))


def test_admissions_today_masked(st):
    out = ms.admissions_today(st)
    assert out["rows"] and "summary" in out and out["synthetic"] is True
    r = out["rows"][0]
    assert "○" in r["patient"] and "patient_id" not in r
    assert set(r) >= {"case", "stage", "bed", "issues"}


def test_bed_board_compact(st, case):
    out = ms.bed_board(st, case)
    assert out["case"] == case and "○" in out["patient"]
    assert "need" in out and "recommend" in out
    for w in out["wards"]:
        assert all(b["fit"] != "no" for b in w["selectable"])  # 기본은 고를 수 있는 병상만
        assert sum(w["not_selectable"].values()) + len(w["selectable"]) == w["beds"]
    full = ms.bed_board(st, case, include_blocked=True)
    assert len(json.dumps(out, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False))
    assert ms.bed_board(st, "NOPE")["error"]


def test_precheck_dry_run(st, case):
    occupied = next(b for b in st.gw.census)  # 재원 환자가 있는 병상
    seq = st.store.last_seq()
    out = ms.gateway_precheck(st, case, occupied)
    assert out["blocked"] == ["bed_occupied"] and out["fit"] == "no" and out["dry_run"] is True
    assert st.store.last_seq() == seq  # 사전 검사는 이벤트를 남기지 않는다
    assert ms.gateway_precheck(st, case, "NO-BED")["blocked"] == ["unknown_case_or_bed"]


def test_case_timeline(st, case):
    out = ms.case_timeline(st, case)
    assert "○" in out["who"] and out["rows"]
    assert {"time", "who", "what"} <= set(out["rows"][0])
    assert ms.case_timeline(st, "NOPE")["error"]


def test_blocked_and_autonomy(st):
    b = ms.blocked_decisions(st)
    assert isinstance(b["items"], list) and b["count"] == len(b["items"])
    a = ms.autonomy_table(st)
    assert a["rows"] and {"unit", "mode", "recommendation"} <= set(a["rows"][0])


def test_no_full_names_in_any_output(st, case):
    names = _names(st)
    bed = next(iter(st.gw.room_of))
    outs = [ms.admissions_today(st), ms.bed_board(st, case, include_blocked=True), ms.gateway_precheck(st, case, bed),
            ms.case_timeline(st, case), ms.blocked_decisions(st), ms.autonomy_table(st)]
    text = json.dumps(outs, ensure_ascii=False)
    leaked = [n for n in names if n in text]
    assert not leaked, leaked[:5]


def test_mcp_end_to_end_in_memory(st, case):
    pytest.importorskip("mcp")
    import anyio
    res = anyio.run(ms.selftest, case)
    assert set(res["tools"]) == EXPECTED
    assert all(res["read_only"].values())
    body = json.loads(res["call"])
    assert body["case"] == case and "○" in body["who"]
