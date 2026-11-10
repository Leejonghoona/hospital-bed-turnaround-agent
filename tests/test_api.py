"""P0/P1: WardFlow 웹 화면용 HTTP API (docs/기획/09_실사용화면_로드맵.md). 합성 데이터.

화면(React)은 이 API만 부른다. 화면 데이터는 기존 ui/views.py와 같은 함수에서 나온다.
"""

import pytest
from fastapi.testclient import TestClient

from turnaround.api.app import create_app


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app())


def _login(client, user="adm-kim"):
    r = client.post("/api/demo-login", json={"user_id": user})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_health(client):
    assert client.get("/api/health").json()["ok"] is True


def test_demo_accounts_are_personal_and_role_scoped(client):
    accs = client.get("/api/demo-accounts").json()
    ids = {a["user_id"] for a in accs}
    assert {"adm-kim", "ward71-park", "ipc-lee", "admin-jung"} <= ids
    assert all(a["name"] and a["role_label"] for a in accs)
    assert not any("공용" in a["name"] for a in accs)  # 공용 계정 없음


def test_password_login_and_failure_lock(client):
    bad = [client.post("/api/login", json={"user_id": "ward72-choi", "password": "x"}).status_code for _ in range(5)]
    assert bad[:4] == [401, 401, 401, 401] and bad[4] == 423  # 다섯 번째부터 잠김
    ok = client.post("/api/login", json={"user_id": "adm-kim", "password": "demo1234"})
    assert ok.status_code == 200 and ok.json()["user"]["name"]


def test_me_requires_token(client):
    assert client.get("/api/me").status_code == 401
    h = _login(client)
    me = client.get("/api/me", headers=h).json()
    assert me["user"]["user_id"] == "adm-kim" and me["user"]["role"] == "bed_manager"


def test_admission_worklist_rows(client):
    h = _login(client)
    w = client.get("/api/worklist", headers=h).json()
    assert w["rows"], "원무 할 일이 있어야 한다"
    row = w["rows"][0]
    for k in ("id", "due", "late", "kind", "kind_label", "patient", "patient_id", "bed", "reason", "status"):
        assert k in row
    lates = [r["late"] for r in w["rows"]]
    assert lates == sorted(lates, reverse=True)  # 마감 지난 일이 위
    assert set(w["counts"]) >= {"all", "late", "carried"}
    assert sum(w["groups"].values()) == w["counts"]["all"]  # 탭 분류는 겹치지 않는다(합 = 전체)


def test_viewing_a_patient_is_written_to_access_log(client):
    h = _login(client)
    row = client.get("/api/worklist", headers=h).json()["rows"][0]
    d = client.get(f"/api/patients/{row['case']}", headers=h)
    assert d.status_code == 200 and d.json()["patient_id"] == row["patient_id"]
    admin = _login(client, "admin-jung")
    log = client.get("/api/access-log", headers=admin).json()["rows"]
    assert any(e["user_id"] == "adm-kim" and e["case"] == row["case"] and e["action"] == "열람" for e in log)


def test_access_log_is_admin_only(client):
    assert client.get("/api/access-log", headers=_login(client)).status_code == 403


# ---- P2 원무 처리 (W2 연락 기록, W4 승인·거절, 병상 없음 처리) ----

def _rows(client, h):
    return client.get("/api/worklist", headers=h).json()["rows"]


def test_detail_shows_what_admission_needs_and_hides_the_rest(client):
    h = _login(client)
    r = _rows(client, h)[0]
    d = client.get(f"/api/patients/{r['case']}", headers=h).json()
    assert d["phone"].startswith("010-0000-") and d["reserved_at"] and "room_pref" in d  # 합성 연락처, 예약 시각
    assert len(d["patient_id"]) == 8 and d["patient_id"].isdigit()
    assert not {"organism", "rrn", "progress_notes", "nursing_notes"} & set(d)  # 병원체명·주민번호·경과기록은 원무 상세에 없음
    whats = " ".join(t["what"] for t in d["timeline"])
    assert not any(w in whats for w in ("실행", "예외 종료", "막음", "작업 완료"))  # 내부 용어 없음


def test_contact_recorded_removes_row(client):
    h = _login(client, "adm-seo")
    c = next(r for r in _rows(client, h) if r["kind"] == "contact")
    assert c["suggest_at"]
    r = client.post(f"/api/contacts/{c['case']}", json={"promised_at": c["suggest_at"]}, headers=h)
    assert r.status_code == 200, r.text
    assert c["id"] not in {x["id"] for x in _rows(client, h)}
    log = client.get("/api/access-log", headers=_login(client, "admin-jung")).json()["rows"]
    assert any(x["user_id"] == "adm-seo" and x["action"] == "입원 안내 연락 기록" for x in log)


def test_no_bed_task_needs_a_choice_and_closes(client):
    h = _login(client)
    t = next(r for r in _rows(client, h) if r["kind_label"] == "병상 없음")
    assert t["choices"]
    assert client.post(f"/api/tasks/{t['id']}/done", json={"result": "아무거나"}, headers=h).status_code == 422
    labels = [c["label"] for c in t["choices"]]
    assert all(c["desc"] for c in t["choices"]) and "기타" in labels
    # '기타'는 메모가 있어야 한다
    assert client.post(f"/api/tasks/{t['id']}/done", json={"result": "기타", "memo": ""}, headers=h).status_code == 422
    assert client.post(f"/api/tasks/{t['id']}/done", json={"result": "기타", "memo": "보호자가 내일 오전 통화 원함"},
                       headers=h).status_code == 200
    assert t["id"] not in {x["id"] for x in _rows(client, h)}


def test_approval_decline_needs_reason_and_ward_cannot_act(client):
    h = _login(client)
    a = next(r for r in _rows(client, h) if r["kind"] == "approval")
    w = _login(client, "ward71-park")
    assert client.post(f"/api/approvals/{a['id']}/decline", json={"reason": "환자 사정으로 보류"}, headers=w).status_code == 403
    assert client.post(f"/api/approvals/{a['id']}/decline", json={"reason": " "}, headers=h).status_code == 422
    r = client.post(f"/api/approvals/{a['id']}/decline", json={"reason": "원무가 직접 처리"}, headers=h)
    assert r.status_code == 200 and r.json()["status"]
    assert a["id"] not in {x["id"] for x in _rows(client, h)}


def test_memo_is_kept_as_free_text_note_and_shown_to_next_shift(client):
    h = _login(client)
    c = next(r for r in _rows(client, h) if r["kind"] == "contact")
    # 연락이 안 되면 할 일은 남고, 메모와 시도 횟수가 보인다
    r = client.post(f"/api/contacts/{c['case']}", json={"outcome": "no_answer", "memo": "두 번 걸었으나 받지 않음"}, headers=h)
    assert r.status_code == 200, r.text
    row = next(x for x in _rows(client, h) if x["id"] == c["id"])
    assert row["status"] == "다시 연락" and "1회" in row["reason"]
    d = client.get(f"/api/patients/{c['case']}", headers=_login(client, "adm-seo")).json()  # 다음 근무자
    assert any(n["text"] == "두 번 걸었으나 받지 않음" and n["who"] == "김하늘" for n in d["notes"])
    # 계획 에이전트가 읽지 않는 자유 서술 기록으로 저장된다(추출 필드만 쓴다, SEC-3)
    from turnaround.events import EventType
    ev = client.app.state.store.demo().store.read()
    assert any(e.type is EventType.FREE_TEXT_NOTE and e.payload.get("text") == "두 번 걸었으나 받지 않음" for e in ev)
    assert client.post(f"/api/contacts/{c['case']}", json={"outcome": "no_answer", "memo": "x" * 501}, headers=h).status_code == 422


def test_assignment_log_lists_agent_and_human_commits(client):
    h = _login(client)
    r = client.get("/api/assignments", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["rows"]
    assert rows and {"time", "patient", "bed", "by", "basis", "state"} <= set(rows[0])
    assert any(x["by"] == "에이전트" for x in rows) and any(x["by"] != "에이전트" for x in rows)
    assert all(x["state"] in {"입실 완료", "입실 대기", "되돌림", "취소"} for x in rows)
    assert [x["at"] for x in rows] == sorted((x["at"] for x in rows), reverse=True)  # 최근 것이 위
    assert client.get("/api/assignments", headers=_login(client, "ward71-park")).status_code == 403
    # 날짜별로 본다: 기록이 있는 날짜 목록이 오고, 어제를 고르면 어제 확정만 나온다
    d = r.json()
    assert d["day"] == d["days"][0] and len(d["days"]) >= 2
    y = client.get(f"/api/assignments?day={d['days'][1]}", headers=h).json()
    assert y["rows"] and all(x["at"].startswith(d["days"][1]) for x in y["rows"])
    assert client.get("/api/assignments?day=어제", headers=h).status_code == 422


# ---- 병상 직접 배정 (현황판 + 추천 후보, 게이트웨이 사전 검사) ----

def _nobed_case(client, h):
    return next(r for r in _rows(client, h) if r["kind_label"] == "병상 없음")["case"]


def test_bed_board_marks_each_bed_with_fit_from_gateway(client):
    h = _login(client)
    case = _nobed_case(client, h)
    b = client.get(f"/api/beds?case={case}", headers=h).json()
    assert {"need", "recommend", "wards"} <= set(b)
    beds = [x for w in b["wards"] for r in w["rooms"] for x in r["beds"]]
    assert len(beds) == 24 and all(x["fit"] in {"ok", "reason", "ipc", "no"} for x in beds)
    assert all(x["why"] for x in beds if x["fit"] != "ok")  # 못 고르는 병상은 이유가 있다
    assert all(r["grade"] for w in b["wards"] for r in w["rooms"])  # 병실 등급(1인실, 다인실 등)


def test_manual_assign_requires_reason_and_follows_gateway():
    """만실이라 오늘 비는 병상이 없다. 같은 병동 남자 병실 환자 한 명의 퇴원 결정을 넣어 고를 병상을 만든다(새 앱)."""
    from datetime import timedelta

    from turnaround.events import Event, EventType
    c = TestClient(create_app())
    h = _login(c)
    st = c.app.state.store.demo()
    case = _nobed_case(c, h)  # 71병동 소화기내과 남자 환자
    room = next(r for r in st.h.rooms if r.ward_id == "W71" and r.gender is not None and r.gender.value == "M")
    bed = next(b.id for b in st.h.beds if b.room_id == room.id and b.id in st.gw.census)
    st.store.append(Event(event_id="t:dd", case_id="SYN-DTEST", type=EventType.DISCHARGE_DECIDED, source="his",
                          occurred_at=st.now - timedelta(minutes=5), observed_at=st.now - timedelta(minutes=5),
                          payload={"bed_id": bed, "name": "퇴원테스트", "patient_id": "SYN-P9999", "gender": "M", "age": 70}))
    b = c.get(f"/api/beds?case={case}", headers=h).json()
    beds = {x["id"]: x for w in b["wards"] for r in w["rooms"] for x in r["beds"]}
    assert beds[bed]["fit"] == "ok" and beds[bed]["status"] == "퇴원 예정"
    other = next(x for x in beds.values() if x["status"].startswith("배정됨"))
    assert other["fit"] == "no" and other["why"] == "다른 환자에게 배정됨"
    r = c.post("/api/assign", json={"case": case, "bed": other["id"], "reason": "patient_request"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]  # 게이트웨이가 거절
    assert c.post("/api/assign", json={"case": case, "bed": bed, "reason": ""}, headers=h).status_code == 422
    assert c.post("/api/assign", json={"case": case, "bed": bed, "reason": "other", "memo": ""}, headers=h).status_code == 422
    r = c.post("/api/assign", json={"case": case, "bed": bed, "reason": "patient_request"}, headers=h)
    assert r.status_code == 200, r.text
    log = c.get("/api/assignments", headers=h).json()["rows"]
    mine = next(x for x in log if x["case"] == case)
    assert mine["by"] == "김하늘" and "환자·보호자 요청" in mine["basis"]
    # 병상이 정해졌으니 그 환자의 '병상 없음' 할 일은 닫히고, 입원 안내 연락 할 일이 생긴다
    rows = _rows(c, h)
    assert case not in {x["case"] for x in rows if x["kind_label"] == "병상 없음"}
    assert case in {x["case"] for x in rows if x["kind"] == "contact"}
