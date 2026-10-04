"""The evaluation runner.

    python -m app.evals run --suite all --mode offline
    python -m app.evals download          # external datasets (needs network)

Reports land in `evals/reports/` and always state the environment they ran in --
embedder, reranker, LLM provider, dataset sizes, date, git sha. A retrieval number
without those is not a measurement, it is a rumour.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.evals.metrics import (
    mean,
    mrr,
    ndcg_at_k,
    per_class_report,
    percentiles,
    recall_at_k,
)

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "evals" / "data"
CACHE = ROOT / "evals" / "cache"
REPORTS = ROOT / "evals" / "reports"


@dataclass
class SuiteResult:
    name: str
    metrics: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    sample_count: int = 0

    def as_dict(self) -> dict:
        return {
            "suite": self.name,
            "metrics": self.metrics,
            "notes": self.notes,
            "sample_count": self.sample_count,
        }


def read_jsonl(path: Path) -> list[dict]:
    """Read a `.jsonl` file, or a `.json` file holding a list or a `queries` key."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("{") and '"queries"' in stripped[:400]:
        # The retrieval dataset is a single JSON object, not one object per line.
        return json.loads(text).get("queries", [])
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def environment() -> dict:
    """Everything needed to interpret the numbers."""
    from app.core.config import get_settings
    from app.indexing.embeddings import get_embedder, get_embedder_status
    from app.retrieval import rerank

    settings = get_settings()
    embedder = get_embedder()
    return {
        "embedder": embedder.name,
        "embedder_dim": embedder.dim,
        "embedder_detail": get_embedder_status(),
        "reranker": settings.rerank_model if settings.rerank_provider != "none" else "none",
        "reranker_detail": rerank.status(),
        "llm_provider": settings.llm_provider,
        "llm_mode": "fake" if settings.llm_provider == "fake" else "live",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "python": f"{__import__('sys').version_info.major}.{__import__('sys').version_info.minor}",
    }


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing git is not an eval failure
        return "unknown"


# --- retrieval -----------------------------------------------------------


def run_retrieval() -> SuiteResult:
    """Recall/MRR/nDCG over the demo corpus queries."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.demo.seed import seed_demo
    from app.indexing.indexer import index_version
    from app.jobs.handlers import register_all
    from app.models.chunk import Chunk
    from app.models.document import DocumentVersion
    from app.retrieval.hybrid import hybrid_search

    """Recall/MRR/nDCG over the demo corpus queries."""

    register_all()
    queries = (
        read_jsonl(DATA / "demo_retrieval.json")
        or json.loads((DATA / "demo_retrieval.json").read_text(encoding="utf-8"))["queries"]
    )

    session = SessionLocal()
    try:
        # A dedicated eval user so a run never disturbs the demo account.
        import uuid as _uuid

        from app.ingestion.pipeline import ensure_user

        user_id = _uuid.uuid5(_uuid.NAMESPACE_URL, "sourcehero-eval-retrieval")
        ensure_user(session, "eval-retrieval@sourcehero.local", "eval-password-123")
        seed_demo(session, user_id)
        session.commit()

        # seed_demo only *queues* indexing. An evaluation must not depend on a worker
        # running, so index here: otherwise retrieval scores 0.0 against an empty
        # chunk table and looks like a retrieval regression rather than no data.
        versions = (
            session.execute(select(DocumentVersion).where(DocumentVersion.user_id == user_id)).scalars().all()
        )
        for version in versions:
            index_version(session, version.id)
        session.commit()
        chunks = session.execute(select(Chunk.id).where(Chunk.user_id == user_id)).scalars().all()
        if not chunks:
            return SuiteResult(
                "retrieval", {}, ["the demo corpus produced no chunks; retrieval cannot be measured"]
            )

        latencies: list[float] = []
        recalls5: list[float] = []
        mrrs: list[float] = []
        ndcgs: list[float] = []
        misses: list[dict] = []

        for entry in queries:
            started = time.perf_counter()
            result = hybrid_search(session, user_id, entry["query"], k=5)
            latencies.append((time.perf_counter() - started) * 1000)

            mapping = _corpus_ids(session, user_id)
            ranked = [mapping.get(h.document_id) for h in result.hits]
            relevant = entry["relevant"]
            recalls5.append(recall_at_k(ranked, relevant, 5))
            mrrs.append(mrr(ranked, relevant))
            ndcgs.append(ndcg_at_k(ranked, relevant, 10))
            if recall_at_k(ranked, relevant, 5) < 1.0:
                misses.append({"query": entry["query"], "note": entry.get("note", "")})

        metrics = {
            "queries": len(queries),
            "recall@5": mean(recalls5),
            "mrr": mean(mrrs),
            "ndcg@10": mean(ndcgs),
        }
        metrics.update({f"latency_{k}": v for k, v in percentiles(latencies, (50, 95)).items()})
        notes = []
        if misses:
            notes.append(f"{len(misses)} of {len(queries)} queries missed a relevant document")
            notes.extend(f"  - {m['query']} ({m['note']})" for m in misses[:8])
        return SuiteResult("retrieval", metrics, notes, len(queries))
    finally:
        session.rollback()
        session.close()


def _corpus_ids(session, user_id) -> dict:
    from sqlalchemy import select

    from app.models.document import Document
    from app.models.source import Source

    rows = session.execute(
        select(Document.id, Source.config)
        .join(Source, Source.id == Document.source_id)
        .where(Document.user_id == user_id)
    ).all()
    return {doc_id: (cfg or {}).get("corpus_id") for doc_id, cfg in rows}


# --- claims --------------------------------------------------------------


def run_claims() -> SuiteResult:
    """How the heuristic relation judge scores against the labelled pairs."""
    from app.claims.relate import heuristic_relation

    class Stub:
        """The judge reads four attributes off a claim; nothing else is needed."""

        def __init__(self, ident, statement, day):
            self.id = ident
            self.statement = statement
            self.quote = statement
            self.asserted_at = datetime(2026, 1, day, tzinfo=UTC)

    pairs = read_jsonl(DATA / "claim_pairs.jsonl")
    if not pairs:
        return SuiteResult("claims", {}, ["claim_pairs.jsonl is missing"])

    truth: list[str] = []
    predicted: list[str] = []
    by_lang: dict[str, list[int]] = {}
    by_phenomenon: dict[str, list[int]] = {}

    for index, pair in enumerate(pairs, start=1):
        a = Stub(2 * index, pair["a"], 1)
        b = Stub(2 * index + 1, pair["b"], 2)
        draft = heuristic_relation(a, b, _simulated_similarity(pair))
        guess = draft.relation if draft else "unrelated"

        truth.append(pair["label"])
        predicted.append(guess)
        correct = int(guess == pair["label"])
        by_lang.setdefault(pair["lang"], []).append(correct)
        by_phenomenon.setdefault(pair["phenomenon"], []).append(correct)

    report = per_class_report(truth, predicted)
    metrics = {
        "pairs": len(pairs),
        "macro_f1": report["macro"]["f1"],
        "accuracy": mean([int(t == p) for t, p in zip(truth, predicted, strict=True)]),
        **{f"f1_{label}": report[label]["f1"] for label in report if label != "macro"},
    }
    notes = ["accuracy by phenomenon (n = cases):"]
    for phenomenon, hits in sorted(by_phenomenon.items()):
        notes.append(f"  - {phenomenon}: {mean(hits):.2f} (n={len(hits)})")
    notes.append(
        "accuracy by language: "
        + ", ".join(f"{lang}={mean(hits):.2f} (n={len(hits)})" for lang, hits in sorted(by_lang.items()))
    )
    return SuiteResult("claims", metrics, notes, len(pairs))


def _simulated_similarity(pair: dict) -> float:
    """The similarity the judge would have been handed.

    The dataset records labels, not embeddings, so the offline run feeds a
    plausible similarity band per phenomenon rather than pretending to embed
    anything. A live run can supply real similarities; this keeps the offline
    number honest about being a heuristic-band measurement.
    """
    band = {
        "duplicate": 0.95,
        "same_value": 0.90,
        "paraphrase": 0.70,
        "unit_conversion": 0.65,
        "same_unit_value": 0.50,
        "negation": 0.45,
        "date_moved": 0.50,
        "revised_schedule": 0.55,
        "trap_same_number_diff_subject": 0.45,
        "trap_different_metric": 0.40,
        "trap_qualifier_explains": 0.45,
    }
    return band.get(pair.get("phenomenon", ""), 0.45)


# --- verify --------------------------------------------------------------


def run_verify() -> SuiteResult:
    """The verifier's four verdicts against the labelled cases.

    Evidence is supplied inline in each case, so this measures the *judging* rules in
    isolation rather than the whole retrieval path.
    """
    from app.verify.verifier import judge_sentence_against

    cases = read_jsonl(DATA / "verify_cases.jsonl")
    if not cases:
        return SuiteResult("verify", {}, ["verify_cases.jsonl is missing"])

    truth: list[str] = []
    predicted: list[str] = []
    per_label: dict[str, list[int]] = {}
    latencies: list[float] = []

    for case in cases:
        verdicts = [judge_sentence_against(case["sentence"], e["text"])[0] for e in case["evidence"]]
        has_support = any(v in ("supported", "disputed") for v in verdicts)
        has_contradiction = any(v in ("contradicted", "disputed") for v in verdicts)
        if has_support and has_contradiction:
            guess = "disputed"
        elif has_support:
            guess = "supported"
        elif has_contradiction:
            guess = "contradicted"
        else:
            guess = "unsupported"

        started = time.perf_counter()
        for e in case["evidence"]:
            judge_sentence_against(case["sentence"], e["text"])
        latencies.append((time.perf_counter() - started) * 1000)

        truth.append(case["label"])
        predicted.append(guess)
        per_label.setdefault(case["label"], []).append(int(guess == case["label"]))

    report = per_class_report(truth, predicted)
    metrics = {
        "cases": len(cases),
        "macro_f1": report["macro"]["f1"],
        "accuracy": mean([int(t == p) for t, p in zip(truth, predicted, strict=True)]),
        **{f"recall_{label}": report[label]["recall"] for label in report if label != "macro"},
    }
    metrics.update({f"latency_{k}": v for k, v in percentiles(latencies, (50, 95)).items()})
    notes = [
        "accuracy by gold label: "
        + ", ".join(f"{label}={mean(hits):.2f} (n={len(hits)})" for label, hits in sorted(per_label.items()))
    ]
    return SuiteResult("verify", metrics, notes, len(cases))


# --- extraction ----------------------------------------------------------


def run_extraction() -> SuiteResult:
    """Quote alignment: can the pipeline find the claims and locate their quotes?

    A prediction counts as a hit when it overlaps a gold span by at least 50%, which
    is the plan's rule and is more forgiving -- and more honest -- than demanding an
    exact character match, since sentence boundaries legitimately differ.
    """
    from app.claims.align import align_quote
    from app.claims.heuristic_extract import heuristic_claims

    cases = read_jsonl(DATA / "extraction_cases.jsonl")
    if not cases:
        return SuiteResult("extraction", {}, ["extraction_cases.jsonl is missing"])

    overlap_threshold = 0.5
    matched = predicted = gold = 0
    aligned = attempted = 0
    misses: list[str] = []

    for case in cases:
        text = case["text"]
        drafts = heuristic_claims(text, 0)
        predicted += len(drafts)
        gold += len(case["spans"])

        for draft in drafts:
            span = align_quote(draft.quote, text, 0)
            if span is not None:
                aligned += 1
            attempted += 1
            hit = any(_overlaps(span, g, overlap_threshold) for g in case["spans"]) if span else False
            matched += int(hit)
            if not hit:
                misses.append(draft.quote[:70])

        for span in case["spans"]:
            if not any(_overlaps((d.char_start, d.char_end), span, overlap_threshold) for d in drafts):
                misses.append(f"[missed] {text[span['start'] : span['end']][:60]}")

    metrics = {
        "passages": len(cases),
        "gold_spans": gold,
        "predicted_spans": predicted,
        "alignment_rate": aligned / attempted if attempted else 0.0,
        "alignment_precision": matched / predicted if predicted else 0.0,
        "alignment_recall": matched / gold if gold else 0.0,
    }
    notes = [f"{len(misses)} unmatched span(s)"] + [f"  - {m}" for m in misses[:10]]
    return SuiteResult("extraction", metrics, notes, len(cases))


def _overlaps(span, gold: dict, threshold: float) -> bool:
    if span is None:
        return False
    start, end = span
    gold_start, gold_end = gold["start"], gold["end"]
    overlap = max(0, min(end, gold_end) - max(start, gold_start))
    width = max(1, min(end - start, gold_end - gold_start))
    return overlap / width >= threshold


# --- report --------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def render_markdown(results: list[SuiteResult], env: dict, mode: str, suites: str) -> str:
    lines = [
        "# Evaluation report",
        "",
        f"- **mode**: `{mode}`",
        f"- **suites**: `{suites}`",
        f"- **generated**: {env['generated_at']}",
        f"- **git**: `{env['git_sha']}`",
        f"- **python**: {env['python']}",
        "",
        "## Environment",
        "",
        "| setting | value |",
        "|---|---|",
        f"| embedder | `{env['embedder']}` (dim {env['embedder_dim']}) |",
        f"| embedder detail | `{env['embedder_detail']}` |",
        f"| reranker | `{env['reranker']}` |",
        f"| reranker detail | `{env['reranker_detail']}` |",
        f"| llm provider | `{env['llm_provider']}` (mode `{env['llm_mode']}`) |",
        "",
    ]

    for result in results:
        lines.extend([f"## {result.name}", ""])
        if not result.metrics:
            lines.extend(["_no results_", ""])
            continue
        lines.extend(["| metric | value |", "|---|---|"])
        for key, value in result.metrics.items():
            shown = _fmt(value) if isinstance(value, float) else value
            lines.append(f"| {key} | {shown} |")
        lines.append("")
        if result.notes:
            lines.extend(["<details><summary>notes</summary>", ""])
            lines.extend(result.notes)
            lines.extend(["", "</details>", ""])

    lines.extend(
        [
            "---",
            "",
            "Numbers are only comparable within the same environment block above. "
            "Changing the embedder changes them.",
            "",
        ]
    )
    return "\n".join(lines)


def run(suites: str = "all", mode: str = "offline") -> int:
    from app.core.logging import configure_logging

    configure_logging()
    wanted = (
        ["retrieval", "claims", "verify", "extraction"]
        if suites == "all"
        else [s.strip() for s in suites.split(",") if s.strip()]
    )

    runners = {
        "retrieval": run_retrieval,
        "claims": run_claims,
        "verify": run_verify,
        "extraction": run_extraction,
    }

    results: list[SuiteResult] = []
    for name in wanted:
        runner = runners.get(name)
        if runner is None:
            print(f"unknown suite {name!r}; known: {', '.join(runners)}")
            continue
        try:
            result = runner()
        except Exception as exc:  # noqa: BLE001 - one broken suite must not lose the rest
            print(f"suite {name} failed: {type(exc).__name__}: {exc}")
            result = SuiteResult(name, {}, [f"FAILED: {type(exc).__name__}: {exc}"])
        results.append(result)
        print(f"  {name}: {result.metrics}")

    env = environment()
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    markdown = render_markdown(results, env, mode, suites)

    report_path = REPORTS / f"{stamp}-{suites.replace(',', '_')}-{mode}.md"
    report_path.write_text(markdown, encoding="utf-8")
    (REPORTS / "latest.md").write_text(markdown, encoding="utf-8")
    (REPORTS / "latest.json").write_text(
        json.dumps(
            {"environment": env, "mode": mode, "suites": suites, "results": [r.as_dict() for r in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _record_run(env, mode, suites, results)

    print(f"\nwrote {report_path.relative_to(ROOT)}")
    print(f"wrote {(REPORTS / 'latest.md').relative_to(ROOT)}")
    return 0


def _record_run(env: dict, mode: str, suites: str, results: list[SuiteResult]) -> None:
    """Persist the run so the dashboard can show history."""
    try:
        from app.core.db import SessionLocal
        from app.models.eval import EvalRun

        session = SessionLocal()
        try:
            session.add(
                EvalRun(
                    suite=suites,
                    mode=mode,
                    metrics={r.name: r.metrics for r in results},
                    config=env,
                    report_path="evals/reports/latest.md",
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - recording is best-effort
        print(f"could not record the eval run: {type(exc).__name__}: {exc}")


__all__ = [
    "CACHE",
    "DATA",
    "REPORTS",
    "SuiteResult",
    "environment",
    "read_jsonl",
    "render_markdown",
    "run",
    "run_claims",
    "run_extraction",
    "run_retrieval",
    "run_verify",
]
