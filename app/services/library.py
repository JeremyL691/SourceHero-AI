from __future__ import annotations

from sqlalchemy import and_, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Collection, CollectionItem, Document, ItemTag, Source, Tag


VALID_ITEM_TYPES = {"source", "document"}


def normalize_name(name: str) -> str:
    value = " ".join(name.strip().split())
    if not value:
        raise ValueError("Name cannot be empty")
    return value


def create_collection(db: Session, user_id: str, name: str, description: str | None = None) -> Collection:
    collection = Collection(user_id=user_id, name=normalize_name(name), description=description)
    db.add(collection)
    _commit_or_duplicate(db, "Collection name already exists")
    db.refresh(collection)
    return collection


def update_collection(db: Session, user_id: str, collection_id: int, name: str | None = None, description: str | None = None) -> Collection:
    collection = _get_collection(db, user_id, collection_id)
    if name is not None:
        collection.name = normalize_name(name)
    if description is not None:
        collection.description = description
    _commit_or_duplicate(db, "Collection name already exists")
    db.refresh(collection)
    return collection


def delete_collection(db: Session, user_id: str, collection_id: int) -> None:
    _get_collection(db, user_id, collection_id)
    db.execute(delete(CollectionItem).where(CollectionItem.collection_id == collection_id))
    db.execute(delete(Collection).where(Collection.id == collection_id))
    db.commit()


def create_tag(db: Session, user_id: str, name: str, color: str | None = None) -> Tag:
    tag = Tag(user_id=user_id, name=normalize_name(name), color=color)
    db.add(tag)
    _commit_or_duplicate(db, "Tag name already exists")
    db.refresh(tag)
    return tag


def update_tag(db: Session, user_id: str, tag_id: int, name: str | None = None, color: str | None = None) -> Tag:
    tag = _get_tag(db, user_id, tag_id)
    if name is not None:
        tag.name = normalize_name(name)
    if color is not None:
        tag.color = color
    _commit_or_duplicate(db, "Tag name already exists")
    db.refresh(tag)
    return tag


def delete_tag(db: Session, user_id: str, tag_id: int) -> None:
    _get_tag(db, user_id, tag_id)
    db.execute(delete(ItemTag).where(ItemTag.tag_id == tag_id))
    db.execute(delete(Tag).where(Tag.id == tag_id))
    db.commit()


def add_collection_item(db: Session, user_id: str, collection_id: int, item_type: str, item_id: int) -> CollectionItem:
    _get_collection(db, user_id, collection_id)
    _validate_item(db, user_id, item_type, item_id)
    link = CollectionItem(collection_id=collection_id, item_type=item_type, item_id=item_id)
    db.add(link)
    _commit_or_duplicate(db, "Item is already in this collection")
    db.refresh(link)
    return link


def remove_collection_item(db: Session, user_id: str, collection_id: int, item_type: str, item_id: int) -> None:
    _get_collection(db, user_id, collection_id)
    _validate_item_type(item_type)
    db.execute(
        delete(CollectionItem).where(
            CollectionItem.collection_id == collection_id,
            CollectionItem.item_type == item_type,
            CollectionItem.item_id == item_id,
        )
    )
    db.commit()


def add_item_tag(db: Session, user_id: str, tag_id: int, item_type: str, item_id: int) -> ItemTag:
    _get_tag(db, user_id, tag_id)
    _validate_item(db, user_id, item_type, item_id)
    link = ItemTag(tag_id=tag_id, item_type=item_type, item_id=item_id)
    db.add(link)
    _commit_or_duplicate(db, "Item already has this tag")
    db.refresh(link)
    return link


def remove_item_tag(db: Session, user_id: str, tag_id: int, item_type: str, item_id: int) -> None:
    _get_tag(db, user_id, tag_id)
    _validate_item_type(item_type)
    db.execute(
        delete(ItemTag).where(
            ItemTag.tag_id == tag_id,
            ItemTag.item_type == item_type,
            ItemTag.item_id == item_id,
        )
    )
    db.commit()


def cleanup_item_links(db: Session, item_type: str, item_ids: list[int]) -> None:
    if not item_ids:
        return
    _validate_item_type(item_type)
    db.execute(delete(CollectionItem).where(CollectionItem.item_type == item_type, CollectionItem.item_id.in_(item_ids)))
    db.execute(delete(ItemTag).where(ItemTag.item_type == item_type, ItemTag.item_id.in_(item_ids)))


def document_filter_ids(
    db: Session,
    user_id: str,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
) -> set[int] | None:
    rows = db.execute(
        select(Document.id, Document.source_id)
        .join(Source, Document.source_id == Source.id)
        .where(Document.user_id == user_id)
    ).all()
    if not rows:
        return set()
    allowed = {document_id for document_id, _ in rows}
    source_by_doc = {document_id: source_id for document_id, source_id in rows}

    if source_ids:
        allowed &= {document_id for document_id, source_id in source_by_doc.items() if source_id in source_ids}

    if source_type:
        type_doc_ids = {
            document_id
            for document_id, in db.execute(
                select(Document.id)
                .join(Source, Document.source_id == Source.id)
                .where(Source.source_type == source_type, Document.user_id == user_id)
            )
        }
        allowed &= type_doc_ids

    if collection_id is not None:
        collection_links = db.scalars(select(CollectionItem).where(CollectionItem.collection_id == collection_id)).all()
        collection_doc_ids = _doc_ids_from_item_links(db, collection_links)
        allowed &= collection_doc_ids

    normalized_tags = [normalize_name(tag) for tag in tags or []]
    for tag_name in normalized_tags:
        tag = db.scalar(select(Tag).where(func.lower(Tag.name) == tag_name.lower(), Tag.user_id == user_id))
        if not tag:
            return set()
        tag_links = db.scalars(select(ItemTag).where(ItemTag.tag_id == tag.id)).all()
        allowed &= _doc_ids_from_item_links(db, tag_links)

    return allowed


def list_documents(
    db: Session,
    user_id: str,
    source_ids: list[int] | None = None,
    source_type: str | None = None,
    collection_id: int | None = None,
    tags: list[str] | None = None,
) -> list[tuple[Document, Source]]:
    allowed_doc_ids = document_filter_ids(db, user_id, source_ids, source_type, collection_id, tags)
    stmt = (
        select(Document, Source)
        .join(Source, Document.source_id == Source.id)
        .where(Document.user_id == user_id)
        .order_by(Document.fetched_at.desc())
    )
    if allowed_doc_ids is not None:
        if not allowed_doc_ids:
            return []
        stmt = stmt.where(Document.id.in_(allowed_doc_ids))
    return db.execute(stmt).all()


def _doc_ids_from_item_links(db: Session, links) -> set[int]:
    doc_ids = {link.item_id for link in links if link.item_type == "document"}
    source_ids = {link.item_id for link in links if link.item_type == "source"}
    if source_ids:
        doc_ids.update(db.scalars(select(Document.id).where(Document.source_id.in_(source_ids))).all())
    return doc_ids


def _get_collection(db: Session, user_id: str, collection_id: int) -> Collection:
    collection = db.get(Collection, collection_id)
    if not collection:
        raise ValueError(f"Collection not found: {collection_id}")
    if collection.user_id != user_id:
        raise ValueError(f"Collection not found: {collection_id}")
    return collection


def _get_tag(db: Session, user_id: str, tag_id: int) -> Tag:
    tag = db.get(Tag, tag_id)
    if not tag:
        raise ValueError(f"Tag not found: {tag_id}")
    if tag.user_id != user_id:
        raise ValueError(f"Tag not found: {tag_id}")
    return tag


def _validate_item(db: Session, user_id: str, item_type: str, item_id: int) -> None:
    _validate_item_type(item_type)
    model = Source if item_type == "source" else Document
    item = db.get(model, item_id)
    if not item:
        raise ValueError(f"{item_type.title()} not found: {item_id}")
    if item.user_id != user_id:
        raise ValueError(f"{item_type.title()} not found: {item_id}")


def _validate_item_type(item_type: str) -> None:
    if item_type not in VALID_ITEM_TYPES:
        raise ValueError("item_type must be source or document")


def _commit_or_duplicate(db: Session, message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError(message) from exc
