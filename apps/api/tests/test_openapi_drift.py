"""Drift tests for the committed OpenAPI artifact.

The committed ``openapi.json`` is the input to the generated TypeScript
contract, so any change to the API surface that is not re-dumped must fail
the build. The test asserts exactly two things:

1. a genuinely fresh render of ``app.openapi()`` equals the committed file,
   byte for byte — this is the drift check;
2. two genuinely fresh renders are identical — the dump is deterministic,
   so a green run cannot hide nondeterministic ordering.

Both checks defeat FastAPI's schema cache before every render: ``app`` keeps
its computed document in ``app.openapi_schema``, so without a reset the
second ``app.openapi()`` returns the same cached object and the comparisons
above become tautologies that can never fail. Setting
``app.openapi_schema = None`` forces each render to rebuild the document from
the routes.

Both checks are offline: ``app.openapi()`` is computed in-process and no
server is started.
"""

from __future__ import annotations

from app.main import app
from scripts.dump_openapi import OPENAPI_PATH, render_openapi_json

# The artifact's location is owned by the dump script (one shared constant,
# so the writer and the test cannot disagree). The tests themselves sit at
# /srv/apps/api/tests in the image but apps/api/tests in a checkout, so
# locating the file relative to this test file would diverge between the
# two layouts.
COMMITTED_OPENAPI = OPENAPI_PATH


def _render_fresh_openapi_json() -> str:
    """Render the OpenAPI document with FastAPI's schema cache defeated.

    ``FastAPI.openapi()`` memoizes its result in ``app.openapi_schema``;
    clearing that attribute makes the next call rebuild the document from
    the current route table, so the caller compares a genuinely fresh build
    rather than a cached object compared against itself.
    """
    app.openapi_schema = None
    return render_openapi_json()


def test_committed_openapi_matches_fresh_render() -> None:
    """The committed artifact equals a fresh render of ``app.openapi()``."""
    assert COMMITTED_OPENAPI.read_text(encoding="utf-8") == _render_fresh_openapi_json(), (
        f"{COMMITTED_OPENAPI.name} is stale; regenerate it with "
        "`python -m scripts.dump_openapi` from the service root."
    )


def test_render_is_deterministic() -> None:
    """Two independent rebuilds of the document produce identical bytes."""
    assert _render_fresh_openapi_json() == _render_fresh_openapi_json()
