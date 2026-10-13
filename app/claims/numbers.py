"""Quantity and date extraction.

Everything downstream that compares two statements -- the verifier, the relation
judge, the wiki validator -- reduces them to numbers and dates first. That is what
lets "450 Wh/kg" and "380 Wh/kg" be recognised as a conflict while "450 Wh/kg" and
"$120/kWh" are simply different measurements.

Units are normalised to a canonical lowercase form (`Wh/kg` -> `wh/kg`, `$9 per day` ->
`usd/day`), so two mentions of the same quantity land on the same unit string and
become comparable.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

# --- unit normalisation ---------------------------------------------------

_UNIT_ALIASES = {
    "wh/kg": "wh/kg",
    "wh per kg": "wh/kg",
    "whkg": "wh/kg",
    "kwh/kg": "kwh/kg",
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
    "dollar": "usd",
    "dollars": "usd",
    "美元": "usd",
    "eur": "eur",
    "euro": "eur",
    "euros": "eur",
    "€": "eur",
    "欧元": "eur",
    "gbp": "gbp",
    "pound": "gbp",
    "pounds": "gbp",
    "£": "gbp",
    "英镑": "gbp",
    "cny": "cny",
    "rmb": "cny",
    "元": "cny",
    "人民币": "cny",
    "¥": "cny",
    "jpy": "jpy",
    "日元": "jpy",
    "%": "%",
    "percent": "%",
    "percentage": "%",
    "percentage point": "pp",
    "percentage points": "pp",
    "percentage_points": "pp",
    "pp": "pp",
    "百分点": "pp",
    "tokens": "tokens",
    "token": "tokens",
    "params": "params",
    "param": "params",
    "parameters": "params",
    "parameter": "params",
    "charge cycles": "cycles",
    "cycles": "cycles",
    "km": "km",
    "公里": "km",
    "mwh": "mwh",
    "gwh": "gwh",
    "千瓦时": "kwh",
    "kilometres": "km",
    "kilometre": "km",
    "kilometers": "km",
    "kilometer": "km",
    "千米": "km",
    "kilograms": "kg",
    "kilogram": "kg",
    "kilogrammes": "kg",
    "kilogramme": "kg",
    "千克": "kg",
    "平方米": "m2",
    "square metres": "m2",
    "square metre": "m2",
    "square meters": "m2",
    "square meter": "m2",
    "hour": "hr",
    "hours": "hr",
    "gigabytes": "gb",
    "gigabyte": "gb",
    "megabytes": "mb",
    "megabyte": "mb",
    "kilobytes": "kb",
    "kilobyte": "kb",
    "terabytes": "tb",
    "terabyte": "tb",
    "usd/per day": "usd/day",
    "usd/per kwh": "usd/kwh",
    "°c": "c",
    "celsius": "c",
    "°f": "f",
    "fahrenheit": "f",
}

# Units whose canonical form is a larger unit: value is divided by the divisor.
_UNIT_DIVISORS = {
    "metres": ("km", 1000.0),
    "metre": ("km", 1000.0),
    "meters": ("km", 1000.0),
    "meter": ("km", 1000.0),
    "米": ("km", 1000.0),
    "克": ("kg", 1000.0),
}

_SCALES = {
    "k": 1e3,
    "thousand": 1e3,
    "thousands": 1e3,
    "千": 1e3,
    "m": 1e6,
    "million": 1e6,
    "millions": 1e6,
    "百万": 1e6,
    "b": 1e9,
    "bn": 1e9,
    "billion": 1e9,
    "billions": 1e9,
    "十亿": 1e9,
    "t": 1e12,
    "trillion": 1e12,
    "trillions": 1e12,
    "万亿": 1e12,
    "百": 1e2,
    "万": 1e4,
    "亿": 1e8,
}

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
    "deferred",
    "rescheduled",
    "publish",
    "published",
    "announce",
    "announced",
    "scheduled",
    "opening",
    "opens",
    "ends",
    "retires",
    "takes effect",
    "量产",
    "发布",
    "上市",
    "推迟",
    "通车",
    "建成",
    "启动",
    "投运",
    "开业",
    "开工",
    "生效",
)


@dataclass(frozen=True)
class Quantity:
    value: float
    unit: str
    raw: str
    start: int
    end: int
    dimension: str = ""
    head: str = ""
    low: float | None = None
    high: float | None = None

    @property
    def span(self) -> tuple[int, int]:
        return (self.start, self.end)

    @property
    def effective_low(self) -> float:
        return self.low if self.low is not None else self.value

    @property
    def effective_high(self) -> float:
        return self.high if self.high is not None else self.value

    @property
    def is_interval(self) -> bool:
        return self.low is not None and self.high is not None


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
        """Comparison key. Day 0 means 'no day known'."""
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

_QUARTER_RE = re.compile(r"\bQ(?P<quarter>[1-4])\s+(?P<year>20\d{2})\b", re.IGNORECASE)
_MONTH_YEAR_RE = re.compile(rf"\b(?P<month>{_MONTH_ALT})\.?\s+(?P<year>20\d{{2}})\b", re.IGNORECASE)
_ISO_RE = re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{1,2})(?:-(?P<day>\d{1,2}))?\b")
_CJK_DATE_RE = re.compile(r"(?P<year>20\d{2})\s*年\s*(?P<month>\d{1,2})\s*月(?:\s*(?P<day>\d{1,2})\s*日)?")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
# ponytail: capitalised English month names only, and "May" is skipped (modal verb
# collision). Lowercase "in july" is not matched; add it when a real corpus needs it.
_MONTH_ONLY_EN_RE = re.compile(
    r"\b(?P<month>"
    + "|".join(m.capitalize() for m in sorted(_MONTHS, key=len, reverse=True) if m != "may")
    + r")\b"
)
_MONTH_ONLY_ZH_RE = re.compile(r"(?<![\d.])(?P<month>1[0-2]|0?[1-9])\s*月")

_UNIT_BLACKLIST = frozenset(
    [
        "higher",
        "lower",
        "more",
        "less",
        "most",
        "least",
        "such",
        "other",
        "another",
        "each",
        "every",
        "both",
        "all",
        "times",
        "timespans",
        "and",
        "or",
        "but",
        "the",
        "a",
        "an",
        "of",
        "to",
        "in",
        "on",
        "at",
        "by",
        "for",
        "with",
        "from",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "than",
        "then",
        "when",
        "while",
        "about",
        "over",
        "under",
        "between",
        "among",
        "through",
        "during",
        "approximately",
        "roughly",
        "nearly",
        "almost",
        "explains",
        "explained",
        "shows",
        "showed",
        "says",
        "said",
        "reports",
        "reported",
        "version",
        "versions",
        "model",
        "models",
        "chapter",
        "chapters",
        "section",
        "sections",
        "page",
        "pages",
        "part",
        "parts",
        "tier",
        "tiers",
        "phase",
        "phases",
        "step",
        "steps",
        "level",
        "levels",
    ]
)

_MODEL_PREFIXES = frozenset(
    [
        "chapter",
        "section",
        "tier",
        "phase",
        "step",
        "rule",
        "article",
        "item",
        "table",
        "figure",
        "fig",
        "model",
        "version",
        "ver",
        "v",
        "iphone",
        "windows",
        "pixel",
        "llama",
        "gpt",
        "claude",
        "galaxy",
        "playstation",
        "xbox",
        "boeing",
        "airbus",
        "rtx",
        "gtx",
        "wifi",
        "wi-fi",
    ]
)

_SPELLED_UNITS = frozenset(
    [
        "metre",
        "metres",
        "meter",
        "meters",
        "kilometre",
        "kilometres",
        "kilometer",
        "kilometers",
        "kilogram",
        "kilograms",
        "kilogramme",
        "kilogrammes",
        "tonne",
        "tonnes",
        "ton",
        "tons",
        "second",
        "seconds",
        "minute",
        "minutes",
        "hour",
        "hours",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
        "metre2",
        "hectares",
        "hectare",
        "litre",
        "litres",
        "liter",
        "liters",
        "degrees",
        "degree",
        "celsius",
        "fahrenheit",
        "kelvin",
        "gigabytes",
        "gigabyte",
        "megabytes",
        "megabyte",
        "kilobytes",
        "kilobyte",
        "terabytes",
        "terabyte",
    ]
)

_LOWERCASE_SYMBOLS = frozenset(
    [
        "kg",
        "mg",
        "ug",
        "ng",
        "lb",
        "lbs",
        "oz",
        "mm",
        "cm",
        "km",
        "um",
        "nm",
        "mi",
        "ft",
        "yd",
        "hz",
        "mhz",
        "ghz",
        "kw",
        "mw",
        "gw",
        "tw",
        "w",
        "kv",
        "wh",
        "kwh",
        "mwh",
        "gwh",
        "btu",
        "pa",
        "mol",
        "atm",
        "psi",
        "ms",
        "us",
        "ns",
        "min",
        "hr",
        "kb",
        "mb",
        "gb",
        "tb",
        "pb",
        "eur",
        "gbp",
        "jpy",
        "cny",
        "rmb",
        "chf",
        "cad",
        "aud",
        "inr",
        "brl",
    ]
)

_GENERIC_UNIT = re.compile(r"^[A-Za-z0-9]{1,8}(?:\s*/\s*[A-Za-z0-9]{1,8})?$")

_UNIT_PHRASES = (
    "percentage points",
    "percentage point",
    "charge cycles",
    "dollars per day",
    "dollars per kwh",
    "usd per day",
    "usd per kwh",
    "wh per kg",
    "wh/kg",
    "whkg",
    "kwh/kg",
    "kwh",
    "mwh",
    "gwh",
    "per day",
    "per kwh",
    "usd/kwh",
    "usd/day",
    "dollars",
    "dollar",
    "usd",
    "eur",
    "gbp",
    "cny",
    "rmb",
    "美元",
    "欧元",
    "英镑",
    "人民币",
    "千瓦时",
    "百分点",
    "percent",
    "percentage",
    "tokens",
    "token",
    "parameters",
    "parameter",
    "params",
    "param",
    "cycles",
    "km",
    "公里",
    "pp",
    "元",
    "°c",
    "°f",
    "gigabytes",
    "gigabyte",
    "megabytes",
    "megabyte",
    "kilobytes",
    "kilobyte",
    "terabytes",
    "terabyte",
    "celsius",
    "fahrenheit",
    "kilometres",
    "kilometre",
    "kilometers",
    "kilometer",
    "千米",
    "kilograms",
    "kilogram",
    "kilogrammes",
    "kilogramme",
    "千克",
    "metres",
    "metre",
    "meters",
    "meter",
    "米",
    "克",
    "square metres",
    "square metre",
    "square meters",
    "square meter",
    "平方米",
)

_CURRENCY_SYMBOLS = {"$": "usd", "€": "eur", "£": "gbp", "¥": "cny"}
_CURRENCY_WORDS = {
    "usd": "usd",
    "dollar": "usd",
    "dollars": "usd",
    "美元": "usd",
    "eur": "eur",
    "euro": "eur",
    "euros": "eur",
    "欧元": "eur",
    "gbp": "gbp",
    "pound": "gbp",
    "pounds": "gbp",
    "英镑": "gbp",
    "cny": "cny",
    "rmb": "cny",
    "元": "cny",
    "人民币": "cny",
    "jpy": "jpy",
    "日元": "jpy",
}

_CN_NUMS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10000,
    "亿": 100000000,
}


def _infer_dimension(unit: str, head: str = "") -> str:
    if not unit and head:
        return "count"
    u = unit.lower()
    if u in ("usd", "eur", "gbp", "cny", "jpy") or u.startswith(("usd/", "eur/", "gbp/", "cny/", "jpy/")):
        return "currency"
    if u in ("%", "percent"):
        return "percentage"
    if u in ("pp", "percentage_points", "percentage point"):
        return "percentage_points"
    if u in ("wh/kg", "kwh/kg", "mwh/t"):
        return "energy_density"
    if u in ("kwh", "mwh", "gwh", "wh", "j", "kj", "mj", "gj"):
        return "energy"
    if u in ("kw", "mw", "gw", "tw", "w", "hp") or u.startswith("w/"):
        return "power"
    if u.startswith("params") or u in ("param", "parameters"):
        return "params"
    if u.startswith("tokens") or u in ("token",):
        return "tokens"
    if u in ("cycles", "charge cycles"):
        return "cycles"
    if u in ("c", "f", "k"):
        return "temperature"
    if u in ("km", "m", "cm", "mm", "mi", "ft", "yd", "mile", "miles") or u.startswith(("km/", "m/", "mi/")):
        return "distance"
    if u in ("kg", "g", "mg", "tonne", "ton", "tonnes", "tons", "lb", "lbs", "oz") or u.startswith("kg/"):
        return "mass"
    if u in (
        "second",
        "seconds",
        "sec",
        "minute",
        "minutes",
        "min",
        "hour",
        "hours",
        "hr",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
        "year",
        "years",
    ):
        return "duration"
    return "measurement"


def _normalize_unit(raw: str) -> str:
    if not raw:
        return ""
    unit = re.sub(r"\s+", " ", raw.strip().lower())
    unit = unit.replace("$", "usd").replace("€", "eur").replace("£", "gbp").replace("¥", "cny")
    return _UNIT_ALIASES.get(unit, unit)


def _canonical_unit(raw: str) -> tuple[str, float]:
    """Canonical unit plus the divisor to apply to the value (1.0 when none)."""
    key = re.sub(r"\s+", " ", raw.strip().lower())
    if key in _UNIT_DIVISORS:
        return _UNIT_DIVISORS[key]
    return _normalize_unit(key), 1.0


def _is_unit_word(word: str) -> bool:
    return (
        word in _UNIT_ALIASES
        or word in _UNIT_DIVISORS
        or word in _SPELLED_UNITS
        or word in _LOWERCASE_SYMBOLS
    )


def _is_count_noun(word: str) -> bool:
    return (
        word in _COMMON_COUNT_NOUNS or (word.endswith("s") and len(word) >= 3)
    ) and word not in _UNIT_BLACKLIST


def _scale_factor(word: str) -> float:
    return _SCALES.get(word.lower(), 1.0)


def _generic_unit_at(text: str, cursor: int):
    match = re.match(r"[ \t]*([A-Za-z0-9]{1,8}(?:[ \t]*/[ \t]*[A-Za-z0-9]{1,8})?)", text[cursor:])
    if not match:
        return None
    candidate = match.group(1)
    if not _GENERIC_UNIT.match(candidate):
        return None
    head = re.split(r"[ \t]*/", candidate)[0]
    lowered = head.lower()
    symbolic = any(ch.isupper() for ch in head)
    has_digit = any(ch.isdigit() for ch in candidate)
    known_lowercase = lowered in _SPELLED_UNITS or lowered in _LOWERCASE_SYMBOLS
    if not symbolic and not known_lowercase and not has_digit:
        return None
    if lowered in _UNIT_BLACKLIST:
        return None
    return re.match(rf"[ \t]*({re.escape(candidate)})", text[cursor:])


def _parse_cn_number(text: str) -> float | None:
    """Parse pure Chinese numerals like 三亿, 一千二百, 百分之十八, 三点五."""
    if not text:
        return None
    s = text.strip()
    if s.startswith("百分之"):
        sub = s[3:]
        val = _parse_cn_number(sub)
        return val if val is not None else None

    if "点" in s:
        parts = s.split("点", 1)
        int_val = _parse_cn_number(parts[0]) if parts[0] else 0.0
        if int_val is None:
            return None
        dec_str = ""
        for ch in parts[1]:
            if ch in _CN_NUMS:
                dec_str += str(_CN_NUMS[ch])
            elif ch.isdigit():
                dec_str += ch
            else:
                break
        if not dec_str:
            return None
        return int_val + float(f"0.{dec_str}")

    total = 0.0
    section = 0.0
    number = 0.0
    has_cn = False

    for ch in s:
        if ch in _CN_NUMS:
            number = float(_CN_NUMS[ch])
            has_cn = True
        elif ch in _CN_UNITS:
            unit_val = float(_CN_UNITS[ch])
            has_cn = True
            if unit_val in (10000, 100000000):
                section = (section + number) * unit_val
                total += section
                section = 0.0
                number = 0.0
            else:
                if number == 0.0 and ch == "十":
                    number = 1.0
                section += number * unit_val
                number = 0.0
        elif ch.isdigit():
            number = number * 10 + float(ch)
            has_cn = True
        else:
            return None

    if not has_cn:
        return None
    return total + section + number


_COMMON_COUNT_NOUNS = frozenset(
    [
        "participants",
        "participant",
        "cores",
        "core",
        "patients",
        "patient",
        "users",
        "user",
        "people",
        "person",
        "employees",
        "employee",
        "workers",
        "worker",
        "vehicles",
        "vehicle",
        "cars",
        "car",
        "servers",
        "server",
        "nodes",
        "node",
        "clusters",
        "cluster",
        "cells",
        "cell",
        "samples",
        "sample",
        "cases",
        "case",
        "items",
        "item",
        "units",
        "devices",
        "device",
        "chips",
        "chip",
        "genes",
        "gene",
        "proteins",
        "protein",
        "subjects",
        "subject",
        "students",
        "student",
        "respondents",
        "respondent",
        "sites",
        "site",
        "locations",
        "location",
        "companies",
        "company",
        "firms",
        "firm",
        "teams",
        "team",
        "members",
        "member",
        "clients",
        "client",
        "customers",
        "customer",
        "transactions",
        "transaction",
        "requests",
        "request",
        "queries",
        "query",
        "records",
        "record",
        "stars",
        "documents",
        "document",
    ]
)

_COMMON_CN_CLASSIFIERS = (
    "名",
    "位",
    "个",
    "台",
    "辆",
    "架",
    "艘",
    "所",
    "家",
    "只",
    "头",
    "条",
    "座",
    "本",
    "张",
)


def _is_preceded_by_model_or_index(text: str, start: int) -> bool:
    """Check if the number is preceded by a model name or index prefix like Chapter 3, iPhone 15."""
    before = text[:start].rstrip()
    if not before:
        return False
    last_word_match = re.search(r"([A-Za-z0-9_-]+)[ \t]*$", before)
    if not last_word_match:
        return False
    last_word = last_word_match.group(1).lower()
    return last_word in _MODEL_PREFIXES


def _is_year_in_temporal_phrase(text: str, start: int, end: int, val: float) -> bool:
    """Check if 1900-2099 number is used as a calendar year rather than a count."""
    if not (1900 <= val <= 2099 and val == int(val)):
        return False
    before = text[:start].rstrip().lower()
    after = text[end:].lstrip()

    has_temp_prep = bool(
        re.search(
            r"\b(?:in|since|by|until|from|before|after|year|dated|during|fy|fiscal\s+year)\b[ \t]*$",
            before,
        )
    )

    next_token_match = re.match(r"^[A-Za-z]+", after)
    if next_token_match:
        next_word = next_token_match.group(0).lower()
        if (
            next_word in _COMMON_COUNT_NOUNS
            or next_word.endswith("s")
            or next_word in _UNIT_ALIASES
            or next_word in _SPELLED_UNITS
            or next_word in _LOWERCASE_SYMBOLS
        ):
            return False

    return has_temp_prep


def extract_quantities(text: str) -> list[Quantity]:
    """Every quantity in `text`, normalised to `(value, canonical unit)`.

    Supports:
    - Scales: K, M, B, bn, thousand, million, billion, 万, 亿
    - Currencies: $, €, £, ¥, 元, 美元, 欧元, bare amounts and with denominators
    - Percent vs percentage points
    - Generic denominators: /kwh, per day, 元/公里, etc.
    - Intervals: 10–20%, from 380 to 450 Wh/kg, between $100 and $200
    - Negative numbers and decrease signals: -2%, −5.0, 下降 15%
    - Chinese numbers: 三亿, 一千二百, 百分之十八
    - Counts with head nouns: 900 participants, 2048 cores, 1000台服务器
    - Exclusions: Years (In 2024), model names (GPT-4, HX-2, iPhone 15), chapter numbers.
    """
    if not text:
        return []

    out: list[Quantity] = []
    taken_spans: list[tuple[int, int]] = []

    def is_taken(s: int, e: int) -> bool:
        return any(s < te and ts < e for ts, te in taken_spans)

    # 1. Chinese percentage: 百分之十八, 百分之零点五, 百分之18
    for match in re.finditer(
        r"(?P<dec>下降|下跌|下滑|降低|减少\s*(?:了\s*)?)?百分之(?P<num>[零一二三四五六七八九十百千万点]+|\d+(?:\.\d+)?)",
        text,
    ):
        if is_taken(match.start(), match.end()):
            continue
        num_str = match.group("num")
        val = _parse_cn_number(num_str)
        if val is None:
            try:
                val = float(num_str)
            except ValueError:
                continue
        if match.group("dec"):
            val = -abs(val)
        span = (match.start(), match.end())
        taken_spans.append(span)
        out.append(
            Quantity(
                value=val,
                unit="%",
                raw=match.group(0),
                start=match.start(),
                end=match.end(),
                dimension="percentage",
                head="",
            )
        )

    # 2. Intervals / Ranges: "from 380 to 450 Wh/kg", "10–20%", "380 到 450 Wh/kg", "between $100 and $200"
    interval_pattern = re.compile(
        r"(?i)(?<![A-Za-z0-9_#-])"
        r"(?:(?P<prefix>from\s+|between\s+))?"
        r"(?P<curr1>[$€£¥])?[ \t]*"
        r"(?P<v1>[-−–]?\d+(?:\.\d+)?)\s*(?P<scale1>[kmbt]|bn|万|亿)?[ \t]*(?P<u1>%|percent)?"
        r"[ \t]*(?P<sep>to|and|–|—|-|到|至)[ \t]*"
        r"(?P<curr2>[$€£¥])?[ \t]*"
        r"(?P<v2>[-−–]?\d+(?:\.\d+)?)\s*(?P<scale2>[kmbt]|bn|万|亿)?"
        r"(?P<tail>[ \t]*[A-Za-z%°\u4e00-\u9fa5/]+(?:\s*/\s*[A-Za-z\u4e00-\u9fa5]+)?)?"
    )

    for match in interval_pattern.finditer(text):
        s_idx, e_idx = match.start(), match.end()
        if is_taken(s_idx, e_idx):
            continue

        sep = match.group("sep").strip()
        v1_raw = match.group("v1").replace("−", "-").replace("–", "-")
        v2_raw = match.group("v2").replace("−", "-").replace("–", "-")
        try:
            val1 = float(v1_raw)
            val2 = float(v2_raw)
        except ValueError:
            continue

        if sep == "-" and (1900 <= val1 <= 2099) and (0 <= val2 <= 99):
            continue

        scale1 = _scale_factor(match.group("scale1") or "")
        scale2 = _scale_factor(match.group("scale2") or "")
        val1 *= scale1
        val2 *= scale2

        curr = match.group("curr2") or match.group("curr1")
        u1 = match.group("u1")
        tail = (match.group("tail") or "").strip()

        unit = ""
        dimension = ""
        head = ""

        if curr:
            unit = _CURRENCY_SYMBOLS.get(curr, "usd")
            dimension = "currency"
        elif u1:
            unit = "%"
            dimension = "percentage"

        if tail:
            tail_lower = tail.lower()
            if tail_lower in ("%", "percent"):
                unit = "%"
                dimension = "percentage"
            elif tail_lower in ("pp", "percentage points", "percentage point", "百分点"):
                unit = "pp"
                dimension = "percentage_points"
            elif tail_lower in _UNIT_ALIASES:
                unit = _UNIT_ALIASES[tail_lower]
                dimension = _infer_dimension(unit)
            elif tail_lower in _COMMON_COUNT_NOUNS or (
                tail_lower.endswith("s") and len(tail_lower) >= 3 and tail_lower not in _UNIT_BLACKLIST
            ):
                unit = ""
                dimension = "count"
                head = tail_lower
            elif _GENERIC_UNIT.match(tail):
                unit = _normalize_unit(tail)
                dimension = _infer_dimension(unit)
            elif any(c in tail for c in _COMMON_CN_CLASSIFIERS) or any(
                w in tail for w in ("参与者", "患者", "用户", "核心", "服务器", "车", "人")
            ):
                unit = ""
                dimension = "count"
                head = re.sub(r"^[名个台辆位所张条本架艘]\s*", "", tail)

        if not unit and not head and not dimension:
            continue

        low_val = min(val1, val2)
        high_val = max(val1, val2)
        mid_val = (low_val + high_val) / 2.0

        taken_spans.append((s_idx, e_idx))
        out.append(
            Quantity(
                value=mid_val,
                unit=unit,
                raw=text[s_idx:e_idx].strip(),
                start=s_idx,
                end=e_idx,
                dimension=dimension,
                head=head,
                low=low_val,
                high=high_val,
            )
        )

    # 3. Chinese spelled-out quantities: e.g. 三亿美元, 一千二百名参与者, 两万五千
    cn_pattern = re.compile(
        r"(?<![A-Za-z0-9_#-])"
        r"(?:(?P<dec>下降|下跌|下滑|降低|减少)\s*(?:了\s*)?)?"
        r"(?P<num>[零一二三四五六七八九十百千万亿两]+(?:点[零一二三四五六七八九]+)?)"
        r"\s*(?P<tail>[名个台辆位所张条本架艘]?\s*(?:核心|服务器|用户|患者|参与者|员工|车|人民币|美元|欧元|英镑|元|人|公里|km|%|百分点))?"
    )
    for match in cn_pattern.finditer(text):
        s_idx, e_idx = match.start(), match.end()
        if is_taken(s_idx, e_idx):
            continue
        if re.search(r"\d[ \t]*$", text[:s_idx]):
            continue  # "2.3 亿元": the digits before the multiplier are the number
        num_str = match.group("num")
        val = _parse_cn_number(num_str)
        if val is None:
            continue
        if match.group("dec"):
            val = -abs(val)

        tail = (match.group("tail") or "").strip()
        unit = ""
        dimension = ""
        head = ""

        # Normalize tail
        clean_tail = re.sub(r"^[名个台辆位所张条本架艘]\s*", "", tail)
        if clean_tail in _CURRENCY_WORDS:
            unit = _CURRENCY_WORDS[clean_tail]
            dimension = "currency"
        elif clean_tail in ("%", "percent"):
            unit = "%"
            dimension = "percentage"
        elif clean_tail in ("pp", "百分点"):
            unit = "pp"
            dimension = "percentage_points"
        elif clean_tail in _UNIT_ALIASES:
            unit = _UNIT_ALIASES[clean_tail]
            dimension = _infer_dimension(unit)
        elif (
            clean_tail in ("参与者", "患者", "用户", "核心", "服务器", "车", "人", "员工")
            or clean_tail in _COMMON_COUNT_NOUNS
        ):
            unit = ""
            dimension = "count"
            head = clean_tail

        if not unit and not head:
            if val >= 10000:
                unit = ""
                dimension = "dimensionless"
            else:
                continue

        taken_spans.append((s_idx, e_idx))
        out.append(
            Quantity(
                value=val,
                unit=unit,
                raw=text[s_idx:e_idx].strip(),
                start=s_idx,
                end=e_idx,
                dimension=dimension,
                head=head,
            )
        )

    # 4. Standard numerical quantities with signs, scales, units, or heads
    num_pattern = re.compile(
        r"(?<![A-Za-z0-9_#-])"
        r"(?:(?P<dec>下降|下跌|下滑|降低|减少)\s*(?:了\s*)?)?"
        r"(?:(?P<currency>[$€£¥]|美元|欧元|英镑|人民币|元)[ \t]*)?"
        r"(?P<sign>[-−–])?"
        r"(?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
        r"(?P<scale>[kmbtKMBT]|bn|BN|万|亿)?"
        r"(?![A-Za-z0-9_#-])"
    )

    for match in num_pattern.finditer(text):
        s_idx = match.start()
        if is_taken(s_idx, match.end()):
            continue

        raw_val = match.group("value").replace(",", "")
        try:
            val = float(raw_val)
        except ValueError:
            continue

        if _is_preceded_by_model_or_index(text, s_idx):
            continue

        if match.group("sign") or match.group("dec"):
            val = -abs(val)

        scale_str = match.group("scale") or ""
        cursor = match.end()

        sep_scale_match = re.match(
            r"[ \t]*(thousand|million|billion|trillion|万|亿)", text[cursor:], re.IGNORECASE
        )
        if sep_scale_match:
            scale_str = sep_scale_match.group(1)
            cursor += sep_scale_match.end()

        if scale_str:
            # Shift the decimal point rather than multiply: 2.3 * 1e8 is not exactly 2.3e8 in binary floats.
            val = float(Decimal(repr(val)).scaleb(round(math.log10(_scale_factor(scale_str)))))

        currency_mark = match.group("currency")

        rest = text[cursor:]
        unit = ""
        dimension = ""
        head = ""
        end_idx = cursor
        divisor = 1.0

        # Check percentage points FIRST (to prevent prefix matching to percent)
        pp_match = re.match(r"[ \t]*(percentage\s+points?|pp\b|个?百分点)", rest, re.IGNORECASE)
        if pp_match:
            unit = "pp"
            dimension = "percentage_points"
            end_idx = cursor + pp_match.end()
        else:
            # Check percentage
            pct_match = re.match(r"[ \t]*(%|percent\b)", rest, re.IGNORECASE)
            if pct_match:
                unit = "%"
                dimension = "percentage"
                end_idx = cursor + pct_match.end()

        # If not matched preceding currency, check following currency symbol or word
        if not unit and not currency_mark:
            post_curr_match = re.match(
                r"[ \t]*(?P<c>[$€£¥]|(?:美元|欧元|英镑|人民币|元)(?=[^A-Za-z0-9]|$))",
                rest,
            )
            if post_curr_match:
                currency_mark = post_curr_match.group("c")
                cursor += post_curr_match.end()
                end_idx = cursor
                rest = text[cursor:]

        # Check multi-word or known unit phrase
        if not unit:
            phrase_pattern = re.compile(
                r"[ \t]*(" + "|".join(re.escape(p) for p in _UNIT_PHRASES) + r")(?:(?=[^A-Za-z0-9])|$)",
                re.IGNORECASE,
            )
            phrase_match = phrase_pattern.match(rest)
            if phrase_match:
                phrase = phrase_match.group(1).lower()
                end_idx = cursor + phrase_match.end()
                curr_code = (
                    _CURRENCY_SYMBOLS.get(currency_mark) or _CURRENCY_WORDS.get(currency_mark, "usd")
                    if currency_mark
                    else ""
                )
                d_match = re.match(
                    r"[ \t]*(?:/|\bper\s+|每\s*)(?P<denom>[A-Za-z0-9]{1,10}|[\u4e00-\u9fa5]{1,4})(?=[^A-Za-z0-9]|$)",
                    text[end_idx:],
                    re.IGNORECASE,
                )
                if phrase.startswith("per "):
                    denom = _normalize_unit(phrase[4:])
                    unit = f"{curr_code}/{denom}" if currency_mark else denom
                elif phrase.startswith("/"):
                    denom = _normalize_unit(phrase[1:])
                    unit = f"{curr_code}/{denom}" if currency_mark else denom
                elif d_match:
                    denom = _normalize_unit(d_match.group("denom"))
                    end_idx += d_match.end()
                    if phrase in ("usd", "$", "dollar", "dollars", "美元"):
                        unit = f"usd/{denom}"
                    elif phrase in ("eur", "euro", "euros", "€", "欧元"):
                        unit = f"eur/{denom}"
                    elif phrase in ("cny", "元", "人民币", "rmb", "¥"):
                        unit = f"cny/{denom}"
                    else:
                        unit = f"{_normalize_unit(phrase)}/{denom}"
                elif currency_mark and not phrase.startswith(("usd", "eur", "gbp", "cny")):
                    unit = f"{curr_code}/{phrase}"
                else:
                    unit, divisor = _canonical_unit(phrase)
                dimension = _infer_dimension(unit)
            elif currency_mark:
                curr_code = _CURRENCY_SYMBOLS.get(currency_mark) or _CURRENCY_WORDS.get(currency_mark, "usd")
                d_match = re.match(
                    r"[ \t]*(?:/|\bper\s+|每\s*)(?P<denom>[A-Za-z0-9]{1,10}|[\u4e00-\u9fa5]{1,4})(?=[^A-Za-z0-9]|$)",
                    rest,
                    re.IGNORECASE,
                )
                if d_match:
                    denom = _normalize_unit(d_match.group("denom"))
                    unit = f"{curr_code}/{denom}"
                    end_idx = cursor + d_match.end()
                else:
                    unit = curr_code
                    end_idx = cursor
                dimension = "currency"
            else:
                generic_match = _generic_unit_at(text, cursor)
                if generic_match:
                    unit, divisor = _canonical_unit(generic_match.group(1))
                    end_idx = cursor + generic_match.end()
                    dimension = _infer_dimension(unit)
                else:
                    # Chinese count
                    cn_count_match = re.match(
                        r"[ \t]*([名个台辆位所张条本架艘]?\s*[\u4e00-\u9fa5]{1,6})", rest
                    )
                    if cn_count_match:
                        cand = cn_count_match.group(1).strip()
                        if any(
                            w in cand
                            for w in ("参与者", "患者", "用户", "核心", "服务器", "车", "人", "员工")
                        ):
                            head = re.sub(r"^[名个台辆位所张条本架艘]\s*", "", cand)
                            unit = ""
                            dimension = "count"
                            end_idx = cursor + cn_count_match.end()

                    if not head:
                        en_word_match = re.match(r"[ \t]*([A-Za-z]{2,20})\b", rest)
                        if en_word_match:
                            word = en_word_match.group(1).lower()
                            word_end = cursor + en_word_match.end()
                            # "6 new classrooms": allow one adjective before the counted noun
                            is_year = 1900 <= val <= 2099 and val == int(val)
                            if not _is_count_noun(word) and not is_year and not _is_unit_word(word):
                                adj_noun = re.match(r"[ \t]+([A-Za-z]{2,20})\b", text[word_end:])
                                if adj_noun and _is_count_noun(adj_noun.group(1).lower()):
                                    word = adj_noun.group(1).lower()
                                    word_end += adj_noun.end()
                            if _is_count_noun(word):
                                head = word
                                unit = ""
                                dimension = "count"
                                end_idx = word_end
                                if head in ("units", "unit"):
                                    after_of = re.match(r"[ \t]+of[ \t]+([A-Za-z]{2,20})\b", text[end_idx:])
                                    if after_of:
                                        head = after_of.group(1).lower()
                                        end_idx += after_of.end()

        if not unit and not head:
            if _is_year_in_temporal_phrase(text, s_idx, match.end(), val):
                continue
            continue

        if is_taken(s_idx, end_idx):
            continue

        taken_spans.append((s_idx, end_idx))
        out.append(
            Quantity(
                value=val / divisor,
                unit=unit,
                raw=text[s_idx:end_idx].strip(),
                start=s_idx,
                end=end_idx,
                dimension=dimension,
                head=head,
            )
        )

    out.sort(key=lambda q: q.start)
    return out


def extract_dates(
    text: str, *, include_bare_year: bool = False, asserted_at: datetime | None = None
) -> list[DateRef]:
    """Explicit dates in `text`.

    A bare year counts only when the text also contains an event word. Callers that
    already know their context can pass `include_bare_year=True`.

    A month with no year ("in July", "7 月") is anchored on the year of `asserted_at`
    and comes back at month precision. Without `asserted_at` it is not extracted.
    """
    if not text:
        return []
    out: list[DateRef] = []
    quantities = extract_quantities(text)
    taken: list[tuple[int, int]] = [q.span for q in quantities]

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
            month = int(match.group("month"))
            if not (1 <= month <= 12):
                continue
            day_str = match.groupdict().get("day")
            day = int(day_str) if day_str else None
            if day is not None and not (1 <= day <= 31):
                continue
            taken.append((match.start(), match.end()))
            out.append(
                DateRef(
                    year=int(match.group("year")),
                    month=month,
                    day=day,
                    raw=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    if asserted_at is not None:
        anchor_year = asserted_at.year

        def add_month(match: re.Match[str], month: int) -> None:
            taken.append((match.start(), match.end()))
            out.append(
                DateRef(
                    year=anchor_year,
                    month=month,
                    day=None,
                    raw=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

        for match in _MONTH_ONLY_EN_RE.finditer(text):
            if not overlaps(match.start(), match.end()):
                add_month(match, _MONTHS[match.group("month").lower()])
        for match in _MONTH_ONLY_ZH_RE.finditer(text):
            if not overlaps(match.start(), match.end()):
                add_month(match, int(match.group("month")))

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


_EVENT_PATTERN = re.compile(
    r"\b(?:"
    + "|".join(re.escape(w) for w in EVENT_WORDS if not any("\u4e00" <= c <= "\u9fff" for c in w))
    + r")\b|"
    + "|".join(re.escape(w) for w in EVENT_WORDS if any("\u4e00" <= c <= "\u9fff" for c in w)),
    re.IGNORECASE,
)


def _has_event_word(text: str) -> bool:
    return bool(_EVENT_PATTERN.search(text))


def quantities_by_unit(text: str) -> dict[str, list[Quantity]]:
    """Quantities grouped by canonical unit or count head -- the shape conflict detection wants."""
    out: dict[str, list[Quantity]] = {}
    for quantity in extract_quantities(text):
        key = quantity.unit if quantity.unit else (f"count:{quantity.head}" if quantity.head else "")
        if key:
            out.setdefault(key, []).append(quantity)
    return out


__all__ = [
    "EVENT_WORDS",
    "DateRef",
    "Quantity",
    "_has_event_word",
    "extract_dates",
    "extract_quantities",
    "quantities_by_unit",
]
