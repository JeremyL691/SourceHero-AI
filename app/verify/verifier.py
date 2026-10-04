"""The sentence verifier: supported / contradicted / disputed / unsupported.

`disputed` is the reason this product exists, so the four verdicts are decided
explicitly rather than by ranking: a sentence is disputed when the evidence both
supports it and contradicts it, which says more than "the top hit disagreed".

Every heuristic signal is tri-state where that matters. `numbers_conflict` returns
`None` -- not `False` -- when the two sides share no unit at all, and treating that as
"no conflict" is what stops unrelated sentences from contradicting each other.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from app.claims.signals import date_conflict, negation_asymmetry, numbers_conflict, token_f1
from app.core.errors import DomainValidationError
from app.ingestion.segment import Span, split_sentences
from app.verify.config import (
    CONTRADICT_F1,
    DEFAULT_EVIDENCE_K,
    LLM_BATCH_SIZE,
    MAX_VERIFY_CHARS,
    SUPPORT_F1,
    SUPPORT_F1_WITH_NUMBERS,
    VERDICTS,
)
from app.verify.evidence import EvidenceSpan, gather_evidence, load_cited_chunks

Verdict = Literal["supported", "contradicted", "disputed", "unsupported"]


@dataclass
class SentenceVerdict:
    index: int
    text: str
    char_start: int
    char_end: int
    verdict: Verdict
    confidence: float = 0.0
    method: str = "heuristic"
    supporting: list[EvidenceSpan] = field(default_factory=list)
    contradicting: list[EvidenceSpan] = field(default_factory=list)
    rationale: str = ""
    # False when the sentence carried citations the verifier could not confirm.
    cited_ok: bool | None = None
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 4),
            "method": self.method,
            "rationale": self.rationale,
            "cited_ok": self.cited_ok,
            "flags": list(self.flags),
            "supporting": [s.citation(i + 1) for i, s in enumerate(self.supporting)],
            "contradicting": [s.citation(i + 1) for i, s in enumerate(self.contradicting)],
        }


@dataclass
class VerifyResult:
    sentences: list[SentenceVerdict]
    summary: dict[str, int]
    timings_ms: dict[str, float] = field(default_factory=dict)
    method: str = "heuristic"

    def as_dict(self) -> dict:
        return {
            "sentences": [s.as_dict() for s in self.sentences],
            "summary": dict(self.summary),
            "timings_ms": {k: round(v, 2) for k, v in self.timings_ms.items()},
            "method": self.method,
        }


def _signals(sentence: str, evidence_text: str) -> dict:
    return {
        "f1": token_f1(sentence, evidence_text),
        "numbers": numbers_conflict(sentence, evidence_text),
        "dates": date_conflict(sentence, evidence_text),
        "negation": negation_asymmetry(sentence, evidence_text),
    }


def judge_sentence_against(sentence: str, evidence_text: str) -> tuple[Verdict, float, str]:
    """The heuristic verdict for one sentence/evidence pair."""
    signals = _signals(sentence, evidence_text)
    f1 = signals["f1"]
    numbers = signals["numbers"]
    dates = signals["dates"]
    negation = signals["negation"]

    supports = (f1 >= SUPPORT_F1 and numbers is not True and dates is not True and not negation) or (
        numbers is False and f1 >= SUPPORT_F1_WITH_NUMBERS
    )
    contradicts = f1 >= CONTRADICT_F1 and (numbers is True or dates is True or negation)

    if supports and contradicts:
        return "disputed", min(0.99, f1), "evidence both supports and contradicts the sentence"
    if supports:
        return "supported", min(0.95, f1), "evidence restates the sentence"
    if contradicts:
        # Each independent signal that fires adds weight, capped so three signals
        # cannot produce a certainty the evidence does not warrant.
        fired = [key for key in ("numbers", "dates", "negation") if signals[key] is True]
        confidence = min(0.9, 0.6 + 0.1 * (len(fired) - 1))
        return "contradicted", confidence, f"evidence differs on {', '.join(fired)}"
    return "unsupported", 0.0, "no evidence addresses the sentence"


def verify_text(
    db: Session,
    user_id: uuid.UUID,
    text: str,
    *,
    mode: str = "auto",
    citations: dict[int, list[int]] | None = None,
    k: int = DEFAULT_EVIDENCE_K,
    use_llm: bool | None = None,
) -> VerifyResult:
    """Verify every sentence in `text` against the user's evidence.

    `citations` maps a sentence's 1-based index to the chunk ids it names. In that
    mode only the cited chunks count, and a sentence citing nothing gets no automatic
    support -- otherwise it could pass by matching whatever retrieval surfaced.
    """
    if not text or not text.strip():
        return VerifyResult([], {v: 0 for v in VERDICTS}, {"total": 0.0}, "empty")
    if len(text) > MAX_VERIFY_CHARS:
        raise DomainValidationError(
            f"text is {len(text)} characters; the limit is {MAX_VERIFY_CHARS}",
            code="text_too_long",
        )

    started = time.perf_counter()
    spans = split_sentences(text)
    cited_mode = citations is not None

    results: list[SentenceVerdict] = []
    for index, span in enumerate(spans, start=1):
        sentence = span.text_of(text)
        chunk_ids = (citations or {}).get(index)
        evidence = gather_evidence(
            db,
            user_id,
            sentence,
            k=k,
            chunk_ids=list(chunk_ids) if chunk_ids is not None else None,
        )

        if cited_mode and chunk_ids is not None:
            allowed = set(load_cited_chunks(db, user_id, chunk_ids))
            evidence = [e for e in evidence if e.chunk_id in allowed]
            if not allowed:
                results.append(
                    SentenceVerdict(
                        index=index,
                        text=sentence,
                        char_start=span.start,
                        char_end=span.end,
                        verdict="unsupported",
                        rationale="the cited evidence does not exist for this user",
                        cited_ok=False,
                        flags=["invalid_citation"],
                    )
                )
                continue

        results.append(_decide(index, sentence, span, evidence))

    method = "heuristic"
    if use_llm is None:
        use_llm = mode != "heuristic"
    if use_llm:
        results, method = _llm_pass(results, mode)

    timings = {"total": (time.perf_counter() - started) * 1000}
    summary = {v: sum(1 for r in results if r.verdict == v) for v in VERDICTS}
    return VerifyResult(results, summary, timings, method)


def _decide(index: int, sentence: str, span: Span, evidence: list[EvidenceSpan]) -> SentenceVerdict:
    """Combine the pairwise judgements into one verdict for a sentence."""
    supporting: list[EvidenceSpan] = []
    contradicting: list[EvidenceSpan] = []
    best_support = 0.0
    best_contradict = 0.0
    rationales: list[str] = []

    for item in evidence:
        verdict, confidence, why = judge_sentence_against(sentence, item.text)
        if verdict in ("supported", "disputed"):
            supporting.append(item)
            best_support = max(best_support, confidence)
            rationales.append(why)
        if verdict in ("contradicted", "disputed"):
            contradicting.append(item)
            best_contradict = max(best_contradict, confidence)
            rationales.append(why)

    if supporting and contradicting:
        verdict: Verdict = "disputed"
    elif supporting:
        verdict = "supported"
    elif contradicting:
        verdict = "contradicted"
    else:
        verdict = "unsupported"

    return SentenceVerdict(
        index=index,
        text=sentence,
        char_start=span.start,
        char_end=span.end,
        verdict=verdict,
        confidence=max(best_support, best_contradict),
        method="heuristic",
        supporting=supporting,
        contradicting=contradicting,
        rationale="; ".join(dict.fromkeys(rationales)) or "no comparable evidence found",
    )


# --- the LLM pass --------------------------------------------------------


def _llm_pass(results: list[SentenceVerdict], mode: str) -> tuple[list[SentenceVerdict], str]:
    """Re-judge the ambiguous sentences with the LLM.

    Only sentences that stayed undecided or ended up contested are sent: the LLM
    adds information about genuinely hard cases, and re-litigating sentences the
    heuristic was confident about mostly introduces noise.
    """
    from app.llm.schemas import VerifyItem, VerifyResponse
    from app.llm.types import LLMError, LLMRequest

    targets = [r for r in results if r.verdict in ("unsupported", "contradicted", "disputed")]
    if not targets:
        return results, "heuristic"

    try:
        from app.llm.client import get_llm
        from app.llm.prompts import render

        client = get_llm()
        if client.mode != "live" and mode != "heuristic":
            # No live provider and the caller wants one: report the fallback rather
            # than silently pretending the heuristic was an LLM judgement.
            raise LLMError("no live provider is configured", code="no_provider")

        for start in range(0, len(targets), LLM_BATCH_SIZE):
            batch = targets[start : start + LLM_BATCH_SIZE]
            items = [
                VerifyItem(
                    index=position + 1,
                    text=row.text,
                    evidence=[
                        span.citation(j + 1) for j, span in enumerate(row.supporting + row.contradicting)
                    ],
                )
                for position, row in enumerate(batch)
            ]
            messages, version = render("verify", schema=VerifyResponse, items=[i.model_dump() for i in items])
            outcome = client.complete(
                LLMRequest(
                    task="verify",
                    messages=messages,
                    tier="fast",
                    schema=VerifyResponse,
                    prompt_version=version,
                )
            )
            if outcome.parsed is not None:
                counts = [len(row.supporting) + len(row.contradicting) for row in batch]
                _apply_llm_results(batch, outcome.parsed, counts)
    except Exception as exc:  # noqa: BLE001 - degrade and say so
        code = getattr(exc, "code", type(exc).__name__)
        for target in targets:
            target.method = f"heuristic(fallback:{code})"
        return results, f"heuristic(fallback:{code})"

    return results, "llm"


def _apply_llm_results(batch: list[SentenceVerdict], response, evidence_counts: list[int]) -> None:
    """Merge the LLM's verdicts into the heuristic results.

    The ids a model returns are the *local* citation numbers of the evidence list it
    was shown for that sentence, so they are range-checked against that list. A model
    citing evidence it was never given is hallucinating, and its answer is discarded
    in favour of the heuristic one.
    """
    for item in response.results:
        position = item.sentence_index - 1
        if not 0 <= position < len(batch):
            # A sentence index outside the batch means the model lost track of which
            # sentence it was judging.
            continue

        limit = evidence_counts[position]
        cited = [*item.supporting_ids, *item.contradicting_ids]
        if any(not 1 <= cid <= limit for cid in cited):
            batch[position].flags.append("llm_invalid_citation")
            continue

        target = batch[position]
        target.verdict = item.verdict
        target.confidence = item.confidence
        target.rationale = item.rationale or target.rationale
        target.method = "llm"


def register_fake_verify() -> None:
    """`FakeProvider`'s `verify` default: the heuristic, as the prompt's schema."""

    from app.llm.providers.fake import register_default
    from app.llm.schemas import VerifyResponse, VerifyResultItem

    @register_default("verify")
    def _verify_default(context: dict) -> dict:
        response = VerifyResponse()
        for item in context.get("items", []):
            response.results.append(
                VerifyResultItem(
                    sentence_index=item.get("index", 0),
                    # The fake defers to the heuristic verdict recorded on the item;
                    # when it is absent the honest answer is "unsupported".
                    verdict=item.get("verdict", "unsupported"),
                    supporting_ids=item.get("supporting_ids", []),
                    contradicting_ids=item.get("contradicting_ids", []),
                    confidence=item.get("confidence", 0.0),
                    rationale=item.get("rationale", "fake provider: no live model configured"),
                )
            )
        return response.model_dump()


__all__ = [
    "SentenceVerdict",
    "Verdict",
    "VerifyResult",
    "judge_sentence_against",
    "register_fake_verify",
    "verify_text",
]
