"""The one version string. `pyproject.toml` is the source; every surface reads it back from here."""

from __future__ import annotations

from importlib import metadata

PACKAGE_NAME = "sourcehero-ai"
UNKNOWN_VERSION = "0+unknown"


def package_version() -> str:
    """Installed distribution version, or `0+unknown` when the package is not installed."""
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        return UNKNOWN_VERSION
