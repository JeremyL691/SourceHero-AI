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

import jieba

from app.claims.numbers import DateRef, Quantity, _has_event_word, extract_dates, extract_quantities
from app.indexing.tokenize import search_tokens

_NEGATIONS_EN = frozenset(
    [
        "not",
        "no",
        "never",
        "none",
        "cannot",
        "cant",
        "cannot",
        "can't",
        "didnt",
        "didn't",
        "doesnt",
        "doesn't",
        "isnt",
        "isn't",
        "wasnt",
        "wasn't",
        "without",
        "nothing",
        "neither",
        "nor",
        "hardly",
        "barely",
        "rarely",
    ]
)

_CN_NEGATION_WHITELIST = frozenset(
    [
        "未来",
        "不同",
        "无线",
        "不仅",
        "无论",
        "不断",
        "未必",
        "无私",
        "无可",
        "无须",
        "无需",
        "不可",
        "不公",
        "不均",
        "不乏",
        "不休",
        "不丹",
    ]
)

_CN_NEGATIONS = frozenset(
    [
        "不",
        "没有",
        "没",
        "未",
        "无",
        "并非",
        "并无",
        "从未",
        "并不",
        "不能",
        "不会",
        "不是",
        "未能",
        "决不",
        "绝不",
        "毫无",
    ]
)

NEGATIONS = _NEGATIONS_EN | _CN_NEGATIONS

# Words that make a date a *scheduled event* date, so two different such dates mean
# the sources really disagree about when something happens.
_EVENT_WORDS = """
begin begins began start starts started launch launches launched ship ships
shipped release releases released production available postpone postponed
delay delayed publish published announce announced
量产 发布 上市 推迟 开始 上线
"""

EVENT_WORDS = frozenset(_EVENT_WORDS.split())

_EVENT_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in EVENT_WORDS if not any("\u4e00" <= c <= "\u9fff" for c in w))
    + r")\b|"
    + "|".join(re.escape(w) for w in EVENT_WORDS if any("\u4e00" <= c <= "\u9fff" for c in w)),
    re.IGNORECASE,
)

_UPDATE_PHRASES_EN = (
    "now expected",
    "pushed back",
    "delayed to",
    "postponed to",
    "revised",
    "updated",
    "postponed",
    "delayed",
    "corrected",
    "amended",
)

_UPDATE_WORDS_ZH = (
    "已推迟",
    "推迟到",
    "推迟至",
    "改为",
    "下调",
    "上调",
    "推迟",
    "修订",
    "更新",
    "修正",
)

UPDATE_CUES = frozenset(_UPDATE_PHRASES_EN + _UPDATE_WORDS_ZH)

_UPDATE_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(p) for p in _UPDATE_PHRASES_EN)
    + r")\b|"
    + "|".join(re.escape(p) for p in _UPDATE_WORDS_ZH),
    re.IGNORECASE,
)

# Relative difference above which two same-unit numbers count as a conflict.
NUMERIC_TOLERANCE = 0.05


def content_tokens(text: str) -> list[str]:
    """Search tokens representing the content of text."""
    return search_tokens(text)


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


def _heads_compatible(h1: str, h2: str) -> bool:
    if not h1 or not h2:
        return True
    a = h1.strip().lower()
    b = h2.strip().lower()
    return a == b or a.rstrip("s") == b.rstrip("s")


def _quantities_comparable(q1: Quantity, q2: Quantity) -> bool:
    if q1.dimension and q2.dimension and q1.dimension != q2.dimension:
        return False
    if q1.unit and q2.unit:
        return q1.unit == q2.unit
    if not q1.unit and not q2.unit:
        return _heads_compatible(q1.head, q2.head)
    return False


def _values_match(q1: Quantity, q2: Quantity, tolerance: float = NUMERIC_TOLERANCE) -> bool:
    if q1.is_interval or q2.is_interval:
        low1, high1 = q1.effective_low, q1.effective_high
        low2, high2 = q2.effective_low, q2.effective_high
        overlap_low = max(low1, low2)
        overlap_high = min(high1, high2)
        if overlap_low <= overlap_high:
            return True
        scale = max(abs(overlap_low), abs(overlap_high), 1e-9)
        return (overlap_low - overlap_high) / scale <= tolerance

    return _relative_difference(q1.value, q2.value) <= tolerance


def numbers_conflict(a: str, b: str) -> bool | None:
    """Do the two statements report different values for comparable quantities?

    True: at least one comparable quantity on either side has no match within tolerance.
    False: comparable quantities exist and all find matches within tolerance.
    None: no comparable quantities exist between the statements.
    """
    qa = extract_quantities(a)
    qb = extract_quantities(b)
    if not qa or not qb:
        return None

    comp_a = [x for x in qa if any(_quantities_comparable(x, y) for y in qb)]
    comp_b = [y for y in qb if any(_quantities_comparable(x, y) for x in qa)]

    if not comp_a and not comp_b:
        return None

    for x in comp_a:
        if not any(_quantities_comparable(x, y) and _values_match(x, y) for y in comp_b):
            return True

    return any(not any(_quantities_comparable(x, y) and _values_match(x, y) for y in comp_a) for y in comp_b)


def _quantities_dimensionally_compatible(q1: Quantity, q2: Quantity) -> bool:
    if q1.dimension and q2.dimension and q1.dimension != q2.dimension:
        generic = {"", "count", "dimensionless", "measurement"}
        if q1.dimension not in generic and q2.dimension not in generic:
            return False
    if q1.unit and q2.unit and q1.unit != q2.unit:
        return False
    return not (q1.head and q2.head and not _heads_compatible(q1.head, q2.head))


def numbers_differ(a: str, b: str) -> bool:
    """Return True if the statements assert materially different numbers for the same entity/metric.

    Used by verifier, relate, and reconcile to guard against false-positive
    matches (supported, duplicate, supports, carried) when numerical claims differ.
    """
    conf = numbers_conflict(a, b)
    if conf is True:
        return True
    if conf is False:
        return False
    qa = extract_quantities(a)
    qb = extract_quantities(b)
    if not qa or not qb:
        return False

    compat_pairs = [(x, y) for x in qa for y in qb if _quantities_dimensionally_compatible(x, y)]
    if not compat_pairs:
        return False

    return not any(_values_match(x, y) for x, y in compat_pairs)


_WORD_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+")


def _words(text: str) -> list[str]:
    return [w for w in _WORD_SPLIT.split(text.lower()) if w]


def _stem(w: str) -> str:
    w = w.lower()
    for suffix in ("tions", "tion", "ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) > len(suffix) + 2:
            return w[: -len(suffix)]
    return w.rstrip("e")


def _has_negation(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    for w in _words(lowered):
        if w in _NEGATIONS_EN:
            return True
    for token in jieba.lcut(text):
        token = token.strip()
        if not token:
            continue
        if token in _CN_NEGATION_WHITELIST:
            continue
        if token in _CN_NEGATIONS:
            return True
        if len(token) > 1 and token[0] in ("不", "未", "无", "没") and token not in _CN_NEGATION_WHITELIST:
            return True
    return False


def _share_predicate(a: str, b: str) -> bool:
    neg_all = _NEGATIONS_EN | _CN_NEGATIONS | _CN_NEGATION_WHITELIST
    ta = {_stem(t) for t in search_tokens(a) if t not in neg_all}
    tb = {_stem(t) for t in search_tokens(b) if t not in neg_all}
    return bool(ta & tb)


def negation_asymmetry(a: str, b: str) -> bool:
    """Negation appears on exactly one side, and the statements share a predicate."""
    neg_a = _has_negation(a)
    neg_b = _has_negation(b)
    if neg_a == neg_b:
        return False
    return _share_predicate(a, b)


def _negations_in(text: str) -> set[str]:
    """Negation words present, counted over raw words."""
    return {w for w in _words(text.lower()) if w in _NEGATIONS_EN}


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


def _has_event(text: str, words: frozenset[str] | None = None) -> bool:
    return _has_event_word(text)


def _month_range(d: DateRef) -> tuple[int, int]:
    if d.month is None:
        return (1, 12)
    if d.quarter:
        q = (d.month - 1) // 3 + 1
        return ((q - 1) * 3 + 1, q * 3)
    return (d.month, d.month)


def _date_contains(broader: DateRef, narrower: DateRef) -> bool:
    if broader.year != narrower.year:
        return False
    bm_start, bm_end = _month_range(broader)
    nm_start, nm_end = _month_range(narrower)
    if not (bm_start <= nm_start and nm_end <= bm_end):
        return False
    if broader.day is not None and narrower.day is not None:
        return broader.day == narrower.day
    return not (broader.day is not None and narrower.day is None)


def _dates_compatible(d1: DateRef, d2: DateRef) -> bool:
    return _date_contains(d1, d2) or _date_contains(d2, d1)


def _dates_conflict(d1: DateRef, d2: DateRef) -> bool:
    return not _dates_compatible(d1, d2)


def _dates_identical(d1: DateRef, d2: DateRef) -> bool:
    return (d1.year, d1.month, d1.day, d1.quarter) == (d2.year, d2.month, d2.day, d2.quarter)


def date_conflict(a: str, b: str) -> bool | None:
    """Do the two statements schedule the same kind of event for conflicting dates?

    Both sides must mention a schedulable event; two arbitrary dates ("published in
    2026" vs "reviewed in 2027") are not a contradiction about anything.
    """
    conflict, _, _ = date_conflict_detail(a, b)
    return conflict


def date_conflict_detail(a: str, b: str) -> tuple[bool | None, DateRef | None, DateRef | None]:
    """As `date_conflict`, but also returns the two dates so the UI can show them."""
    if not (_has_event_word(a) and _has_event_word(b)):
        return None, None, None

    left = extract_dates(a)
    right = extract_dates(b)
    if not left or not right:
        return None, None, None

    # Filter out identical dates appearing on both sides (e.g. shared announcement/publication dates)
    left_rem = list(left)
    right_rem = list(right)
    for dl in list(left_rem):
        match = next((dr for dr in right_rem if _dates_identical(dl, dr)), None)
        if match is not None:
            left_rem.remove(dl)
            right_rem.remove(match)

    if not left_rem or not right_rem:
        return False, left[0], right[0]

    for dl in left_rem:
        for dr in right_rem:
            if _dates_conflict(dl, dr):
                return True, dl, dr

    return False, left_rem[0], right_rem[0]


def temporal_update_cue(text: str) -> bool:
    """Does the text signal that a previously stated value changed?"""
    return bool(_UPDATE_PATTERN.search(text))


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
    "numbers_differ",
    "temporal_update_cue",
    "token_f1",
]
