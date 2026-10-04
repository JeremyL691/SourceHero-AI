"""Export the wiki as an Obsidian-compatible zip.

`wiki/<slug>.md` for the pages (front matter + `[[links]]`), `_index.md` to open
from, and `sources/<document_id>.md` so a reader can get from any footnote back to
the document it came from without this app running.
"""

from __future__ import annotations

import io
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.models.document import Document, DocumentVersion
from app.models.wiki import WikiPage
from app.wiki.render import render_front_matter

SOURCE_DIR = "sources"
WIKI_DIR = "wiki"


def _page_markdown(page: WikiPage) -> str:
    return render_front_matter(page) + (page.body_md or "")


def _index_markdown(pages: list[WikiPage]) -> str:
    lines = ["# Wiki", ""]
    for page in sorted(pages, key=lambda p: p.title):
        lines.append(f"- [[{page.slug}]] — {page.title} (revision {page.revision})")
    return "\n".join(lines) + "\n"


def _source_markdown(db: Session, document_id) -> str:
    document = db.get(Document, document_id)
    if document is None:
        return "# (missing document)\n"
    version = None
    if document.current_version_id is not None:
        version = db.get(DocumentVersion, document.current_version_id)

    lines = [f"# {document.title or '(untitled)'}", ""]
    if document.url:
        lines.append(f"Source: {document.url}")
    if document.published_at:
        lines.append(f"Published: {document.published_at.date().isoformat()}")
    lines.append("")
    lines.append((version.clean_text if version else "").strip())
    return "\n".join(lines).rstrip() + "\n"


def export_zip(db: Session, user_id) -> bytes:
    """The whole wiki as a zip archive."""
    pages = list(
        db.execute(select(WikiPage).where(WikiPage.user_id == user_id).order_by(WikiPage.slug)).scalars()
    )
    # Every document the user has, so a footnote can always be followed to its source.
    document_ids = {
        document_id
        for (document_id,) in db.execute(select(Document.id).where(Document.user_id == user_id)).all()
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{WIKI_DIR}/_index.md", _index_markdown(pages))
        for page in pages:
            archive.writestr(f"{WIKI_DIR}/{page.slug}.md", _page_markdown(page))
        for document_id in sorted(document_ids, key=str):
            archive.writestr(f"{SOURCE_DIR}/{document_id}.md", _source_markdown(db, document_id))
    return buffer.getvalue()


def pages_for_export(db: Session, user_id) -> list[WikiPage]:
    return list(
        db.execute(select(WikiPage).where(WikiPage.user_id == user_id).order_by(WikiPage.slug)).scalars()
    )


def require_page(db: Session, user_id, slug: str) -> WikiPage:
    page = db.execute(
        select(WikiPage).where(WikiPage.user_id == user_id, WikiPage.slug == slug)
    ).scalar_one_or_none()
    if page is None:
        raise NotFoundError("page not found")
    return page


__all__ = ["SOURCE_DIR", "WIKI_DIR", "export_zip", "pages_for_export", "require_page"]
