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
            self.state = build_demo(seed=2, day=3, until=time(13, 0), via_fhir=False, policy=load_policy(),
                                    config=GeneratorConfig.realistic(), occupancy="full",
                                    live_from=time(7, 0))
            from turnaround.api.shift_model import apply_night_shift
            apply_night_shift(self.state)  # 이전 근무가 처리한 일 (데모 화면용 사람 응답 모형)
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


class ContactIn(BaseModel):
    outcome: str = "done"  # done: 통화함, no_answer: 연락 안 됨(할 일은 남는다)
    promised_at: str = ""  # 환자에게 실제로 안내한 수속 시각 HH:MM
    memo: str = ""


class ResultIn(BaseModel):
    result: str = ""
    memo: str = ""


class ReasonIn(BaseModel):
    reason: str = ""
    memo: str = ""


class AssignIn(BaseModel):
    case: str
    bed: str
    reason: str = ""
    memo: str = ""


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
        d = patient_detail(st, case, store.accounts)
        if d is None:
            raise HTTPException(404, "없는 사례입니다")
        store.log(s, "열람", case=case)
        return d

    # ---- 원무 처리 (P2). 기록은 사건 저장소에 사람 사건으로 남고, 접속기록에도 남는다 ----
    def admission_only(s: Session) -> None:
        if s.user["role"] not in ("bed_manager", "admin"):
            raise HTTPException(403, "원무 담당만 처리할 수 있습니다")

    def row_of(st, rid: str) -> dict:
        from turnaround.api.worklist import admission_rows
        r = next((x for x in admission_rows(st)["rows"] if x["id"] == rid), None)
        if r is None:
            raise HTTPException(404, "이미 처리됐거나 없는 일입니다")
        return r

    def human(st, case: str, key: str, etype, **payload) -> None:
        from turnaround.events import Event
        st.store.append(Event(event_id=f"{case}:web:{key}", case_id=case, type=etype, occurred_at=st.now,
                              observed_at=st.now, source="human", payload=payload))

    def memo_of(text: str, required: bool) -> str:
        from turnaround.api.worklist import MEMO_MAX
        m = (text or "").strip()
        if len(m) > MEMO_MAX:
            raise HTTPException(422, f"메모는 {MEMO_MAX}자까지 적을 수 있습니다")
        if required and not m:
            raise HTTPException(422, "'기타'를 고르면 메모에 내용을 적어 주세요")
        return m

    def note(st, s: Session, case: str, text: str, tag: str = "") -> None:
        """특이사항 메모: 자유 서술 기록(FREE_TEXT_NOTE)으로 남긴다. 계획 에이전트는 원문을 읽지 않고 추출 필드만 쓴다(SEC-3)."""
        from turnaround.events import EventType
        if text or tag:
            human(st, case, f"note:{secrets.token_hex(4)}", EventType.FREE_TEXT_NOTE, text=text, tag=tag,
                  by=s.user["user_id"], case_kind="admission")

    @app.post("/api/contacts/{case}")
    def contact(case: str, body: ContactIn, s: Session = Depends(session)):
        from datetime import datetime as _dt

        from turnaround.events import EventType
        from turnaround.saga import fold
        admission_only(s)
        st = store.demo()
        r = row_of(st, f"contact:{case}")
        memo = memo_of(body.memo, False)
        if body.outcome == "no_answer":
            note(st, s, case, memo, tag="contact_no_answer")
            store.log(s, "입원 안내 연락 안 됨", case=case, detail=memo)
            return {"ok": True}
        try:
            hh, mm = map(int, body.promised_at.split(":"))
            promised = st.now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        except ValueError:
            raise HTTPException(422, "안내한 시각을 HH:MM으로 적어 주세요") from None
        m = fold(st.store.read()).matches.get(case)
        bed = m.bed_id if m else None
        human(st, case, f"contacted:{_dt.now().timestamp()}", EventType.PATIENT_CONTACTED, bed_id=bed,
              by=s.user["user_id"], promised_at=promised.isoformat())
        note(st, s, case, memo)
        store.log(s, "입원 안내 연락 기록", case=case, detail=f"{r['bed']} 수속 {body.promised_at}")
        return {"ok": True}

    @app.post("/api/tasks/{task_id}/done")
    def task_done(task_id: str, body: ResultIn, s: Session = Depends(session)):
        from turnaround.events import EventType
        admission_only(s)
        st = store.demo()
        r = row_of(st, task_id)
        if body.result not in [c["label"] for c in r["choices"]]:
            raise HTTPException(422, "처리 내용을 목록에서 골라 주세요")
        memo = memo_of(body.memo, body.result == "기타")
        note(st, s, r["case"], memo)
        human(st, r["case"], f"done:{task_id}", EventType.HUMAN_TASK_DONE, task_id=task_id, by=s.user["user_id"],
              result=body.result)
        store.log(s, "작업 처리", case=r["case"], detail=f"{r['action']}: {body.result}")
        return {"ok": True}

    @app.post("/api/approvals/{eid}/{verb}")
    def approval(eid: str, verb: str, body: ReasonIn, s: Session = Depends(session)):
        admission_only(s)
        st = store.demo()
        r = row_of(st, eid)
        if verb == "approve":
            dec = st.gw.approve_action(eid, "bed_manager", st.now, approver_id=s.user["user_id"])
            store.log(s, "승인", case=r["case"], detail=f"{r['action']}: {dec.status}")
        elif verb == "decline":
            if not body.reason.strip():
                raise HTTPException(422, "거절 사유를 골라 주세요")
            note(st, s, r["case"], memo_of(body.memo, body.reason.strip() == "기타"))
            dec = st.gw.decline_action(eid, "bed_manager", body.reason.strip(), st.now, approver_id=s.user["user_id"])
            store.log(s, "거절", case=r["case"], detail=f"{r['action']}: {body.reason.strip()}")
        else:
            raise HTTPException(404, "없는 처리입니다")
        return {"status": dec.status, "reasons": list(getattr(dec, "reasons", None) or [])}

    @app.get("/api/beds")
    def beds(case: str, s: Session = Depends(session)):
        from turnaround.api.beds import board
        admission_only(s)
        out = board(store.demo(), case)
        if out is None:
            raise HTTPException(404, "없는 사례입니다")
        store.log(s, "병상 현황 조회", case=case)
        return out

    @app.post("/api/assign")
    def assign(body: AssignIn, s: Session = Depends(session)):
        from turnaround.api.beds import REASON_CODES, refusal_ko
        admission_only(s)
        st = store.demo()
        if body.reason not in REASON_CODES:
            raise HTTPException(422, "배정 사유를 골라 주세요")
        memo = memo_of(body.memo, body.reason == "other")
        d = st.gw.manual_override(body.case, body.bed, s.user["user_id"], "bed_manager", body.reason, st.now,
                                  reason_text=memo or None)
        if d.status != "applied":
            msg = refusal_ko(d.reasons)
            if d.tier == "reason_entry":
                msg += ". 메모에 차선책 사유를 적어 주세요"
            elif d.tier == "preapproval":
                msg += ". 감염관리실 승인이 먼저 필요합니다"
            raise HTTPException(409, msg)
        note(st, s, body.case, memo)
        # 병상이 정해졌으니 이 환자의 '병상 없음'(사람 확인 요청) 할 일은 처리된 것으로 닫는다
        from turnaround.api.worklist import admission_rows
        from turnaround.events import EventType
        for r in admission_rows(st)["rows"]:
            if r["case"] == body.case and r["kind_label"] == "병상 없음":
                human(st, body.case, f"done:{r['id']}", EventType.HUMAN_TASK_DONE, task_id=r["id"], by=s.user["user_id"],
                      result="병상 직접 배정")
        store.log(s, "병상 직접 배정", case=body.case, detail=f"{body.bed} {REASON_CODES[body.reason]}")
        return {"ok": True, "tier": d.tier}

    @app.get("/api/assignments")
    def assignment_log(s: Session = Depends(session)):
        from turnaround.api.worklist import assignments
        admission_only(s)
        out = assignments(store.demo(), store.accounts)
        store.log(s, "목록 조회", detail="배정 기록")
        return out

    @app.get("/api/access-log")
    def access_log(s: Session = Depends(session)):
        if s.user["role"] != "admin":
            raise HTTPException(403, "접속기록은 관리자만 볼 수 있습니다")
        return {"rows": list(reversed(store.access_log))}

    # 빌드된 화면(web/dist)이 있으면 같은 주소에서 내준다(한 컨테이너 배포). API 경로 뒤에 둔다.
    dist = ROOT / "web" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
