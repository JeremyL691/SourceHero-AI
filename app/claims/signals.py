"""Signals used to compare two statements.

These are the primitives both the verifier and the relation judge are built from. The
distinction that matters throughout is between *incomparable* and *disagreeing*:

    numbers_conflict -> True   the same unit, materially different values
                     -> False  the same unit, agreeing values
                     -> None   nothing comparable in common

`None` is load-bearing. Treating "no comparable quantity" as a conflict would make
every sentence about a different subject contradict every other one.
"""

from __future__ import annotations

import re

from app.claims.numbers import DateRef, Quantity, extract_dates, extract_quantities
from app.indexing.tokenize import search_tokens

# Negation words, English and Chinese. Asymmetry is a contradiction signal: one side
# denies what the other asserts.
_NEGATION_WORDS = """
not no never none cannot cant cannot can't didnt didn't doesnt doesn't
isnt isn't wasnt wasn't without nothing neither nor hardly barely rarely
不 没有 未 无 并非 并无 从未 并不
"""

NEGATIONS = frozenset(_NEGATION_WORDS.split())

# Words that make a date a *scheduled event* date, so two different such dates mean
# the sources really disagree about when something happens.
_EVENT_WORDS = """
begin begins began start starts started launch launches launched ship ships
shipped release releases released production available postpone postponed
delay delayed publish published announce announced
量产 发布 上市 推迟 开始 上线
"""

EVENT_WORDS = frozenset(_EVENT_WORDS.split())

# Words that mean a value was revised, which upgrades a contradiction to a supersession.
_UPDATE_WORDS = """
postponed delayed revised updated corrected expected amended
推迟 修订 更新 修正 改为
"""

UPDATE_CUES = frozenset(_UPDATE_WORDS.split())

# Relative difference above which two same-unit numbers count as a conflict.
NUMERIC_TOLERANCE = 0.05


def content_tokens(text: str) -> list[str]:
    """Search tokens minus pure numbers.

    Numbers are removed on purpose: overlap between "450 Wh/kg" and "380 Wh/kg" is
    carried by the numbers, and counting them as shared content would make two
    conflicting measurements look similar.
    """
    out = []
    for token in search_tokens(text):
        if token.replace(".", "", 1).isdigit():
            continue
        out.append(token)
    return out


def token_f1(a: str, b: str) -> float:
    """F1 over content-token multisets: 0.0 (disjoint) to 1.0 (identical)."""
    left, right = content_tokens(a), content_tokens(b)
    if not left or not right:
        return 0.0
    shared = 0
    remaining = list(right)
    for token in left:
        if token in remaining:
            remaining.remove(token)
            shared += 1
    if shared == 0:
        return 0.0
    precision = shared / len(left)
    recall = shared / len(right)
    return 2 * precision * recall / (precision + recall)


def _relative_difference(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    if scale == 0:
        return 0.0
    return abs(a - b) / scale


def numbers_conflict(a: str, b: str) -> bool | None:
    """Do the two statements report different values for the same unit?

    True: same canonical unit, values differing by more than `NUMERIC_TOLERANCE`.
    False: same unit, values that agree within tolerance.
    None: no unit appears on both sides, so there is nothing to compare.
    """
    left: dict[str, list[Quantity]] = {}
    for quantity in extract_quantities(a):
        left.setdefault(quantity.unit, []).append(quantity)
    right: dict[str, list[Quantity]] = {}
    for quantity in extract_quantities(b):
        right.setdefault(quantity.unit, []).append(quantity)

    shared = set(left) & set(right)
    if not shared:
        return None

    for unit in shared:
        for x in left[unit]:
            for y in right[unit]:
                if _relative_difference(x.value, y.value) > NUMERIC_TOLERANCE:
                    return True
    return False


def negation_asymmetry(a: str, b: str) -> bool:
    """Negation appears on exactly one side.

    Both negated is not a contradiction ("neither said X" vs "neither said Y"); one
    negated and one affirmed is.
    """
    return bool(_negations_in(a).symmetric_difference(_negations_in(b)))


def _negations_in(text: str) -> set[str]:
    """Negation words present, counted over *raw* words.

    Not `search_tokens`: that drops stopwords, and "not" is one -- which silently
    removed every English negation this function was supposed to detect.
    """
    return _has_any(text, NEGATIONS)


_WORD_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+")


def _words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(text.lower()) if w]


_CJK_ONLY = re.compile(r"^[\u4e00-\u9fff]+$")


def _has_any(text: str, words: frozenset[str]) -> set[str]:
    """Which entries of `words` appear in `text`.

    Chinese entries are matched as substrings: CJK text has no spaces, so
    "该措施没有减少交通" is a single whitespace-delimited word and whole-word
    matching would never find 没有 inside it.
    """
    lowered = text.lower()
    latin = {w for w in _words(lowered) if w in words and not _CJK_ONLY.match(w)}
    cjk = {w for w in words if _CJK_ONLY.match(w) and w in lowered}
    return latin | cjk


def _has_event(text: str, words: frozenset[str]) -> bool:
    return bool(_has_any(text, words))


def date_conflict(a: str, b: str) -> bool | None:
    """Do the two statements schedule the same kind of event for different months?

    Both sides must mention a schedulable event; two arbitrary dates ("published in
    2026" vs "reviewed in 2027") are not a contradiction about anything.
    """
    if not (_has_event(a, EVENT_WORDS) and _has_event(b, EVENT_WORDS)):
        return None

    left = extract_dates(a)
    right = extract_dates(b)
    if not left or not right:
        return None

    # Compare at month precision: "September 2026" and "2026-09" must collide.
    left_months = {d.month_key for d in left}
    right_months = {d.month_key for d in right}
    if not left_months or not right_months:
        return None
    return not (left_months & right_months)


def date_conflict_detail(a: str, b: str) -> tuple[bool | None, DateRef | None, DateRef | None]:
    """As `date_conflict`, but also returns the two dates so the UI can show them."""
    if not (_has_event(a, EVENT_WORDS) and _has_event(b, EVENT_WORDS)):
        return None, None, None
    left = extract_dates(a)
    right = extract_dates(b)
    if not left or not right:
        return None, None, None
    left_months = {d.month_key for d in left}
    right_months = {d.month_key for d in right}
    if left_months & right_months:
        return False, left[0], right[0]
    return True, left[0], right[0]


def temporal_update_cue(text: str) -> bool:
    """Does the text signal that a previously stated value changed?"""
    return bool(_has_any(text, UPDATE_CUES))


def identical_normalized(a: str, b: str) -> bool:
    """Are the two statements the same once punctuation and case are ignored?

    Trailing punctuation is stripped: "450 Wh/kg." and "450 Wh/kg" are the same
    sentence, and treating the full stop as a difference would make the test
    depend on sentence position rather than content.
    """
    return _normalize_for_compare(a) == _normalize_for_compare(b)


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w一-鿿%\.\-/]+")


def _normalize_for_compare(text: str) -> str:
    stripped = _PUNCT.sub(" ", text.lower())
    return _WS.sub(" ", stripped).strip(" .,;:!?-/")


__all__ = [
    "EVENT_WORDS",
    "NEGATIONS",
    "NUMERIC_TOLERANCE",
    "UPDATE_CUES",
    "content_tokens",
    "date_conflict",
    "date_conflict_detail",
    "identical_normalized",
    "negation_asymmetry",
    "numbers_conflict",
    "temporal_update_cue",
    "token_f1",
]
