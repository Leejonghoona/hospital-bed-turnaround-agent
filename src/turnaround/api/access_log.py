"""접속기록: 파일(SQLite)에 추가만 하고, 앞 줄의 해시를 이어 붙여 위·변조를 확인할 수 있게 한다.

근거(docs/검토/현실괴리/05_관리자_보안.md, 원문 확인): 개인정보의 안전성 확보조치 기준 제8조 "1년 이상 보관ㆍ관리"(민감정보 2년),
"위ㆍ변조 및 도난, 분실되지 않도록", EMR 인증 해설서 F008 "식별자, 접속 일시, 접속지 정보, 처리한 정보주체 정보, 수행업무",
"시간서버에서 제공하는 정확한 시각". 시각은 서버 실제 시각(UTC)이고, 합성 병원 시각은 따로 적는다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_COLS = ("real_at", "sim_at", "user_id", "name", "ip", "case_id", "action", "detail", "prev", "hash")


class AccessLog:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute("create table if not exists access_log (id integer primary key autoincrement, "
                         + ", ".join(f"{c} text" for c in _COLS) + ")")
        # 고치거나 지우지 못하게 막는다 (추가만 하는 기록)
        self._db.execute("create trigger if not exists no_update before update on access_log begin "
                         "select raise(abort, 'append only'); end")
        self._db.execute("create trigger if not exists no_delete before delete on access_log begin "
                         "select raise(abort, 'append only'); end")
        self._db.commit()

    @staticmethod
    def _digest(prev: str, row: dict) -> str:
        body = json.dumps({k: row[k] for k in _COLS[:-2]}, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256((prev + body).encode()).hexdigest()

    def append(self, *, sim_at: str, user_id: str, name: str, ip: str, case: str | None, action: str, detail: str) -> None:
        with self._lock:
            last = self._db.execute("select hash from access_log order by id desc limit 1").fetchone()
            row = {"real_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sim_at": sim_at,
                   "user_id": user_id, "name": name, "ip": ip or "", "case_id": case or "", "action": action,
                   "detail": detail or "", "prev": last[0] if last else ""}
            row["hash"] = self._digest(row["prev"], row)
            self._db.execute(f"insert into access_log ({', '.join(_COLS)}) values ({', '.join('?' for _ in _COLS)})",
                             [row[c] for c in _COLS])
            self._db.commit()

    def rows(self, limit: int = 500) -> list[dict]:
        cur = self._db.execute(f"select {', '.join(_COLS)} from access_log order by id desc limit ?", (limit,))
        out = []
        for r in cur.fetchall():
            d = dict(zip(_COLS, r))
            d["case"] = d.pop("case_id") or None
            d["at"] = d["sim_at"]
            out.append(d)
        return out

    def verify(self) -> bool:
        prev = ""
        for r in self._db.execute(f"select {', '.join(_COLS)} from access_log order by id").fetchall():
            d = dict(zip(_COLS, r))
            if d["prev"] != prev or self._digest(prev, d) != d["hash"]:
                return False
            prev = d["hash"]
        return True
