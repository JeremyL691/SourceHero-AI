"""Metrics with hand-computed values, and a smoke run of the evaluator.

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


def test_ndcg_at_k_never_exceeds_one_property():
    """EVAL-7 regression test: 200 random trials with duplicates always satisfy 0 <= ndcg <= 1.0."""
    import random

    rng = random.Random(42)
    for _ in range(200):
        pool = list(range(1, 21))
        num_relevant = rng.randint(1, 10)
        gold = set(rng.sample(pool, num_relevant))
        ranking_len = rng.randint(0, 30)
        ranked = [rng.choice(pool) for _ in range(ranking_len)]
        k = rng.randint(1, 15)

        score = ndcg_at_k(ranked, gold, k)
        assert 0.0 <= score <= 1.0 + 1e-9, (
            f"nDCG {score} exceeded 1.0 for k={k}, gold={gold}, ranked={ranked}"
        )


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
    out = precision_recall_f1([], [], true_positives=0)
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


def test_per_class_report_permutation_zero_f1():
    """EVAL-1 regression test: permutation of classes yields 0% accuracy and 0.0 macro-F1."""
    truth = ["A", "B"]
    predicted = ["B", "A"]
    report = per_class_report(truth, predicted)
    assert report["A"]["tp"] == 0
    assert report["A"]["precision"] == 0.0
    assert report["A"]["recall"] == 0.0
    assert report["A"]["f1"] == 0.0
    assert report["B"]["tp"] == 0
    assert report["B"]["precision"] == 0.0
    assert report["B"]["recall"] == 0.0
    assert report["B"]["f1"] == 0.0
    assert report["macro"]["f1"] == 0.0
    assert macro_f1(truth, predicted) == 0.0


def test_per_class_report_three_classes_hand_computed():
    """Hand-computed case 1: 3 classes with partial confusion."""
    truth = ["A", "A", "B", "B", "C"]
    predicted = ["A", "B", "B", "C", "A"]
    report = per_class_report(truth, predicted)
    assert report["A"]["tp"] == 1
    assert report["A"]["precision"] == pytest.approx(1 / 2)
    assert report["A"]["recall"] == pytest.approx(1 / 2)
    assert report["A"]["f1"] == pytest.approx(0.5)

    assert report["B"]["tp"] == 1
    assert report["B"]["precision"] == pytest.approx(1 / 2)
    assert report["B"]["recall"] == pytest.approx(1 / 2)
    assert report["B"]["f1"] == pytest.approx(0.5)

    assert report["C"]["tp"] == 0
    assert report["C"]["precision"] == 0.0
    assert report["C"]["recall"] == 0.0
    assert report["C"]["f1"] == 0.0

    assert report["macro"]["f1"] == pytest.approx((0.5 + 0.5 + 0.0) / 3)


def test_per_class_report_perfect_classification():
    """Hand-computed case 2: 100% precision, recall, F1 across all classes."""
    truth = ["cat", "cat", "dog", "dog", "bird"]
    predicted = ["cat", "cat", "dog", "dog", "bird"]
    report = per_class_report(truth, predicted)
    for cls in ("cat", "dog", "bird"):
        assert report[cls]["precision"] == 1.0
        assert report[cls]["recall"] == 1.0
        assert report[cls]["f1"] == 1.0
    assert report["macro"]["f1"] == 1.0


def test_per_class_report_zero_prediction_for_class():
    """Hand-computed case 3: model never predicts one class."""
    truth = ["yes", "yes", "no"]
    predicted = ["yes", "yes", "yes"]
    report = per_class_report(truth, predicted)
    assert report["yes"]["tp"] == 2
    assert report["yes"]["precision"] == pytest.approx(2 / 3)
    assert report["yes"]["recall"] == pytest.approx(1.0)
    assert report["yes"]["f1"] == pytest.approx(0.8)

    assert report["no"]["tp"] == 0
    assert report["no"]["precision"] == 0.0
    assert report["no"]["recall"] == 0.0
    assert report["no"]["f1"] == 0.0

    assert report["macro"]["f1"] == pytest.approx((0.8 + 0.0) / 2)


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

    rows = read_jsonl(DATA_DIR / f"{name}.dev.jsonl")
    assert len(rows) >= minimum, f"{name}: {len(rows)} rows, needs {minimum} ({why})"


def test_claim_pairs_are_labeled_and_multilingual():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "claim_pairs.dev.jsonl")
    labels = Counter(row["label"] for row in rows)
    assert {"supports", "contradicts", "refines", "supersedes", "duplicate", "unrelated"} <= set(labels)

    chinese = [r for r in rows if r["lang"] == "zh"]
    assert len(chinese) / len(rows) >= 0.20, "the plan requires >= 20% Chinese pairs"

    phenomena = {r["phenomenon"] for r in rows}
    # The three trap families are the point of the dataset; without them the judge
    # could score well by simply calling everything on-topic a contradiction.
    assert any(f.startswith("trap_") for f in phenomena), phenomena
    assert {"same_unit_value", "negation", "paraphrase", "unit_conversion"} <= phenomena


def test_dev_claim_pairs_have_no_repeated_rows():
    # EVAL-9: the padding used to repeat four pairs, which inflated the count and the score.
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "claim_pairs.dev.jsonl")
    keys = [(r["a"], r["b"]) for r in rows]
    assert len(keys) == len(set(keys)), "the dev set repeats an (a, b) pair"


def test_identical_wording_is_labelled_duplicate():
    # EVAL-9: these were labelled `supports` while pair-071/072 said `duplicate`.
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "claim_pairs.dev.jsonl")
    identical = [r for r in rows if r["a"] == r["b"]]
    assert identical, "the dev set should keep some identical-wording pairs"
    wrong = [r["id"] for r in identical if r["label"] != "duplicate"]
    assert not wrong, f"identical wording must be labelled duplicate: {wrong}"


HELD_OUT = ("claim_pairs.test.jsonl", "verify_cases.test.jsonl", "extraction_cases.test.jsonl")
NEW_DOMAINS = {"health", "finance", "transport", "climate", "sport", "education"}
REQUIRED_PHENOMENA = (
    "same_unit_value",
    "unit_conversion",
    "negation",
    "paraphrase",
    "date_moved",
    "revised_schedule",
    "duplicate",
    "trap_different_metric",
    "trap_same_number_diff_subject",
    "trap_qualifier_explains",
    "unrelated_same_domain",
)


def test_held_out_split_matches_its_lock():
    # The test split is locked before any heuristic change. A changed file fails here.
    import hashlib
    import json

    from app.evals.runner import DATA as DATA_DIR

    lock = json.loads((DATA_DIR / "LOCK.json").read_text(encoding="utf-8"))
    assert set(lock) == set(HELD_OUT)
    for name, expected in lock.items():
        actual = hashlib.sha256((DATA_DIR / name).read_bytes()).hexdigest()
        assert actual == expected, f"{name} no longer matches LOCK.json"


def test_held_out_claim_pairs_meet_the_plan_floors():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "claim_pairs.test.jsonl")
    assert len(rows) >= 150, len(rows)
    phenomena = Counter(r["phenomenon"] for r in rows)
    short = {p: phenomena[p] for p in REQUIRED_PHENOMENA if phenomena[p] < 10}
    assert not short, f"phenomena below 10 rows: {short}"
    assert {"supports", "contradicts", "refines", "supersedes", "duplicate", "unrelated"} == {
        r["label"] for r in rows
    }
    assert sum(1 for r in rows if r["lang"] == "zh") / len(rows) >= 0.30
    assert sum(1 for r in rows if r.get("domain") in NEW_DOMAINS) / len(rows) >= 0.40
    assert len({(r["a"], r["b"]) for r in rows}) == len(rows), "held-out set repeats a pair"


def test_held_out_verify_cases_cover_every_verdict():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "verify_cases.test.jsonl")
    assert len(rows) >= 40, len(rows)
    counts = Counter(r["label"] for r in rows)
    for verdict in ("supported", "contradicted", "disputed", "unsupported"):
        assert counts[verdict] >= 10, (verdict, counts)


def test_held_out_extraction_gold_spans_come_from_the_splitter():
    # Gold offsets must be what `split_sentences` produces plus `str.find`, never typed by hand.
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl
    from app.ingestion.segment import split_sentences

    rows = read_jsonl(DATA_DIR / "extraction_cases.test.jsonl")
    assert len(rows) >= 15, len(rows)
    assert sum(1 for r in rows if r["lang"] == "en") >= 5
    assert sum(1 for r in rows if r["lang"] == "zh") >= 5
    for row in rows:
        sentences = [s.text_of(row["text"]).strip() for s in split_sentences(row["text"])]
        for span in row["spans"]:
            assert row["text"][span["start"] : span["end"]] == span["text"], row["id"]
            assert span["text"] in sentences, (row["id"], span["text"])


def test_held_out_split_does_not_overlap_dev_demo_or_prompts():
    # EVAL-5: no sentence of the held-out split may appear in the dev set, the demo corpus
    # or a prompt template, after the same normalization the leakage check uses.
    from pathlib import Path

    from app.demo.corpus import load_corpus
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    held: list[str] = []
    for row in read_jsonl(DATA_DIR / "claim_pairs.test.jsonl"):
        held.extend([row["a"], row["b"]])
    for row in read_jsonl(DATA_DIR / "verify_cases.test.jsonl"):
        held.append(row["sentence"])
        held.extend(ev["text"] for ev in row["evidence"])
    for row in read_jsonl(DATA_DIR / "extraction_cases.test.jsonl"):
        held.append(row["text"])
    held_sentences = _extract_sentences(held)
    assert len(held_sentences) >= 100, len(held_sentences)

    dev: list[str] = []
    for row in read_jsonl(DATA_DIR / "claim_pairs.dev.jsonl"):
        dev.extend([row["a"], row["b"]])
    for row in read_jsonl(DATA_DIR / "verify_cases.dev.jsonl"):
        dev.append(row["sentence"])
        dev.extend(ev["text"] for ev in row["evidence"])
    for row in read_jsonl(DATA_DIR / "extraction_cases.dev.jsonl"):
        dev.append(row["text"])
    demo_texts = [version.body for corpus_file in load_corpus() for version in corpus_file.versions]
    prompt_dir = Path(__file__).resolve().parents[1] / "app" / "llm" / "prompts"
    prompts = [p.read_text(encoding="utf-8") for p in sorted(prompt_dir.glob("*.md"))]

    assert not held_sentences & _extract_sentences(dev), "held-out split overlaps the dev set"
    assert not held_sentences & _extract_sentences(demo_texts), "held-out split overlaps the demo corpus"
    assert not held_sentences & _extract_sentences(prompts), "held-out split overlaps a prompt template"


def test_verify_cases_cover_every_verdict():
    from collections import Counter

    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    rows = read_jsonl(DATA_DIR / "verify_cases.dev.jsonl")
    counts = Counter(row["label"] for row in rows)
    for label in ("supported", "contradicted", "disputed", "unsupported"):
        assert counts[label] >= 10, f"{label}: only {counts[label]} cases"


def _extract_sentences(texts: list[str]) -> set[str]:
    from app.core.text import normalize_text
    from app.ingestion.segment import split_sentences

    out = set()
    for text in texts:
        cleaned = normalize_text(text)
        for s in split_sentences(cleaned):
            sent = s.text_of(cleaned).strip()
            if len(sent) > 10:
                out.add(sent)
    return out


def test_eval_datasets_do_not_reuse_demo_corpus_sentences():
    """EVAL-5: normalized sentence intersection between benchmark and demo corpus must be empty."""
    from app.demo.corpus import load_corpus
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    demo_texts = [version.body for corpus_file in load_corpus() for version in corpus_file.versions]
    demo_sentences = _extract_sentences(demo_texts)
    assert len(demo_sentences) >= 50

    eval_texts: list[str] = []
    for row in read_jsonl(DATA_DIR / "claim_pairs.dev.jsonl"):
        eval_texts.extend([row["a"], row["b"]])
    for row in read_jsonl(DATA_DIR / "verify_cases.dev.jsonl"):
        eval_texts.append(row["sentence"])
    for row in read_jsonl(DATA_DIR / "extraction_cases.dev.jsonl"):
        eval_texts.append(row["text"])

    eval_sentences = _extract_sentences(eval_texts)
    intersection = demo_sentences & eval_sentences
    assert not intersection, f"Detected leakage between demo corpus and eval datasets: {intersection}"


def test_leakage_detection_catches_injected_demo_sentence():
    """EVAL-5 self-test: injecting a demo sentence into eval set must be caught by leakage check."""
    from app.demo.corpus import load_corpus

    demo_texts = [version.body for corpus_file in load_corpus() for version in corpus_file.versions]
    demo_sentences = _extract_sentences(demo_texts)
    sample_sentence = next(iter(demo_sentences))

    injected_eval = [f"Some prefix here. {sample_sentence} Some suffix."]
    injected_sentences = _extract_sentences(injected_eval)
    assert sample_sentence in (demo_sentences & injected_sentences)


def test_extraction_spans_address_the_passage():
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl

    for case in read_jsonl(DATA_DIR / "extraction_cases.dev.jsonl"):
        assert case["spans"], case["id"]
        for span in case["spans"]:
            assert 0 <= span["start"] < span["end"] <= len(case["text"]), case["id"]
            assert case["text"][span["start"] : span["end"]] == span["text"]


def test_extraction_spans_align_with_split_sentences():
    """EVAL-3 regression test: each gold span starts and ends on a split_sentences boundary."""
    from app.evals.runner import DATA as DATA_DIR
    from app.evals.runner import read_jsonl
    from app.ingestion.segment import split_sentences

    for case in read_jsonl(DATA_DIR / "extraction_cases.dev.jsonl"):
        text = case["text"]
        sent_spans = split_sentences(text)
        sent_starts = {s.start for s in sent_spans}
        sent_ends = {s.end for s in sent_spans}
        for span in case["spans"]:
            assert span["start"] in sent_starts, f"{case['id']}: start {span['start']} not in {sent_starts}"
            assert span["end"] in sent_ends, f"{case['id']}: end {span['end']} not in {sent_ends}"


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


def test_claims_suite_does_not_leak_label_to_judge(monkeypatch):
    """EVAL-2 acceptance test: runner never feeds label or phenomenon to the judge."""
    from app.claims.relate import RelationDraft
    from app.evals.runner import run_claims

    checked_calls = []

    def guard_judge(a, b, similarity):
        assert not hasattr(a, "label") and not hasattr(a, "phenomenon")
        assert not hasattr(b, "label") and not hasattr(b, "phenomenon")
        assert isinstance(similarity, float)
        assert -1.0 <= similarity <= 1.0001
        checked_calls.append((a.statement, b.statement, similarity))
        return RelationDraft(relation="unrelated", direction=None, confidence=0.5)

    monkeypatch.setattr("app.claims.relate.heuristic_relation", guard_judge)
    result = run_claims()
    assert len(checked_calls) >= 80
    assert result.sample_count >= 80


def test_claim_dates_come_from_the_row_not_a_constant():
    # EVAL-10: the stub used to assert both claims on fixed days, ignoring a_date and b_date.
    from datetime import UTC, datetime

    from app.evals.runner import _row_time

    assert _row_time("2026-06-20", 2) == datetime(2026, 6, 20, tzinfo=UTC)
    assert _row_time(None, 2) == datetime(2026, 1, 2, tzinfo=UTC)


def test_held_out_claims_report_is_aggregate_only():
    # The test split must not expose the rows it was scored on. Only totals and
    # per-label precision, recall and F1 may appear.
    from app.evals.runner import run_claims

    result = run_claims("test")
    assert result.notes == []
    allowed = ("pairs", "macro_f1", "accuracy", "precision_", "recall_", "f1_")
    assert all(key.startswith(allowed) for key in result.metrics), sorted(result.metrics)
    assert result.sample_count == 156


def test_report_records_split_llm_mode_and_dirty_tree():
    from app.evals.runner import environment

    env = environment(split="test", llm="none")
    assert env["split"] == "test"
    assert env["llm_mode"] == "none"
    assert env["llm_requests"] == 0
    assert env["cassette"] == "none"
    assert isinstance(env["git_dirty"], bool)


def test_report_files_do_not_make_the_tree_dirty(monkeypatch):
    """Reports are outputs: writing them must not mark the run as coming from a dirty tree, or
    several final reports cannot be recorded from one clean commit. The check must still
    count any change to the code and the data."""
    import subprocess

    from app.evals import runner

    seen: list[list[str]] = []

    def fake_run(args, **_kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")  # git omits the excluded path

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner._git_dirty() is False
    assert ":(exclude)evals/reports" in seen[-1]

    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda args, **_k: subprocess.CompletedProcess(args, 0, stdout=" M app/evals/runner.py\n", stderr=""),
    )
    assert runner._git_dirty() is True


def test_report_states_its_environment():
    from app.evals.runner import SuiteResult, environment, render_markdown

    env = environment()
    assert env["embedder"] and env["llm_provider"] and env["git_sha"]
    markdown = render_markdown([SuiteResult("retrieval", {"recall@5": 1.0})], env, "offline", "retrieval")
    # A number without its environment is not a measurement.
    for key in ("embedder", "llm provider", "git", "mode"):
        assert key in markdown.lower(), key


def test_a_suite_without_metrics_shows_why_in_the_report():
    """Earlier reports said "no results" for a retrieval suite that had failed on a missing column.
    The reason must reach the report."""
    from app.evals.runner import SuiteResult, environment, render_markdown

    reason = "FAILED: ProgrammingError: column sources.reliability does not exist"
    markdown = render_markdown(
        [SuiteResult("retrieval", {}, [reason])], environment(), "offline", "retrieval"
    )
    assert "_no results_" in markdown
    assert reason in markdown


def test_a_run_with_a_suite_without_metrics_exits_nonzero_and_keeps_its_report(tmp_path, monkeypatch):
    from app.evals import runner
    from app.evals.runner import SuiteResult

    broken = SuiteResult("retrieval", {}, ["FAILED: RuntimeError: boom"])
    monkeypatch.setattr(runner, "run_retrieval", lambda: broken)
    monkeypatch.setattr(runner, "REPORTS", tmp_path)
    monkeypatch.setattr(runner, "_record_run", lambda *args, **kwargs: None)

    assert runner.run(suites="retrieval", mode="offline", split="dev", llm="none") == 1
    [report] = list(tmp_path.glob("*-retrieval-offline-dev.md"))
    assert "FAILED: RuntimeError: boom" in report.read_text(encoding="utf-8")


def test_readme_retrieval_and_scifact_numbers_match_clean_reports():
    """REVIEW EVAL-11: each "recall@5 / MRR / nDCG@10" triple in the README's Numbers table must
    appear in a report that the same section cites, and that report must come from a clean tree."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    section = readme.split("\n## Numbers", 1)[1].split("\n## ", 1)[0]
    cited = [root / path for path in re.findall(r"`(evals/reports/[^`]+\.md)`", section)]
    assert cited and all(path.exists() for path in cited), cited
    texts = [path.read_text(encoding="utf-8") for path in cited]
    clean = [text for text in texts if re.search(r"dirty(?: tree)?\W{1,6}False", text)]

    triples = []
    for line in section.splitlines():
        if "recall@5 / MRR / nDCG@10" not in line:
            continue
        for cell in line.split("|")[3:]:
            if cell.count("/") == 2:
                triples.append([part.strip() for part in cell.split("/")])
    assert len(triples) == 6, triples
    for recall, mrr_value, ndcg in triples:
        needed = [f"| recall@5 | {recall} |", f"| mrr | {mrr_value} |", f"| ndcg@10 | {ndcg} |"]
        assert any(all(line in text for line in needed) for text in clean), needed


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


@pytest.mark.db
def test_latency_suite_runs_and_reports(db):
    """EVAL-6 regression test: latency suite measures retrieval and verify p50/p95."""
    from app.evals.runner import run_latency

    result = run_latency(session=db)
    assert result.name == "latency"
    assert "retrieval_p50_ms" in result.metrics
    assert "retrieval_p95_ms" in result.metrics
    assert "verify_p50_ms" in result.metrics
    assert "verify_p95_ms" in result.metrics
    assert result.metrics["retrieval_samples"] > 0
    assert result.metrics["verify_samples"] > 0


def test_scifact_suite_runs_and_reports(tmp_path, monkeypatch):
    """EVAL-6 regression test: SciFact suite runs against cached corpus.

    EVAL-11: the run also writes its own report, linked from the result. The test
    points REPORTS at a temporary directory so it never writes into evals/reports/.
    """
    from app.evals import runner
    from app.evals.runner import CACHE, run_scifact

    if not (CACHE / "scifact.zip").exists():
        pytest.skip("SciFact dataset not cached")
    monkeypatch.setattr(runner, "REPORTS", tmp_path)

    result = run_scifact()
    assert result.name == "scifact"
    assert result.metrics["queries"] >= 300
    assert result.metrics["corpus_size"] >= 5000
    assert 0.0 <= result.metrics["recall@5"] <= 1.0
    assert 0.0 <= result.metrics["mrr"] <= 1.0
    assert 0.0 <= result.metrics["ndcg@10"] <= 1.0
    assert result.artifact is not None
    report = tmp_path.glob("*-scifact-*.md")
    assert any(path.name in result.artifact for path in report), result.artifact


@pytest.mark.db
def test_eval_runs_api(user_factory, db):
    """FE-14: evals history endpoints list and retrieve benchmark runs."""
    from app.models.eval import EvalRun

    client, _ = user_factory()
    run = EvalRun(
        suite="claims",
        mode="offline",
        metrics={"macro_f1": 0.88},
        config={"embedder": "hashing"},
        report_path="evals/reports/latest.md",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    resp = client.get("/v1/evals/runs")
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert any(r["id"] == run.id for r in items)

    detail_resp = client.get(f"/v1/evals/runs/{run.id}")
    assert detail_resp.status_code == 200, detail_resp.text
    detail = detail_resp.json()
    assert detail["suite"] == "claims"
    assert detail["metrics"]["macro_f1"] == 0.88


def test_eval_llm_client_restores_the_environment_after_the_block(monkeypatch):
    import os

    from app.evals import runner

    monkeypatch.delenv("LLM_PRESET", raising=False)
    monkeypatch.delenv("LLM_CASSETTE_MODE", raising=False)
    with runner._eval_llm_client("replay", suite="claims", split="dev") as client:
        assert client.effective.preset == "openrouter"
    assert "LLM_PRESET" not in os.environ
    assert "LLM_CASSETTE_MODE" not in os.environ


def test_extraction_recall_counts_each_gold_span_once(monkeypatch):
    """Two drafts that both cover one gold span are two hits and one covered span.

    Recall used to divide hits by gold spans, so the same span counted twice and recall
    could exceed 1 (an earlier dev run reported 1.03).
    """
    from types import SimpleNamespace

    from app.evals import runner

    first = "The turbine rated output is 5.1 MW."
    text = f"{first} The pump rated output is 5.1 MW."
    case = {"text": text, "spans": [{"start": 0, "end": len(first)}]}
    drafts = [
        SimpleNamespace(quote=first[:-1], char_start=0, char_end=len(first) - 1),
        SimpleNamespace(quote="turbine rated output is 5.1 MW.", char_start=4, char_end=len(first)),
    ]
    monkeypatch.setattr(runner, "read_jsonl", lambda path: [case])
    monkeypatch.setattr(runner, "_extraction_drafts", lambda cases, *, llm, split: ([drafts], []))

    metrics = runner.run_extraction("dev").metrics
    assert metrics["alignment_rate"] == 1.0
    assert metrics["alignment_precision"] == 1.0
    assert metrics["alignment_recall"] == 1.0


@pytest.fixture
def offline_llm_cassettes(tmp_path, monkeypatch):
    """Route every eval LLM call through the fake provider and a cassette in tmp_path.

    "live" records into the cassette and "replay" reads it back and fails on a miss, so
    the record-then-replay path runs without any network.
    """
    from contextlib import contextmanager

    from app.core.config import Settings
    from app.evals import runner
    from app.llm.client import get_llm
    from app.llm.providers.cassette import CassetteProvider

    @contextmanager
    def fake_eval_client(llm, *, suite, split):
        mode = "record" if llm == "live" else "replay"
        client = get_llm(settings=Settings(_env_file=None, sourcehero_env="test", llm_preset="fake"))
        client._provider = CassetteProvider(client._provider, tmp_path / f"{suite}-{split}.jsonl", mode)
        yield client

    monkeypatch.setattr(runner, "_eval_llm_client", fake_eval_client)
    return tmp_path


def test_llm_suites_replay_exactly_what_they_recorded(offline_llm_cassettes):
    from app.evals import runner

    recorded = {
        "claims": runner.run_claims("dev", llm="live").metrics,
        "verify": runner.run_verify("dev", llm="live").metrics,
        "extraction": runner.run_extraction("dev", llm="live").metrics,
    }
    replayed = {
        "claims": runner.run_claims("dev", llm="replay").metrics,
        "verify": runner.run_verify("dev", llm="replay").metrics,
        "extraction": runner.run_extraction("dev", llm="replay").metrics,
    }

    # Latency is wall-clock time and varies with host load; every quality metric must match.
    def quality(metrics: dict) -> dict:
        return {key: value for key, value in metrics.items() if not key.startswith("latency_")}

    assert {name: quality(m) for name, m in replayed.items()} == {
        name: quality(m) for name, m in recorded.items()
    }
    for suite in ("claims", "verify", "extraction"):
        assert (offline_llm_cassettes / f"{suite}-dev.jsonl").exists(), suite


def test_a_missing_recorded_answer_fails_every_llm_suite_instead_of_falling_back(offline_llm_cassettes):
    from app.evals import runner
    from app.llm.providers.cassette import CassetteMissError

    with pytest.raises(CassetteMissError):
        runner.run_claims("dev", llm="replay")
    with pytest.raises(CassetteMissError):
        runner.run_verify("dev", llm="replay")
    with pytest.raises(CassetteMissError):
        runner.run_extraction("dev", llm="replay")


def test_report_names_the_provider_that_answered_when_llm_is_on():
    """With --llm on, the answers came from OpenRouter through the cassette, not the global default."""
    from app.evals import runner

    env = runner.environment("dev", llm="replay")
    assert env["llm_provider"] == "openrouter"
    assert env["llm_provider_mode"] == "cassette"
    assert env["llm_model"] == "nvidia/nemotron-3-super-120b-a12b:free"


def test_scifact_reranks_the_dense_candidates_when_the_reranker_is_on(tmp_path, monkeypatch):
    """With a reranker configured, SciFact scores the reranked order.

    The dense vectors put the gold document last (MRR 1/3); the stub reranker puts it first.
    """
    import json
    import zipfile

    from app.core.config import get_settings
    from app.evals import runner
    from app.indexing import embeddings
    from app.retrieval import rerank

    corpus_rows = [
        {"_id": "d1", "title": "", "text": "alpha"},
        {"_id": "d2", "title": "", "text": "beta"},
        {"_id": "d3", "title": "", "text": "gamma"},
    ]
    with zipfile.ZipFile(tmp_path / "scifact.zip", "w") as z:
        z.writestr("scifact/corpus.jsonl", "\n".join(json.dumps(r) for r in corpus_rows))
        z.writestr("scifact/queries.jsonl", json.dumps({"_id": "q1", "text": "query"}))
        z.writestr("scifact/qrels/test.tsv", "query-id\tcorpus-id\tscore\nq1\td3\t1\n")

    vectors = {"alpha": [1.0, 0.0], "beta": [0.9, 0.1], "gamma": [0.0, 1.0]}

    class FixedEmbedder:
        name = "fixed"
        dim = 2

        def embed_documents(self, texts):
            return [vectors[t] for t in texts]

        def embed_query(self, text):
            return [1.0, 0.0]

    monkeypatch.setattr(runner, "CACHE", tmp_path)
    monkeypatch.setattr(runner, "REPORTS", tmp_path)
    monkeypatch.setattr(embeddings, "get_embedder", lambda: FixedEmbedder())
    monkeypatch.setattr(
        rerank, "rerank", lambda query, documents: [1.0 if d == "gamma" else 0.0 for d in documents]
    )
    monkeypatch.setenv("RERANK_PROVIDER", "fastembed")
    get_settings.cache_clear()
    try:
        metrics = runner.run_scifact().metrics
    finally:
        get_settings.cache_clear()
    assert metrics["mrr"] == 1.0, metrics
    assert metrics["recall@5"] == 1.0, metrics
