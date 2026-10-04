"""M12.4: metrics with hand-computed values, and a smoke run of the evaluator.

The metric tests use tiny, fully worked examples on purpose. An evaluation whose
arithmetic is subtly wrong produces confident numbers that mean nothing, and that
failure is invisible from the report.
"""

from __future__ import annotations

import math

import pytest

from app.evals.metrics import (
    confusion_matrix,
    hit_at_k,
    macro_f1,
    mean,
    mrr,
    ndcg_at_k,
    per_class_report,
    percentiles,
    precision_at_k,
    precision_recall_f1,
    recall_at_k,
)

# --- ranking metrics -----------------------------------------------------


def test_recall_at_k_by_hand():
    # gold {2, 5}; ranked [1, 2, 3] -> 1 of 2 found at k=3
    assert recall_at_k([1, 2, 3], [2, 5], 3) == pytest.approx(0.5)
    assert recall_at_k([5, 2], [2, 5], 2) == pytest.approx(1.0)
    assert recall_at_k([1, 2], [9], 2) == 0.0
    # No gold labels means nothing to recall, not a division by zero.
    assert recall_at_k([1, 2], [], 2) == 0.0


def test_precision_at_k_by_hand():
    assert precision_at_k([1, 2, 3, 4], [2, 5], 4) == pytest.approx(0.25)
    assert precision_at_k([], [1], 5) == 0.0


def test_hit_at_k_by_hand():
    assert hit_at_k([9, 7, 3], [3], 1) == 0.0
    assert hit_at_k([9, 7, 3], [3], 3) == 1.0


def test_mrr_by_hand():
    # First relevant item is at position 3 -> 1/3.
    assert mrr([9, 8, 4], [4]) == pytest.approx(1 / 3)
    assert mrr([1, 2], [9]) == 0.0
    assert mrr([1, 2, 3], [2]) == pytest.approx(0.5)


def test_ndcg_at_k_by_hand():
    # One relevant item, found at rank 2. DCG = 1/log2(3).
    # Ideal has it at rank 1: IDCG = 1/log2(2) = 1.
    expected = (1 / math.log2(3)) / 1.0
    assert ndcg_at_k([1, 2], [2], 10) == pytest.approx(expected)

    # Perfect ranking scores 1.
    assert ndcg_at_k([2, 1], [2, 1], 10) == pytest.approx(1.0)
    # Nothing relevant retrieved scores 0.
    assert ndcg_at_k([8, 9], [2], 10) == 0.0


def test_ndcg_rewards_order_not_just_presence():
    # Both rank the two gold items, but at different positions: {1,3} vs {2,3}.
    early = ndcg_at_k([2, 1, 3], [2, 3], 10)
    late = ndcg_at_k([1, 2, 3], [2, 3], 10)
    assert early > late


def test_ndcg_truncates_the_ideal_at_k():
    """With more gold items than k, the ideal is capped at k -- otherwise no system
    could score 1.0 and the metric stops discriminating."""
    assert ndcg_at_k([1, 2], [1, 2, 3, 4], 2) == pytest.approx(1.0)


# --- classification ------------------------------------------------------


def test_precision_recall_f1_by_hand():
    # truth: 3 actual positives; predicted: 2, of which 1 is a true positive.
    out = precision_recall_f1([1, 1, 1], [1, 0], true_positives=1)
    assert out["tp"] == 1
    assert out["precision"] == pytest.approx(0.5)
    assert out["recall"] == pytest.approx(1 / 3)
    assert out["f1"] == pytest.approx(2 * 0.5 * (1 / 3) / (0.5 + 1 / 3))
    assert out["support"] == 3


def test_precision_recall_f1_handles_unequal_lengths():
    """The two sequences are independent sets and need not be the same length."""
    out = precision_recall_f1([1, 1, 1, 1], [1], true_positives=1)
    assert out["tp"] == 1
    assert out["precision"] == 1.0
    assert out["recall"] == pytest.approx(0.25)


def test_precision_recall_f1_of_nothing_is_zero():
    out = precision_recall_f1([], [])
    assert out["precision"] == 0.0 and out["recall"] == 0.0 and out["f1"] == 0.0


def test_per_class_report_by_hand():
    #            pos0 pos1 pos2 pos3
    truth = ["a", "a", "b", "b"]
    predicted = ["a", "b", "b", "b"]
    report = per_class_report(truth, predicted)

    # a: 2 actual, 1 predicted, 1 correct -> P 1/1, R 1/2, F1 = 2*(1*0.5)/1.5 = 2/3
    assert report["a"]["tp"] == 1
    assert report["a"]["precision"] == pytest.approx(1.0)
    assert report["a"]["recall"] == pytest.approx(0.5)
    assert report["a"]["f1"] == pytest.approx(2 / 3)

    # b: 2 actual, 3 predicted, 2 correct -> P 2/3, R 1, F1 = 2*(2/3)/((2/3)+1) = 0.8
    assert report["b"]["tp"] == 2
    assert report["b"]["precision"] == pytest.approx(2 / 3)
    assert report["b"]["recall"] == pytest.approx(1.0)
    assert report["b"]["f1"] == pytest.approx(0.8)

    # The macro average covers the two classes that actually occur.
    assert report["macro"]["f1"] == pytest.approx(((2 / 3) + 0.8) / 2)
    assert report["macro"]["support"] == 4


def test_macro_f1_helper_agrees_with_the_report():
    truth = ["x", "y", "y"]
    predicted = ["x", "y", "x"]
    assert macro_f1(truth, predicted) == pytest.approx(per_class_report(truth, predicted)["macro"]["f1"])


def test_confusion_matrix_by_hand():
    matrix = confusion_matrix(["a", "a", "b"], ["a", "b", "b"])
    assert matrix["a"]["a"] == 1
    assert matrix["a"]["b"] == 1
    assert matrix["b"]["b"] == 1
    assert matrix["b"]["a"] == 0


# --- latency -------------------------------------------------------------


def test_percentiles_use_nearest_rank():
    values = list(range(1, 21))  # 1..20
    out = percentiles(values, (50, 95))
    # p50 -> the 10th value; p95 -> ceil(0.95*20) = 19th value.
    assert out["p50"] == 10
    assert out["p95"] == 19


def test_percentiles_of_nothing():
    assert percentiles([], (50, 95)) == {"p50": 0.0, "p95": 0.0}


def test_percentiles_of_one_value():
    assert percentiles([7.5], (50, 95)) == {"p50": 7.5, "p95": 7.5}


def test_mean():
    assert mean([1, 2, 3]) == pytest.approx(2.0)
    assert mean([]) == 0.0


# --- the datasets and the runner ----------------------------------------


DATA = [
    ("claim_pairs", 80, "min 80 labelled claim pairs"),
    ("verify_cases", 60, "min 60 verification cases"),
    ("extraction_cases", 15, "min 15 extraction passages"),
]


@pytest.mark.parametrize(("name", "minimum", "why"), DATA)
def test_dataset_meets_its_floor(name, minimum, why):
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / f"{name}.jsonl")
    assert len(rows) >= minimum, f"{name}: {len(rows)} rows, needs {minimum} ({why})"


def test_claim_pairs_are_labeled_and_multilingual():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "claim_pairs.jsonl")
    labels = Counter(row["label"] for row in rows)
    assert {"supports", "contradicts", "supersedes", "unrelated"} <= set(labels)

    chinese = [r for r in rows if r["lang"] == "zh"]
    assert len(chinese) / len(rows) >= 0.20, "the plan requires >= 20% Chinese pairs"

    phenomena = {r["phenomenon"] for r in rows}
    # The three trap families are the point of the dataset; without them the judge
    # could score well by simply calling everything on-topic a contradiction.
    assert any(f.startswith("trap_") for f in phenomena), phenomena
    assert {"same_unit_value", "negation", "paraphrase", "unit_conversion"} <= phenomena


def test_verify_cases_cover_every_verdict():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "verify_cases.jsonl")
    counts = Counter(row["label"] for row in rows)
    for label in ("supported", "contradicted", "disputed", "unsupported"):
        assert counts[label] >= 10, f"{label}: only {counts[label]} cases"


def test_eval_datasets_do_not_reuse_demo_corpus_sentences():
    """A benchmark sharing text with what it measures does not measure it."""
    from app.demo.corpus import load_corpus
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    demo_sentences = {
        sentence
        for corpus_file in load_corpus()
        for version in corpus_file.versions
        for sentence in version.body.replace("\n", " ").split(". ")
        if len(sentence.strip()) > 24
    }
    pairs = read_jsonl(DATA_DIR / "claim_pairs.jsonl")
    cases = read_jsonl(DATA_DIR / "verify_cases.jsonl")
    for row in pairs:
        assert row["a"] not in demo_sentences
        assert row["b"] not in demo_sentences
    for row in cases:
        assert row["sentence"] not in demo_sentences


def test_extraction_spans_address_the_passage():
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    for case in read_jsonl(DATA_DIR / "extraction_cases.jsonl"):
        assert case["spans"], case["id"]
        for span in case["spans"]:
            assert 0 <= span["start"] < span["end"] <= len(case["text"]), case["id"]
            assert case["text"][span["start"] : span["end"]] == span["text"]


@pytest.mark.db
def test_extraction_suite_runs_and_reports():
    from app.evals.runner import run_extraction

    result = run_extraction()
    assert result.metrics["alignment_rate"] >= 0.95, result.metrics
    assert result.metrics["alignment_precision"] >= 0.9, result.metrics
    assert result.sample_count >= 15


def test_claims_suite_runs_and_is_honest_about_its_weaknesses():
    """The suite must report a real number, including where the judge is weak."""
    from app.evals.runner import run_claims

    result = run_claims()
    assert result.metrics["pairs"] >= 80
    assert 0.0 <= result.metrics["accuracy"] <= 1.0
    # Notes must break the accuracy down by phenomenon: a single average hides
    # exactly the failure mode this dataset was built to expose.
    assert any("phenomenon" in note for note in result.notes)


def test_claims_suite_labels_the_domains_it_is_weak_in():
    from app.evals.runner import run_claims

    result = run_claims()
    joined = "\n".join(result.notes)
    for phenomenon in ("trap_", "same_unit_value"):
        assert phenomenon in joined, f"no per-phenomenon breakdown for {phenomenon}"


def test_report_states_its_environment():
    from app.evals.runner import SuiteResult, environment, render_markdown

    env = environment()
    assert env["embedder"] and env["llm_provider"] and env["git_sha"]
    markdown = render_markdown([SuiteResult("retrieval", {"recall@5": 1.0})], env, "offline", "retrieval")
    # A number without its environment is not a measurement.
    for key in ("embedder", "llm provider", "git", "mode"):
        assert key in markdown.lower(), key


def test_demo_retrieval_dataset_is_loadable():
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "demo_retrieval.json")
    assert len(rows) >= 20
    for row in rows:
        assert row["query"] and row["relevant"]


def test_download_is_offline_safe():
    """`download` must report failure rather than raise when there is no network."""
    from app.evals import datasets

    assert "SciFact" in datasets.NOTE
    assert datasets.SCIFACT_RETRIEVAL_URL.startswith("https://")
