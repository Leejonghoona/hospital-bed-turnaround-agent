"""SQLite 이벤트 저장소. 추가만 하는 기록이고, 이벤트 ID로 중복을 막는다."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .models import Event, EventType

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL UNIQUE,
    case_id     TEXT NOT NULL,
    type        TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source      TEXT NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_case ON events(case_id, seq);
CREATE INDEX IF NOT EXISTS ix_events_observed ON events(observed_at);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events는 추가만 할 수 있다'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events는 추가만 할 수 있다'); END;
"""


def _ts(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class EventStore:
    def __init__(self, path: str | Path = ":memory:"):
        # 화면(Streamlit)은 다른 스레드에서 다시 그린다. 쓰기는 한 실행기에서만 하므로 스레드 검사를 끈다
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        # 추가만 하는 기록이라 한 번 읽은 이벤트는 바뀌지 않는다. 해석한 이벤트를 순번 순서로 기억하고 새 행만 읽는다
        self._cache: list[Event] = []

    def close(self) -> None:
        self._conn.close()

    def _insert(self, e: Event) -> int | None:
        # 중복을 먼저 걸러 순번에 빈칸이 생기지 않게 한다. UNIQUE 제약은 마지막 방어선이다.
        if self._conn.execute("SELECT 1 FROM events WHERE event_id = ?", (e.event_id,)).fetchone():
            return None
        cur = self._conn.execute(
            "INSERT INTO events (event_id, case_id, type, occurred_at, observed_at, source, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (e.event_id, e.case_id, e.type.value, _ts(e.occurred_at), _ts(e.observed_at), e.source,
             json.dumps(e.payload, ensure_ascii=False, sort_keys=True, default=str)),
        )
        return cur.lastrowid

    def append(self, e: Event) -> int | None:
        """새 순번을 돌려준다. 이미 있는 이벤트 ID면 None."""
        with self._conn:
            return self._insert(e)

    def extend(self, events: Iterable[Event]) -> list[int | None]:
        with self._conn:
            return [self._insert(e) for e in events]

    def last_seq(self) -> int:
        return self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    def read(self, case_id: str | None = None, as_of: datetime | None = None,
             after_seq: int | None = None) -> list[Event]:
        """as_of를 주면 그 시각까지 인지된 이벤트만 돌려준다(미래 정보 차단)."""
        last = self._cache[-1].seq if self._cache else 0
        self._cache += self._read_db(after_seq=last)
        out = self._cache
        if after_seq is not None:
            out = [e for e in out if e.seq > after_seq]
        if case_id is not None:
            out = [e for e in out if e.case_id == case_id]
        if as_of is not None:
            cut = datetime.fromisoformat(_ts(as_of))
            out = [e for e in out if e.observed_at <= cut]
        return list(out)

    def _read_db(self, case_id: str | None = None, as_of: datetime | None = None,
                 after_seq: int | None = None) -> list[Event]:
        sql, args = "SELECT seq, event_id, case_id, type, occurred_at, observed_at, source, payload FROM events", []
        cond = []
        if case_id is not None:
            cond.append("case_id = ?"); args.append(case_id)
        if as_of is not None:
            cond.append("observed_at <= ?"); args.append(_ts(as_of))
        if after_seq is not None:
            cond.append("seq > ?"); args.append(after_seq)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY seq"
        return [
            Event(seq=r[0], event_id=r[1], case_id=r[2], type=EventType(r[3]),
                  occurred_at=datetime.fromisoformat(r[4]), observed_at=datetime.fromisoformat(r[5]),
                  source=r[6], payload=json.loads(r[7]))
            for r in self._conn.execute(sql, args)
        ]
