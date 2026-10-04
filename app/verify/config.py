"""Verifier thresholds.

Kept in their own module so the reasoning behind each number is in one place and
`DECISIONS.md` has something concrete to refer to. Every value was tuned against the
demo corpus's pinned expectations (M7.6) -- the four sentences whose verdicts the
plan fixes.

The thresholds encode four different questions:

* `SUPPORT_F1` / `SUPPORT_F1_WITH_NUMBERS` -- how much wording overlap before we will
  call a sentence supported at all.
* `CONTRADICT_F1` -- a weaker bar, because a contradicting sentence is by definition
  *not* a restatement; it usually shares the subject and differs in one value.
* `NUMBER_TOLERANCE` and `DATE_TOLERANCE` -- how much a value may move before the two
  mentions stop being "the same measurement".
"""

from __future__ import annotations

# Token-F1 at or above which a sentence counts as restating its evidence.
SUPPORT_F1 = 0.50

# A lower bar suffices when the numbers agree, which is strong evidence on its own:
# two independently written sentences naming the same value are almost certainly
# about the same fact.
SUPPORT_F1_WITH_NUMBERS = 0.35

# Contradiction needs less overlap: the subject usually matches while the value
# differs, so a high F1 would rule out exactly the cases we want to catch.
CONTRADICT_F1 = 0.35

# Relative difference two values of the same unit may differ by and still be "equal".
NUMBER_TOLERANCE = 0.05

# How many sentences of each chunk are kept as evidence, best-scoring first.
EVIDENCE_SENTENCES_PER_CHUNK = 2

# Evidence spans fetched per sentence when the sentence cites nothing in particular.
DEFAULT_EVIDENCE_K = 5

# Longest text `/v1/verify` accepts, to bound the work one request can cause.
MAX_VERIFY_CHARS = 8000

# Sentences sent to the LLM verifier per call.
LLM_BATCH_SIZE = 6

VERDICTS = ("supported", "contradicted", "disputed", "unsupported")

__all__ = [
    "CONTRADICT_F1",
    "DEFAULT_EVIDENCE_K",
    "EVIDENCE_SENTENCES_PER_CHUNK",
    "LLM_BATCH_SIZE",
    "MAX_VERIFY_CHARS",
    "NUMBER_TOLERANCE",
    "SUPPORT_F1",
    "SUPPORT_F1_WITH_NUMBERS",
    "VERDICTS",
]
