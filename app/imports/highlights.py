"""Highlight CSVs become notes, one per highlight, deduplicated by highlight text."""

from __future__ import annotations

import csv
import hashlib
import io
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import time as clock
from app.core.errors import DomainValidationError
from app.imports import MAX_IMPORT_ITEMS, ImportError_, ImportResult
from app.ingestion.sources import create_source
from app.ingestion.types import ExtractedDocument
from app.ingestion.versions import upsert_document_version
from app.jobs import queue as q
from app.models.document import Document

REQUIRED_COLUMNS = ("Highlight", "Title", "Author", "URL", "Note", "Location", "Date")
SOURCE_NAME = "Imported highlights"


@dataclass
class HighlightRow:
    row: int
    highlight: str
    title: str
    author: str
    url: str
    note: str
    location: str
    date: str


def highlight_key(text: str) -> str:
    """Identity of one highlight: hash of its whitespace- and case-normalized text."""
    normalized = " ".join(text.split()).casefold()
    return "highlight:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def parse_highlights_csv(data: bytes) -> list[HighlightRow]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DomainValidationError("CSV must be UTF-8", code="bad_csv") from exc

    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = reader.fieldnames or []
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise DomainValidationError(
            f"CSV is missing required columns: {', '.join(missing)}",
            code="bad_csv",
            details={"missing": missing},
        )

    rows: list[HighlightRow] = []
    for number, record in enumerate(reader, start=2):
        if len(rows) >= MAX_IMPORT_ITEMS:
            raise DomainValidationError(
                f"a highlights file may hold at most {MAX_IMPORT_ITEMS} rows", code="too_many_items"
            )
        rows.append(
            HighlightRow(
                row=number,
                highlight=(record.get("Highlight") or "").strip(),
                title=(record.get("Title") or "").strip(),
                author=(record.get("Author") or "").strip(),
                url=(record.get("URL") or "").strip(),
                note=(record.get("Note") or "").strip(),
                location=(record.get("Location") or "").strip(),
                date=(record.get("Date") or "").strip(),
            )
        )
    return rows


def _note_body(row: HighlightRow) -> str:
    lines = [row.highlight, ""]
    for label, value in (
        ("Title", row.title),
        ("Author", row.author),
        ("URL", row.url),
        ("Note", row.note),
        ("Location", row.location),
        ("Date", row.date),
    ):
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


def import_highlights(db: Session, user_id: uuid.UUID, data: bytes) -> ImportResult:
    result = ImportResult()
    source = None
    seen: set[str] = set()

    for row in parse_highlights_csv(data):
        if not row.highlight:
            result.errors.append(ImportError_(code="empty_highlight", url=row.url or None, row=row.row))
            continue
        key = highlight_key(row.highlight)
        already = db.execute(
            select(Document.id).where(Document.user_id == user_id, Document.identity_key == key)
        ).first()
        if key in seen or already:
            result.skipped += 1
            continue
        seen.add(key)

        if source is None:
            source = create_source(db, user_id, source_type="note", name=SOURCE_NAME)
        # No url here: a URL identity would merge every highlight from one article into one document.
        extracted = ExtractedDocument(title=row.title or row.highlight[:80], text=_note_body(row))
        res = upsert_document_version(db, source=source, extracted=extracted, origin="upload")
        # The stored identity is the highlight, not the note body, so re-importing dedupes.
        res.document.identity_key = key
        db.commit()
        result.created += 1
        if res.version:
            q.enqueue(
                db,
                "index_version",
                {"version_id": res.version.id},
                user_id=user_id,
                dedupe_key=f"index-version:{res.version.id}",
                now=clock.utcnow(),
            )
            db.commit()
    return result


__all__ = ["HighlightRow", "highlight_key", "import_highlights", "parse_highlights_csv"]
