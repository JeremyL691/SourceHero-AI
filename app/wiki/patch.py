"""Incremental wiki updates: rewrite only the sections a claim change touched.

A full compile rewrites every sentence of a page, so a one-claim change churns the
whole page and the revision diff stops being readable. A patch names the sections it
replaces; every other section is carried over unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.llm.schemas import WikiBody, WikiPatch, WikiSection

# ponytail: above this share of changed inputs a full compile is simpler and safer
MAX_PATCH_FRACTION = 0.30


@dataclass
class ChangeSet:
    added_ids: list[int] = field(default_factory=list)
    removed_ids: list[int] = field(default_factory=list)
    changed_ids: list[int] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.added_ids) + len(self.removed_ids) + len(self.changed_ids)

    def within_budget(self, input_count: int) -> bool:
        """True when there is something to patch and it is a small share of the inputs."""
        return 0 < self.total <= MAX_PATCH_FRACTION * input_count


def _normal(item: dict) -> dict:
    # Stored inputs come back from JSON, so tuples have become lists: compare in JSON form.
    return json.loads(json.dumps(item, default=str, sort_keys=True))


def changed_inputs(old_compiled_input: list[dict], new_inputs: list[dict]) -> ChangeSet:
    """Which claims arrived, left or changed since the page was last compiled."""
    old = {item["id"]: _normal(item) for item in old_compiled_input}
    new = {item["id"]: _normal(item) for item in new_inputs}
    return ChangeSet(
        added_ids=sorted(cid for cid in new if cid not in old),
        removed_ids=sorted(cid for cid in old if cid not in new),
        changed_ids=sorted(cid for cid in new if cid in old and new[cid] != old[cid]),
    )


def merge_patch(old_body: WikiBody, patch: WikiPatch) -> WikiBody:
    """Apply a patch: replace by id, append new sections, delete removed ones.

    Sections the patch does not name are kept as they are, in their original order.
    """
    removed = set(patch.removed_section_ids)
    incoming = {s.id: s for s in [*patch.updated_sections, *patch.new_sections]}

    sections: list[WikiSection] = []
    placed: set[str] = set()
    for section in old_body.sections:
        if section.id in removed:
            continue
        sections.append(incoming.get(section.id, section))
        placed.add(section.id)
    sections.extend(s for sid, s in incoming.items() if sid not in placed and sid not in removed)

    return WikiBody(
        title=old_body.title,
        summary=old_body.summary,
        sections=sections,
        see_also=old_body.see_also,
    )


def section_changes(old_body: dict | None, new_body: dict | None) -> dict[str, list[str]]:
    """Section ids added, removed and changed between two stored bodies (compared by id)."""
    old = {s["id"]: s for s in (old_body or {}).get("sections") or []}
    new = {s["id"]: s for s in (new_body or {}).get("sections") or []}
    return {
        "added": [sid for sid in new if sid not in old],
        "removed": [sid for sid in old if sid not in new],
        "changed": [sid for sid in new if sid in old and new[sid] != old[sid]],
    }


__all__ = [
    "MAX_PATCH_FRACTION",
    "ChangeSet",
    "changed_inputs",
    "merge_patch",
    "section_changes",
]
