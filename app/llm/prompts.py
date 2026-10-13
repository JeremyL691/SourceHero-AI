"""Prompt loading.

Files are Markdown with YAML front matter (`id`, `version`) and `## system` /
`## user` sections whose bodies are Jinja2 templates. Editing a prompt requires
bumping its `version`, because the version is part of the cache key -- otherwise an
edited prompt would keep serving stale cached answers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined
from pydantic import BaseModel

from app.core.errors import DomainValidationError

PROMPT_DIR = Path(__file__).parent / "prompts"

# StrictUndefined: a typo in a template variable must fail loudly rather than render
# an empty string into a prompt, because an empty variable produces a subtly wrong
# prompt rather than an error.
_env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    system: str
    user: str

    def render(self, **variables) -> tuple[list, str]:
        """Return `(messages, version)`."""
        from app.llm.types import Message

        return [
            Message("system", _env.from_string(self.system).render(**variables)),
            Message("user", _env.from_string(self.user).render(**variables)),
        ], self.version


def schema_instruction(schema: type[BaseModel]) -> str:
    """The JSON Schema instruction appended to the system prompt."""
    return (
        "Respond with ONE JSON object that validates against this JSON Schema. "
        "No prose, no code fences.\n"
        + json.dumps(schema.model_json_schema(), separators=(",", ":"), sort_keys=True)
    )


@lru_cache(maxsize=1)
def _load_all() -> dict[str, Prompt]:
    if not PROMPT_DIR.exists():
        return {}
    out: dict[str, Prompt] = {}
    for path in sorted(PROMPT_DIR.glob("*.md")):
        prompt = _parse(path)
        if prompt.id in out:
            raise DomainValidationError(f"duplicate prompt id {prompt.id!r}")
        out[prompt.id] = prompt
    return out


def _parse(path: Path) -> Prompt:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise DomainValidationError(f"{path.name}: prompt has no YAML front matter")
    try:
        _, front_matter, body = raw.split("---", 2)
    except ValueError as exc:
        raise DomainValidationError(f"{path.name}: unterminated front matter") from exc

    meta = yaml.safe_load(front_matter) or {}
    for required in ("id", "version"):
        if required not in meta:
            raise DomainValidationError(f"{path.name}: front matter is missing '{required}'")

    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(buffer).strip("\n")
            current, buffer = line[3:].strip(), []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        sections[current] = "\n".join(buffer).strip("\n")

    for required in ("system", "user"):
        if required not in sections:
            raise DomainValidationError(f"{path.name}: prompt has no '## {required}' section")

    return Prompt(
        id=str(meta["id"]),
        version=str(meta["version"]),
        system=sections["system"],
        user=sections["user"],
    )


def get_prompt(name: str) -> Prompt:
    prompts = _load_all()
    if name not in prompts:
        raise DomainValidationError(f"unknown prompt {name!r}; available: {', '.join(sorted(prompts))}")
    return prompts[name]


def render(name: str, *, schema: type[BaseModel] | None = None, **variables) -> tuple[list, str]:
    """Render a prompt into `(messages, version)`.

    With a schema, the JSON Schema instruction is appended to the system message so
    callers do not have to remember to do it.
    """
    prompt = get_prompt(name)
    messages, version = prompt.render(**variables)
    if schema is not None:
        messages[0] = type(messages[0])(
            role="system", content=messages[0].content + "\n\n" + schema_instruction(schema)
        )
    return messages, version


def list_prompts() -> list[Prompt]:
    return [_load_all()[k] for k in sorted(_load_all())]


def reset_cache() -> None:
    _load_all.cache_clear()


__all__ = [
    "PROMPT_DIR",
    "Prompt",
    "get_prompt",
    "list_prompts",
    "render",
    "reset_cache",
    "schema_instruction",
]
