"""WardFlow 웹 화면용 HTTP API (docs/기획/09_실사용화면_로드맵.md, DECISIONS 2026-11-10). 합성 데이터 전용.

화면(React, web/)은 이 API만 부른다. 화면 데이터는 기존 ui/views.py와 같은 함수에서 나온다(화면과 시험이 같은 함수).
실제 인증 수단은 범위 밖이다. 합성 개인 계정(config/accounts.yaml), 로그인 실패 잠금, 세션 토큰, 접속기록만 흉내 낸다.

실행: uvicorn turnaround.api.app:app --reload
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time as _time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[3]
ACCOUNTS = ROOT / "config" / "accounts.yaml"
MAX_FAIL = 5  # 로그인 실패 허용 횟수 (설계 가정, 개인정보 안전성 확보조치 기준: 일정 횟수 실패 시 접근 제한)

ROLE_SCREEN = {"bed_manager": "admission", "bed_lead": "ops", "ward": "ward", "ipc": "ipc", "id_oncall": "ipc", "admin": "manager"}


_BUILD_LOCK = threading.Lock()
MAX_TOURS = 6
IDLE_SEC = 15 * 60  # 일정 시간 미사용 시 자동 차단 (고시 제6조④, EMR 인증 F004 "10분~60분 이내"). 15분은 설계 가정
_CLIENT_IP: ContextVar[str] = ContextVar("client_ip", default="")


@dataclass
class Session:
    token: str
    user: dict
    ward: str | None = None
    shift: str | None = None
    last_seen: float = field(default_factory=_time.monotonic)
    locked: bool = False
    tour_state: object = None  # 시연 세션이면 따로 만든 합성 병원
    tour_onboarding: object = None  # 시연 세션의 새 병원 설정판(운영 화면과 따로)


@dataclass
class Store:
    accounts: dict[str, dict]
    sessions: dict[str, Session] = field(default_factory=dict)
    fails: dict[str, int] = field(default_factory=dict)
    access_log: object = None  # AccessLog (파일, 추가만)
    state: object = None  # 합성 병원 하루 재생 (요청이 처음 올 때 만든다)
    onboarding: object = None  # 표본 새 병원 설정판 (AI 운영 화면이 처음 열 때 만든다)

    def demo(self, s=None):
        """이 세션이 쓰는 합성 병원. 시연(따라 해 보기) 세션은 따로 만든 병원을 써서 운영 화면 데이터와 섞이지 않는다."""
        own = getattr(s, "tour_state", None)
        if own is not None:
            return own
        with _BUILD_LOCK:  # 첫 요청 여러 개가 동시에 오면 한 번만 만든다 (동시에 만들면 공유 캐시가 엉킨다)
            if self.state is None:
                self.state = build_hospital()
            return self.state

    def log(self, s, action: str, case: str | None = None, detail: str = "") -> None:
        """s: Session 또는 사용자 정보 dict(로그인 실패처럼 세션이 없을 때)."""
        u = s.user if isinstance(s, Session) else s
        sim = self.state.now.isoformat(timespec="minutes") if self.state is not None else ""
        self.access_log.append(sim_at=sim, user_id=u["user_id"], name=u.get("name", ""), ip=_CLIENT_IP.get(),
                               case=case, action=action, detail=detail)


def build_hospital():
    """시연용 합성 병원 하루(11:45). 운영 화면과 시연 세션이 같은 방법으로 만든다."""
    from turnaround.api.shift_model import apply_night_shift
    from turnaround.gateway import load_policy
    from turnaround.sim import GeneratorConfig
    from turnaround.ui.demo import build_demo
    st = build_demo(seed=2, day=3, until=time(11, 15), via_fhir=False, policy=load_policy(),
                    config=GeneratorConfig.demo_hospital(), occupancy="high", backlog_days=1, live_from=time(7, 0))
    _inject_late_isolation(st)  # 감염관리실 화면 시연용: 확정 뒤 격리 오더 1건 (합성)
    st.advance(st.now.replace(hour=11, minute=45))
    apply_night_shift(st)  # 이전 근무가 처리한 일 (데모 화면용 사람 응답 모형)
    return st


def _inject_late_isolation(st) -> None:
    """시연용 늦은 격리 1건: 4인실에 확정됐지만 아직 입실 전인 환자에게 MRSA 접촉주의 오더가 난다(합성 사례).
    이후 재생에서 사가가 늦은 격리 예외를 올리고, 병상 결정은 감염관리실이 승인한다(늦은 격리는 감염관리실만 결정)."""
    from turnaround.events import Event, EventType
    from turnaround.saga import fold
    ev = st.store.read()
    s = fold(ev)
    room = {b.id: st.gw.room_by_id[b.room_id] for b in st.gw.beds}
    pick = next((m for c, m in sorted(s.matches.items()) if m.active and EventType.MATCH_COMMITTED in m.facts
                 and EventType.PATIENT_ARRIVED not in m.facts and m.bed_id in room and room[m.bed_id].capacity == 4
                 and EventType.PATIENT_CONTACTED in m.facts), None)
    if pick is None:
        return
    t = st.now
    st.store.append(Event(event_id=f"{pick.admission_id}:demo:late_isolation", case_id=pick.admission_id,
                          type=EventType.ISOLATION_ORDER_ADDED, occurred_at=t, observed_at=t, source="his",
                          payload={"organism": "MRSA", "precautions": ["contact"], "precaution_status": "confirmed",
                                   "isolation_ordered_by": "ipc", "isolation_type": "contact", "bed_id": pick.bed_id}))


def _load_accounts() -> dict[str, dict]:
    raw = yaml.safe_load(ACCOUNTS.read_text(encoding="utf-8"))
    return {a["user_id"]: a for a in raw["accounts"]}


def _public(a: dict) -> dict:
    return {k: a[k] for k in ("user_id", "name", "role", "role_label", "ward")}


def _check(a: dict, pw: str) -> bool:
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), a["salt"].encode(), 120000).hex()
    return hmac.compare_digest(h, a["password_hash"])


class AutonomyIn(BaseModel):
    to: str
    memo: str = ""


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


class TourIn(BaseModel):
    chapter: str


class UnlockIn(BaseModel):
    password: str = ""


UNDO_REASONS = ("다른 병상이 더 적합", "환자 요청", "병동 사정", "기타")


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
    from turnaround.api.access_log import AccessLog
    data_dir = Path(os.environ.get("WARDFLOW_DATA_DIR") or (ROOT / "var"))
    store = Store(_load_accounts(), access_log=AccessLog(data_dir / "access_log.sqlite"))
    app.state.store = store

    @app.on_event("startup")
    def warm() -> None:
        if os.environ.get("WARDFLOW_LAZY") != "1":  # 서버를 켤 때 합성 병원을 미리 만든다 (첫 화면이 기다리지 않게)
            threading.Thread(target=store.demo, daemon=True).start()

    @app.middleware("http")
    async def remember_ip(request: Request, call_next):
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        _CLIENT_IP.set(fwd or (request.client.host if request.client else ""))
        return await call_next(request)

    def any_session(authorization: str | None = Header(default=None)) -> Session:
        tok = (authorization or "").removeprefix("Bearer ").strip()
        s = store.sessions.get(tok)
        if s is None:
            raise HTTPException(401, "로그인이 필요합니다")
        return s

    def session(s: Session = Depends(any_session)) -> Session:
        """일정 시간 쓰지 않은 세션은 서버에서 잠근다. 잠금은 본인 비밀번호로만 푼다(/api/unlock)."""
        now = _time.monotonic()
        if s.locked or now - s.last_seen > IDLE_SEC:
            if not s.locked:
                s.locked = True
                store.log(s, "자동 잠금")
            raise HTTPException(401, "오래 쓰지 않아 화면이 잠겼습니다")
        s.last_seen = now
        return s

    def start(a: dict, how: str = "로그인") -> dict:
        tok = secrets.token_urlsafe(24)
        s = Session(tok, _public(a), ward=a.get("ward"))
        store.sessions[tok] = s
        store.log(s, how)
        return {"token": tok, "user": s.user, "screen": ROLE_SCREEN.get(a["role"], "admission")}

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/demo-accounts")
    def demo_accounts():
        return [_public(a) for a in store.accounts.values()]

    LOCKED_MSG = "로그인 실패가 반복되어 계정이 잠겼습니다. 관리자에게 잠금 해제를 요청하세요"

    @app.post("/api/login")
    def login(body: LoginIn):
        """5회 실패하면 계정을 잠그고, 잠긴 계정은 맞는 비밀번호로도 열리지 않는다. 관리자가 확인한 뒤 푼다 (고시 제5조⑥, EMR F003)."""
        a = store.accounts.get(body.user_id)
        who = {"user_id": body.user_id, "name": (a or {}).get("name", "")}
        if store.fails.get(body.user_id, 0) >= MAX_FAIL:
            store.log(who, "로그인 거부(잠긴 계정)")
            raise HTTPException(423, LOCKED_MSG)
        if a is None or not _check(a, body.password):
            n = store.fails[body.user_id] = store.fails.get(body.user_id, 0) + 1
            store.log(who, "로그인 실패", detail=f"{n}회")
            if n >= MAX_FAIL:
                store.log(who, "계정 잠김")
                raise HTTPException(423, LOCKED_MSG)
            raise HTTPException(401, "사번 또는 비밀번호가 맞지 않습니다")
        store.fails.pop(body.user_id, None)
        return start(a)

    @app.post("/api/demo-login")
    def demo_login(body: LoginIn):
        if os.environ.get("TURNAROUND_MODE") == "production":
            raise HTTPException(404, "운영 모드에서는 데모 로그인이 없습니다")
        a = store.accounts.get(body.user_id)
        if a is None:
            raise HTTPException(404, "없는 데모 계정입니다")
        if store.fails.get(body.user_id, 0) >= MAX_FAIL:
            store.log({"user_id": body.user_id, "name": a["name"]}, "로그인 거부(잠긴 계정)")
            raise HTTPException(423, LOCKED_MSG)
        return start(a, how="데모 로그인")

    @app.post("/api/tour/login")
    def tour_login():
        """시연: 원무 데모 계정으로 바로 들어가되, 이 세션만 쓰는 합성 병원을 새로 만든다(운영 화면 데이터와 따로)."""
        a = store.accounts["adm-kim"]
        out = start(a, how="시연 로그인")
        s = store.sessions[out["token"]]
        with _BUILD_LOCK:
            s.tour_state = build_hospital()
        tours = [t for t, x in store.sessions.items() if x.tour_state is not None]
        for t in tours[:-MAX_TOURS]:  # 오래된 시연 세션부터 정리
            store.sessions.pop(t, None)
        s.shift = "office"
        return {**out, "tour": True}

    @app.post("/api/tour/as/{uid}")
    def tour_as(uid: str, s: Session = Depends(session)):
        """시연 안내가 다른 팀 화면(감염관리실, 의료정보팀)을 보여 줄 때 쓴다. 시연 세션만, 같은 시연용 합성 병원을 그대로 쓴다."""
        if s.tour_state is None:
            raise HTTPException(403, "시연에서만 다른 팀 화면으로 바꿀 수 있습니다")
        a = store.accounts.get(uid)
        if a is None:
            raise HTTPException(404, "없는 계정입니다")
        s.user, s.ward = _public(a), a.get("ward")
        s.shift = "day" if a["role"] == "ward" else "office"
        store.log(s, "시연 화면 바꿈", detail=a["role_label"])
        return {"user": s.user, "screen": ROLE_SCREEN.get(a["role"], "admission")}

    @app.post("/api/accounts/{uid}/unlock")
    def unlock_account(uid: str, s: Session = Depends(session)):
        if s.user["role"] != "admin":
            raise HTTPException(403, "계정 잠금은 관리자만 풀 수 있습니다")
        if uid not in store.accounts:
            raise HTTPException(404, "없는 계정입니다")
        store.fails.pop(uid, None)
        store.log(s, "계정 잠금 해제", detail=uid)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(s: Session = Depends(any_session)):
        store.sessions.pop(s.token, None)
        store.log(s, "로그아웃")
        return {"ok": True}

    @app.get("/api/me")
    def me(s: Session = Depends(session)):
        st = store.demo(s)
        return {"user": s.user, "ward": s.ward, "shift": s.shift, "now": st.now.isoformat(timespec="minutes"),
                "screen": ROLE_SCREEN.get(s.user["role"], "admission")}

    @app.post("/api/shift")
    def shift(body: ShiftIn, s: Session = Depends(session)):
        acct_ward = store.accounts[s.user["user_id"]].get("ward")
        if s.user["role"] == "ward" and body.ward and body.ward != acct_ward:
            store.log(s, "근무 병동 변경 거부", detail=body.ward)
            raise HTTPException(403, "소속 병동이 아닌 곳으로 바꿀 수 없습니다")
        s.ward, s.shift = (acct_ward if s.user["role"] == "ward" else body.ward or s.ward), body.shift
        store.log(s, "근무 시작", detail=f"{s.ward or ''} {s.shift or ''}".strip())
        return {"ward": s.ward, "shift": s.shift}

    @app.get("/api/worklist")
    def worklist(s: Session = Depends(session)):
        from turnaround.api.worklist import admission_rows
        admission_only(s)
        st = store.demo(s)
        out = admission_rows(st)
        store.log(s, "목록 조회", detail="오늘 할 일")
        return out

    @app.get("/api/admissions")
    def admissions(s: Session = Depends(session)):
        from turnaround.api.worklist import admissions_today
        admission_only(s)
        out = admissions_today(store.demo(s), store.accounts)
        store.log(s, "목록 조회", detail="오늘 입원")
        return out

    @app.get("/api/patients/{case}")
    def patient(case: str, s: Session = Depends(session)):
        from turnaround.api.worklist import patient_detail
        st = store.demo(s)
        d = patient_detail(st, case, store.accounts)
        if d is None:
            raise HTTPException(404, "없는 사례입니다")
        if not may_view(st, s, case):
            store.log(s, "열람 거부", case=case)  # 권한 밖 열람 시도도 접속기록에 남긴다
            raise HTTPException(403, "이 환자를 볼 권한이 없습니다")
        store.log(s, "열람", case=case)
        return d

    def may_view(st, s: Session, case: str) -> bool:
        """최소 권한: 원무는 입원 대기 환자 전체, 병동은 자기 병동에 있거나 올 환자, 감염관리실은 격리 환자만."""
        role = s.user["role"]
        if role == "bed_manager":
            return True  # 의료정보팀(admin)은 환자 정보가 아니라 접속기록과 계정을 본다
        ev = st.store.read()
        if role == "ward":
            bed = st.gw._assignments(ev).get(case)
            ward = next((b.ward_id for b in st.h.beds if b.id == bed), None) if bed else None
            if ward is None:  # 아직 병상이 없으면 진료과 담당 병동 기준
                from turnaround.ui import views
                dept = (views._people(ev).get(case) or {}).get("department")
                ward = next((w.id for w in st.h.wards if dept in w.departments), None)
            return ward is not None and ward == s.ward
        if role in ("ipc", "id_oncall"):
            patient, _ = st.gw._admission(case, ev)
            return bool(patient and (patient.precautions or patient.isolation_order))
        return False

    # ---- 원무 처리 (P2). 기록은 사건 저장소에 사람 사건으로 남고, 접속기록에도 남는다 ----
    def admission_only(s: Session) -> None:
        if s.user["role"] != "bed_manager":
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
        st = store.demo(s)
        r = row_of(st, f"contact:{case}")
        memo = memo_of(body.memo, body.outcome in ("postpone", "cancel"))
        if body.outcome in ("postpone", "cancel"):
            # 통화 중 환자가 입원을 미루거나 취소하면 입원 취소로 기록하고 사가가 병상을 푼다. 연기는 새 날짜로 다시 접수(설계 가정)
            label = "입원 연기" if body.outcome == "postpone" else "입원 취소"
            human(st, case, f"cancel:{secrets.token_hex(3)}", EventType.ADMISSION_CANCELLED, by=s.user["user_id"],
                  reason=body.outcome, note=label)
            note(st, s, case, f"{label}: {memo}")
            if getattr(st, "saga", None) is not None:
                st.saga.step(st.now)
            store.log(s, label, case=case)
            return {"ok": True}
        if body.outcome == "no_answer":
            note(st, s, case, memo, tag="contact_no_answer")
            store.log(s, "입원 안내 연락 안 됨", case=case)
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
        st = store.demo(s)
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
        st = store.demo(s)
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
        out = board(store.demo(s), case)
        if out is None:
            raise HTTPException(404, "없는 사례입니다")
        store.log(s, "병상 현황 조회", case=case)
        return out

    @app.post("/api/assign")
    def assign(body: AssignIn, s: Session = Depends(session)):
        from turnaround.api.beds import REASON_CODES, refusal_ko
        admission_only(s)
        st = store.demo(s)
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

    @app.post("/api/unlock")
    def unlock(body: UnlockIn, s: Session = Depends(any_session)):
        """자동 잠금 해제: 같은 사람이 비밀번호를 다시 입력한다 (EMR 인증 해설서: 일정 시간 미사용 시 잠금, 화면보호기는 인정 안 됨)."""
        a = store.accounts.get(s.user["user_id"])
        if a is None or not _check(a, body.password):
            store.log(s, "잠금 해제 실패")
            raise HTTPException(401, "비밀번호가 맞지 않습니다")
        s.locked, s.last_seen = False, _time.monotonic()
        store.log(s, "잠금 해제")
        return {"ok": True}

    @app.post("/api/assignments/{case}/undo")
    def undo(case: str, body: ReasonIn, s: Session = Depends(session)):
        """에이전트 확정 배정을 시한 안에 되돌린다(W3). 연락 전이면 병상을 풀고 다시 찾고, 연락 뒤면 담당 검토와 재연락."""
        from turnaround.api.worklist import assignments
        admission_only(s)
        st = store.demo(s)
        if body.reason not in UNDO_REASONS:
            raise HTTPException(422, "되돌리는 사유를 골라 주세요")
        memo = memo_of(body.memo, body.reason == "기타")
        row = next((r for r in assignments(st, store.accounts)["rows"] if r["case"] == case and r["undo_open"]), None)
        if row is None:
            raise HTTPException(409, "되돌리기 시한이 지났거나 되돌릴 배정이 없습니다")
        from turnaround.events import EventType
        pid = next(e.payload.get("proposal_id") for e in reversed(st.store.read())
                   if e.type is EventType.WARD_ASSIGNMENT_NOTIFIED and e.case_id == case)
        d = st.gw.ward_objection(pid, f"원무 되돌림: {body.reason}", st.now)
        note(st, s, case, memo)
        store.log(s, "배정 되돌림", case=case, detail=f"{row['bed']} {body.reason}")
        return {"status": d.status}

    # ---- 병동 화면 (P3) ----
    def ward_only(s: Session) -> str:
        if s.user["role"] != "ward" or not s.ward:
            raise HTTPException(403, "병동 담당만 쓸 수 있습니다")
        return s.ward

    def my_ward_case(st, s: Session, case: str) -> None:
        bed = st.gw._assignments(st.store.read()).get(case)
        if not bed or next((b.ward_id for b in st.h.beds if b.id == bed), None) != s.ward:
            store.log(s, "병동 처리 거부", case=case)
            raise HTTPException(403, "우리 병동으로 오는 환자가 아닙니다")

    @app.get("/api/ward/incoming")
    def ward_incoming(s: Session = Depends(session)):
        from turnaround.api.ward import incoming
        w = ward_only(s)
        out = incoming(store.demo(s), w)
        store.log(s, "목록 조회", detail="입실 예정")
        return out

    @app.get("/api/ward/board")
    def ward_board(s: Session = Depends(session)):
        from turnaround.api.ward import board
        w = ward_only(s)
        store.log(s, "목록 조회", detail="병상 현황")
        return board(store.demo(s), w)

    @app.get("/api/ward/discharges")
    def ward_discharges(s: Session = Depends(session)):
        from turnaround.api.ward import discharges
        w = ward_only(s)
        store.log(s, "목록 조회", detail="퇴원 진행")
        return discharges(store.demo(s), w)

    @app.post("/api/ward/prep/{case}")
    def ward_prep(case: str, s: Session = Depends(session)):
        from turnaround.events import EventType
        ward_only(s)
        st = store.demo(s)
        my_ward_case(st, s, case)
        bed = st.gw._assignments(st.store.read()).get(case)
        human(st, case, "ward_prep_done", EventType.WARD_PREP_DONE, bed_id=bed, by=s.user["user_id"])
        store.log(s, "병동 준비 완료", case=case)
        return {"ok": True}

    @app.post("/api/ward/opinion/{case}")
    def ward_opinion(case: str, body: ReasonIn, s: Session = Depends(session)):
        """병동 의견: 배정을 풀지 않고 원무 할 일로 보낸다. 원무가 유지하거나 병상을 바꾼다."""
        from turnaround.api.ward import OPINION_REASONS
        from turnaround.events import EventType
        ward_only(s)
        st = store.demo(s)
        my_ward_case(st, s, case)
        if body.reason not in OPINION_REASONS:
            raise HTTPException(422, "의견 사유를 골라 주세요")
        memo = memo_of(body.memo, body.reason == "기타")
        bed = st.gw._assignments(st.store.read()).get(case)
        human(st, case, f"ward_opinion:{secrets.token_hex(3)}", EventType.HUMAN_TASK_CREATED, kind="ward_opinion",
              assignee="bed_manager", bed_id=bed, reason=body.reason, by=s.user["user_id"], due=(st.now + timedelta(minutes=30)).isoformat())  # 원무 회신 기한 30분(설계 가정)
        note(st, s, case, f"병동 의견({body.reason}): {memo}" if memo else f"병동 의견: {body.reason}")
        store.log(s, "병동 의견", case=case, detail=body.reason)
        return {"ok": True}

    # ---- 감염관리실 (P4): 늦은 격리 병상 결정, 종말소독 대기, 격리 오더 ----
    IPC_DECLINE = ("지금 병상 유지(제자리 격리)", "원무팀이 직접 처리", "다른 병동과 협의 중", "기타")

    def ipc_only(s: Session) -> None:
        if s.user["role"] not in ("ipc", "id_oncall"):
            raise HTTPException(403, "감염관리실만 쓸 수 있습니다")

    @app.get("/api/ipc")
    def ipc_home(s: Session = Depends(session)):
        from turnaround.api.ipc import home
        from turnaround.api.worklist import notes
        ipc_only(s)
        st = store.demo(s)
        ev = st.store.read()
        out = home(st, lambda case: notes(ev, case, store.accounts))
        handover = [n for n in notes(ev, "IPC-HANDOVER", store.accounts)][-3:]
        store.log(s, "목록 조회", detail="감염관리")
        return {**out, "handover": handover, "decline_reasons": list(IPC_DECLINE)}

    @app.post("/api/ipc/check/{case}")
    def ipc_check(case: str, s: Session = Depends(session)):
        """오늘 들어올 격리 환자의 배정 병상을 감염관리실이 확인했다는 표시."""
        ipc_only(s)
        st = store.demo(s)
        note(st, s, case, "감염관리실 배정 확인", tag="ipc_checked")
        store.log(s, "격리 배정 확인", case=case)
        return {"ok": True}

    @app.post("/api/ipc/handover")
    def ipc_handover(body: ReasonIn, s: Session = Depends(session)):
        """야간 당직에게 넘기는 인계 메모 (자유 서술. 계획 에이전트는 읽지 않는다)."""
        ipc_only(s)
        st = store.demo(s)
        text = memo_of(body.memo, True)
        note(st, s, "IPC-HANDOVER", text, tag="handover")
        store.log(s, "감염관리 인계 메모")
        return {"ok": True}

    @app.post("/api/ipc/approvals/{eid}/{verb}")
    def ipc_decide(eid: str, verb: str, body: ReasonIn, s: Session = Depends(session)):
        """늦은 격리의 병상 결정은 감염관리실(야간·휴일은 감염내과 당직)만 한다. 게이트웨이가 근무 중인 사람인지 다시 확인한다."""
        ipc_only(s)
        st = store.demo(s)
        role = s.user["role"]
        if verb == "approve":
            d = st.gw.approve_action(eid, role, st.now, approver_id=s.user["user_id"])
        elif verb == "decline":
            if body.reason not in IPC_DECLINE:
                raise HTTPException(422, "결정 사유를 골라 주세요")
            memo = memo_of(body.memo, body.reason == "기타")
            d = st.gw.decline_action(eid, role, body.reason + (f": {memo}" if memo else ""), st.now, approver_id=s.user["user_id"])
        else:
            raise HTTPException(404, "없는 처리입니다")
        if d.status in ("rejected", "unknown"):
            raise HTTPException(409, "처리할 수 없습니다: " + ", ".join(d.reasons or [d.status]))
        case = next((e.case_id for e in st.store.read() if e.payload.get("exception_id") == eid), None)
        store.log(s, "격리 병상 결정", case=case, detail=f"{'승인' if verb == 'approve' else '거절: ' + body.reason}")
        return {"status": d.status}

    # ---- 관리자(의료정보팀) (P4): 흐름과 안전 요약, 감사용 판단 경과(이름 가림), 접속기록 ----
    def admin_only(s: Session) -> None:
        if s.user["role"] != "admin":
            raise HTTPException(403, "관리자만 볼 수 있습니다")

    def _flow_summary(s: Session, st) -> dict:
        from turnaround.ui import views
        m = views.manager_view(st)
        hl = m["human_load"]
        load = {"입원 사례": hl.get("admission_cases"), "확정": hl.get("committed"),
                "사람 손을 안 거친 확정": hl.get("no_touch_committed"),
                "사례당 사람 판단": hl.get("judgement_per_case"), "자동 확정 비율": hl.get("fully_automatic_ratio")}
        return {"flow": m["flow"], "safety": m["safety"], "load": load, "unassigned": m["unassigned"], "note": "합성 데이터 기준 추정"}

    @app.get("/api/ops/summary")
    def ops_summary(s: Session = Depends(session)):
        """원무팀장의 오늘 흐름: 퇴실, 병상 준비, 입실 대기, 배정 못 한 이유. 집계만 보고 환자 상세는 없다."""
        if s.user["role"] != "bed_lead":
            raise HTTPException(403, "원무팀장만 볼 수 있습니다")
        out = _flow_summary(s, store.demo(s))
        store.log(s, "목록 조회", detail="오늘 흐름")
        return out

    # ---- AI 운영 (의료정보팀): 에이전트 성과, 막은 판단, 자율 범위, 모델과 평가, 새 병원 설정 ----
    def _onboarding(s: Session | None = None):
        from turnaround.api import ai_ops
        if s is not None and s.tour_state is not None:  # 시연에서 승인해도 운영 설정판에 남지 않게
            if s.tour_onboarding is None:
                s.tour_onboarding = ai_ops.build_onboarding()
            return s.tour_onboarding
        with _BUILD_LOCK:
            if store.onboarding is None:
                store.onboarding = ai_ops.build_onboarding()
            return store.onboarding

    @app.get("/api/differentiators")
    def differentiators():
        """시연 첫 화면의 차별점 표. 숫자는 측정 요약(합성 데이터 기준 추정)과 표본 새 병원 설정판에서 읽는다."""
        from turnaround.api import ai_ops
        return ai_ops.differentiators(ai_ops.onboarding_out(_onboarding()))

    @app.get("/api/admin/ai-ops")
    def ai_ops_home(s: Session = Depends(session)):
        from turnaround.api import ai_ops
        from turnaround.ui import views
        admin_only(s)
        st = store.demo(s)
        f = _flow_summary(s, st)
        r = ai_ops.results()
        blocked = views.blocked_decisions(st, limit=50)
        reasons: dict[str, int] = {}
        for b in blocked:
            for x in (b["reason"] or "기타").split(", "):
                reasons[x] = reasons.get(x, 0) + 1
        store.log(s, "목록 조회", detail="AI 운영")
        return {
            "today": {"committed": f["load"]["확정"], "no_touch": f["load"]["사람 손을 안 거친 확정"],
                      "admissions": f["load"]["입원 사례"],
                      "safety": sum(int(x["위반"] or 0) for x in f["safety"]), "safety_rows": f["safety"]},
            "measured": {k: r[k] for k in ("no_touch", "entry_wait", "entry_breakdown", "safety", "autonomy")}, "note": r["note"],
            "blocked": blocked[::-1], "blocked_reasons": sorted(reasons.items(), key=lambda x: -x[1]),
            "autonomy": ai_ops.autonomy(st), "autonomy_rule": ai_ops.rule_text(st),
            "model": ai_ops.model_info(), "onboarding": ai_ops.onboarding_out(_onboarding(s)),
        }

    @app.post("/api/admin/autonomy/{unit}")
    def set_autonomy(unit: str, body: AutonomyIn, s: Session = Depends(session)):
        from turnaround.api import ai_ops
        admin_only(s)
        if body.to not in ("auto", "human"):
            raise HTTPException(422, "to는 auto 또는 human입니다")
        st = store.demo(s)
        try:
            ai_ops.set_autonomy(st, unit, body.to)
        except ValueError as e:
            store.log(s, "자율 범위 변경 거부", detail=f"{unit} {body.to}")
            raise HTTPException(409, str(e))
        name = ai_ops.UNIT_KO.get(unit, unit)
        store.log(s, "자율 범위 변경", detail=f"{name} → {'에이전트' if body.to == 'auto' else '사람 확인'} {body.memo}".strip())
        return {"ok": True, "autonomy": ai_ops.autonomy(st),
                "message": f"{name}: {'이제 에이전트가 확정합니다' if body.to == 'auto' else '이제 사람이 확인합니다'}"}

    @app.post("/api/admin/onboarding/approve")
    def onboarding_approve(s: Session = Depends(session)):
        from turnaround.agent.onboarding_flow import OnboardingError
        from turnaround.api import ai_ops
        admin_only(s)
        ob = _onboarding(s)
        try:
            ob["pkg"] = ob["pkg"].approve(by_role="medical_informatics", user_id=s.user["user_id"], acknowledge_profile_questions=True)
        except OnboardingError as e:
            raise HTTPException(409, str(e))
        store.log(s, "새 병원 설정 승인", detail=ob["pkg"].hospital)
        return ai_ops.onboarding_out(ob)

    @app.get("/api/admin/summary")
    def admin_summary(s: Session = Depends(session)):
        from turnaround.ui import views
        admin_only(s)
        st = store.demo(s)
        m = views.manager_view(st)
        hl = m["human_load"]
        load = {"입원 사례": hl.get("admission_cases"), "확정": hl.get("committed"),
                "사람 손을 안 거친 확정": hl.get("no_touch_committed"),
                "사례당 사람 판단": hl.get("judgement_per_case"), "자동 확정 비율": hl.get("fully_automatic_ratio")}
        locked = [{"user_id": u, "name": store.accounts[u]["name"], "fails": n} for u, n in store.fails.items()
                  if n >= MAX_FAIL and u in store.accounts]
        store.log(s, "목록 조회", detail="관리자 요약")
        return {"flow": m["flow"], "safety": m["safety"], "load": load, "unassigned": m["unassigned"], "locked": locked,
                "note": "합성 데이터 기준 추정"}

    @app.get("/api/admin/case/{case}")
    def admin_case(case: str, s: Session = Depends(session)):
        """감사용: 한 환자의 판단 경과를 처음부터 따라간다. 진료 정보가 아니라 결정 기록이고 이름은 가린다."""
        from turnaround.api.worklist import _timeline
        from turnaround.ui import views
        admin_only(s)
        st = store.demo(s)
        t = views.case_timeline(st, case)
        if not t["rows"]:
            raise HTTPException(404, "없는 사례입니다")
        store.log(s, "감사 열람", case=case)
        return {"case": case, "patient": t["who"], "status": t["status"], "bed": t["bed_ko"],
                "trail": [dict(r, who=r["who"]) for r in _timeline(t["rows"])]}

    # ---- 시연 화면 (P5): 로그인 없이, 운영 데이터와 따로 ----
    @app.get("/api/tour")
    def tour_chapters():
        from turnaround.api.demo_tour import chapters
        return {"chapters": chapters()}

    @app.post("/api/tour/start")
    def tour_start(body: TourIn):
        from turnaround.api.demo_tour import start
        out = start(body.chapter)
        if out is None:
            raise HTTPException(404, "없는 사례입니다")
        return out

    @app.post("/api/tour/{rid}/next")
    def tour_next(rid: str):
        from turnaround.api.demo_tour import step
        out = step(rid)
        if out is None:
            raise HTTPException(404, "시연이 끝났거나 없습니다. 다시 시작하세요")
        return out

    @app.get("/api/assignments")
    def assignment_log(day: str | None = None, s: Session = Depends(session)):
        from datetime import date as _date

        from turnaround.api.worklist import assignments
        admission_only(s)
        try:
            _date.fromisoformat(day) if day else None
        except ValueError:
            raise HTTPException(422, "날짜는 YYYY-MM-DD로 적어 주세요") from None
        out = assignments(store.demo(s), store.accounts, day)
        store.log(s, "목록 조회", detail="배정 기록")
        return out

    @app.get("/api/access-log")
    def access_log(s: Session = Depends(session)):
        if s.user["role"] != "admin":
            raise HTTPException(403, "접속기록은 관리자만 볼 수 있습니다")
        store.log(s, "접속기록 조회")
        return {"rows": store.access_log.rows()}

    @app.get("/api/access-log/verify")
    def access_log_verify(s: Session = Depends(session)):
        if s.user["role"] != "admin":
            raise HTTPException(403, "접속기록은 관리자만 볼 수 있습니다")
        return {"ok": store.access_log.verify()}

    # 빌드된 화면(web/dist)이 있으면 같은 주소에서 내준다(한 컨테이너 배포). API 경로 뒤에 둔다.
    dist = ROOT / "web" / "dist"
    if dist.exists():
        from fastapi.staticfiles import StaticFiles
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
