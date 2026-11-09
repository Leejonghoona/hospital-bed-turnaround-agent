"""화면 디자인 시스템 (4단계 UI 개편). 색·글자·간격 토큰과 HTML 컴포넌트. Streamlit은 상태와 버튼만 맡는다.

원칙: 한 화면 한 목적, 맨 위에 "지금 할 일", 상태는 색과 글자를 함께(색만으로 구분하지 않음), 숫자에는 뜻을 붙인다.
"""

from __future__ import annotations

from html import escape

TOKENS = {
    "blue": "#1B64DA", "blue_bg": "#E8F3FF", "ink": "#191F28", "sub": "#4E5968", "mute": "#5F6B7A",
    "line": "#E5E8EB", "bg": "#F7F8FA", "card": "#FFFFFF", "green": "#067A4B", "green_bg": "#E6F8EF",
    "amber": "#F59F00", "amber_bg": "#FFF4DB", "red": "#C8102E", "red_bg": "#FFEBEE", "violet": "#5B32D6",
    "violet_bg": "#F1ECFF", "gray_bg": "#F2F4F6",
}

import os as _os

# 폐쇄망 병원에서는 외부 글꼴을 부르지 않는다 (TURNAROUND_OFFLINE=1이면 시스템 글꼴, 2회차 CIO 검토)
_FONT = "" if _os.environ.get("TURNAROUND_OFFLINE") == "1" else \
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">'

CSS = """
%(font_link)s
<style>
html, body, [class*="css"], .stApp, button, input, select, textarea { font-family: Pretendard, -apple-system, sans-serif !important; }
.stApp { background: %(bg)s; }
header[data-testid="stHeader"] { display: none; }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] { display: none !important; }
.block-container { padding-top: 0 !important; padding-bottom: 48px; max-width: 1360px; }
section[data-testid="stSidebar"] { background: #fff; border-right: 1px solid %(line)s; }
section[data-testid="stSidebar"] .block-container { padding-top: 24px !important; }
div[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 16px; }
.stButton > button { border-radius: 10px; font-weight: 700; border: 1px solid %(line)s; padding: 6px 14px; }
.stButton > button[kind="primary"] { background: %(blue)s; border-color: %(blue)s; }
.wl-top { display:flex; align-items:center; justify-content:space-between; background:#fff; border-bottom:1px solid %(line)s;
          margin: 0 -9999px 20px; padding: 14px 9999px; }
.wl-brand { display:flex; align-items:center; gap:10px; font-weight:800; font-size:19px; color:%(ink)s; }
.wl-logo { width:28px; height:28px; border-radius:8px; background:%(blue)s; color:#fff; display:flex; align-items:center;
           justify-content:center; font-size:15px; }
.wl-meta { display:flex; gap:18px; color:%(sub)s; font-size:14px; align-items:center; }
.wl-badge { background:%(gray_bg)s; color:%(sub)s; border-radius:999px; padding:4px 10px; font-size:13px; font-weight:700; }
.wl-page-h { display:flex; align-items:flex-end; justify-content:space-between; margin: 4px 0 16px; }
.wl-page-h h2 { font-size:26px; font-weight:800; letter-spacing:-0.5px; margin:0; color:%(ink)s; }
.wl-page-h p { margin:4px 0 0; color:%(sub)s; font-size:14px; }
.wl-kpis { display:grid; gap:12px; margin-bottom: 18px; }
.wl-kpi { background:#fff; border:1px solid %(line)s; border-radius:16px; padding:16px 18px; }
.wl-kpi .l { color:%(sub)s; font-size:13px; font-weight:600; }
.wl-kpi .v { font-size:28px; font-weight:800; color:%(ink)s; margin-top:4px; letter-spacing:-0.5px; }
.wl-kpi .h { color:%(mute)s; font-size:13px; margin-top:2px; }
.wl-kpi.accent .v { color:%(blue)s; }
.wl-kpi.warn .v { color:%(red)s; }
.wl-sec { display:flex; align-items:center; gap:8px; margin: 22px 0 10px; }
.wl-sec h3 { font-size:17px; font-weight:800; margin:0; color:%(ink)s; }
.wl-sec .n { background:%(blue)s; color:#fff; border-radius:999px; font-size:13px; font-weight:800; padding:2px 8px; }
.wl-sec .d { color:%(mute)s; font-size:13px; }
.wl-card { background:#fff; border:1px solid %(line)s; border-radius:16px; padding:16px 18px; margin-bottom:10px; }
.wl-card.urgent { border-left: 4px solid %(red)s; }
.wl-card.todo { border-left: 4px solid %(blue)s; }
.wl-card .row1 { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
.wl-card .ttl { font-weight:800; font-size:16px; color:%(ink)s; }
.wl-card .sub { color:%(sub)s; font-size:14px; margin-top:6px; line-height:1.55; }
.wl-card .facts { display:flex; gap:6px; flex-wrap:wrap; margin-top:10px; }
.wl-chip { display:inline-flex; align-items:center; gap:4px; border-radius:8px; padding:3px 8px; font-size:13px; font-weight:700; }
.c-blue { background:%(blue_bg)s; color:%(blue)s; } .c-green { background:%(green_bg)s; color:%(green)s; }
.c-amber { background:%(amber_bg)s; color:#8A5300; } .c-red { background:%(red_bg)s; color:%(red)s; }
.c-gray { background:%(gray_bg)s; color:%(sub)s; } .c-violet { background:%(violet_bg)s; color:%(violet)s; }
.wl-note { color:%(mute)s; font-size:13px; }
.wl-empty { background:#fff; border:1px dashed %(line)s; border-radius:16px; padding:22px; color:%(mute)s; text-align:center; font-size:14px; }
.wl-board { display:grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap:10px; }
.wl-bed { border-radius:14px; padding:10px 12px; border:1px solid %(line)s; background:#fff; min-height:86px; }
.wl-bed .id { font-size:13px; color:%(mute)s; font-weight:700; display:flex; justify-content:space-between; }
.wl-bed .st { font-weight:800; font-size:14px; margin-top:6px; }
.wl-bed .who { font-size:13px; color:%(sub)s; margin-top:3px; }
.wl-bed.s-occupied { background:%(gray_bg)s; } .wl-bed.s-occupied .st { color:%(sub)s; }
.wl-bed.s-discharging { background:%(amber_bg)s; border-color:#FFE2A8; } .wl-bed.s-discharging .st { color:#8A5300; }
.wl-bed.s-dirty { background:#FFF0E6; border-color:#FFD3B5; } .wl-bed.s-dirty .st { color:#A3360A; }
.wl-bed.s-cleaning { background:%(violet_bg)s; border-color:#DDD2FF; } .wl-bed.s-cleaning .st { color:%(violet)s; }
.wl-bed.s-ready, .wl-bed.s-empty { background:%(green_bg)s; border-color:#BDEBD4; } .wl-bed.s-ready .st, .wl-bed.s-empty .st { color:%(green)s; }
.wl-bed.s-arrived { background:%(blue_bg)s; border-color:#C9E2FF; } .wl-bed.s-arrived .st { color:%(blue)s; }
.wl-legend { display:flex; gap:14px; flex-wrap:wrap; color:%(sub)s; font-size:13px; margin: 6px 0 10px; }
.wl-legend i { display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
.wl-kanban { display:grid; grid-template-columns: repeat(4, 1fr); gap:12px; }
.wl-col { background:%(gray_bg)s; border-radius:16px; padding:12px; min-height:140px; }
.wl-col h4 { margin:2px 4px 10px; font-size:14px; font-weight:800; color:%(sub)s; display:flex; justify-content:space-between; gap:8px; }
.wl-mini { background:#fff; border-radius:12px; padding:10px 12px; margin-bottom:8px; border:1px solid %(line)s; font-size:13px; }
.wl-mini b { font-size:14px; }
.wl-steps { display:flex; gap:6px; align-items:center; flex-wrap:wrap; margin: 4px 0 14px; }
.wl-step { border-radius:999px; padding:6px 12px; font-size:13px; font-weight:700; background:%(gray_bg)s; color:%(mute)s; }
.wl-step.done { background:%(blue_bg)s; color:%(blue)s; } .wl-step.on { background:%(blue)s; color:#fff; }
.wl-guide { background:%(blue_bg)s; color:%(ink)s; border-radius:14px; padding:12px 16px; margin-bottom:18px;
            border-left:4px solid %(blue)s; }
.wl-guide .k { color:%(blue)s; font-size:13px; font-weight:800; }
.wl-guide .t { font-size:14px; font-weight:700; margin-top:2px; line-height:1.5; }
.wl-guide ul { margin:6px 0 0 18px; padding:0; color:%(sub)s; font-size:13px; line-height:1.6; }
.wl-check { display:flex; gap:10px; align-items:flex-start; padding:10px 0; border-bottom:1px solid %(line)s; }
.wl-check:last-child { border-bottom:0; }
.wl-check .ic { width:22px; height:22px; border-radius:999px; display:flex; align-items:center; justify-content:center; font-size:13px; font-weight:800; flex:none; }
.wl-check .tt { font-weight:700; font-size:14px; } .wl-check .dd { color:%(sub)s; font-size:13px; margin-top:2px; }
.wl-table { width:100%%; border-collapse:collapse; font-size:13px; background:#fff; border-radius:12px; overflow:hidden; }
.wl-table th { text-align:left; color:%(mute)s; font-weight:700; padding:10px 12px; border-bottom:1px solid %(line)s; background:#FAFBFC; }
.wl-table td { padding:10px 12px; border-bottom:1px solid %(line)s; color:%(ink)s; }
</style>
<script>try{(window.parent||window).document.documentElement.lang="ko"}catch(e){}</script>
""" % {**TOKENS, "font_link": _FONT}


def e(x) -> str:
    return escape("" if x is None else str(x))


def chip(text: str, tone: str = "gray") -> str:
    return f'<span class="wl-chip c-{tone}">{e(text)}</span>'


def topbar(subtitle: str, now: str | None, mode: str) -> str:
    return (f'<div class="wl-top"><div class="wl-brand"><div class="wl-logo">병</div>병실관리AI'
            f'<span class="wl-badge">합성 데이터 데모</span></div><div class="wl-meta"><span>{e(subtitle)}</span>'
            + (f'<span><b>{e(now)}</b> 기준</span>' if now else "") + f'<span class="wl-badge">{e(mode)}</span></div></div>')


def footnote(text: str) -> str:
    return f'<div style="color:#5F6B7A;font-size:13px;margin-top:18px">{e(text)}</div>'


def page_header(title: str, desc: str) -> str:
    return f'<div class="wl-page-h"><div><h2>{e(title)}</h2><p>{e(desc)}</p></div></div>'


def kpis(items: list[tuple]) -> str:
    """(라벨, 값, 설명, 강조: '' | accent | warn)"""
    def _zero(v):  # 0은 강조하지 않는다(강조색은 '봐야 할 숫자'에만)
        return str(v).strip() in ("0", "0명", "0건", "0개")
    items = [(l, v, h, "" if t == "accent" and _zero(v) else t) for l, v, h, t in items]
    cells = "".join(f'<div class="wl-kpi {t}"><div class="l">{e(l)}</div><div class="v">{e(v)}</div>'
                    f'<div class="h">{e(h)}</div></div>' for l, v, h, t in items)
    return f'<div class="wl-kpis" style="grid-template-columns:repeat({len(items)},1fr)">{cells}</div>'


def section(title: str, n: int | None = None, desc: str = "") -> str:
    return (f'<div class="wl-sec"><h3>{e(title)}</h3>' + (f'<span class="n">{n}</span>' if n is not None else "")
            + (f'<span class="d">{e(desc)}</span>' if desc else "") + "</div>")


def card(title: str, sub: str = "", chips: list[str] | None = None, facts: list[str] | None = None,
         kind: str = "") -> str:
    head = f'<div class="row1">{"".join(chips or [])}<span class="ttl">{e(title)}</span></div>'
    body = f'<div class="sub">{sub}</div>' if sub else ""
    fx = f'<div class="facts">{"".join(facts or [])}</div>' if facts else ""
    return f'<div class="wl-card {kind}">{head}{body}{fx}</div>'


def empty(text: str) -> str:
    return f'<div class="wl-empty">{e(text)}</div>'


BED_LEGEND = [("occupied", "재원 중", "#E5E8EB"), ("discharging", "퇴원 진행", "#FFE2A8"), ("dirty", "청소 대기", "#FFD3B5"),
              ("cleaning", "청소 중", "#DDD2FF"), ("ready", "입실 가능", "#BDEBD4"), ("arrived", "새 환자 입실", "#C9E2FF")]


def bed_board(beds: list[dict]) -> str:
    legend = "".join(f'<span><i style="background:{c}"></i>{e(t)}</span>' for _, t, c in BED_LEGEND)
    wards: dict[str, list[str]] = {}
    for b in beds:
        tags = chip("격리", "red") if b.get("isolation") else ""
        wards.setdefault(b["ward"], []).append(
            f'<div class="wl-bed s-{b["state"]}"><div class="id"><span>{e(b["label"])}</span>'
            f'<span>{e(b["room"])}</span></div><div class="st">{e(b["state_ko"])} {tags}</div>'
            f'<div class="who">{e(b.get("detail") or "")}</div></div>')
    blocks = "".join(f'<div style="font-weight:800;color:#4E5968;margin:12px 0 8px">{e(w[1:])}병동</div>'
                     f'<div class="wl-board">{"".join(cells)}</div>' for w, cells in wards.items())
    return f'<div class="wl-legend">{legend}</div>{blocks}'


def kanban(columns: list[tuple[str, list[str]]]) -> str:
    cols = "".join(f'<div class="wl-col"><h4><span>{e(t)}</span><span>{len(items)}</span></h4>{"".join(items) or ""}</div>'
                   for t, items in columns)
    return f'<div class="wl-kanban">{cols}</div>'


def mini(title: str, sub: str = "", extra: str = "") -> str:
    return f'<div class="wl-mini"><b>{e(title)}</b><div style="color:#4E5968;margin-top:2px">{e(sub)}</div>{extra}</div>'


def steps(names: list[str], current: int) -> str:
    out = []
    for i, n in enumerate(names):
        cls = "on" if i == current else "done" if i < current else ""
        out.append(f'<span class="wl-step {cls}">{i + 1}. {e(n)}</span>')
    return f'<div class="wl-steps">{"".join(out)}</div>'


def guide(kicker: str, text: str, points: list[str]) -> str:
    li = "".join(f"<li>{e(p)}</li>" for p in points)
    return f'<div class="wl-guide"><div class="k">{e(kicker)}</div><div class="t">{e(text)}</div><ul>{li}</ul></div>'


def checks(items: list[tuple]) -> str:
    """(통과 여부 True/False/None(해당 없음), 제목, 설명)"""
    rows = []
    for ok, t, d in items:
        ic = ('<span class="ic" style="background:#F2F4F6;color:#5F6B7A">–</span>' if ok is None else
              '<span class="ic" style="background:#E6F8EF;color:#067A4B">✓</span>' if ok
              else '<span class="ic" style="background:#FFEBEE;color:#C8102E">!</span>')
        rows.append(f'<div class="wl-check">{ic}<div><div class="tt">{e(t)}</div><div class="dd">{e(d)}</div></div></div>')
    return f'<div class="wl-card">{"".join(rows)}</div>'


def table(cols: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{e(c)}</th>" for c in cols)
    tr = "".join("<tr>" + "".join(f"<td>{e(x)}</td>" for x in r) + "</tr>" for r in rows)
    return f'<table class="wl-table"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


__all__ = ["footnote", "CSS", "TOKENS", "bed_board", "card", "checks", "chip", "e", "empty", "guide", "kanban", "kpis", "mini",
           "page_header", "section", "steps", "table", "topbar"]
