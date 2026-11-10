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
    assert not {"organism", "rrn", "notes"} & set(d)  # 병원체명·주민번호·기록은 원무 상세에 없음
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
    assert client.post(f"/api/tasks/{t['id']}/done", json={"result": t["choices"][0]}, headers=h).status_code == 200
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
