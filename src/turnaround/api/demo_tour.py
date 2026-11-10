"""시연 화면(P5): 대표 사례 3개를 단계별로 따라간다. 로그인 없이 보고, 운영 화면 데이터와 섞이지 않는다(시연마다 따로 만든 합성 병원).

사례 1 평범한 배정(에이전트가 확정하고 통보), 사례 2 안내 시각 지연 재연락(사람 승인), 사례 3 늦은 격리를 시스템이 막음.
단계 내용은 기존 시연 대본(ui/scenes.py)을 그대로 쓴다.
"""

from __future__ import annotations

import re
import secrets
import threading

from turnaround.ui.scenes import SCENES, SceneRun

CHAPTERS = [
    {"id": "day", "title": "평범한 배정", "summary": "에이전트가 조건에 맞는 병상을 확정하고 원무와 병동에 알립니다. 사람은 환자 연락과 병동 준비만 합니다.",
     "scene": "day", "first": 0, "last": 2},
    {"id": "delay", "title": "안내 시각 지연 재연락", "summary": "안내한 입실 시각을 지키기 어려우면 에이전트가 재연락을 제안하고, 원무팀이 승인합니다.",
     "scene": "exception", "first": 0, "last": 1},
    {"id": "iso", "title": "늦은 격리를 시스템이 막음", "summary": "확정 뒤 격리 오더가 오면 병상 결정은 감염관리실로 가고, 에이전트가 스스로 병상을 바꾸려 하면 게이트웨이가 막습니다.",
     "scene": "exception", "first": 2, "last": 3},
]
_RUNS: dict[str, tuple[dict, SceneRun]] = {}
_LOCK = threading.Lock()


def _clean(text: str) -> str:
    text = re.sub(r"^\d+\. [^:]+: ", "", text or "")
    return text.replace(" · ", ", ").replace("·", ", ")


def _view(ch: dict, run: SceneRun) -> dict:
    v = run.view()
    i = run.index
    step = run.scene.steps[i] if i >= 0 else None
    return {"chapter": ch["id"], "chapter_title": ch["title"], "summary": ch["summary"],
            "step": i - ch["first"] + 1, "steps": ch["last"] - ch["first"] + 1, "done": i >= ch["last"],
            "title": step.title if step else ch["title"], "caption": _clean(v.get("caption", "")),
            "look": [_clean(x) for x in (step.look if step else [])], "now": v.get("now"),
            "changed": [{**c, "what": c.get("what", "").replace("·", ", "), "detail": _clean(c.get("detail", ""))}
                        for c in v.get("changed") or []]}


def chapters() -> list[dict]:
    return [{k: c[k] for k in ("id", "title", "summary")} | {"steps": c["last"] - c["first"] + 1} for c in CHAPTERS]


def start(chapter: str) -> dict | None:
    ch = next((c for c in CHAPTERS if c["id"] == chapter), None)
    if ch is None:
        return None
    run = SceneRun(SCENES[ch["scene"]])
    while run.index < ch["first"]:  # 앞 사례의 단계는 조용히 지나간다(사례 3은 사례 2의 병원 상태에서 시작)
        run.next()
    rid = secrets.token_urlsafe(8)
    with _LOCK:
        if len(_RUNS) > 50:
            _RUNS.pop(next(iter(_RUNS)))
        _RUNS[rid] = (ch, run)
    return {"run": rid, **_view(ch, run)}


def step(rid: str) -> dict | None:
    with _LOCK:
        got = _RUNS.get(rid)
    if got is None:
        return None
    ch, run = got
    if run.index < ch["last"]:
        run.next()
    return {"run": rid, **_view(ch, run)}
