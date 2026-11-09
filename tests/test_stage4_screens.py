"""4단계: 역할별 화면 데이터와 시연 대본 3장면 클릭 재현 (08 계획). 합성 데이터."""

import pytest

from turnaround.events import EventType
from turnaround.ui.scenes import SCENES, SceneRun


def _play(sid):
    r = SceneRun(SCENES[sid])
    views = [r.next() for _ in SCENES[sid].steps]
    return r, views


@pytest.fixture(scope="module")
def day():
    return _play("day")


@pytest.fixture(scope="module")
def exception():
    return _play("exception")


@pytest.fixture(scope="module")
def onboard():
    return _play("onboard")


def test_three_scenes_exist_and_finish(day, exception, onboard):
    for r, views in (day, exception, onboard):
        assert r.done and len(views) == len(r.scene.steps)
        assert all(v["caption"] and v["role"] for v in views)


def test_day_scene_shows_agent_commit_with_basis_and_ward_flow(day):
    r, views = day
    a = views[0]["data"]
    assert a["notices"] and all(n["undo_until"] for n in a["notices"])  # 되돌릴 수 있는 시한
    w = views[1]["data"]
    assert w["flow"] and {f["step"] for f in w["flow"]} & {"준비 완료 · 호출 대기", "호출됨 · 이동 중", "입실 완료"}
    m = views[2]["data"]
    assert all(row["위반"] == 0 for row in m["safety"]) and m["flow"]


def test_exception_scene_approval_isolation_and_blocked(exception):
    r, views = exception
    assert views[0]["data"]["approvals"]
    assert "실행됨" in views[1]["caption"] and len(views[1]["data"]["approvals"]) == len(views[0]["data"]["approvals"]) - 1
    assert views[2]["data"]["isolation_orders"] and views[2]["data"]["alerts"]  # 감염관리실이 통지를 받는다
    case = r.memo["iso_case"]
    ev = r.st.store.read()
    assert not any(e.type is EventType.PATIENT_CALL_UP and e.case_id == case and e.observed_at.hour >= 14 for e in ev)
    blocked = views[3]["data"]["blocked"]
    assert any(b["case_id"] == case and "연락·확정" in b["reason"] for b in blocked)  # 이 장면에서 새로 막힌 판단
    assert "거부" in views[3]["caption"]


def test_onboard_scene_reaches_operation(onboard):
    r, views = onboard
    assert views[0]["data"]["stage"] == "structure"
    assert views[3]["data"]["package"]["proof"][0]["결과"] == "통과"
    assert r.pkg.status == "approved" and r.pkg.verify()
    assert views[4]["role"] == "manager" and views[4]["data"]["cards"]


def test_views_have_korean_labels(exception):
    r, views = exception
    for b in views[3]["data"]["blocked"]:
        assert b["reason"] and not b["reason"].isascii()


# ---------------- 화면 자동 시험 (Streamlit AppTest: 브라우저 없이 버튼을 누른다) ----------------

APP = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src/turnaround/ui/app.py")


@pytest.mark.parametrize("sid", list(SCENES))
def test_app_clicks_through_scene(sid):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.selectbox[0].set_value(sid).run()
    for _ in SCENES[sid].steps:
        at.button(key="next").click().run()
    assert not at.exception, at.exception
    text = " ".join(h.proto.body for h in at.get("html"))
    assert "화면에서 볼 것" in text and "wl-kpi" in text  # 단계 안내와 지표 카드가 그려진다
    assert at.button(key="next").disabled


@pytest.mark.parametrize("role", ["admission", "ward", "ipc", "manager", "vendor"])
def test_app_role_screens_render(role):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value("역할 화면").run()
    at.sidebar.radio[1].set_value(role).run()
    assert not at.exception, at.exception
    text = " ".join(h.proto.body for h in at.get("html"))
    assert "wl-page-h" in text and ("wl-kpi" in text or "wl-table" in text or "wl-empty" in text)


def test_browser_screenshot_of_scene(tmp_path):
    """실제 브라우저(Playwright)로 화면을 띄워 '다음'을 누르고 찍는다. 단계마다 화면이 그려지고 오류가 없어야 한다."""
    import shutil
    import sys
    if not shutil.which("node"):
        pytest.skip("node 없음")
    sys.path.insert(0, str(__import__("pathlib").Path(APP).resolve().parents[3] / "scripts"))
    from screenshots import shoot
    shots = shoot(tmp_path, scale=1, only="onboard")
    assert len(shots) == 5 and all(s["ok"] for s in shots)
    assert all((tmp_path / f"scene_onboard_{i}.png").stat().st_size > 10_000 for i in range(1, 6))


INTERNAL = ("isolation_order_after_commit", "isolation_after_commit", "bed_occupancy_conflict", "'structure'",
            "proposed", "approved", "None", "stage_locked", "promise_at_risk", "late_isolation")


def _strings(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, (list, tuple)):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x


@pytest.mark.parametrize("sid", list(SCENES))
def test_no_internal_terms_in_screen_text(sid, request):
    r, views = request.getfixturevalue(sid if sid != "onboard" else "onboard")
    for v in views:
        texts = [v["caption"]] + [t for k, t in _iter_display(v["data"])]
        for t in texts:
            for bad in INTERNAL:
                assert bad not in t, (sid, bad, t)


def _iter_display(d):
    skip = {"exception_id", "proposal_id", "task_id", "version", "stage", "role", "action", "id"}
    if isinstance(d, dict):
        for k, v in d.items():
            if k in skip:
                continue
            if isinstance(v, str):
                yield k, v
            else:
                yield from _iter_display(v)
    elif isinstance(d, list):
        for v in d:
            yield from _iter_display(v)


def test_admission_screen_shows_only_today_or_labels_other_days(day):
    r, views = day
    a = views[0]["data"]
    assert all(":" in n["time"] and "어제" not in n["time"] for n in a["notices"])
    assert all("어제" not in b["time"] for b in a["blocked"])
