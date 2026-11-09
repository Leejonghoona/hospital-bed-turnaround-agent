"""M6-2·3: 서로 다른 병원 내보내기 형식 → 매핑 → 같은 정규 사건 (로드맵 07)."""

import pytest

from turnaround.events.replay import hospital_to_events
from turnaround.ontology.mapping import apply_mapping
from turnaround.sim import GeneratorConfig, generate
from turnaround.sim.exports import FORMATS, export, gold_mapping


def _canonical(seed):
    h = generate(seed=seed, config=GeneratorConfig.realistic())
    ev = [e.model_copy(update={"observed_at": e.occurred_at}) for e in hospital_to_events(h, seed=seed)]
    return h, ev


def _key(e):
    return (e.event_id, e.type, e.occurred_at, e.source, tuple(sorted((k, str(v)) for k, v in e.payload.items())))


def test_formats_really_differ():
    h, ev = _canonical(1)
    cols = [set(next(iter(export(h, ev, f).values()))[0]) for f in FORMATS]
    assert not (cols[0] & cols[1]) and not (cols[1] & cols[2])


@pytest.mark.parametrize("fmt", FORMATS)
@pytest.mark.parametrize("seed", [0, 1, 2])
def test_gold_mapping_restores_canonical_events_and_layout(fmt, seed):
    h, ev = _canonical(seed)
    out = apply_mapping(gold_mapping(fmt), export(h, ev, fmt))
    assert sorted(map(_key, out.events)) == sorted(map(_key, ev))
    assert out.rooms == h.rooms and out.beds == h.beds
    assert {w.id: w.departments for w in out.wards} == {w.id: w.departments for w in h.wards}
