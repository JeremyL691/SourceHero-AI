"""Bulk import endpoints: OPML subscriptions and highlight CSVs."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_db, require_scope
from app.core.config import get_settings
from app.core.errors import DomainValidationError, PayloadTooLargeError
from app.core.ratelimit import enforce
from app.imports import MAX_IMPORT_BYTES, ImportResult
from app.imports.highlights import import_highlights
from app.imports.opml import import_opml
from app.schemas.imports import ImportErrorOut, ImportResultOut

router = APIRouter(tags=["imports"])


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        raise PayloadTooLargeError(
            f"file is larger than {MAX_IMPORT_BYTES // (1024 * 1024)}MB", code="file_too_large"
        )
    if not data:
        raise DomainValidationError("provide a file", code="empty_import")
    return data


def _out(result: ImportResult) -> ImportResultOut:
    return ImportResultOut(
        created=result.created,
        skipped=result.skipped,
        errors=[ImportErrorOut(code=e.code, url=e.url, row=e.row) for e in result.errors],
    )


@router.post(
    "/imports/opml",
    response_model=ImportResultOut,
    status_code=status.HTTP_200_OK,
    summary="Import an OPML subscription list as RSS sources",
)
def post_opml_import(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ImportResultOut:
    enforce(db, f"/v1/imports:{user.id}", get_settings().rate_limit_capture)
    return _out(import_opml(db, user.id, _read_upload(file)))


@router.post(
    "/imports/highlights",
    response_model=ImportResultOut,
    status_code=status.HTTP_200_OK,
    summary="Import a highlights CSV as notes",
)
def post_highlights_import(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> ImportResultOut:
    enforce(db, f"/v1/imports:{user.id}", get_settings().rate_limit_capture)
    return _out(import_highlights(db, user.id, _read_upload(file)))


__all__ = ["router"]
