"""The page validator: the last gate before a sentence reaches a reader.

Runs on every page, whoever produced it. Four rules, in order:

1. A sentence must cite at least one claim, and every id must belong to this page.
2. Every quantity in the sentence must be matched, within 5%, by a quantity in the
   claims it cites.
3. A model-written sentence must share enough vocabulary with its claims to be
   *about* them.
4. Empty sections are dropped, and dropped sentences are recorded.

Rule 2 deliberately does **not** use `numbers_conflict`. A history sentence citing
both "18%" and "11%" and a disputed sentence citing both camps are correct, and a
conflict test would delete exactly the sentences that carry the product's value.
"""

from __future__ import annotations

from app.claims.numbers import DateRef, Quantity, extract_dates, extract_quantities
from app.claims.signals import token_f1
from app.llm.schemas import WikiBody, WikiSentence
from app.models.claim import Claim

# How close two quantities of the same unit must be to count as the same measurement.
NUMBER_TOLERANCE = 0.05
# How much a generated sentence must overlap its claims to be about them.
MIN_OVERLAP_F1 = 0.25
# A bare integer with no unit is ignored: "the second version", a section number.
_MIN_UNIT_LENGTH = 1


def _matches(candidate: Quantity, reference: Quantity) -> bool:
    if candidate.dimension and reference.dimension and candidate.dimension != reference.dimension:
        return False
    if candidate.unit != reference.unit:
        return False
    if candidate.head and reference.head and candidate.head.lower() != reference.head.lower():
        return False

    def close(v1: float, v2: float) -> bool:
        scale = max(abs(v1), abs(v2))
        return True if scale == 0 else abs(v1 - v2) / scale <= NUMBER_TOLERANCE

    if candidate.is_interval and reference.is_interval:
        return close(candidate.effective_low, reference.effective_low) and close(
            candidate.effective_high, reference.effective_high
        )
    if candidate.is_interval:
        return False
    if reference.is_interval:
        if close(candidate.value, reference.effective_low) or close(
            candidate.value, reference.effective_high
        ):
            return True
        return reference.effective_low <= candidate.value <= reference.effective_high

    return close(candidate.value, reference.value)


def _date_matches(candidate: DateRef, reference: DateRef) -> bool:
    if candidate.year != reference.year:
        return False
    if candidate.quarter and reference.quarter:
        return candidate.month == reference.month
    if candidate.month is not None and reference.month is not None:
        if candidate.month != reference.month:
            return False
        if candidate.day is not None and reference.day is not None and candidate.day != reference.day:
            return False
    return True


def sentence_is_supported(
    sentence: WikiSentence, claims: dict[int, Claim], *, allow_paraphrase: bool
) -> tuple[bool, str]:
    """Is this sentence defensible from the claims it cites?"""
    if not sentence.claim_ids:
        return False, "no claim cited"
    unknown = [cid for cid in sentence.claim_ids if cid not in claims]
    if unknown:
        return False, f"cites claims not on this page: {unknown}"

    cited = [claims[cid] for cid in sentence.claim_ids]

    # Rule 2: every stated quantity must be present in the cited claims.
    available = [q for claim in cited for q in extract_quantities(claim.statement)]
    for quantity in extract_quantities(sentence.text):
        if not any(_matches(quantity, other) for other in available):
            return False, f"states {quantity.raw!r}, which no cited claim contains"

    # Rule 2b: every stated date must be consistent with cited claims.
    sentence_dates = extract_dates(sentence.text)
    if sentence_dates:
        available_dates: list[DateRef] = []
        for claim in cited:
            available_dates.extend(extract_dates(claim.statement, include_bare_year=True))
            if claim.asserted_at:
                available_dates.append(
                    DateRef(
                        year=claim.asserted_at.year,
                        month=claim.asserted_at.month,
                        day=claim.asserted_at.day,
                        raw="",
                        start=0,
                        end=0,
                    )
                )
        for date_ref in sentence_dates:
            if not any(_date_matches(date_ref, other) for other in available_dates):
                return False, f"states date {date_ref.raw!r}, which no cited claim contains"

    # Rule 3: a generated sentence must be *about* its claims.
    if allow_paraphrase:
        combined = " ".join(c.statement for c in cited)
        if token_f1(sentence.text, combined) < MIN_OVERLAP_F1:
            return False, "shares too little vocabulary with the claims it cites"

    return True, ""


def validate_body(
    body: WikiBody, claims: dict[int, Claim], *, allow_paraphrase: bool = False
) -> tuple[WikiBody, list[dict]]:
    """Drop unsupported sentences; report what was dropped and why.

    `allow_paraphrase` is True for model output (a model legitimately rewords) and
    False for the heuristic compiler, whose sentences are quotes and would fail a
    paraphrase check for no reason.
    """
    dropped: list[dict] = []

    def check(sentence: WikiSentence, where: str) -> bool:
        ok, why = sentence_is_supported(sentence, claims, allow_paraphrase=allow_paraphrase)
        if not ok:
            dropped.append({"where": where, "text": sentence.text, "reason": why})
        return ok

    summary = [s for s in body.summary if check(s, "summary")]
    sections = []
    for section in body.sections:
        sentences = [s for s in section.sentences if check(s, section.id)]
        # An emptied section carries no information; drop it rather than render a stub.
        if sentences:
            sections.append(section.model_copy(update={"sentences": sentences}))

    return (
        WikiBody(
            title=body.title,
            summary=summary,
            sections=sections,
            see_also=[slug for slug in body.see_also if slug],
        ),
        dropped,
    )


def covers_dispute(body: WikiBody, claims: dict[int, Claim], camps: dict[int, str]) -> bool:
    """Does any sentence cite claims from both camps of a disagreement?

    Lint only: a page that silently presents one side of a live dispute as settled is
    misleading even when every sentence is individually supported.
    """
    sides = {camps[cid] for cid in camps if cid in claims}
    if len(sides) < 2:
        return True
    for sentence in body.summary:
        cited = {camps.get(cid) for cid in sentence.claim_ids if cid in camps}
        cited.discard(None)
        if len(cited) >= 2:
            return True
    for section in body.sections:
        for sentence in section.sentences:
            cited = {camps.get(cid) for cid in sentence.claim_ids if cid in camps}
            cited.discard(None)
            if len(cited) >= 2:
                return True
    return False


__all__ = [
    "MIN_OVERLAP_F1",
    "NUMBER_TOLERANCE",
    "covers_dispute",
    "sentence_is_supported",
    "validate_body",
]
