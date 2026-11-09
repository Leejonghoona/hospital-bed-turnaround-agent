"""S6: 데모 D2 장면 시나리오(개발용 별도 세트, 현장 프로필)."""

from pathlib import Path

import pytest

from turnaround.evaluation.runners_demo import DEMO_RUNNERS
from turnaround.evaluation.scenario import load_scenarios

DEMO = Path(__file__).resolve().parents[1] / "evals" / "scenarios" / "demo"


@pytest.mark.parametrize("s", load_scenarios(DEMO), ids=lambda s: s.id)
def test_demo_scene_passes(s):
    r = DEMO_RUNNERS[s.id](s)
    assert r.passed, r.failures


def test_demo_set_is_separate_from_regression_and_holdout():
    ids = {s.id for s in load_scenarios(DEMO)}
    assert ids == {"X01", "X02", "X03"} and all(i.startswith("X") for i in ids)
