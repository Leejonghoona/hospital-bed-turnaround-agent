"""WardFlow 웹 화면용 HTTP API (docs/기획/09_실사용화면_로드맵.md, DECISIONS 2026-11-10). 합성 데이터 전용.

화면(React, web/)은 이 API만 부른다. 화면 데이터는 기존 ui/views.py와 같은 함수에서 나온다(화면과 시험이 같은 함수).
실제 인증 수단은 범위 밖이다. 합성 개인 계정(config/accounts.yaml), 로그인 실패 잠금, 세션 토큰, 접속기록만 흉내 낸다.

실행: uvicorn turnaround.api.app:app --reload
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
ACCOUNTS = ROOT / "config" / "accounts.yaml"
MAX_FAIL = 5  # 로그인 실패 허용 횟수 (설계 가정, 개인정보 안전성 확보조치 기준: 일정 횟수 실패 시 접근 제한)

ROLE_SCREEN = {"bed_manager": "admission", "ward": "ward", "ipc": "ipc", "id_oncall": "ipc", "admin": "manager"}


@dataclass
class Session:
    token: str
    user: dict
    ward: str | None = None
    shift: str | None = None


@dataclass
class Store:
    accounts: dict[str, dict]
    sessions: dict[str, Session] = field(default_factory=dict)
    fails: dict[str, int] = field(default_factory=dict)
    access_log: list[dict] = field(default_factory=list)
    state: object = None  # 합성 병원 하루 재생 (요청이 처음 올 때 만든다)

    def demo(self):
        if self.state is None:
            from turnaround.gateway import load_policy
            from turnaround.sim import GeneratorConfig
            from turnaround.ui.demo import build_demo
            self.state = build_demo(seed=0, day=3, until=time(10, 30), via_fhir=False, policy=load_policy(),
                                    config=GeneratorConfig.realistic(), occupancy="full")
        return self.state

    def log(self, s: Session, action: str, case: str | None = None, detail: str = "") -> None:
        st = self.demo()
        self.access_log.append({"at": st.now.isoformat(timespec="minutes"), "user_id": s.user["user_id"],
                                "name": s.user["name"], "case": case, "action": action, "detail": detail})


def _load_accounts() -> dict[str, dict]:
    raw = yaml.safe_load(ACCOUNTS.read_text(encoding="utf-8"))
    return {a["user_id"]: a for a in raw["accounts"]}


def _public(a: dict) -> dict:
    return {k: a[k] for k in ("user_id", "name", "role", "role_label", "ward")}


def _check(a: dict, pw: str) -> bool:
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), a["salt"].encode(), 120000).hex()
    return hmac.compare_digest(h, a["password_hash"])


class LoginIn(BaseModel):
    user_id: str
    password: str = ""


class ShiftIn(BaseModel):
    ward: str | None = None
    shift: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="WardFlow API")
    store = Store(_load_accounts())
    app.state.store = store

    def session(authorization: str | None = Header(default=None)) -> Session:
        tok = (authorization or "").removeprefix("Bearer ").strip()
        s = store.sessions.get(tok)
        if s is None:
            raise HTTPException(401, "로그인이 필요합니다")
        return s

    def start(a: dict) -> dict:
        tok = secrets.token_urlsafe(24)
        s = Session(tok, _public(a), ward=a.get("ward"))
        store.sessions[tok] = s
        store.log(s, "로그인")
        return {"token": tok, "user": s.user, "screen": ROLE_SCREEN.get(a["role"], "admission")}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/demo-accounts")
    def demo_accounts():
        return [_public(a) for a in store.accounts.values()]

    @app.post("/api/login")
    def login(body: LoginIn):
        a = store.accounts.get(body.user_id)
        if store.fails.get(body.user_id, 0) >= MAX_FAIL - 1 and (a is None or not _check(a, body.password)):
            store.fails[body.user_id] = store.fails.get(body.user_id, 0) + 1
            raise HTTPException(423, "로그인 실패가 반복되어 계정이 잠겼습니다")
        if a is None or not _check(a, body.password):
            store.fails[body.user_id] = store.fails.get(body.user_id, 0) + 1
            raise HTTPException(401, "사번 또는 비밀번호가 맞지 않습니다")
        store.fails.pop(body.user_id, None)
        return start(a)

    @app.post("/api/demo-login")
    def demo_login(body: LoginIn):
        a = store.accounts.get(body.user_id)
        if a is None:
            raise HTTPException(404, "없는 데모 계정입니다")
        return start(a)

    @app.get("/api/me")
    def me(s: Session = Depends(session)):
        st = store.demo()
        return {"user": s.user, "ward": s.ward, "shift": s.shift, "now": st.now.isoformat(timespec="minutes"),
                "screen": ROLE_SCREEN.get(s.user["role"], "admission")}

    @app.post("/api/shift")
    def shift(body: ShiftIn, s: Session = Depends(session)):
        s.ward, s.shift = body.ward or s.ward, body.shift
        store.log(s, "근무 시작", detail=f"{s.ward or ''} {s.shift or ''}".strip())
        return {"ward": s.ward, "shift": s.shift}

    @app.get("/api/worklist")
    def worklist(s: Session = Depends(session)):
        from turnaround.api.worklist import admission_rows
        st = store.demo()
        out = admission_rows(st)
        store.log(s, "목록 조회", detail="오늘 할 일")
        return out

    @app.get("/api/patients/{case}")
    def patient(case: str, s: Session = Depends(session)):
        from turnaround.api.worklist import patient_detail
        st = store.demo()
        d = patient_detail(st, case)
        if d is None:
            raise HTTPException(404, "없는 사례입니다")
        store.log(s, "열람", case=case)
        return d

    @app.get("/api/access-log")
    def access_log(s: Session = Depends(session)):
        if s.user["role"] != "admin":
            raise HTTPException(403, "접속기록은 관리자만 볼 수 있습니다")
        return {"rows": list(reversed(store.access_log))}

    return app


app = create_app()
