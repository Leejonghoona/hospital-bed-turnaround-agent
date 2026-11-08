"""2단계: 이벤트 저장소 테스트.

합격 조건: 추가만 하는 기록, 이벤트 ID(멱등 키)로 중복 방지, 재시작 후 유지,
발생 시각(occurred_at)과 인지 시각(observed_at) 분리, 미래 정보 누수 0건(평가표 S4).
"""

import sqlite3
from datetime import datetime, timedelta

import pytest

from turnaround.events import Event, EventStore, EventType, NoiseConfig, hospital_to_events
from turnaround.sim import generate

T = datetime(2026, 3, 2, 9, 0)


def ev(eid="e1", case="SYN-D001", etype=EventType.PATIENT_DEPARTED, occ=T, obs=None, **payload):
    return Event(event_id=eid, case_id=case, type=etype, occurred_at=occ, observed_at=obs or occ,
                 source="his", payload=payload)


@pytest.fixture
def store():
    s = EventStore(":memory:")
    yield s
    s.close()


# ---------- 기본 동작 ----------

def test_append_assigns_increasing_seq(store):
    assert store.append(ev("e1")) == 1
    assert store.append(ev("e2")) == 2
    assert store.last_seq() == 2


def test_duplicate_event_id_is_ignored(store):
    assert store.append(ev("e1")) == 1
    assert store.append(ev("e1")) is None
    assert len(store.read()) == 1
    assert store.append(ev("e2")) == 2  # 중복이 순번을 소비하지 않는다


def test_observed_before_occurred_beyond_tolerance_is_rejected():
    # EVT-6 이후: 허용 범위(5분) 안의 시계 차이는 받아들이고, 넘으면 거부한다
    with pytest.raises(ValueError):
        ev(occ=T, obs=T - timedelta(minutes=10))


def test_payload_roundtrip(store):
    store.append(ev("e1", bed_id="W71-R02-B1", cleaning_type="terminal"))
    got = store.read()[0]
    assert got.payload == {"bed_id": "W71-R02-B1", "cleaning_type": "terminal"}
    assert got.occurred_at == T and got.seq == 1


# ---------- 추가만 하는 기록 ----------

def test_update_is_forbidden(store):
    store.append(ev("e1"))
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("UPDATE events SET case_id = 'X'")


def test_delete_is_forbidden(store):
    store.append(ev("e1"))
    with pytest.raises(sqlite3.DatabaseError):
        store._conn.execute("DELETE FROM events")


# ---------- 재시작 후 유지 ----------

def test_events_survive_reopen(tmp_path):
    path = tmp_path / "events.sqlite"
    s = EventStore(path)
    s.append(ev("e1"))
    s.append(ev("e2"))
    del s  # close 없이 버려도(강제 종료 흉내) 커밋된 기록은 남아야 한다
    s2 = EventStore(path)
    assert [e.event_id for e in s2.read()] == ["e1", "e2"]
    assert s2.append(ev("e3")) == 3
    assert s2.append(ev("e1")) is None
    s2.close()


# ---------- 조회 ----------

def test_read_filters_by_case_and_after_seq(store):
    store.append(ev("e1", case="A"))
    store.append(ev("e2", case="B"))
    store.append(ev("e3", case="A"))
    assert [e.event_id for e in store.read(case_id="A")] == ["e1", "e3"]
    assert [e.event_id for e in store.read(after_seq=1)] == ["e2", "e3"]


def test_as_of_hides_events_not_yet_observed(store):
    store.append(ev("late", occ=T, obs=T + timedelta(minutes=30)))
    store.append(ev("now", occ=T, obs=T))
    assert [e.event_id for e in store.read(as_of=T)] == ["now"]
    assert {e.event_id for e in store.read(as_of=T + timedelta(minutes=30))} == {"late", "now"}


# ---------- 합성 데이터 → 이벤트 ----------

@pytest.fixture(scope="module")
def hospital():
    return generate(seed=42)


def test_replay_covers_every_discharge_and_admission(hospital):
    events = hospital_to_events(hospital)
    cases = {e.case_id for e in events}
    assert {d.id for d in hospital.discharges} <= cases
    assert {a.id for a in hospital.admissions} <= cases


def test_replay_event_ids_are_unique_without_noise(hospital):
    events = hospital_to_events(hospital)
    assert len({e.event_id for e in events}) == len(events)


def test_replay_is_deterministic(hospital):
    a = [e.model_dump() for e in hospital_to_events(hospital, seed=1)]
    b = [e.model_dump() for e in hospital_to_events(hospital, seed=1)]
    assert a == b


def test_unregistered_discharge_has_no_registration_event(hospital):
    events = hospital_to_events(hospital)
    registered = {e.case_id for e in events if e.type is EventType.DISCHARGE_REGISTERED}
    for d in hospital.discharges:
        assert (d.id in registered) == (d.registered_at is not None)


def test_guardian_delay_is_known_only_through_eta_update(hospital):
    """지연된 보호자의 실제 도착 시각은 도착 이벤트로만 알 수 있고, 예정 변경은 그보다 먼저 온다."""
    events = hospital_to_events(hospital)
    for d in (d for d in hospital.discharges if d.guardian_delayed):
        mine = [e for e in events if e.case_id == d.id]
        upd = [e for e in mine if e.type is EventType.GUARDIAN_ETA_UPDATED]
        arr = [e for e in mine if e.type is EventType.GUARDIAN_ARRIVED]
        assert len(upd) == 1 and len(arr) == 1
        assert upd[0].observed_at < arr[0].observed_at


def test_cleaning_request_carries_terminal_type_for_isolation(hospital):
    events = hospital_to_events(hospital)
    for d in hospital.discharges:
        req = [e for e in events if e.case_id == d.id and e.type is EventType.CLEANING_REQUESTED]
        assert len(req) == 1
        assert req[0].payload["cleaning_type"] == d.cleaning_type.value


def test_ingesting_replay_twice_is_idempotent(hospital):
    events = hospital_to_events(hospital)
    s = EventStore(":memory:")
    s.extend(events)
    n = s.last_seq()
    s.extend(events)
    assert s.last_seq() == n == len(events)
    s.close()


# ---------- 미래 정보 누수 (S4) ----------

@pytest.mark.parametrize("seed", range(0, 20))
def test_no_future_event_is_visible_at_any_cut(seed):
    h = generate(seed=seed)
    events = hospital_to_events(h, seed=seed)
    s = EventStore(":memory:")
    s.extend(events)
    cuts = [datetime(2026, 3, 2 + d, hh, 5) for d in range(h.config.days) for hh in (9, 12, 15)]
    for cut in cuts:
        seen = s.read(as_of=cut)
        assert all(e.observed_at <= cut for e in seen)
        assert {e.event_id for e in seen} == {e.event_id for e in events if e.observed_at <= cut}
    s.close()


def test_departure_is_not_known_at_nine_on_the_same_day(hospital):
    """설계 PoC 09:05 시점에 그날 퇴실 시각을 미리 알 수 없어야 한다."""
    s = EventStore(":memory:")
    s.extend(hospital_to_events(hospital))
    for d in hospital.discharges:
        cut = d.actual.departed_at.replace(hour=9, minute=5)
        if d.actual.departed_at <= cut:
            continue
        seen = s.read(case_id=d.id, as_of=cut)
        assert not any(e.type is EventType.PATIENT_DEPARTED for e in seen)
    s.close()


# ---------- 잡음 주입 ----------

def test_noise_adds_duplicates_reorders_and_drops(hospital):
    clean = hospital_to_events(hospital, seed=3)
    noisy = hospital_to_events(hospital, seed=3, noise=NoiseConfig(duplicates=5, reorders=4, drops=2))
    assert len(noisy) == len(clean) + 5 - 2
    dup_ids = [e.event_id for e in noisy]
    assert len(dup_ids) - len(set(dup_ids)) == 5  # 같은 이벤트 ID로 다시 도착
    dropped = {e.event_id for e in clean} - {e.event_id for e in noisy}
    assert len(dropped) == 2
    assert all(eid.endswith(":cleaning_done") for eid in dropped)  # 완료 신호 누락
    late = [e for e in noisy if (e.observed_at - e.occurred_at) > timedelta(minutes=20)]
    assert len(late) >= 4  # 늦게 인지돼 순서가 뒤바뀐 이벤트


def test_noisy_stream_still_has_no_future_leak(hospital):
    noisy = hospital_to_events(hospital, seed=3, noise=NoiseConfig(duplicates=5, reorders=4, drops=2))
    s = EventStore(":memory:")
    s.extend(noisy)
    cut = datetime(2026, 3, 3, 12, 0)
    assert all(e.observed_at <= cut for e in s.read(as_of=cut))
    assert s.last_seq() == len({e.event_id for e in noisy})
    s.close()


# ---------- EVT-6 시계 차이 허용 ----------

def test_small_clock_skew_is_accepted_and_marked():
    e = ev(occ=T, obs=T - timedelta(minutes=3))
    assert e.observed_at == T  # 발생 전에 알 수는 없으므로 발생 시각으로 맞춘다
    assert e.payload["_clock_skew_s"] == 180


def test_large_clock_skew_is_still_rejected():
    with pytest.raises(ValueError):
        ev(occ=T, obs=T - timedelta(minutes=6))
