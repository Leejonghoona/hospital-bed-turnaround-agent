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


def test_v2_sets_have_designed_sizes_and_draft_status():
    from turnaround.evaluation.eval_sets import load
    sizes = {n: len(load(n)) for n in ("extraction_v2_holdout", "injection", "planning", "qa")}
    assert sizes == {"extraction_v2_holdout": 400, "injection": 60, "planning": 200, "qa": 150}
    assert all(x["status"] == "draft" for n in sizes for x in load(n))


def test_injection_attacks_differ_from_gold_so_success_is_detectable():
    from turnaround.evaluation.eval_sets import load
    for x in load("injection"):
        assert x["gold"].get(x["attack_field"]) != x["attack_value"]


def test_planning_gold_contains_rule_choice():
    from turnaround.evaluation.eval_sets import load
    for x in load("planning"):
        assert x["context"]["candidates"][0]["bed_id"] in x["gold_beds"]


def test_budget_guard_stops_before_overspending():
    from turnaround.agent.llm import Usage
    from turnaround.evaluation.model_check import Budgeted, BudgetExceeded, injection_v2

    class Costly(FakeLLM):
        def complete_json(self, task, system, user):
            self.usage.input_tokens += 100_000  # 호출마다 0.5달러 (입력 5달러/100만 토큰)
            return super().complete_json(task, system, user)

    r = injection_v2(Budgeted(Costly(), {"input": 5.0, "output": 30.0}, max_usd=1.0))
    assert r["stopped"] and r["pairs"] == 1  # 2호출(1쌍) 뒤 1달러 도달로 멈춤


class _Broken(FakeLLM):
    def complete_json(self, task, system, user):
        from turnaround.agent.llm import LLMError
        raise LLMError("형식 오류")


def test_format_errors_are_wrong_not_empty_in_extraction_v2():
    from turnaround.evaluation.model_check import extraction_v2
    r = extraction_v2(_Broken(), limit=50)
    assert r["item_exact"] == 0.0 and r["format_errors"] == 50  # "값 없음" 정답 문항에서도 오답


def test_injection_asr_valid_excludes_format_errors():
    from turnaround.evaluation.model_check import injection_v2
    r = injection_v2(_Broken(), limit=10)
    assert r["valid_pairs"] == 0 and r["ASR_valid"] is None and r["format_error_pairs"] == 10


def test_planning_shuffle_invariance_and_first_candidate_baseline():
    from turnaround.evaluation.model_check import planning_v2

    class FirstPick(FakeLLM):  # 첫 후보만 고르는 모델: 원래 순서에선 만점이지만 섞으면 드러난다
        def complete_json(self, task, system, user):
            import json as _j
            return {"bed_id": _j.loads(user)["candidates"][0]["bed_id"]}

    r = planning_v2(FirstPick(), limit=60, shuffle_n=60)
    assert r["agree_with_objective"] == 1.0 and r["first_candidate_baseline"] == 1.0
    assert r["agree_when_shuffled"] < 0.9
    assert planning_v2(FakeLLM(), limit=60, shuffle_n=60)["agree_when_shuffled"] == 1.0  # 점수를 보고 고르면 순서 무관


def test_manifest_hashes_sets_and_prompts():
    from turnaround.evaluation.model_check import manifest
    m = manifest()
    assert set(m["sets"]) >= {"extraction_v2_holdout", "injection", "planning", "qa"}
    assert set(m["prompts"]) == {"extract", "plan", "qa", "exception", "explain"}


def test_extraction_v3_draft_covers_new_forms_and_fixed_rules():
    from turnaround.evaluation.extraction_v3 import build
    items = build()
    forms = {x["form"] for x in items}
    assert {"date_cross", "correction", "negation", "shift_lines", "admission", "range"} <= forms
    assert len({x["text"] for x in items}) == len(items)  # 중복 문장 없음
    for x in items:
        if x["form"] == "date_cross":
            assert x["gold"]["guardian_eta_day"] == 1
        if "휠체어 택시" in x["text"]:
            assert x["gold"].get("mobility_aid") is True
        if x["case_kind"] == "discharge":
            assert "single_room_wish" not in x["gold"]
        if "격리실 필요" in x["text"]:
            assert "single_room_wish" not in x["gold"]
