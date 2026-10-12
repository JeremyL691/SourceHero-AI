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
    hits: list[Hit], numbers: list[int], db: Session | None, user_id: uuid.UUID, sentence: str = ""
) -> list[EvidenceSpan]:
    """Load the evidence spans for the citation numbers, best sentence per chunk."""
    if db is None or not numbers:
        return []
    from app.verify.evidence import chunk_evidence_spans, load_cited_chunks

    chunk_ids = [hits[n - 1].chunk_id for n in sorted(set(numbers)) if 1 <= n <= len(hits)]
    spans: list[EvidenceSpan] = []
    for chunk_id in load_cited_chunks(db, user_id, chunk_ids):
        spans.extend(chunk_evidence_spans(db, chunk_id, sentence))
    return spans


def check_sentence(
    sentence: str, numbers: list[int], hits: list[Hit], db: Session | None, user_id: uuid.UUID
) -> tuple[Verdict, float, list[str]]:
    """Decide one generated sentence against the evidence it cited and the whole hit list.

    A citation outside the retrieved range is reported with an `invalid_citation` flag.
    To prevent circular self-proving, the sentence is checked against the cited chunks
    for support and against the entire retrieved hit set for contradictions.
    """
    flags: list[str] = []
    if not numbers:
        flags.append("uncited")
    if any(n < 1 or n > len(hits) for n in numbers):
        flags.append("invalid_citation")

    clean, _ = parse_citations(sentence)
    cited_spans = _evidence_for(hits, numbers, db, user_id, clean) if numbers else []

    all_spans: list[EvidenceSpan] = []
    if db is not None:
        from app.verify.evidence import chunk_evidence_spans

        for hit in hits:
            all_spans.extend(chunk_evidence_spans(db, hit.chunk_id, clean))
    else:
        for hit in hits:
            spans = split_sentences(hit.text)
            for s in spans:
                t = s.text_of(hit.text).strip()
                if t:
                    all_spans.append(
                        EvidenceSpan(
                            chunk_id=hit.chunk_id,
                            text=t,
                            score=0.0,
                            char_start=s.start,
                            char_end=s.end,
                        )
                    )

    if not all_spans and not cited_spans:
        return "unsupported", 0.0, [*flags, "no_evidence"]

    judgements_cited = [judge_sentence_against(clean, span.text) for span in cited_spans]
    judgements_all = [judge_sentence_against(clean, span.text) for span in all_spans]

    has_cited_support = any(v in ("supported", "disputed") for v, _, _ in judgements_cited)
    has_any_contradiction = any(v in ("contradicted", "disputed") for v, _, _ in judgements_all)

    # Support must come from cited evidence if citations are provided
    has_support = (
        has_cited_support if numbers else any(v in ("supported", "disputed") for v, _, _ in judgements_all)
    )

    if has_support and has_any_contradiction:
        verdict: Verdict = "disputed"
    elif has_any_contradiction:
        verdict = "contradicted"
    elif has_support:
        verdict = "supported"
    else:
        verdict = "unsupported"

    confidence = max(
        (c for _, c, _ in (judgements_all if has_any_contradiction else judgements_cited or judgements_all)),
        default=0.0,
    )
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


def _extractive_answer(question: str, hits: list[Hit], disputes: list[dict] | None = None) -> str:
    """A fallback answer built from the evidence itself using relevance ranking and MMR."""
    from app.claims.signals import token_f1

    candidates: list[dict] = []
    for position, hit in enumerate(hits, start=1):
        spans = split_sentences(hit.text)
        for s in spans:
            quote = s.text_of(hit.text).strip()
            if len(quote) < 10:
                continue
            sim = token_f1(question, quote)
            candidates.append(
                {
                    "text": quote,
                    "hit_idx": position,
                    "score": sim,
                }
            )

    if not candidates:
        return "The available evidence does not address this question."

    # MMR selection: up to 4 sentences
    # Balance relevance to question and diversity among selected sentences
    selected: list[dict] = []
    remaining = list(candidates)
    lambda_param = 0.7

    while remaining and len(selected) < 4:
        if not selected:
            best = max(remaining, key=lambda c: c["score"])
        else:

            def mmr_score(cand: dict) -> float:
                redundancy = max(token_f1(cand["text"], s["text"]) for s in selected)
                return lambda_param * cand["score"] - (1.0 - lambda_param) * redundancy

            best = max(remaining, key=mmr_score)
        selected.append(best)
        remaining.remove(best)

    parts: list[str] = [with_citation(s["text"], s["hit_idx"]) for s in selected]
    return " ".join(parts)


def build_answer_context(question: str, hits: list, disputes: list | None = None) -> dict:
    evidence_vars = [
        {
            "n": i + 1,
            "title": getattr(h, "title", "") or "",
            "source": getattr(h, "source_name", "") or "",
            "date": h.published_at.date().isoformat() if getattr(h, "published_at", None) else None,
            "text": getattr(h, "text", "") or "",
        }
        for i, h in enumerate(hits)
    ]
    disputes_vars = [
        {
            "label": d.get("label", "disputed"),
            "a": d.get("a", ""),
            "b": d.get("b", ""),
        }
        for d in (disputes or [])
    ]
    return {
        "question": question,
        "evidence": evidence_vars,
        "disputes": disputes_vars,
    }


def _sse(event: str, data: dict) -> str:
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def answer_stream(question: str, user_id: uuid.UUID, *, k: int = 8) -> StreamingResponse:
    """Stream `retrieval` -> `conflicts` -> (`delta` | `sentence`)* -> `done`.

    A model failure part-way through emits `error` (with a `code`), then the answer is
    rebuilt extractively and still stored, so the reader always gets a finished answer.
    """

    def generate() -> Iterator[str]:
        from app.api.deps import get_session_factory
        from app.core.db import SessionLocal
        from app.llm.client import get_llm
        from app.retrieval.hybrid import hybrid_search

        # The request-level session serves retrieval and configuration only. Holding it
        # open through a multi-second model stream would pin a pooled connection.
        with get_session_factory()() as db:
            result = hybrid_search(db, user_id, question, k=k)
            hits = list(result.hits)
            yield _sse("retrieval", {"hits": [_hit_out(h) for h in hits]})

            disputes = related_disputes(db, hits, user_id)
            yield _sse("conflicts", {"disputes": disputes})

            client = get_llm(db=db, user_id=user_id)

        answer = Answer(question=question, evidence=hits, disputes=disputes)
        if client.effective.provider != "heuristic":
            try:
                answer.method = f"llm({client.effective.provider})"
                yield from _llm_answer(client, question, user_id, hits, disputes, answer)
            except Exception as exc:  # noqa: BLE001 - the reader must still get an answer
                code = getattr(exc, "code", None) or type(exc).__name__
                log.warning("ask: LLM answer failed (%s), answering extractively", code)
                yield _sse("error", {"code": code, "message": str(exc)[:300]})
                answer = Answer(question=question, evidence=hits, disputes=disputes)
                yield from _extractive_stream(
                    answer, question, hits, disputes, user_id, f"heuristic(fallback:{code})"
                )
        else:
            yield from _extractive_stream(answer, question, hits, disputes, user_id, "heuristic(extractive)")

        with SessionLocal() as db:
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


def _llm_answer(
    client,
    question: str,
    user_id: uuid.UUID,
    hits: list[Hit],
    disputes: list[dict],
    answer: Answer,
) -> Iterator[str]:
    """Stream the model's answer, verifying each sentence as soon as it completes."""
    from app.llm.client import LLMRequest
    from app.llm.prompts import render

    ctx = build_answer_context(question, hits, disputes)
    messages, version = render("answer", **ctx)
    req = LLMRequest(
        task="answer",
        prompt_version=version,
        messages=messages,
        tier="smart",
        user_id=user_id,
        context={**ctx, "hits": hits},
    )
    method = client.effective.provider
    accumulated = ""
    emitted = 0
    for delta in client.stream(req):
        yield _sse("delta", {"text": delta})
        accumulated += delta
        spans = split_sentences(accumulated)
        # The last span may still be growing, so only earlier spans are complete.
        emitted = yield from _emit_new_sentences(
            accumulated, spans[:-1], emitted, hits, user_id, method, answer
        )

    yield from _emit_new_sentences(
        accumulated, split_sentences(accumulated), emitted, hits, user_id, method, answer
    )
    answer.answer_md = accumulated


def _emit_new_sentences(
    text: str,
    spans: list,
    emitted: int,
    hits: list[Hit],
    user_id: uuid.UUID,
    method: str,
    answer: Answer,
) -> Iterator[str]:
    """Verify and emit the spans from index `emitted` on. Returns the new emitted count."""
    while emitted < len(spans):
        span = spans[emitted]
        record = _checked_sentence(
            span.text_of(text), emitted + 1, span.start, span.end, hits, user_id, method
        )
        answer.sentences.append(record)
        yield _sentence_event(record)
        emitted += 1
    return emitted


def _extractive_stream(
    answer: Answer,
    question: str,
    hits: list[Hit],
    disputes: list[dict],
    user_id: uuid.UUID,
    method: str,
) -> Iterator[str]:
    """The MMR extractive answer, streamed the same way as a model answer."""
    answer.method = method
    answer.sentences = []
    text = _extractive_answer(question, hits, disputes)
    for piece in _chunks(text):
        yield _sse("delta", {"text": piece})
    offset = 0
    for index, span in enumerate(split_sentences(text), start=1):
        sentence = span.text_of(text)
        record = _checked_sentence(sentence, index, offset, offset + len(sentence), hits, user_id, method)
        answer.sentences.append(record)
        offset += len(sentence) + 1
        yield _sentence_event(record)
    answer.answer_md = text


def _chunks(text: str, size: int = 64) -> Iterator[str]:
    for start in range(0, len(text), size):
        yield text[start : start + size]


def _checked_sentence(
    sentence_text: str,
    index: int,
    char_start: int,
    char_end: int,
    hits: list[Hit],
    user_id: uuid.UUID,
    method: str,
) -> AnswerSentence:
    """Verify one sentence in a short session, so no request session is held meanwhile."""
    from app.core.db import SessionLocal

    clean, numbers = parse_citations(sentence_text)
    with SessionLocal() as db:
        verdict, confidence, flags = check_sentence(sentence_text, numbers, hits, db, user_id)
    return AnswerSentence(
        index=index,
        text=clean,
        char_start=char_start,
        char_end=char_end,
        verdict=verdict,
        confidence=confidence,
        citations=sorted(set(numbers)),
        flags=flags,
        method=method,
    )


def _sentence_event(record: AnswerSentence) -> str:
    return _sse(
        "sentence",
        {
            "index": record.index,
            "text": record.text,
            "verdict": record.verdict,
            "confidence": round(record.confidence, 4),
            "citations": record.citations,
            "flags": record.flags,
        },
    )


def _persist(db: Session, user_id: uuid.UUID, answer: Answer) -> Answer:
    from app.models.answer import Answer as AnswerRow

    answer.citations = [_hit_citation(i + 1, h) for i, h in enumerate(answer.evidence)]
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
        "quote": hit.text,
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
        "source_name": hit.source_name,
        "published_at": hit.published_at.isoformat() if hit.published_at else None,
        "page": hit.page,
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


def related_disputes(db: Session, hits: list[Hit], user_id: uuid.UUID | None = None) -> list[dict]:
    """Clusters that the retrieved chunks take part in, when those clusters conflict."""
    from app.claims.camps import conflicts_for_chunks

    chunk_ids = [h.chunk_id for h in hits]
    if not chunk_ids:
        return []
    if user_id is None:
        from app.models.chunk import Chunk

        first = db.get(Chunk, chunk_ids[0])
        if first is not None:
            user_id = first.user_id
        else:
            return []
    return conflicts_for_chunks(db, user_id, chunk_ids)


def answer_json(question: str, user_id: uuid.UUID, db: Session, *, k: int = 8) -> dict:
    """The same answer, returned whole (`?stream=false`)."""
    from app.llm.client import LLMRequest, get_llm
    from app.llm.prompts import render
    from app.retrieval.hybrid import hybrid_search

    result = hybrid_search(db, user_id, question, k=k)
    hits = list(result.hits)
    disputes = related_disputes(db, hits, user_id)
    answer = Answer(question=question, evidence=hits, disputes=disputes)

    client = get_llm(db=db)
    use_llm = client.effective.provider != "heuristic"

    if use_llm:
        answer.method = f"llm({client.effective.provider})"
        ctx = build_answer_context(question, hits, disputes)
        messages, version = render(
            "answer",
            **ctx,
        )
        req = LLMRequest(
            task="answer",
            prompt_version=version,
            messages=messages,
            tier="smart",
            user_id=user_id,
            context={
                **ctx,
                "hits": hits,
            },
        )
        res = client.complete(req)
        answer.answer_md = res.text
    else:
        answer.method = "heuristic(extractive)"
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
                method=client.effective.provider if use_llm else "heuristic",
            )
        )
    answer = _persist(db, user_id, answer)
    return answer.as_dict()


try:
    from app.llm.providers.fake import register_default

    @register_default("answer")
    def _fake_default_answer(context: dict) -> str:
        question = context.get("question", "")
        hits = context.get("hits", [])
        disputes = context.get("disputes", [])
        return _extractive_answer(question, hits, disputes)
except ImportError:
    pass


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
