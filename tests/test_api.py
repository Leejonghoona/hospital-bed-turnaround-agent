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
    assert set(w["counts"]) >= {"all", "late", "approval", "contact", "ipc_wait", "carried"}


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
