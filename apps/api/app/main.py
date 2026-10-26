"""FastAPI application for Katalog Kilat.

Scope of this module (live probe): the application object, the ``GET /api/health``
route, the static mount that serves the built SPA assets, and the SPA
catch-all that returns ``index.html`` for unknown non-API paths so a deep
link survives a refresh.

What this module deliberately does not do: it does not own the catalog, the
job state, the pipeline, or any Meta call. It only wires HTTP.

Registration order is binding (spec, ``architecture specification`` §13): every API
route is registered first, then the static mount, then the catch-all last.
Reversing that order makes the catch-all swallow every ``/api/*`` request and
answer 404. The catch-all therefore refuses to serve HTML for ``/api/*`` and
for ``/assets/*``.

The public product pages under ``/p/`` are not here yet — the pipeline owns them. Until
then the catch-all answers them with the SPA shell, which is a known and
accepted limitation (spec).

The module never crashes at import time when the built SPA is missing: a
developer running bare ``uvicorn`` still gets a working ``/api/health``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app.settings import settings

logger = logging.getLogger(__name__)

# The built SPA sits next to the application package: /srv/app and /srv/static
# in the image, apps/api/app and apps/api/static in a local checkout. The path
# is derived from this file, never hardcoded.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
INDEX_FILE = STATIC_DIR / "index.html"

# Error code from the closed enum owned by schemas.py (spec). schemas.py does
# not exist yet — stage declares the enum — so the literal lives here and must be
# kept in sync with it.
ERROR_CODE_NOT_FOUND = "not_found"

app = FastAPI(title="Katalog Kilat API")


@app.get("/api/health")
async def health() -> dict[str, bool]:
    """Report process liveness and whether the service runs in mock mode."""
    return {"ok": True, "mock": settings.meta_mock}


# --- static mount and SPA catch-all -----------------------------------------
# Everything below is registered after the API routes above. Do not move it.

# Path prefixes the catch-all must never answer with the HTML shell: an
# unknown API path is a missing route, an unknown asset path a missing file.
RESERVED_PREFIXES = ("api", "assets")


def _not_found(path: str) -> JSONResponse:
    """Return the standard error shape for a path that matches no route."""
    return JSONResponse(
        status_code=404,
        content={
            "error": {
                "code": ERROR_CODE_NOT_FOUND,
                "message": f"No route matches {path!r}.",
            }
        },
    )


def _mount_assets() -> None:
    """Mount the built asset directory when it is really there.

    Starlette stats the served directory on every request and raises when it
    is gone, so the mount is registered only when the directory exists. A
    developer running bare ``uvicorn`` without a build then gets a working
    API and a 404 on ``/assets/*`` — never a 500, never the HTML shell.
    """
    if ASSETS_DIR.is_dir():
        # check_dir=False: the constructor must not raise at import time.
        app.mount(
            "/assets",
            StaticFiles(directory=ASSETS_DIR, check_dir=False),
            name="assets",
        )
        return
    if not STATIC_DIR.is_dir():
        logger.warning(
            "Built SPA not found at %s; only the API routes are served. "
            "Run the web build to produce it.",
            STATIC_DIR,
        )
    else:
        logger.warning(
            "Built SPA assets not found at %s; /assets will answer 404.",
            ASSETS_DIR,
        )


_mount_assets()


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    response_model=None,
    include_in_schema=False,
)
async def spa_catch_all(full_path: str) -> Response:
    """Serve the SPA shell for unknown non-API paths, 404 for unknown API paths.

    A deep link such as ``/review`` must survive a browser refresh, so any
    unknown non-API path returns ``index.html``. An unknown ``/api/*`` path
    must never look like a working route, so it returns the standard error
    shape instead of HTML. The same holds for ``/assets/*``: a missing file
    is a 404, never the HTML shell.
    """
    if full_path.split("/", 1)[0] in RESERVED_PREFIXES:
        return _not_found(full_path)
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE)
    return _not_found(full_path)
