"""Secret scanner over every git-tracked text file (AGENTS.md 守则 22; plan K0.3).

Failures report file names and line numbers only, never the matched text. The detector
self-test builds its planted values at runtime, so no secret-shaped literal is committed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {
    "openrouter": re.compile(r"sk-or-v1-[0-9a-f]{20,}"),
    "generic-sk": re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}"),
    "sh_pat": re.compile(r"sh_pat_[A-Za-z0-9_-]{20,}"),
    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def scan_text(text: str) -> list[tuple[int, str]]:
    """(line number, pattern name) for every secret-shaped line. The matched text is never returned."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        hits.extend((lineno, name) for name, pattern in PATTERNS.items() if pattern.search(line))
    return hits


def _git_ls_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [name for name in out.split("\0") if name]


def test_detector_catches_planted_values():
    planted = {
        "openrouter": "sk-or-v1-" + "ab12" * 6,
        "generic-sk": "sk-" + "Q" * 24,
        "sh_pat": "sh_pat_" + "x" * 24,
        "private-key": "-----BEGIN " + "RSA " + "PRIVATE KEY-----",
    }
    for expected, value in planted.items():
        names = {name for _, name in scan_text(value)}
        assert expected in names, f"detector missed a planted {expected} value"


def test_detector_ignores_ordinary_prose():
    prose = "risk-management framework; task-runner config; disk-space alerts; sk-learn is a library"
    assert scan_text(prose) == []


def test_no_secret_shaped_text_in_tracked_files():
    hits: list[str] = []
    for name in _git_ls_files():
        path = REPO_ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\x00" in data[:8000]:
            continue  # binary artifacts are not scanned
        for lineno, pattern_name in scan_text(data.decode("utf-8", errors="ignore")):
            hits.append(f"{name}:{lineno} ({pattern_name})")
    assert not hits, "secret-shaped text in tracked files (values not shown): " + ", ".join(hits)


def test_env_file_is_not_tracked():
    assert ".env" not in _git_ls_files()
