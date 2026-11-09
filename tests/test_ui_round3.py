"""UI 3차: 실사용 기능 (추천 병상 안내, 단계별 바뀐 것, 작업 처리 완료). 합성 데이터."""

from turnaround.events import Event, EventType
from turnaround.saga.worklist import open_tasks
from turnaround.ui import views
from turnaround.ui.scenes import SCENES, SceneRun


def _play(sid):
    r = SceneRun(SCENES[sid])
    out = [r.next() for _ in SCENES[sid].steps]
    return r, out


def test_rematch_approval_always_explains_bed_options():
    r, out = _play("exception")
    # 늦은 격리의 병상 결정은 감염관리실 화면에서 승인한다 (원무 화면에는 '결정 대기'로만)
    recs = [a for v in out if v["role"] == "ipc" for a in v["data"]["approvals"] if a["action"] == "propose_rematch"]
    assert recs, "재배정 승인 단계가 있어야 한다"
    adm = [a for v in out if v["role"] == "admission" for a in v["data"]["approvals"] if a["action"] == "propose_rematch"]
    assert not adm
    # 만실·격리로 맞는 병상이 없어도 빈 목록 대신 이유를 보여 준다
    assert all(a["recommend"] and all(isinstance(x, str) and x for x in a["recommend"]) for a in recs)


def test_each_step_lists_what_changed():
    r, out = _play("day")
    assert all("changed" in v for v in out)
    assert any(v["changed"] for v in out[1:])  # 시각이 흐르면 새 결정 기록이 생긴다
    for v in out:
        for row in v["changed"]:
            assert row["what"] and row["actor"]


def test_task_done_closes_the_task():
    r, out = _play("exception")
    st = r.st
    tasks = views.admission_view(st)["tasks"]
    assert tasks
    t = tasks[0]
    st.store.append(Event(event_id=f"{t['case']}:ui:done:{t['task_id']}", case_id=t["case"], type=EventType.HUMAN_TASK_DONE,
                          occurred_at=st.now, observed_at=st.now, source="human",
                          payload={"task_id": t["task_id"], "by": "admission_office"}))
    assert t["task_id"] not in open_tasks(st.store.read(), st.now)
    assert t["task_id"] not in {x["task_id"] for x in views.admission_view(st)["tasks"]}


def test_changed_rows_have_no_internal_codes():
    import re
    for sid in ("day", "exception"):
        r, out = _play(sid)
        for v in out:
            for row in v["changed"]:
                assert row["what"] != "담당 검토로 보냄"  # 반복되는 계획 주기 기록은 강조하지 않는다
                assert not re.search(r"[a-z]+_[a-z]+", row["detail"] + row["what"]), row


SKIP_KEYS = {"exception_id", "proposal_id", "task_id", "version", "stage", "role", "action", "id", "raw_kind",
             "raw_proposal", "case", "bed", "raw", "raw_action", "trigger", "kind_raw",
             "reasons"}  # reasons는 화면에서 REVIEW_KO로 바꿔 그린다


def _texts(d, key=""):
    if isinstance(d, dict):
        for k, v in d.items():
            if k not in SKIP_KEYS:
                yield from _texts(v, k)
    elif isinstance(d, (list, tuple)):
        for v in d:
            yield from _texts(v, key)
    elif isinstance(d, str):
        yield key, d


def test_no_snake_case_codes_on_any_scene_screen():
    import re
    bad = []
    for sid in SCENES:
        r, out = _play(sid)
        for v in out:
            for k, t in _texts({"caption": v["caption"], "data": v["data"], "changed": v["changed"]}):
                if re.search(r"\b[a-z]+_[a-z_]+\b", t):
                    bad.append((sid, v["step"], k, t[:80]))
    assert not bad, bad[:10]
