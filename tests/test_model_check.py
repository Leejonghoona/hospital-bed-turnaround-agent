"""모델 성능점검 하네스 (EVL-4, M1)."""

from turnaround.evaluation import extraction_set
from turnaround.evaluation.model_check import Recorder, bootstrap_ci, extraction_eval, mcnemar_exact
from turnaround.agent import FakeLLM


def test_extraction_set_has_200_items_split_and_draft_status():
    dev, hold = extraction_set.load("dev"), extraction_set.load("holdout")
    assert len(dev) == 100 and len(hold) == 100
    assert all(x["status"] == "draft" for x in dev + hold)  # 사용자 검토 전 초안
    assert len({x["id"] for x in dev + hold}) == 200


def test_extraction_set_is_reproducible():
    built = extraction_set.build()
    assert built["dev"] == extraction_set.load("dev") and built["holdout"] == extraction_set.load("holdout")


def test_injection_sentences_never_appear_in_gold():
    for x in extraction_set.load("dev") + extraction_set.load("holdout"):
        assert set(x["gold"]) <= {"guardian_eta", "delay_reason", "mobility_aid", "single_room_wish"}


def test_fake_llm_extraction_metrics_and_injection_pairs():
    r = extraction_eval(Recorder(FakeLLM()), "dev")
    assert r["n"] == 100 and 0 <= r["E1_field_accuracy"] <= 1
    assert r["I1_injection_pairs_changed"] == 0  # 지시문을 빼도 결과가 같다
    lo, hi = r["E1_ci95"]
    assert lo <= r["E1_field_accuracy"] <= hi


def test_mcnemar_and_bootstrap():
    assert mcnemar_exact([True] * 10, [True] * 10)["p"] == 1.0
    m = mcnemar_exact([True] * 10 + [False] * 0, [False] * 10)
    assert m["a_only"] == 10 and m["p"] < 0.01
    lo, hi = bootstrap_ci([1.0] * 50 + [0.0] * 50)
    assert lo < 0.5 < hi
