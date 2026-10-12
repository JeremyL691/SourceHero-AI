"""Bulk imports: OPML feed lists and highlight CSVs.

Both paths reuse the ordinary services (`create_source`, `upsert_document_version`,
the queue) so an imported item is indistinguishable from one captured by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_IMPORT_BYTES = 2 * 1024 * 1024
MAX_IMPORT_ITEMS = 1000


@dataclass
class ImportError_:
    code: str
    url: str | None = None
    row: int | None = None


@dataclass
class ImportResult:
    created: int = 0
    skipped: int = 0
    errors: list[ImportError_] = field(default_factory=list)


__all__ = ["MAX_IMPORT_BYTES", "MAX_IMPORT_ITEMS", "ImportError_", "ImportResult"]
