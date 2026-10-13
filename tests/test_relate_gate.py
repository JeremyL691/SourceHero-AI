"""DOM-24: the heuristic must defer subject, metric and qualifier mismatches to the LLM.

Before the gate, "The turbine rated power is 5.1 MW." and "The pump rated power is
5.1 MW." were judged `supports` and stored, and the LLM never saw the pair.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.claims.relate import heuristic_relation


def _claim(statement: str, day: int, ident: int, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=ident,
        statement=statement,
        quote=statement,
        asserted_at=datetime(2026, 1, day, tzinfo=UTC),
        **extra,
    )


# Subject differs, the rest of the sentence is the same.
SUBJECT_MISMATCH = [
    ("The turbine output is 5.1 MW.", "The pump output is 5.1 MW."),
    ("涡轮 5.1 MW 已安装。", "水泵 5.1 MW 已安装。"),
    (
        "Orchid-7 scored 68% on the ReasonBench evaluation.",
        "Vexa-3 scored 68% on the ReasonBench evaluation.",
    ),
    ("The Orchid-7 model has 7 billion parameters.", "The Vexa-3 model has 7 billion parameters."),
]

# Same subject, different metric.
METRIC_MISMATCH = [
    ("The turbine rated power is 5.1 MW.", "The turbine rated voltage is 5.1 V."),
    ("Orchid-7 training cost is 7 million dollars.", "Orchid-7 parameter count is 7 million."),
    ("该电池的额定功率为 5.1 MW。", "该电池的额定容量为 5.1 MW。"),
    (
        "The congestion charge costs drivers $9 per day.",
        "The congestion charge raised revenue by $9 per day.",
    ),
]

# A time or range qualifier on one side only.
QUALIFIER_ASYMMETRY = [
    (
        "HX-2 pack prices will be above $150/kWh in the first year.",
        "HX-2 pack prices will be above $150/kWh.",
    ),
    ("首年 HX-2 电池价格高于 150 美元/kWh。", "HX-2 电池价格高于 150 美元/kWh。"),
    ("Orchid-7 scored 68% in Q1 on ReasonBench.", "Orchid-7 scored 68% on ReasonBench."),
    (
        "Downtown traffic fell by 18% every year after the congestion charge.",
        "Downtown traffic fell by 18% after the congestion charge.",
    ),
    ("拥堵费使下城交通量每年下降 18%。", "拥堵费使下城交通量下降 18%。"),
]

# Same subject, same metric, no asymmetric qualifier: the gate must stay out of the way.
# Expected results were recorded from the code before the gate existed.
SAME_SUBJECT_AND_METRIC = [
    ("The turbine rated power is 5.1 MW.", "The turbine rated power is 5.1 MW.", "supports"),
    (
        "Orchid-7 was trained on 2 trillion tokens.",
        "Orchid-7 was trained on 2 trillion tokens in total.",
        "supports",
    ),
    (
        "The HX-2 cell delivers an energy density of 450 Wh/kg.",
        "The HX-2 cell delivers an energy density of 380 Wh/kg.",
        "contradicts",
    ),
    ("涡轮的额定功率为 5.1 MW。", "涡轮的额定功率为 5.1 MW。", "supports"),
    ("Orchid-7 scored 68% in Q1 on ReasonBench.", "Orchid-7 scored 61% in Q1 on ReasonBench.", "contradicts"),
]


@pytest.mark.parametrize(("a", "b"), SUBJECT_MISMATCH)
def test_subject_mismatch_defers_to_llm(a: str, b: str) -> None:
    assert heuristic_relation(_claim(a, 3, 1), _claim(b, 4, 2), 0.6) is None


@pytest.mark.parametrize(("a", "b"), METRIC_MISMATCH)
def test_metric_mismatch_defers_to_llm(a: str, b: str) -> None:
    assert heuristic_relation(_claim(a, 3, 1), _claim(b, 4, 2), 0.6) is None


@pytest.mark.parametrize(("a", "b"), QUALIFIER_ASYMMETRY)
def test_one_sided_qualifier_defers_to_llm(a: str, b: str) -> None:
    assert heuristic_relation(_claim(a, 3, 1), _claim(b, 4, 2), 0.6) is None


@pytest.mark.parametrize(("a", "b", "expected"), SAME_SUBJECT_AND_METRIC)
def test_same_subject_and_metric_keeps_heuristic_result(a: str, b: str, expected: str) -> None:
    draft = heuristic_relation(_claim(a, 3, 1), _claim(b, 4, 2), 0.6)
    assert draft is not None
    assert draft.relation == expected


def test_explicit_subject_fields_override_text() -> None:
    """When both claims carry a subject and it differs, the pair is deferred."""
    a = _claim("Orchid-7 has 7 billion parameters.", 3, 1, subject="Orchid-7", predicate="parameters")
    b = _claim("Orchid-7 has 7 billion parameters.", 4, 2, subject="Vexa-3", predicate="parameters")
    assert heuristic_relation(a, b, 0.6) is None


def test_matching_explicit_fields_do_not_block() -> None:
    a = _claim("Orchid-7 has 7 billion parameters.", 3, 1, subject="Orchid-7", predicate="parameters")
    b = _claim("Orchid-7 has 9 billion parameters.", 4, 2, subject="Orchid-7", predicate="parameters")
    draft = heuristic_relation(a, b, 0.6)
    assert draft is not None
    assert draft.relation == "contradicts"


# Values that are dates, a value on one side only, or the same metric slot: the slot
# rule must not defer these. Each would be deferred by a whole-sentence comparison.
NOT_DEFERRED = [
    ("Mass production begins in September 2026.", "Mass production now expected to begin in January 2027."),
    (
        "The congestion charge did not reduce downtown traffic.",
        "Downtown traffic fell by 18% after the charge was introduced.",
    ),
    (
        "The HX-2 cell delivers an energy density of 450 Wh/kg.",
        "Independent lab tests measured the HX-2 energy density at 380 Wh/kg.",
    ),
    ("该项目原定于 5 月开工。", "该项目已推迟至 9 月开工。"),
    # A quarter in a subordinate clause qualifies the budgeting action, not the claim.
    (
        "Mass production of the HX-2 will begin in September 2026, so we should budget for it in Q4.",
        "Mass production of the HX-2 has been postponed to January 2027.",
    ),
]


@pytest.mark.parametrize(("a", "b"), NOT_DEFERRED)
def test_dates_and_shared_metric_slot_are_not_deferred(a: str, b: str) -> None:
    draft = heuristic_relation(_claim(a, 3, 1), _claim(b, 4, 2), 0.6)
    assert draft is not None
