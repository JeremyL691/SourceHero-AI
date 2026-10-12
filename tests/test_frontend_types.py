"""FE-16: the frontend's response types come from the generated OpenAPI schemas, not from copies."""

from __future__ import annotations

from pathlib import Path

TYPES = Path(__file__).resolve().parents[1] / "dashboard-web" / "lib" / "types.ts"
DERIVED = ("Source", "Document", "Claim", "Conflict", "Topic", "Job", "Watch", "ApiToken")


def test_types_are_derived_from_the_generated_schemas():
    text = TYPES.read_text(encoding="utf-8")
    assert 'from "./api-types"' in text, "lib/types.ts must import the generated types"
    aliases = text.count('Schemas["')
    assert aliases >= 15, f"only {aliases} types are aliases of the generated schemas"
    for name in DERIVED:
        assert f"export interface {name} " not in text, f"{name} was re-typed by hand"
