"""Regression tests for DOM-20, DOM-21, DOM-23.

Tests:
- Quote alignment with character expansions (ß -> ss), ligatures (ﬁ -> fi), combining accents, fullwidth characters.
- Chunk-local quote alignment for repeated sentences in multi-chunk documents.
- Savepoint handling during extract_claims insertion conflicts.
- Heuristic claim extraction modal stem boundaries (can != cancer, will != William).
- Subject extraction skipping quarters and prepositional time phrases.
- Chunk truncation count recorded when drafts exceed MAX_CLAIMS.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.claims.align import align_quote
from app.claims.extract import extract_version_claims
from app.claims.heuristic_extract import (
    MAX_CLAIMS,
    _classify,
    _matches_stem,
    _subject_of,
    heuristic_claims,
)
from app.models.chunk import Chunk
from app.models.claim import Claim
from app.models.document import Document, DocumentVersion
from app.models.source import Source
from app.models.user import User

# --- DOM-21: Quote Alignment Properties ---


def test_align_casefold_sharp_s():
    source = "Die große Straße hat viele Autos."
    quote = "Die grosse Strasse hat viele Autos."
    span = align_quote(quote, source, 0)
    assert span is not None, "Failed to align casefold-expanded ß"
    assert source[span[0] : span[1]] == "Die große Straße hat viele Autos."


def test_align_ligature_fi():
    source = "A ﬁne example of battery design."
    quote = "A fine example of battery design."
    span = align_quote(quote, source, 0)
    assert span is not None, "Failed to align ligature ﬁ"
    assert source[span[0] : span[1]] == "A ﬁne example of battery design."


def test_align_combining_accent():
    source = "We visited the cafe\u0301 yesterday in Ferro City."
    quote = "We visited the café yesterday in Ferro City."
    span = align_quote(quote, source, 0)
    assert span is not None, "Failed to align combining accent"
    assert source[span[0] : span[1]] == "We visited the cafe\u0301 yesterday in Ferro City."


def test_align_fullwidth_characters():
    source = "Battery pack ＨＸ－２ delivered 450 Wh/kg."
    quote = "Battery pack HX-2 delivered 450 Wh/kg."
    span = align_quote(quote, source, 0)
    assert span is not None, "Failed to align fullwidth characters"
    assert source[span[0] : span[1]] == "Battery pack ＨＸ－２ delivered 450 Wh/kg."


# --- DOM-23: Heuristic extraction stem matching and subject extraction ---


def test_heuristic_modals_do_not_match_word_prefixes():
    # 'cancer' should not match modal 'can'
    assert not _matches_stem("cancer", ("can",))
    assert not _matches_stem("candid", ("can",))
    assert _matches_stem("can", ("can",))

    # 'William' should not match modal 'will'
    assert not _matches_stem("william", ("will",))
    assert _matches_stem("will", ("will",))

    # Classification: "Cancer showed positive response." is fact, not prediction
    verdict = _classify("Cancer showed positive response.")
    assert verdict != "prediction"

    # Classification: "William presented the report." is fact, not prediction
    verdict_william = _classify("William presented the report.")
    assert verdict_william != "prediction"


def test_heuristic_subject_skips_temporal_and_quarters():
    subj1 = _subject_of("In March, Halcyon Cells announced new results.")
    assert subj1 == "Halcyon Cells", f"Expected Halcyon Cells, got: {subj1}"

    subj2 = _subject_of("In Q3 2026, HX-2 pack prices reached $120/kWh.")
    assert subj2 == "HX-2", f"Expected HX-2, got: {subj2}"


def test_heuristic_chunk_truncation_recorded():
    from app.claims.extract import ExtractStats

    # Construct a chunk with 15 claim sentences
    sentences = [f"Halcyon test run {i} delivered {300 + i} Wh/kg." for i in range(15)]
    chunk_text = " ".join(sentences)
    stats = ExtractStats()
    drafts = heuristic_claims(chunk_text, 0, stats=stats)
    assert len(drafts) == MAX_CLAIMS
    assert stats.rejected_over_limit == 3


# --- DOM-20: Extract chunk scope and savepoints ---


@pytest.mark.db
def test_extract_repeated_sentences_in_different_chunks(db):
    user = User(email=f"extract_test_{uuid.uuid4().hex[:8]}@example.com", password_hash="pw")
    db.add(user)
    db.flush()

    source = Source(user_id=user.id, source_type="note", name="test")
    db.add(source)
    db.flush()

    doc = Document(
        user_id=user.id,
        source_id=source.id,
        identity_key=f"doc:{uuid.uuid4()}",
        title="Repeated Sentences Doc",
    )
    db.add(doc)
    db.flush()

    s1 = "Halcyon Cells delivered 450 Wh/kg at launch. "
    s2 = "Other unrelated sentences fill the space in between. "
    s3 = "Halcyon Cells delivered 450 Wh/kg at launch. "
    full_text = s1 + s2 + s3

    from app.core import time as clock

    now = clock.utcnow()
    version = DocumentVersion(
        document_id=doc.id,
        user_id=user.id,
        version_no=1,
        clean_text=full_text,
        content_hash="hash_repeated_1",
        effective_at=now,
    )
    db.add(version)
    db.flush()

    chunk1 = Chunk(
        user_id=user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_index=0,
        chunk_hash="hash_c1",
        text=s1.strip(),
        char_start=0,
        char_end=len(s1),
    )
    chunk2 = Chunk(
        user_id=user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_index=1,
        chunk_hash="hash_c2",
        text=s3.strip(),
        char_start=len(s1) + len(s2),
        char_end=len(full_text),
    )
    db.add_all([chunk1, chunk2])
    db.flush()

    stats = extract_version_claims(db, version.id)
    assert stats.created == 2, f"Expected 2 claims from 2 chunks, got {stats.created}"

    claims = list(
        db.execute(select(Claim).where(Claim.version_id == version.id).order_by(Claim.char_start)).scalars()
    )
    assert len(claims) == 2
    assert claims[0].chunk_id == chunk1.id
    assert claims[1].chunk_id == chunk2.id


@pytest.mark.db
def test_extract_integrity_error_handled_by_savepoint(db, monkeypatch):
    user = User(email=f"extract_savepoint_{uuid.uuid4().hex[:8]}@example.com", password_hash="pw")
    db.add(user)
    db.flush()

    source = Source(user_id=user.id, source_type="note", name="test")
    db.add(source)
    db.flush()

    doc = Document(
        user_id=user.id,
        source_id=source.id,
        identity_key=f"doc:{uuid.uuid4()}",
        title="Savepoint Doc",
    )
    db.add(doc)
    db.flush()

    s1 = "Halcyon Cells delivered 450 Wh/kg at launch. "
    s2 = "Ferro City announced 380 Wh/kg target. "
    full_text = s1 + s2

    from app.core import time as clock

    now = clock.utcnow()
    version = DocumentVersion(
        document_id=doc.id,
        user_id=user.id,
        version_no=1,
        clean_text=full_text,
        content_hash="hash_savepoint_1",
        effective_at=now,
    )
    db.add(version)
    db.flush()

    chunk1 = Chunk(
        user_id=user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_index=0,
        chunk_hash="hash_c1",
        text=s1.strip(),
        char_start=0,
        char_end=len(s1),
    )
    chunk2 = Chunk(
        user_id=user.id,
        document_id=doc.id,
        version_id=version.id,
        chunk_index=1,
        chunk_hash="hash_c2",
        text=s2.strip(),
        char_start=len(s1),
        char_end=len(full_text),
    )
    db.add_all([chunk1, chunk2])
    db.flush()

    # Pre-insert a claim that has the exact same digest as chunk2's claim
    from app.claims.extract import _drafts_for, _persist

    drafts2, _ = _drafts_for(chunk2.text, chunk2.char_start)
    assert len(drafts2) == 1
    # Store it first
    c2 = _persist(db, version, chunk2, drafts2[0], extractor="heuristic")
    assert c2 is not None and c2 is not False
    db.commit()

    # Now run extract_version_claims: chunk1 should succeed, chunk2 will see existing or conflict
    stats = extract_version_claims(db, version.id)
    assert stats.created == 1
    assert stats.unchanged == 1
    # And transaction is alive and well
    claims = list(
        db.execute(select(Claim).where(Claim.version_id == version.id).order_by(Claim.char_start)).scalars()
    )
    assert len(claims) == 2
    assert claims[0].char_start == 0
    assert claims[1].char_start == chunk2.char_start
