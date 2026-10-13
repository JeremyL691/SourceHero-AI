"""Recall and precision of the sentence filter in heuristic claim extraction (DOM-27).

Each recovered sentence was silently dropped before this fix: short Chinese sentences
failed the character minimum before their number was looked at, and some assertion
verbs and units were missing from the vocabulary. Each noise sentence must stay out.
"""

from __future__ import annotations

import pytest

from app.claims.heuristic_extract import is_claim_sentence

RECOVERED_SENTENCES = [
    # Short Chinese sentences carrying a number (character minimum used to drop them).
    "运维人员共 320 人。",
    "可利用率达到 97%。",
    "该机房有 120 座。",
    # "指标为数值" structure: a metric followed by a value.
    "PUE 为 1.25。",
    "PUE is 1.25.",
    # Chinese completion / pass verbs.
    "一期工程完成建设。",
    "二期施工通过验收。",
    # English assertion verbs missing from the vocabulary.
    "The team finally opened the second campus.",
    "The committee passed the amended charter.",
    "The lab holds the design patent.",
    "The campus finished phase two construction.",
]

NOISE_SENTENCES = [
    "Is the plant running at full capacity?",
    "点击这里查看详情。",
    "2026-10-12",
    "2026年10月12日",
    "Overview of the plant.",
    "This sentence rambles on and on without ever asserting anything in particular.",
]


@pytest.mark.parametrize("sentence", RECOVERED_SENTENCES)
def test_recovered_sentence_is_a_claim(sentence: str) -> None:
    assert is_claim_sentence(sentence) is True


@pytest.mark.parametrize("sentence", NOISE_SENTENCES)
def test_noise_sentence_is_not_a_claim(sentence: str) -> None:
    assert is_claim_sentence(sentence) is False
