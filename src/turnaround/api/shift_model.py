"""이전 근무가 처리한 일 (데모 화면용 사람 응답 모형, 설계 가정). 합성 데이터.

시뮬레이션은 사람이 작업을 처리하는 장면을 만들지 않아 어제 올라온 작업이 아침 화면에 그대로 쌓인다(3회차 현업 검토: "마감 지남 10건"은
실제 아침 화면과 다르다). 데모 화면에서는 오늘 낮 근무 시작(07:00) 전에 올라온 원무 작업을 야간 당직 원무가 처리했다고 기록하고,
가장 최근 몇 건만 '이전 근무에서 넘어온 일'로 남긴다. 측정(재생 평가)에는 쓰지 않는다(DECISIONS 2026-11-10).
"""

from __future__ import annotations

from datetime import datetime, time

from turnaround.events import Event, EventType
from turnaround.saga.worklist import open_tasks

KEEP_CARRIED = 2  # 넘어온 일로 남길 건수 (설계 가정)


def apply_night_shift(st) -> int:
    ev = st.store.read()
    start = datetime.combine(st.now.date(), time(7, 0))
    old = sorted((t for t in open_tasks(ev, st.now).values()
                  if t.type is EventType.HUMAN_TASK_CREATED and t.observed_at < start
                  and (t.payload.get("assignee") in (None, "bed_manager", "admission_office"))),
                 key=lambda t: t.observed_at)
    done = old[:-KEEP_CARRIED] if len(old) > KEEP_CARRIED else []
    for t in done:
        st.store.append(Event(event_id=f"{t.event_id}:night_done", case_id=t.case_id, type=EventType.HUMAN_TASK_DONE,
                              occurred_at=start, observed_at=start, source="human",
                              payload={"task_id": t.event_id, "by": "night_admin", "result": "야간 근무에서 처리"}))
    return len(done)
