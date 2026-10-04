"""Quantity and date extraction.

Everything downstream that compares two statements -- the verifier, the relation
judge, the wiki validator -- reduces them to numbers and dates first. That is what
lets "450 Wh/kg" and "380 Wh/kg" be recognised as a conflict while "450 Wh/kg" and
"$120/kWh" are simply different measurements.

Units are normalised to a canonical lowercase form (`Wh/kg` -> `wh/kg`, `$9 per day` ->
`usd/day`), so two mentions of the same quantity land on the same unit string and
become comparable.

Numbers are found first and their unit is read from the text that follows, rather
than matching one large alternation. That keeps the accepted spellings readable and
makes it obvious which are supported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- unit normalisation ---------------------------------------------------

_UNIT_ALIASES = {
    "wh/kg": "wh/kg",
    "wh per kg": "wh/kg",
    "whkg": "wh/kg",
    "kwh": "kwh",
    "usd/kwh": "usd/kwh",
    "$/kwh": "usd/kwh",
    "usd per kwh": "usd/kwh",
    "usd/day": "usd/day",
    "$/day": "usd/day",
    "usd per day": "usd/day",
    "dollars per day": "usd/day",
    "usd": "usd",
    "$": "usd",
    "美元": "usd",
    "%": "%",
    "percent": "%",
    "tokens": "tokens",
    "token": "tokens",
    "params": "params",
    "param": "params",
    "parameters": "params",
    "parameter": "params",
    "charge cycles": "cycles",
    "cycles": "cycles",
    "km": "km",
    "mwh": "mwh",
    "千瓦时": "kwh",
    "usd/per day": "usd/day",
    "usd/per kwh": "usd/kwh",
}

_SCALES = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
    "trillion": 1e12,
    "万": 1e4,
    "亿": 1e8,
}

# What may follow a number. Longer spellings first, so "charge cycles" is preferred
# over "cycles" and "per day" over "day".
_UNIT_PHRASES = (
    # Slash denominators: "$120/kWh", "$9/day".
    "/kwh",
    "/day",
    "wh per kg",
    "wh/kg",
    "whkg",
    "charge cycles",
    "dollars per day",
    "usd per day",
    "usd per kwh",
    "per day",
    "per kwh",
    "usd/kwh",
    "dollars",
    "dollar",
    "usd",
    "美元",
    "千瓦时",
    "percent",
    "tokens",
    "token",
    "parameters",
    "parameter",
    "params",
    "param",
    "cycles",
    "mwh",
    "km",
)

# Words that turn a bare year into a meaningful date. "launched in 2026" is a date;
# "the 2026 model year" is not, so a lone year is only accepted near an event word.
EVENT_WORDS = (
    "begin",
    "begins",
    "began",
    "start",
    "starts",
    "started",
    "launch",
    "launches",
    "launched",
    "ship",
    "ships",
    "shipped",
    "release",
    "releases",
    "released",
    "production",
    "available",
    "postpone",
    "postponed",
    "delay",
    "delayed",
    "publish",
    "published",
    "announce",
    "announced",
    "量产",
    "发布",
    "上市",
    "推迟",
)


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str
    raw: str
    start: int
    end: int


@dataclass(frozen=True)
class DateRef:
    year: int
    month: int | None
    day: int | None
    raw: str
    start: int
    end: int
    quarter: bool = False

    @property
    def key(self) -> tuple[int, int]:
        """Comparison key. Day 0 means "no day known".

        `2026-09` and `September 2026` must collide, so month alone is preserved;
        `2026-09-15` stays distinguishable from both.
        """
        return (self.year, (self.month or 0) * 100 + (self.day or 0))

    @property
    def month_key(self) -> tuple[int, int]:
        return (self.year, self.month or 0)


_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))

# `1,000` / `450` / `3.5` / `$120` / `-1.2`
_NUMBER_RE = re.compile(r"(?P<currency>[$]|美元)?\s*(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)")
# No `^` anchor: `pattern.match(text, pos)` already anchors at `pos`, and `^` would
# still mean start-of-string there -- so an anchored pattern never matches mid-sentence.
_SCALE_RE = re.compile(r"[ \t]*(thousand|million|billion|trillion|万|亿)", re.IGNORECASE)
_PHRASE_RE = re.compile(r"[ \t]*(%|" + "|".join(re.escape(p) for p in _UNIT_PHRASES) + r")", re.IGNORECASE)

_QUARTER_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s+(?P<year>20\d{2})\b", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(rf"\b(?P<month>{_MONTH_ALT})\.?\s+(?P<year>20\d{{2}})\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})(?:-(?P<day>\d{1,2}))?\b")
_CJK_DATE_RE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月(?:\s*(?P<day>\d{1,2})\s*日)?")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _normalize_unit(raw: str) -> str:
    if not raw:
        return ""
    unit = re.sub(r"\s+", " ", raw.strip().lower())
    unit = unit.replace("$", "usd")
    return _UNIT_ALIASES.get(unit, unit)


def _scale_factor(word: str) -> float:
    return _SCALES.get(word.lower(), 1.0)


_PER_DENOM_RE = re.compile(r"[ \t]*per[ \t]+(?P<denom>day|kwh)", re.IGNORECASE)


def _resolve_unit(phrase: str, *, text: str, span_end: int, has_currency: bool) -> str:
    """Turn the matched phrase into a canonical unit string.

    Order matters. A currency amount and its denominator can arrive as separate
    spans -- `$9` then `per day` -- so the denominator has to be folded in
    *before* normalising, or `usd/per day` never matches its alias.
    """
    if phrase == "%":
        return "%"

    bare = re.sub(r"\s+", " ", phrase.strip().lower())

    if bare in ("usd", "$", "美元", "dollar", "dollars"):
        # A plain money amount may still be followed by its denominator.
        trailing = _PER_DENOM_RE.match(text, span_end)
        if trailing:
            return f"usd/{trailing.group('denom').lower()}"
        return "usd"

    if bare.startswith("/"):
        # "$120/kWh": the slash already carries the denominator.
        return "usd" + bare

    if bare.startswith("per "):
        # "$9 per day" -- the currency mark supplies the numerator.
        denom = _normalize_unit(bare[4:])
        return f"usd/{denom}" if has_currency else denom

    unit = _normalize_unit(bare)
    if has_currency and unit and not unit.startswith("usd"):
        # "$120 kWh": the currency mark qualifies a non-money unit.
        unit = f"usd/{unit}"
    return unit


def extract_quantities(text: str) -> list[Quantity]:
    """Every quantity in `text`, normalised to `(value, canonical unit)`.

    Numbers with no unit are skipped: "the second version", "chapter 3" and a real
    measurement are indistinguishable here, and inventing a unit for the first two
    would manufacture conflicts that do not exist.
    """
    if not text:
        return []
    out: list[Quantity] = []

    for match in _NUMBER_RE.finditer(text):
        start = match.start()
        cursor = match.end()

        # "2 trillion tokens": the scale word sits between the value and the unit.
        scale_match = _SCALE_RE.match(text, cursor)
        scale_word = ""
        if scale_match:
            scale_word = scale_match.group(1)
            cursor = scale_match.end()

        phrase_match = _PHRASE_RE.match(text, cursor)
        if phrase_match is None:
            continue

        phrase = phrase_match.group(1).lower()
        end = phrase_match.end()

        unit = _resolve_unit(
            phrase,
            text=text,
            span_end=phrase_match.end(),
            has_currency=bool(match.group("currency")),
        )
        if not unit:
            continue

        out.append(
            Quantity(
                value=float(match.group("value").replace(",", "")) * _scale_factor(scale_word),
                unit=unit,
                raw=text[start:end],
                start=start,
                end=end,
            )
        )

    out.sort(key=lambda q: q.start)
    return out


def extract_dates(text: str, *, include_bare_year: bool = False) -> list[DateRef]:
    """Explicit dates in `text`.

    A bare year counts only when the text also contains an event word. Callers that
    already know their context can pass `include_bare_year=True`.
    """
    if not text:
        return []
    out: list[DateRef] = []
    taken: list[tuple[int, int]] = []

    def overlaps(start: int, end: int) -> bool:
        return any(start < e and s < end for s, e in taken)

    for match in _QUARTER_RE.finditer(text):
        if overlaps(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        quarter = int(match.group("quarter"))
        out.append(
            DateRef(
                year=int(match.group("year")),
                month=(quarter - 1) * 3 + 1,
                day=None,
                raw=match.group(0),
                start=match.start(),
                end=match.end(),
                quarter=True,
            )
        )

    for match in _MONTH_YEAR_RE.finditer(text):
        if overlaps(match.start(), match.end()):
            continue
        taken.append((match.start(), match.end()))
        out.append(
            DateRef(
                year=int(match.group("year")),
                month=_MONTHS[match.group("month").lower()],
                day=None,
                raw=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )

    for pattern in (_ISO_RE, _CJK_DATE_RE):
        for match in pattern.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            taken.append((match.start(), match.end()))
            day = match.groupdict().get("day")
            out.append(
                DateRef(
                    year=int(match.group("year")),
                    month=int(match.group("month")),
                    day=int(day) if day else None,
                    raw=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    if include_bare_year or _has_event_word(text):
        for match in _YEAR_RE.finditer(text):
            if overlaps(match.start(), match.end()):
                continue
            out.append(
                DateRef(
                    year=int(match.group(1)),
                    month=None,
                    day=None,
                    raw=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    out.sort(key=lambda d: d.start)
    return out


def _has_event_word(text: str) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in EVENT_WORDS)


def quantities_by_unit(text: str) -> dict[str, list[Quantity]]:
    """Quantities grouped by canonical unit -- the shape conflict detection wants."""
    out: dict[str, list[Quantity]] = {}
    for quantity in extract_quantities(text):
        out.setdefault(quantity.unit, []).append(quantity)
    return out


__all__ = [
    "EVENT_WORDS",
    "DateRef",
    "Quantity",
    "extract_dates",
    "extract_quantities",
    "quantities_by_unit",
]
