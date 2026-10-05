"""Text normalization.

`normalize_text` is the only thing that produces a `clean_text`. Every character
offset in the system -- chunks, claims, quotes, evidence highlights -- is an offset
into its output, so this function must be deterministic and must not shift offsets
once they are stored.
"""

from __future__ import annotations

import re

# Paragraph breaks survive; intra-paragraph whitespace collapses to one space.
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t　]*\n")
_INNER_WS = re.compile(r"[ \t 　]+")
_SINGLE_NEWLINE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Canonical form for stored document text.

    * line endings become `\\n`;
    * NUL bytes are dropped (they break Postgres text columns and chunk hashing);
    * runs of horizontal whitespace inside a paragraph become one space;
    * paragraphs are separated by exactly two newlines.
    """
    if not text:
        return ""
    cleaned = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = _PARAGRAPH_SPLIT.split(cleaned)
    normalized = []
    for paragraph in paragraphs:
        # A paragraph's internal line breaks are soft: fold them into spaces so a
        # wrapped sentence is not split in half by the sentence splitter.
        flat = paragraph.replace("\n", " ")
        flat = _INNER_WS.sub(" ", flat).strip()
        if flat:
            normalized.append(flat)
    return _SINGLE_NEWLINE.sub("\n\n", "\n\n".join(normalized))


def snippet(text: str, center: int, radius: int = 150) -> str:
    """A window of `text` around `center`, with ellipses where it was cut.

    Snaps to a word boundary so the result reads as prose rather than mid-token.
    """
    if not text:
        return ""
    center = max(0, min(center, len(text)))
    start = max(0, center - radius)
    end = min(len(text), center + radius)
    out = text[start:end]
    if start > 0:
        space = out.find(" ")
        if 0 <= space < 40:
            out = out[space + 1 :]
        out = "…" + out
    if end < len(text):
        space = out.rfind(" ")
        if space > len(out) - 40:
            out = out[:space]
        out = out + "…"
    return out


__all__ = ["normalize_text", "snippet"]
