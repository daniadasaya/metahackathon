"""Dump the FastAPI OpenAPI document to ``openapi.json`` without a server.

The committed ``openapi.json`` is the single source for the generated
TypeScript contract: ``openapi-typescript`` reads a static file, so no
server may be started to produce it. Run from the service root (``/srv`` in
the image, ``apps/api/`` in a checkout)::

    python -m scripts.dump_openapi

Both this script and the drift test in ``apps/api/tests/`` call
:func:`render_openapi_json`, so the written artifact and the tested artifact
cannot diverge in formatting.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.main import app

# apps/api/openapi.json in a checkout, /srv/openapi.json in the image: the
# script lives one directory below the service root in both layouts.
OPENAPI_PATH = Path(__file__).resolve().parent.parent / "openapi.json"


def render_openapi_json() -> str:
    """Serialize ``app.openapi()`` deterministically, with a trailing newline.

    ``sort_keys`` plus ``indent=2`` keeps the committed artifact diff-stable;
    ``ensure_ascii=False`` keeps non-ASCII text (Indonesian copy) readable.
    """
    return json.dumps(
        app.openapi(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    ) + "\n"


def main() -> Path:
    """Write the OpenAPI document to ``OPENAPI_PATH`` and return that path."""
    OPENAPI_PATH.write_text(render_openapi_json(), encoding="utf-8")
    return OPENAPI_PATH


if __name__ == "__main__":
    print(main())
