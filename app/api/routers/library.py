"""Collections, tags and document flags. All routes are scoped to the current user."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, Pagination, get_db, require_scope
from app.library import service as lib
from app.schemas.library import (
    CollectionIn,
    CollectionItemIn,
    CollectionOut,
    CollectionPatchIn,
    DocumentFlagsIn,
    DocumentFlagsOut,
    DocumentTagIn,
    TagIn,
    TagOut,
)

router = APIRouter(tags=["library"])


def _collection_out(collection, count: int) -> CollectionOut:
    out = CollectionOut.model_validate(collection)
    out.document_count = count
    return out


@router.get("/collections", response_model=list[CollectionOut], summary="List collections")
def list_collections(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[CollectionOut]:
    rows = lib.list_collections(db, user.id, limit=page.limit, offset=page.offset)
    return [_collection_out(collection, count) for collection, count in rows]


@router.post(
    "/collections",
    response_model=CollectionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a collection",
)
def create_collection(
    payload: CollectionIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> CollectionOut:
    collection = lib.create_collection(db, user.id, name=payload.name, description=payload.description)
    return _collection_out(collection, 0)


@router.get("/collections/{collection_id}", response_model=CollectionOut, summary="One collection")
def get_collection(
    collection_id: int,
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> CollectionOut:
    collection = lib.owned_collection(db, user.id, collection_id)
    return _collection_out(collection, lib.collection_document_count(db, collection.id))


@router.patch("/collections/{collection_id}", response_model=CollectionOut, summary="Rename or describe")
def patch_collection(
    collection_id: int,
    payload: CollectionPatchIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> CollectionOut:
    collection = lib.update_collection(db, user.id, collection_id, payload.model_dump(exclude_unset=True))
    return _collection_out(collection, lib.collection_document_count(db, collection.id))


@router.delete(
    "/collections/{collection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a collection (documents are kept)",
)
def delete_collection(
    collection_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.delete_collection(db, user.id, collection_id)


@router.post(
    "/collections/{collection_id}/items",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add a document to a collection",
)
def add_collection_item(
    collection_id: int,
    payload: CollectionItemIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.add_to_collection(db, user.id, collection_id, payload.document_id)


@router.delete(
    "/collections/{collection_id}/items",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a document from a collection",
)
def remove_collection_item(
    collection_id: int,
    payload: CollectionItemIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.remove_from_collection(db, user.id, collection_id, payload.document_id)


@router.get("/tags", response_model=list[TagOut], summary="List tags")
def list_tags(
    page: Pagination = Depends(),
    user: CurrentUser = Depends(require_scope("read")),
    db: Session = Depends(get_db),
) -> list[TagOut]:
    return [
        TagOut.model_validate(t) for t in lib.list_tags(db, user.id, limit=page.limit, offset=page.offset)
    ]


@router.post("/tags", response_model=TagOut, status_code=status.HTTP_201_CREATED, summary="Create a tag")
def create_tag(
    payload: TagIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> TagOut:
    return TagOut.model_validate(lib.create_tag(db, user.id, name=payload.name))


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a tag")
def delete_tag(
    tag_id: int,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.delete_tag(db, user.id, tag_id)


@router.post(
    "/documents/{document_id}/tags",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Tag a document",
)
def attach_tag(
    document_id: uuid.UUID,
    payload: DocumentTagIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.attach_tag(db, user.id, document_id, payload.tag_id)


@router.delete(
    "/documents/{document_id}/tags",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a tag from a document",
)
def detach_tag(
    document_id: uuid.UUID,
    payload: DocumentTagIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> None:
    lib.detach_tag(db, user.id, document_id, payload.tag_id)


@router.patch(
    "/documents/{document_id}/flags",
    response_model=DocumentFlagsOut,
    summary="Star or archive a document",
)
def patch_document_flags(
    document_id: uuid.UUID,
    payload: DocumentFlagsIn,
    user: CurrentUser = Depends(require_scope("write")),
    db: Session = Depends(get_db),
) -> DocumentFlagsOut:
    flag = lib.set_document_flags(
        db, user.id, document_id, starred=payload.starred, archived=payload.archived
    )
    return DocumentFlagsOut(document_id=document_id, starred=flag.starred, archived=flag.archived)


__all__ = ["router"]
