"""Collections, tags and document flags. Every lookup is scoped to one user.

A row that belongs to another user is reported as not found, never as forbidden,
so a caller cannot probe for ids that exist.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError
from app.models.document import Document
from app.models.library import Collection, CollectionItem, DocumentFlag, ItemTag, Tag
from app.schemas.library import CollectionRef, TagRef


def owned_document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.user_id != user_id:
        raise NotFoundError("document not found")
    return document


def owned_collection(db: Session, user_id: uuid.UUID, collection_id: int) -> Collection:
    collection = db.get(Collection, collection_id)
    if collection is None or collection.user_id != user_id:
        raise NotFoundError("collection not found")
    return collection


def owned_tag(db: Session, user_id: uuid.UUID, tag_id: int) -> Tag:
    tag = db.get(Tag, tag_id)
    if tag is None or tag.user_id != user_id:
        raise NotFoundError("tag not found")
    return tag


def collection_document_count(db: Session, collection_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(CollectionItem)
            .where(CollectionItem.collection_id == collection_id)
        ).scalar_one()
    )


def list_collections(
    db: Session, user_id: uuid.UUID, *, limit: int, offset: int
) -> list[tuple[Collection, int]]:
    counts = (
        select(CollectionItem.collection_id, func.count().label("n"))
        .group_by(CollectionItem.collection_id)
        .subquery()
    )
    rows = db.execute(
        select(Collection, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.collection_id == Collection.id)
        .where(Collection.user_id == user_id)
        .order_by(Collection.name, Collection.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [(collection, int(n)) for collection, n in rows]


def _ensure_name_free(
    db: Session, model: type[Collection] | type[Tag], user_id: uuid.UUID, name: str
) -> None:
    taken = db.execute(select(model.id).where(model.user_id == user_id, model.name == name)).first()
    if taken is not None:
        kind = "collection" if model is Collection else "tag"
        raise ConflictError(f"a {kind} named '{name}' already exists", code="name_taken")


def create_collection(db: Session, user_id: uuid.UUID, *, name: str, description: str | None) -> Collection:
    name = name.strip()
    _ensure_name_free(db, Collection, user_id, name)
    collection = Collection(user_id=user_id, name=name, description=description)
    db.add(collection)
    db.commit()
    db.refresh(collection)
    return collection


def update_collection(db: Session, user_id: uuid.UUID, collection_id: int, changes: dict) -> Collection:
    """Apply only the keys the caller sent (`changes` is `model_dump(exclude_unset=True)`)."""
    collection = owned_collection(db, user_id, collection_id)
    if changes.get("name") is not None:
        name = changes["name"].strip()
        if name != collection.name:
            _ensure_name_free(db, Collection, user_id, name)
        collection.name = name
    if "description" in changes:
        collection.description = changes["description"]
    db.commit()
    db.refresh(collection)
    return collection


def delete_collection(db: Session, user_id: uuid.UUID, collection_id: int) -> None:
    collection = owned_collection(db, user_id, collection_id)
    db.delete(collection)  # collection_items cascade via the FK
    db.commit()


def add_to_collection(db: Session, user_id: uuid.UUID, collection_id: int, document_id: uuid.UUID) -> None:
    owned_collection(db, user_id, collection_id)
    owned_document(db, user_id, document_id)
    exists = db.execute(
        select(CollectionItem.id).where(
            CollectionItem.collection_id == collection_id, CollectionItem.document_id == document_id
        )
    ).first()
    if exists is None:
        db.add(CollectionItem(collection_id=collection_id, document_id=document_id))
        db.commit()


def remove_from_collection(
    db: Session, user_id: uuid.UUID, collection_id: int, document_id: uuid.UUID
) -> None:
    owned_collection(db, user_id, collection_id)
    owned_document(db, user_id, document_id)
    db.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == collection_id, CollectionItem.document_id == document_id
        )
    )
    db.commit()


def list_tags(db: Session, user_id: uuid.UUID, *, limit: int, offset: int) -> list[Tag]:
    return list(
        db.execute(
            select(Tag).where(Tag.user_id == user_id).order_by(Tag.name, Tag.id).limit(limit).offset(offset)
        ).scalars()
    )


def create_tag(db: Session, user_id: uuid.UUID, *, name: str) -> Tag:
    name = name.strip()
    _ensure_name_free(db, Tag, user_id, name)
    tag = Tag(user_id=user_id, name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


def delete_tag(db: Session, user_id: uuid.UUID, tag_id: int) -> None:
    tag = owned_tag(db, user_id, tag_id)
    db.delete(tag)  # item_tags cascade via the FK
    db.commit()


def attach_tag(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, tag_id: int) -> None:
    owned_document(db, user_id, document_id)
    owned_tag(db, user_id, tag_id)
    exists = db.execute(
        select(ItemTag.id).where(ItemTag.tag_id == tag_id, ItemTag.document_id == document_id)
    ).first()
    if exists is None:
        db.add(ItemTag(tag_id=tag_id, document_id=document_id))
        db.commit()


def detach_tag(db: Session, user_id: uuid.UUID, document_id: uuid.UUID, tag_id: int) -> None:
    owned_document(db, user_id, document_id)
    owned_tag(db, user_id, tag_id)
    db.execute(delete(ItemTag).where(ItemTag.tag_id == tag_id, ItemTag.document_id == document_id))
    db.commit()


def get_or_create_flags(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> DocumentFlag:
    owned_document(db, user_id, document_id)
    flag = (
        db.execute(
            select(DocumentFlag).where(
                DocumentFlag.user_id == user_id, DocumentFlag.document_id == document_id
            )
        )
        .scalars()
        .first()
    )
    if flag is None:
        flag = DocumentFlag(user_id=user_id, document_id=document_id, starred=False, archived=False)
        db.add(flag)
    return flag


def set_document_flags(
    db: Session,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
    *,
    starred: bool | None,
    archived: bool | None,
) -> DocumentFlag:
    flag = get_or_create_flags(db, user_id, document_id)
    if starred is not None:
        flag.starred = starred
    if archived is not None:
        flag.archived = archived
    db.commit()
    return flag


def document_refs(
    db: Session, user_id: uuid.UUID, document_ids: Iterable[uuid.UUID]
) -> tuple[dict[uuid.UUID, list[TagRef]], dict[uuid.UUID, list[CollectionRef]]]:
    """Tags and collections for many documents in two queries, not one per document."""
    ids = list(document_ids)
    tags: dict[uuid.UUID, list[TagRef]] = {}
    collections: dict[uuid.UUID, list[CollectionRef]] = {}
    if not ids:
        return tags, collections
    tag_rows = db.execute(
        select(ItemTag.document_id, Tag.id, Tag.name)
        .join(Tag, Tag.id == ItemTag.tag_id)
        .where(Tag.user_id == user_id, ItemTag.document_id.in_(ids))
        .order_by(Tag.name, Tag.id)
    ).all()
    for document_id, tag_id, name in tag_rows:
        tags.setdefault(document_id, []).append(TagRef(id=tag_id, name=name))
    collection_rows = db.execute(
        select(CollectionItem.document_id, Collection.id, Collection.name)
        .join(Collection, Collection.id == CollectionItem.collection_id)
        .where(Collection.user_id == user_id, CollectionItem.document_id.in_(ids))
        .order_by(Collection.name, Collection.id)
    ).all()
    for document_id, collection_id, name in collection_rows:
        collections.setdefault(document_id, []).append(CollectionRef(id=collection_id, name=name))
    return tags, collections


__all__ = [
    "add_to_collection",
    "attach_tag",
    "collection_document_count",
    "create_collection",
    "create_tag",
    "delete_collection",
    "delete_tag",
    "detach_tag",
    "document_refs",
    "list_collections",
    "list_tags",
    "owned_collection",
    "owned_document",
    "owned_tag",
    "remove_from_collection",
    "set_document_flags",
    "update_collection",
]
