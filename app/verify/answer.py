"""Grounded answers, streamed sentence by sentence with a verdict for each.

Two things make this more than "call the model and print":

*The verdict arrives with the sentence, not after it.* The buffer is scanned for
completed sentences as text arrives, and each one is checked against the evidence it
cited before being emitted. A reader watches claims get checked as they are read.

*The session is opened inside the generator.* An SSE body starts executing after the
response is returned, so a request-scoped session would already be closed. `get_session_factory`
exists so tests can supply a differently-scoped session.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.ingestion.segment import split_sentences
from app.retrieval.types import Hit
from app.verify.evidence import EvidenceSpan
from app.verify.verifier import Verdict, judge_sentence_against

log = get_logger(__name__)

# `[2]` or `[1, 3]` at the end of a sentence.
CITATION_RE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Tells nginx not to buffer the stream.
    "X-Accel-Buffering": "no",
}


@dataclass
class AnswerSentence:
    index: int
    text: str
    char_start: int
    char_end: int
    verdict: Verdict = "unsupported"
    confidence: float = 0.0
    citations: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    method: str = "heuristic"


@dataclass
class Answer:
    question: str
    answer_md: str = ""
    sentences: list[AnswerSentence] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    evidence: list[Hit] = field(default_factory=list)
    disputes: list[dict] = field(default_factory=list)
    method: str = "heuristic"
    answer_id: uuid.UUID | None = None

    def as_dict(self) -> dict:
        return {
            "answer_id": str(self.answer_id) if self.answer_id else None,
            "question": self.question,
            "answer_md": self.answer_md,
            "sentences": [
                {
                    "index": s.index,
                    "text": s.text,
                    "char_start": s.char_start,
                    "char_end": s.char_end,
                    "verdict": s.verdict,
                    "confidence": round(s.confidence, 4),
                    "citations": s.citations,
                    "flags": s.flags,
                    "method": s.method,
                }
                for s in self.sentences
            ],
            "citations": self.citations,
            "method": self.method,
        }


def parse_citations(text: str) -> tuple[str, list[int]]:
    """Strip `[n]` groups and return the numbers they named.

    The space a marker leaves behind before the sentence's full stop is closed up,
    so the cleaned sentence reads naturally rather than "450 Wh/kg ." -- it is shown
    to the reader verbatim.
    """
    numbers: list[int] = []
    for match in CITATION_RE.finditer(text):
        for part in match.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                numbers.append(int(part))
    cleaned = CITATION_RE.sub("", text)
    cleaned = re.sub(r"[ \t]+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip(), numbers


def _evidence_for(
    hits: list[Hit], numbers: list[int], db: Session | None, user_id: uuid.UUID
) -> list[EvidenceSpan]:
    """Load the evidence spans for the citation numbers, best sentence per chunk."""
    if db is None or not numbers:
        return []
    from app.verify.evidence import chunk_evidence_spans, load_cited_chunks

    chunk_ids = [hits[n - 1].chunk_id for n in sorted(set(numbers)) if 1 <= n <= len(hits)]
    spans: list[EvidenceSpan] = []
    for chunk_id in load_cited_chunks(db, user_id, chunk_ids):
        spans.extend(chunk_evidence_spans(db, chunk_id))
    return spans


def check_sentence(
    sentence: str, numbers: list[int], hits: list[Hit], db: Session | None, user_id: uuid.UUID
) -> tuple[Verdict, float, list[str]]:
    """Decide one generated sentence against the evidence it cited.

    A citation outside the retrieved range is a defect in the answer, not a fact about
    the world, so it is reported as `unsupported` with an `invalid_citation` flag.
    """
    flags: list[str] = []
    if not numbers:
        flags.append("uncited")
    if any(n < 1 or n > len(hits) for n in numbers):
        return "unsupported", 0.0, [*flags, "invalid_citation"]

    clean, _ = parse_citations(sentence)
    evidence = _evidence_for(hits, numbers, db, user_id)
    if not evidence:
        return "unsupported", 0.0, [*flags, "no_evidence"]

    # `judge_sentence_against` returns (verdict, confidence, rationale).
    judgements = [judge_sentence_against(clean, span.text) for span in evidence]
    has_support = any(v in ("supported", "disputed") for v, _, _ in judgements)
    has_contradiction = any(v in ("contradicted", "disputed") for v, _, _ in judgements)
    if has_support and has_contradiction:
        verdict: Verdict = "disputed"
    elif has_support:
        verdict = "supported"
    elif has_contradiction:
        verdict = "contradicted"
    else:
        verdict = "unsupported"
    confidence = max((c for _, c, _ in judgements), default=0.0)
    return verdict, confidence, flags


_TERMINAL = ".!?。！？"


def with_citation(text: str, number: int) -> str:
    """Append a citation marker *inside* the sentence, before its final punctuation.

    Writing "…market. [1]" instead would put the marker outside the sentence's own
    span, so the next split would attach the citation to the following sentence --
    shifting every verdict by one. Moving the period after the marker also reads the
    way a person writes a citation.
    """
    stripped = text.rstrip()
    if stripped and stripped[-1] in _TERMINAL:
        return f"{stripped[:-1].rstrip()} [{number}]{stripped[-1]}"
    return f"{stripped} [{number}]"


def _extractive_answer(question: str, hits: list[Hit], disputes: list[dict]) -> str:
    """A fallback answer built from the evidence itself.

    Every sentence is a verbatim quote from a source, so it is checkable by
    construction -- which is what the whole product is for. When sources disagree the
    disagreement is stated rather than resolved.
    """
    parts: list[str] = []
    for position, hit in enumerate(hits[:4], start=1):
        sentences = split_sentences(hit.text)
        if not sentences:
            continue
        quote = sentences[0].text_of(hit.text).strip()
        if not quote:
            continue
        parts.append(with_citation(quote, position))
    if not parts:
        return "The available evidence does not address this question."

    body = " ".join(parts)
    if disputes:
        lines = "\n".join(
            f'- {d.get("label", "disputed")}: "{d.get("a", "")}" vs "{d.get("b", "")}"' for d in disputes
        )
        body += f"\n\nSources disagree on this:\n{lines}"
    return body


def _sse(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def answer_stream(question: str, user_id: uuid.UUID, *, k: int = 8) -> StreamingResponse:
    """Stream `retrieval` -> `conflicts` -> (`delta` | `sentence`)* -> `done`."""

    def generate() -> Iterator[str]:
        from app.api.deps import get_session_factory

        factory = get_session_factory()
        with factory() as db:
            from app.retrieval.hybrid import hybrid_search

            result = hybrid_search(db, user_id, question, k=k)
            hits = list(result.hits)
            yield _sse("retrieval", {"hits": [_hit_out(h) for h in hits]})

            disputes = related_disputes(db, hits)
            yield _sse("conflicts", {"disputes": disputes})

            answer = Answer(question=question, evidence=hits, disputes=disputes)
            text = _extractive_answer(question, hits, disputes)
            answer.method = "heuristic(extractive)"

            for piece in _chunks(text):
                yield _sse("delta", {"text": piece})

            yield from _emit_sentences(text, hits, db, answer, user_id)

            answer.answer_md = text
            answer = _persist(db, user_id, answer)
            yield _sse(
                "done",
                {
                    "answer_id": str(answer.answer_id),
                    "method": answer.method,
                    "summary": _summary(answer),
                    "timings_ms": {k2: round(v, 2) for k2, v in result.timings_ms.items()},
                },
            )

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


def _chunks(text: str, size: int = 64) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _emit_sentences(
    text: str, hits: list[Hit], db: Session, answer: Answer, user_id: uuid.UUID
) -> Iterator[str]:
    """Emit each sentence once it is complete, with its verdict."""
    offset = 0
    for index, span in enumerate(split_sentences(text), start=1):
        sentence = span.text_of(text)
        _, numbers = parse_citations(sentence)
        verdict, confidence, flags = check_sentence(sentence, numbers, hits, db, user_id)
        record = AnswerSentence(
            index=index,
            text=sentence,
            char_start=offset,
            char_end=offset + len(sentence),
            verdict=verdict,
            confidence=confidence,
            citations=sorted(set(numbers)),
            flags=flags,
        )
        answer.sentences.append(record)
        offset += len(sentence) + 1
        yield _sse(
            "sentence",
            {
                "index": index,
                "text": sentence,
                "verdict": verdict,
                "confidence": round(confidence, 4),
                "citations": sorted(set(numbers)),
                "flags": flags,
            },
        )


def _persist(db: Session, user_id: uuid.UUID, answer: Answer) -> Answer:
    from app.models.answer import Answer as AnswerRow

    row = AnswerRow(
        user_id=user_id,
        question=answer.question,
        answer_md=answer.answer_md,
        sentences=[s.__dict__ for s in answer.sentences],
        citations=answer.citations,
        retrieval={"hits": len(answer.evidence), "disputes": len(answer.disputes)},
        method=answer.method,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    answer.answer_id = row.id
    answer.citations = [_hit_citation(i + 1, h) for i, h in enumerate(answer.evidence)]
    return answer


def _hit_citation(number: int, hit: Hit) -> dict:
    return {
        "n": number,
        "chunk_id": hit.chunk_id,
        "document_id": str(hit.document_id),
        "version_id": hit.version_id,
        "title": hit.title,
        "url": hit.url,
        "source": hit.source_name,
        "date": hit.published_at.isoformat() if hit.published_at else None,
        "quote": hit.text[:400],
        "char_start": hit.char_start,
        "char_end": hit.char_end,
        "page": hit.page,
        "score": hit.scores.get("rrf", 0.0),
    }


def _hit_out(hit: Hit) -> dict:
    return {
        "chunk_id": hit.chunk_id,
        "document_id": str(hit.document_id),
        "version_id": hit.version_id,
        "title": hit.title,
        "url": hit.url,
        "source": hit.source_name,
        "text": hit.text,
        "char_start": hit.char_start,
        "char_end": hit.char_end,
        "scores": hit.scores,
    }


def _summary(answer: Answer) -> dict[str, int]:
    out: dict[str, int] = {}
    for sentence in answer.sentences:
        out[sentence.verdict] = out.get(sentence.verdict, 0) + 1
    return out


def related_disputes(db: Session, hits: list[Hit]) -> list[dict]:
    """Clusters that the retrieved chunks take part in, when those clusters conflict.

    Empty until M8 lands the claim ledger; the Ask flow calls it unconditionally so
    the disagreement banner appears without further changes.
    """
    try:
        from app.claims.camps import find_conflicts_for_chunks
    except ImportError:
        return []
    chunk_ids = [h.chunk_id for h in hits]
    return find_conflicts_for_chunks(db, chunk_ids)


def answer_json(question: str, user_id: uuid.UUID, db: Session, *, k: int = 8) -> dict:
    """The same answer, returned whole (`?stream=false`)."""
    from app.retrieval.hybrid import hybrid_search

    result = hybrid_search(db, user_id, question, k=k)
    hits = list(result.hits)
    disputes = related_disputes(db, hits)
    answer = Answer(question=question, evidence=hits, disputes=disputes, method="heuristic(extractive)")
    answer.answer_md = _extractive_answer(question, hits, disputes)
    for index, span in enumerate(split_sentences(answer.answer_md), start=1):
        sentence = span.text_of(answer.answer_md)
        _, numbers = parse_citations(sentence)
        verdict, confidence, flags = check_sentence(sentence, numbers, hits, db, user_id)
        answer.sentences.append(
            AnswerSentence(
                index=index,
                text=sentence,
                char_start=span.start,
                char_end=span.end,
                verdict=verdict,
                confidence=confidence,
                citations=sorted(set(numbers)),
                flags=flags,
            )
        )
    answer = _persist(db, user_id, answer)
    return answer.as_dict()


__all__ = [
    "Answer",
    "AnswerSentence",
    "answer_json",
    "answer_stream",
    "check_sentence",
    "parse_citations",
    "related_disputes",
    "with_citation",
]
