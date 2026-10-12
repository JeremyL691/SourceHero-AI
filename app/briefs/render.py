"""Pure Markdown rendering for evidence briefs. No database access in this module.

A brief is a list of sections. Each line may carry a verdict badge and the evidence it
rests on; every distinct piece of evidence becomes one numbered footnote whose quote is
the exact source slice the caller supplied. Footnotes are numbered in order of first
use, so the same evidence always gets the same number inside one brief.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Source:
    """One piece of evidence. `quote` must already be the verbatim source slice."""

    title: str
    url: str | None
    date: str | None
    quote: str


@dataclass(frozen=True)
class Line:
    text: str
    verdict: str | None = None
    sources: tuple[Source, ...] = ()


@dataclass(frozen=True)
class Section:
    heading: str
    lines: tuple[Line, ...] = ()
    empty: str = "Nothing to report."


@dataclass
class _Notes:
    numbers: dict[Source, int] = field(default_factory=dict)

    def mark(self, source: Source) -> str:
        n = self.numbers.get(source)
        if n is None:
            n = len(self.numbers) + 1
            self.numbers[source] = n
        return f"[^{n}]"

    def definitions(self) -> list[str]:
        lines: list[str] = []
        for source, n in sorted(self.numbers.items(), key=lambda item: item[1]):
            meta = [part for part in (source.url, source.date) if part]
            lines.append(f"[^{n}]: {source.title or '(untitled)'}")
            if meta:
                lines.append(f"    {' · '.join(meta)}")
            for quote_line in source.quote.split("\n"):
                lines.append(f"    > {quote_line}")
        return lines


def _line_markdown(line: Line, notes: _Notes) -> str:
    badge = f"[{line.verdict}] " if line.verdict else ""
    markers = "".join(notes.mark(source) for source in line.sources)
    return f"- {badge}{line.text} {markers}".rstrip()


def render_brief(*, title: str, subtitle: str, sections: list[Section]) -> str:
    """The brief as Markdown: title, sections, then one Sources block with every footnote."""
    notes = _Notes()
    lines: list[str] = [f"# {title}", "", f"_{subtitle}_", ""]
    for section in sections:
        lines.extend([f"## {section.heading}", ""])
        if section.lines:
            lines.extend(_line_markdown(line, notes) for line in section.lines)
        else:
            lines.append(section.empty)
        lines.append("")

    definitions = notes.definitions()
    if definitions:
        lines.extend(["## Sources", ""])
        lines.extend(definitions)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["Line", "Section", "Source", "render_brief"]
