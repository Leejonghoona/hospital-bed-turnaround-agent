"""WardFlow MCP 서버 (DECISIONS 2026-11-10 MCP 서버와 에이전트 추적).

MCP 클라이언트(Claude Desktop, Claude Code 등)가 WardFlow의 조회 도구를 쓰게 한다. stdio로 돈다.

    pip install -e ".[mcp,api]"
    python -m turnaround.mcp_server

- 읽기 전용이다. 확정, 직접 배정, 승인처럼 상태를 바꾸는 도구는 없다. 실행은 실행 게이트웨이가 사람 확인 등급을 거쳐서만 한다.
  `gateway_precheck`는 게이트웨이의 사전 검사(`override_precheck`)를 그대로 부르고 이벤트를 남기지 않는다.
- 합성 데이터만 낸다. 시연용 합성 병원(`turnaround.api.app.build_hospital`, 11:45)을 첫 호출 때 한 번 만든다(약 6초).
- 환자 이름은 화면처럼 가린다(이○태). 합성 등록번호와 연락처는 응답에서 뺀다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

_STATE = None
_DROP = {"patient_id", "phone", "guardian_phone"}


def get_state():
    """시연용 합성 병원(화면 API와 같은 방법). 처음 한 번만 만든다."""
    global _STATE
    if _STATE is None:
        from turnaround.api.app import build_hospital
        _STATE = build_hospital()
    return _STATE


# ---------- 이름 가리기 ----------

def _mask(name: str) -> str:
    return (name[0] + "○" + name[-1]) if len(name) >= 3 else (name[:1] + "○" if name else "환자")


def _drop(obj):
    if isinstance(obj, dict):
        return {k: _drop(v) for k, v in obj.items() if k not in _DROP}
    if isinstance(obj, list):
        return [_drop(x) for x in obj]
    return obj


def safe(st, obj) -> dict:
    """응답 전체에서 환자 이름을 가리고 등록번호·연락처를 뺀다(합성 데이터라도 화면과 같은 기준)."""
    from turnaround.ui import views
    text = json.dumps(_drop(obj), ensure_ascii=False, default=str)
    names = {str(p.get("name") or "") for p in views._people(st.store.read()).values()}
    for n in sorted((n for n in names if len(n) >= 2), key=len, reverse=True):
        text = text.replace(n, _mask(n))
    out = json.loads(text)
    if isinstance(out, dict):
        out.setdefault("synthetic", True)
    return out


# ---------- 도구 (상태 st를 받는 순수 함수, 시험은 이것을 직접 부른다) ----------

def admissions_today(st) -> dict:
    from turnaround.api.worklist import admissions_today as _adm
    a = _adm(st)
    rows = [{k: r.get(k) for k in ("case", "patient", "age", "sex", "department", "checkin", "checkin_note", "bed",
                                    "bed_state", "by", "contact", "stage")}
            | {"issues": [{k: i.get(k) for k in ("label", "late", "due")} for i in r.get("issues") or []]}
            for r in a["rows"]]
    return safe(st, {"now": a.get("now"), "summary": a.get("summary"), "rows": rows})


def bed_board(st, case: str, include_blocked: bool = False) -> dict:
    from turnaround.api.beds import board
    b = board(st, case)
    if b is None:
        return {"error": f"없는 사례: {case} (unknown case)"}
    out = {k: b[k] for k in ("case", "patient", "need", "current", "recommend")}
    if include_blocked:
        out["wards"] = b["wards"]
        return safe(st, out)
    wards = []
    for w in b["wards"]:
        sel, nots, n = [], {}, 0
        for r in w["rooms"]:
            for bed in r["beds"]:
                n += 1
                if bed["fit"] == "no":
                    nots[bed["why"] or "선택 불가"] = nots.get(bed["why"] or "선택 불가", 0) + 1
                else:
                    sel.append({"bed": bed["id"], "room": r["label"], "grade": r["grade"], "sex": r["sex"],
                                "status": bed["status"], "fit": bed["fit"], "why": bed["why"]})
        wards.append({"id": w["id"], "name": w["name"], "specialty": w["specialty"], "beds": n,
                      "selectable": sel, "not_selectable": dict(sorted(nots.items(), key=lambda x: -x[1]))})
    out["wards"] = wards
    return safe(st, out)


def gateway_precheck(st, case: str, bed_id: str) -> dict:
    from turnaround.api.beds import BLOCK_KO, VIOLATION_KO, _fit
    pre = st.gw.override_precheck(case, bed_id)
    fit, why = _fit(pre)
    return safe(st, {"case": case, "bed_id": bed_id, "dry_run": True, **pre, "fit": fit, "why": why,
                     "labels": {x: BLOCK_KO.get(x) or VIOLATION_KO.get(x, x) for x in pre["blocked"] + pre["violations"]}})


def case_timeline(st, case: str, limit: int = 40) -> dict:
    from turnaround.ui import views
    t = views.case_timeline(st, case)
    if not t["rows"]:
        return {"error": f"없는 사례: {case} (unknown case)"}
    t = dict(t, rows=[{k: r[k] for k in ("time", "who", "what", "detail") if k in r} | ({"repeat": r["repeat"]} if r.get("repeat") else {})
                      for r in t["rows"]][-limit:])
    return safe(st, {"case": case, **t})


def blocked_decisions(st, limit: int = 20) -> dict:
    from turnaround.ui import views
    items = views.blocked_decisions(st, limit=limit)
    return safe(st, {"count": len(items), "items": items})


def autonomy_table(st) -> dict:
    from turnaround.api import ai_ops
    return safe(st, {"rows": ai_ops.autonomy(st), "rule": ai_ops.rule_text(st)})


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    fn: Callable
    read_only: bool = True


TOOLS = [
    ToolSpec("admissions_today",
             "오늘 입원 환자 전체 요약(병상, 배정 주체, 연락, 단계, 확인할 일). 사례 ID(case)를 여기서 얻는다. "
             "English: Today's admissions with bed, who assigned it, contact status, stage and open issues; use it to get case IDs.",
             admissions_today),
    ToolSpec("bed_board",
             "환자 한 명(case)의 병상 현황판: 필요 조건, 추천 병상, 병동별 고를 수 있는 병상과 못 고르는 이유 개수. "
             "English: Bed board for one admission case: needs, recommended beds, selectable beds per ward and counts of blocked reasons. "
             "include_blocked=true returns every bed.",
             bed_board),
    ToolSpec("gateway_precheck",
             "실행 게이트웨이의 사전 검사(기록 없음): 이 환자를 이 병상에 둘 수 있는지, 막는 사실, 안전 위반과 예외 등급. "
             "English: Dry-run of the execution gateway check for (case, bed_id): physical blocks, safety violations and exception tier. Records nothing.",
             gateway_precheck),
    ToolSpec("case_timeline",
             "환자 한 명의 경과: 누가(병원 전산, 에이전트, 안전 검사, 사람) 언제 무엇을 했는지. "
             "English: Decision trail for one case: who (HIS, agent, gateway, human) did what and when.",
             case_timeline),
    ToolSpec("blocked_decisions",
             "오늘 게이트웨이가 막은 에이전트 판단과 사유. "
             "English: Agent decisions the gateway rejected today, with reasons.",
             blocked_decisions),
    ToolSpec("autonomy_table",
             "판단 유형별 자율 범위(에이전트 확정 / 사람 확인), 근거 건수, 위임·되돌림 권고. "
             "English: Autonomy table per decision type (agent commits vs human confirms), evidence counts and promote/demote recommendation.",
             autonomy_table),
]


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_server(state: Callable = get_state):
    """공식 MCP 파이썬 SDK 서버. 2.x(MCPServer)와 1.x(FastMCP)를 모두 받는다."""
    try:
        from mcp.server.mcpserver import MCPServer as Server
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as Server
    from mcp.types import ToolAnnotations
    srv = Server("wardflow", instructions=(
        "WardFlow 병상 턴어라운드 관제 데모(합성 데이터 전용, 읽기 전용). 먼저 admissions_today로 사례 ID를 얻는다. "
        "Synthetic demo hospital only; read-only tools. Start with admissions_today to get case IDs."))
    ro = ToolAnnotations.model_validate({"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True,
                                         "openWorldHint": False})
    spec = {t.name: t for t in TOOLS}

    def reg(fn):
        t = spec[fn.__name__]
        srv.add_tool(fn, name=t.name, description=t.description, annotations=ro)

    def admissions_today() -> str:
        return _dump(spec["admissions_today"].fn(state()))

    def bed_board(case: str, include_blocked: bool = False) -> str:
        return _dump(spec["bed_board"].fn(state(), case, include_blocked))

    def gateway_precheck(case: str, bed_id: str) -> str:
        return _dump(spec["gateway_precheck"].fn(state(), case, bed_id))

    def case_timeline(case: str, limit: int = 40) -> str:
        return _dump(spec["case_timeline"].fn(state(), case, limit))

    def blocked_decisions(limit: int = 20) -> str:
        return _dump(spec["blocked_decisions"].fn(state(), limit))

    def autonomy_table() -> str:
        return _dump(spec["autonomy_table"].fn(state()))

    for f in (admissions_today, bed_board, gateway_precheck, case_timeline, blocked_decisions, autonomy_table):
        reg(f)
    return srv


async def selftest(case: str) -> dict:
    """메모리 안 클라이언트로 list_tools·call_tool을 한 번 돈다(시험용, 네트워크·프로세스 없음)."""
    srv = build_server()
    try:
        from mcp.client import Client
        cm = Client(srv)
    except ImportError:  # mcp 1.x
        from mcp.shared.memory import create_connected_server_and_client_session
        cm = create_connected_server_and_client_session(srv._mcp_server)
    async with cm as c:
        tools = (await c.list_tools()).tools
        res = await c.call_tool("case_timeline", {"case": case, "limit": 5})
    ro = {t.name: bool(t.annotations and getattr(t.annotations, "read_only_hint", getattr(t.annotations, "readOnlyHint", False)))
          for t in tools}
    return {"tools": [t.name for t in tools], "read_only": ro, "call": res.content[0].text}


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
