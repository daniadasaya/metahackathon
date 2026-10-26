"""Server-side configuration for the Katalog Kilat API.

Reads exactly three environment variables and nothing else:
``MODEL_API_KEY``, ``BASE_URL`` and ``META_MOCK``.

The module never raises at import time. Mock mode is the default and runs
without an API key, so an empty ``MODEL_API_KEY`` is a valid, non-fatal state
rather than a startup error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_BASE_URL = "http://127.0.0.1:8180"


def _env(name: str, default: str = "") -> str:
    """Return the stripped value of an environment variable, or ``default``."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip()


def _meta_mock() -> bool:
    """Return ``True`` unless ``META_MOCK`` is explicitly ``false``.

    Unset, empty and any other value mean mock mode: this service is
    mock-first and must never require a key to boot.
    """
    return _env("META_MOCK").lower() != "false"


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the three server-side environment variables."""

    model_api_key: str | None
    base_url: str
    meta_mock: bool


settings = Settings(
    model_api_key=_env("MODEL_API_KEY") or None,
    base_url=_env("BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL,
    meta_mock=_meta_mock(),
)
