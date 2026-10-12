"""The evaluation runner.

    python -m app.evals run --suite all --mode offline
    python -m app.evals download          # external datasets (needs network)

Reports land in `evals/reports/` and always state the environment they ran in --
embedder, reranker, LLM provider, dataset sizes, date, git sha. A retrieval number
without those is not a measurement, it is a rumour.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
    artifact: str | None = None  # a separate report file this suite wrote, if any

    def as_dict(self) -> dict:
        return {
            "suite": self.name,
            "metrics": self.metrics,
            "notes": self.notes,
            "sample_count": self.sample_count,
            "artifact": self.artifact,
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


def environment(split: str = "dev", llm: str = "none") -> dict:
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
        "llm_provider_mode": "fake" if settings.llm_provider == "fake" else "live",
        "llm_mode": llm,
        "llm_model": "none" if llm == "none" else settings.llm_model_fast,
        "llm_requests": 0,
        "cassette": "none",
        "split": split,
        "git_dirty": _git_dirty(),
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


def _git_dirty() -> bool:
    """True when the tree has uncommitted changes. Such a run cannot be reproduced from HEAD."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True, check=True, timeout=10
        ).stdout
        return bool(out.strip())
    except Exception:  # noqa: BLE001 - when in doubt, call the tree dirty
        return True


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


@dataclass
class ClaimStub:
    """What the relation judge reads off a claim. Dates come from the row when it has them (EVAL-10)."""

    id: int
    statement: str
    asserted_at: datetime
    subject: str | None = None
    qualifiers: str | None = None
    quote: str = ""

    def __post_init__(self) -> None:
        self.quote = self.quote or self.statement


def _row_time(value: str | None, fallback_day: int) -> datetime:
    """The row's ISO date, or a fixed fallback for rows that predate the date fields."""
    if value:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)
    return datetime(2026, 1, fallback_day, tzinfo=UTC)


def run_claims(split: str = "dev") -> SuiteResult:
    """How the heuristic relation judge scores against the labelled pairs.

    `split` selects the dev or the held-out test file. The test split reports aggregates
    only: no per-row errors and no per-phenomenon breakdown, so tuning cannot read it.
    """
    from app.claims.relate import heuristic_relation
    from app.indexing.embeddings import get_embedder

    pairs = read_jsonl(DATA / f"claim_pairs.{split}.jsonl")
    if not pairs:
        return SuiteResult("claims", {}, [f"claim_pairs.{split}.jsonl is missing"])

    embedder = get_embedder()
    all_texts: list[str] = []
    for pair in pairs:
        all_texts.append(pair["a"])
        all_texts.append(pair["b"])
    vectors = embedder.embed_documents(all_texts)

    truth: list[str] = []
    predicted: list[str] = []
    by_lang: dict[str, list[int]] = {}
    by_phenomenon: dict[str, list[int]] = {}

    for index, pair in enumerate(pairs, start=1):
        a = ClaimStub(
            2 * index,
            pair["a"],
            _row_time(pair.get("a_date"), 1),
            subject=pair.get("subject"),
            qualifiers=pair.get("qualifiers"),
        )
        b = ClaimStub(
            2 * index + 1,
            pair["b"],
            _row_time(pair.get("b_date"), 2),
            subject=pair.get("subject"),
            qualifiers=pair.get("qualifiers"),
        )
        va = vectors[2 * (index - 1)]
        vb = vectors[2 * (index - 1) + 1]
        dot = sum(x * y for x, y in zip(va, vb, strict=False))
        norm_a = math.sqrt(sum(x * x for x in va))
        norm_b = math.sqrt(sum(x * x for x in vb))
        sim = dot / (norm_a * norm_b) if (norm_a > 0 and norm_b > 0) else 0.0

        draft = heuristic_relation(a, b, sim)
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
        **{f"precision_{label}": report[label]["precision"] for label in report if label != "macro"},
        **{f"recall_{label}": report[label]["recall"] for label in report if label != "macro"},
        **{f"f1_{label}": report[label]["f1"] for label in report if label != "macro"},
    }
    if split == "test":
        return SuiteResult("claims", metrics, [], len(pairs))
    notes = ["accuracy by phenomenon (n = cases):"]
    for phenomenon, hits in sorted(by_phenomenon.items()):
        notes.append(f"  - {phenomenon}: {mean(hits):.2f} (n={len(hits)})")
    notes.append(
        "accuracy by language: "
        + ", ".join(f"{lang}={mean(hits):.2f} (n={len(hits)})" for lang, hits in sorted(by_lang.items()))
    )
    return SuiteResult("claims", metrics, notes, len(pairs))


# --- verify --------------------------------------------------------------


def run_verify(split: str = "dev") -> SuiteResult:
    """The verifier's four verdicts against the labelled cases.

    Evidence is supplied inline in each case, so this measures the *judging* rules in
    isolation rather than the whole retrieval path.
    """
    from app.verify.verifier import judge_sentence_against

    cases = read_jsonl(DATA / f"verify_cases.{split}.jsonl")
    if not cases:
        return SuiteResult("verify", {}, [f"verify_cases.{split}.jsonl is missing"])

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
        **{f"precision_{label}": report[label]["precision"] for label in report if label != "macro"},
        **{f"recall_{label}": report[label]["recall"] for label in report if label != "macro"},
        **{f"f1_{label}": report[label]["f1"] for label in report if label != "macro"},
    }
    metrics.update({f"latency_{k}": v for k, v in percentiles(latencies, (50, 95)).items()})
    if split == "test":
        return SuiteResult("verify", metrics, [], len(cases))
    notes = [
        "accuracy by gold label: "
        + ", ".join(f"{label}={mean(hits):.2f} (n={len(hits)})" for label, hits in sorted(per_label.items()))
    ]
    return SuiteResult("verify", metrics, notes, len(cases))


# --- extraction ----------------------------------------------------------


def run_extraction(split: str = "dev") -> SuiteResult:
    """Quote alignment: can the pipeline find the claims and locate their quotes?

    A prediction counts as a hit when it overlaps a gold span by at least 50%, which
    is the plan's rule and is more forgiving -- and more honest -- than demanding an
    exact character match, since sentence boundaries legitimately differ.
    """
    from app.claims.align import align_quote
    from app.claims.heuristic_extract import heuristic_claims

    cases = read_jsonl(DATA / f"extraction_cases.{split}.jsonl")
    if not cases:
        return SuiteResult("extraction", {}, [f"extraction_cases.{split}.jsonl is missing"])

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
            if span is not None and text[span[0] : span[1]] == draft.quote:
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
    if split == "test":
        return SuiteResult("extraction", metrics, [], len(cases))
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


# --- latency -------------------------------------------------------------


def run_latency(session: Any = None) -> SuiteResult:
    """Measure retrieval and verify latencies (p50/p95 in ms)."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.demo.seed import seed_demo
    from app.indexing.indexer import index_version
    from app.ingestion.pipeline import ensure_user
    from app.jobs.handlers import register_all
    from app.models.document import DocumentVersion
    from app.retrieval.hybrid import hybrid_search
    from app.verify.verifier import judge_sentence_against

    register_all()
    queries = (
        read_jsonl(DATA / "demo_retrieval.json")
        or json.loads((DATA / "demo_retrieval.json").read_text(encoding="utf-8"))["queries"]
    )
    verify_cases = read_jsonl(DATA / "verify_cases.dev.jsonl")

    own_session = session is None
    if own_session:
        session = SessionLocal()
    user_id = _uuid.uuid5(_uuid.NAMESPACE_URL, "sourcehero-eval-latency")
    try:
        ensure_user(session, "eval-latency@sourcehero.local", "eval-password-123")
        seed_demo(session, user_id)
        session.commit()

        versions = (
            session.execute(select(DocumentVersion).where(DocumentVersion.user_id == user_id)).scalars().all()
        )
        for version in versions:
            index_version(session, version.id)
        session.commit()

        retrieval_latencies: list[float] = []
        for entry in queries[:20]:
            started = time.perf_counter()
            hybrid_search(session, user_id, entry["query"], k=5)
            retrieval_latencies.append((time.perf_counter() - started) * 1000)

        verify_latencies: list[float] = []
        for case in verify_cases[:20]:
            started = time.perf_counter()
            for e in case.get("evidence", []):
                judge_sentence_against(case["sentence"], e["text"])
            verify_latencies.append((time.perf_counter() - started) * 1000)

        ret_perc = percentiles(retrieval_latencies, (50, 95))
        ver_perc = percentiles(verify_latencies, (50, 95))

        metrics = {
            "retrieval_p50_ms": ret_perc["p50"],
            "retrieval_p95_ms": ret_perc["p95"],
            "verify_p50_ms": ver_perc["p50"],
            "verify_p95_ms": ver_perc["p95"],
            "retrieval_samples": len(retrieval_latencies),
            "verify_samples": len(verify_latencies),
        }
        notes = [
            f"retrieval: p50={ret_perc['p50']:.1f}ms, p95={ret_perc['p95']:.1f}ms (n={len(retrieval_latencies)})",
            f"verify: p50={ver_perc['p50']:.1f}ms, p95={ver_perc['p95']:.1f}ms (n={len(verify_latencies)})",
        ]
        return SuiteResult("latency", metrics, notes, len(retrieval_latencies) + len(verify_latencies))
    finally:
        if own_session:
            try:
                from app.models.auth import User
                from app.models.document import Chunk, Document, DocumentVersion
                from app.models.source import Source

                session.query(Chunk).filter(Chunk.user_id == user_id).delete()
                session.query(DocumentVersion).filter(DocumentVersion.user_id == user_id).delete()
                session.query(Document).filter(Document.user_id == user_id).delete()
                session.query(Source).filter(Source.user_id == user_id).delete()
                session.query(User).filter(User.id == user_id).delete()
                session.commit()
            except Exception:
                session.rollback()
            session.close()


# --- scifact -------------------------------------------------------------


def _display_path(path: Path) -> str:
    """Repo-relative when the file is inside the repo; absolute otherwise (tests redirect REPORTS)."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def _save_scifact_report(metrics: dict, notes: list[str], embedder_name: str) -> Path:
    """SciFact numbers in a file of their own, so README can cite a path (EVAL-11)."""
    slug = re.sub(r"[^a-z0-9]+", "-", embedder_name.lower()).strip("-") or "unknown"
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    path = REPORTS / f"{stamp}-scifact-{slug}.md"
    lines = [
        "# SciFact report",
        "",
        f"- **embedder**: `{embedder_name}`",
        f"- **git**: `{_git_sha()}` (dirty: `{_git_dirty()}`)",
        f"- **generated**: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
        "| metric | value |",
        "|---|---|",
        *[
            f"| {key} | {_fmt(value) if isinstance(value, float) else value} |"
            for key, value in metrics.items()
        ],
        "",
        *notes,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_scifact() -> SuiteResult:
    """Evaluate retrieval over the BEIR SciFact benchmark dataset."""
    import zipfile

    import numpy as np

    from app.indexing.embeddings import get_embedder

    zip_path = CACHE / "scifact.zip"
    if not zip_path.exists():
        return SuiteResult("scifact", {}, ["SciFact dataset not found in cache; run 'make eval-data' first"])

    try:
        with zipfile.ZipFile(zip_path) as z:
            corpus = []
            doc_ids = []
            with z.open("scifact/corpus.jsonl") as f:
                for line in f:
                    d = json.loads(line)
                    doc_ids.append(d["_id"])
                    corpus.append((d.get("title", "") + " " + d.get("text", "")).strip())

            queries = {}
            with z.open("scifact/queries.jsonl") as f:
                for line in f:
                    d = json.loads(line)
                    queries[d["_id"]] = d["text"]

            qrels: dict[str, set[str]] = {}
            with z.open("scifact/qrels/test.tsv") as f:
                f.readline()  # header
                for line in f:
                    parts = line.decode().strip().split("\t")
                    if len(parts) >= 3 and int(parts[2]) > 0:
                        qrels.setdefault(parts[0], set()).add(parts[1])

        embedder = get_embedder()
        doc_vecs = np.array(embedder.embed_documents(corpus), dtype=np.float32)

        recalls, mrrs, ndcgs = [], [], []
        for qid, gold in qrels.items():
            if qid not in queries:
                continue
            qv = np.array(embedder.embed_query(queries[qid]), dtype=np.float32)
            scores = np.dot(doc_vecs, qv)
            top_indices = np.argsort(scores)[::-1][:10]
            ranked = [doc_ids[idx] for idx in top_indices]
            recalls.append(recall_at_k(ranked, gold, 5))
            mrrs.append(mrr(ranked, gold))
            ndcgs.append(ndcg_at_k(ranked, gold, 10))

        metrics = {
            "queries": len(recalls),
            "corpus_size": len(corpus),
            "recall@5": mean(recalls),
            "mrr": mean(mrrs),
            "ndcg@10": mean(ndcgs),
        }
        notes = [
            f"BEIR SciFact test split ({len(recalls)} queries against {len(corpus)} documents)",
            f"embedder: {embedder.name}",
        ]
        saved = _save_scifact_report(metrics, notes, embedder.name)
        return SuiteResult("scifact", metrics, notes, len(recalls), artifact=_display_path(saved))
    except Exception as exc:
        return SuiteResult("scifact", {}, [f"FAILED: {type(exc).__name__}: {exc}"])


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
        f"| llm provider | `{env['llm_provider']}` (provider mode `{env['llm_provider_mode']}`) |",
        f"| eval llm mode | `{env['llm_mode']}` (model `{env['llm_model']}`, requests {env['llm_requests']}, cassette `{env['cassette']}`) |",
        f"| split | `{env['split']}` |",
        f"| dirty tree | `{env['git_dirty']}` |",
        "",
    ]

    for result in results:
        lines.extend([f"## {result.name}", ""])
        if not result.metrics:
            lines.extend(["_no results_", ""])
            continue
        if result.artifact:
            lines.append(f"artifact: `{result.artifact}`")
            lines.append("")
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


SPLIT_SUITES = ("claims", "verify", "extraction")


def _use_fastembed() -> None:
    """`--mode full`: the configured embedder becomes fastembed, through the settings the app reads."""
    import os

    from app.core.config import get_settings
    from app.indexing.embeddings import reset_embedder

    os.environ["EMBEDDING_PROVIDER"] = "fastembed"
    get_settings.cache_clear()
    reset_embedder()


def run(suites: str = "all", mode: str = "offline", split: str = "dev", llm: str = "none") -> int:
    from app.core.logging import configure_logging

    configure_logging()
    if llm != "none":
        # replay and live need the cassette provider (K1.4). Refuse rather than fall back silently.
        print(f"--llm {llm} needs the cassette provider, which lands in K1.4; run with --llm none")
        return 2
    if mode == "full":
        _use_fastembed()
    splits = ["dev", "test"] if split == "both" else [split]

    runners = {
        "retrieval": run_retrieval,
        "claims": run_claims,
        "verify": run_verify,
        "extraction": run_extraction,
        "latency": run_latency,
        "scifact": run_scifact,
    }

    if suites == "all":
        wanted = ["retrieval", "claims", "verify", "extraction", "latency"]
        if mode == "full" or (CACHE / "scifact.zip").exists():
            wanted.append("scifact")
    else:
        wanted = [s.strip() for s in suites.split(",") if s.strip()]

    results: list[SuiteResult] = []
    for name in wanted:
        runner = runners.get(name)
        if runner is None:
            print(f"unknown suite {name!r}; known: {', '.join(runners)}")
            continue
        variants = splits if name in SPLIT_SUITES else [None]
        for variant in variants:
            label = name if variant in (None, "dev") else f"{name}[{variant}]"
            try:
                result = runner(variant) if variant else runner()
            except Exception as exc:  # noqa: BLE001 - one broken suite must not lose the rest
                print(f"suite {label} failed: {type(exc).__name__}: {exc}")
                result = SuiteResult(label, {}, [f"FAILED: {type(exc).__name__}: {exc}"])
            result.name = label
            results.append(result)
            print(f"  {label}: {result.metrics}")

    env = environment(split=split, llm=llm)
    REPORTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    markdown = render_markdown(results, env, mode, suites)

    report_path = REPORTS / f"{stamp}-{suites.replace(',', '_')}-{mode}-{split}.md"
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
    "run_latency",
    "run_retrieval",
    "run_scifact",
    "run_verify",
]
