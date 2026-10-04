"""Rendering a compiled page to Markdown with footnote citations.

Each sentence's claims become footnotes, so the reader sees the source text inline
rather than having to trust a summary. `see_also` renders as `[[slug]]` so the export
opens as a linked vault in Obsidian.
"""

from __future__ import annotations

from app.llm.schemas import WikiBody

KIND_ORDER = ("overview", "consensus", "disputed", "history", "details")


def order_sections(body: WikiBody) -> list:
    """Disputed before history: what disagrees matters more than how it changed."""
    return sorted(
        body.sections, key=lambda s: (KIND_ORDER.index(s.kind) if s.kind in KIND_ORDER else 99, s.id)
    )


def render_markdown(body: WikiBody) -> str:
    """The page as Markdown, with one footnote per cited claim."""
    lines: list[str] = [f"# {body.title}", ""]
    footnotes: list[tuple[int, str]] = []
    seen: dict[int, int] = {}

    def markers(claim_ids: list[int]) -> str:
        marks = []
        for claim_id in claim_ids:
            if claim_id not in seen:
                seen[claim_id] = len(seen) + 1
                footnotes.append((seen[claim_id], claim_id))
            marks.append(f"[^{seen[claim_id]}]")
        return "".join(marks)

    for sentence in body.summary:
        lines.append(f"{sentence.text} {markers(sentence.claim_ids)}".rstrip())

    for section in order_sections(body):
        lines.extend(["", f"## {section.heading}", ""])
        for sentence in section.sentences:
            lines.append(f"{sentence.text} {markers(sentence.claim_ids)}".rstrip())

    if body.see_also:
        lines.extend(["", "## See also", ""])
        for slug in body.see_also:
            lines.append(f"- [[{slug}]]")

    if footnotes:
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.extend(f"[^{n}]: see source claim {cid}" for n, cid in sorted(footnotes))
    return "\n".join(lines).rstrip() + "\n"


def render_front_matter(page) -> str:
    """YAML front matter for the exported vault."""
    import yaml

    compiled_at = page.compiled_at.isoformat() if page.compiled_at else ""
    data = {
        "title": page.title,
        "slug": page.slug,
        "revision": page.revision,
        "compiled_at": compiled_at,
        "compile_method": page.compile_method,
        "source_claims": len(page.compiled_input or []),
    }
    return "---\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True) + "---\n\n"


__all__ = ["KIND_ORDER", "order_sections", "render_front_matter", "render_markdown"]
