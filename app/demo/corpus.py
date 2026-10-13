"""Loading the demo corpus.

Each file is Markdown with YAML front matter. A file with a `versions:` list is one
document whose body contains several `=== version: <date> ===` sections -- that is how
the demo produces a document that *changed over time* without inventing a second
source for it.

The URLs point at `demo.sourcehero.local`, a name that cannot resolve, so a stray
network fetch is impossible by construction rather than by convention.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

CORPUS_DIR = Path(__file__).parent / "corpus"
VERSION_SEPARATOR = re.compile(r"^=== version: (\d{4}-\d{2}-\d{2}) ===$", re.MULTILINE)


class CorpusError(ValueError):
    """A corpus file is malformed."""


@dataclass(frozen=True)
class CorpusVersion:
    date: date
    body: str


@dataclass(frozen=True)
class CorpusFile:
    id: str
    title: str
    url: str | None
    source: str
    source_type: str
    published_at: date
    versions: tuple[CorpusVersion, ...]
    path: Path

    @property
    def has_multiple_versions(self) -> bool:
        return len(self.versions) > 1


def _parse_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise CorpusError(f"bad date {value!r} in corpus front matter") from exc
    raise CorpusError(f"unsupported date value {value!r}")


def load_file(path: Path) -> CorpusFile:
    """Parse one corpus file."""
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise CorpusError(f"{path.name}: missing YAML front matter")

    try:
        _, front_matter, body = raw.split("---", 2)
    except ValueError as exc:
        raise CorpusError(f"{path.name}: unterminated front matter") from exc

    try:
        meta = yaml.safe_load(front_matter) or {}
    except yaml.YAMLError as exc:
        raise CorpusError(f"{path.name}: invalid front matter: {exc}") from exc

    for required in ("id", "title", "source", "source_type", "published_at"):
        if required not in meta:
            raise CorpusError(f"{path.name}: front matter is missing '{required}'")

    published = _parse_date(meta["published_at"])
    url = meta.get("url") or None

    declared = meta.get("versions")
    if declared:
        declared_dates = [_parse_date(d) for d in declared]
        versions = _split_versions(body, declared_dates, path)
    else:
        versions = (CorpusVersion(date=published, body=body.strip()),)

    return CorpusFile(
        id=str(meta["id"]),
        title=str(meta["title"]),
        url=url,
        source=str(meta["source"]),
        source_type=str(meta["source_type"]),
        published_at=published,
        versions=versions,
        path=path,
    )


def _split_versions(body: str, declared: list[date], path: Path) -> tuple[CorpusVersion, ...]:
    """Split on `=== version: <date> ===`.

    N declared versions means N-1 separators: the first version's body is the text
    *before* the first marker. Demanding N separators would reject the natural
    reading of "body sections are separated by".
    """
    marks = list(VERSION_SEPARATOR.finditer(body))
    if len(marks) != len(declared) - 1:
        raise CorpusError(
            f"{path.name}: front matter declares {len(declared)} version(s), which needs "
            f"{len(declared) - 1} '=== version: ... ===' separator(s), but the body has {len(marks)}"
        )

    out: list[CorpusVersion] = []
    for index, declared_date in enumerate(declared):
        start = marks[index - 1].end() if index > 0 else 0
        end = marks[index].start() if index < len(marks) else len(body)
        section = body[start:end].strip()
        if not section:
            raise CorpusError(f"{path.name}: version {declared_date} has an empty body")
        out.append(CorpusVersion(date=declared_date, body=section))

    seen = [v.date for v in out]
    if len(set(seen)) != len(seen):
        raise CorpusError(f"{path.name}: duplicate version dates {seen}")
    if seen != sorted(seen):
        raise CorpusError(f"{path.name}: versions must be in chronological order, got {seen}")
    return tuple(out)


def load_corpus(directory: Path | None = None) -> list[CorpusFile]:
    """Every corpus file, ordered by id so seeding is deterministic."""
    root = directory or CORPUS_DIR
    files = sorted(root.glob("*.md"))
    if not files:
        raise CorpusError(f"no corpus files under {root}")
    loaded = [load_file(path) for path in files]
    ids = [f.id for f in loaded]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise CorpusError(f"duplicate corpus ids: {sorted(duplicates)}")
    return loaded


def to_extracted(corpus_file: CorpusFile, version: CorpusVersion):
    """Turn one corpus version into the extractor output shape."""
    from app.ingestion.types import ExtractedDocument, detect_language

    text = version.body
    return ExtractedDocument(
        title=corpus_file.title,
        text=text,
        url=corpus_file.url,
        author=None,
        published_at=datetime(version.date.year, version.date.month, version.date.day, tzinfo=UTC),
        lang=detect_language(text),
        raw_text=None,
        warnings=[],
    )


__all__ = [
    "CORPUS_DIR",
    "CorpusError",
    "CorpusFile",
    "CorpusVersion",
    "VERSION_SEPARATOR",
    "load_corpus",
    "load_file",
    "to_extracted",
]
