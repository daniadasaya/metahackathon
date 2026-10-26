"""FastAPI application for Katalog Kilat.

Scope of this module: the application object, ``GET /api/health``, the
``GET /api/jobs/{job_id}`` poll route, the static mount that serves the built
SPA assets, and the SPA catch-all that returns ``index.html`` for unknown
non-API paths so a deep link survives a refresh.

The poll route reads the job record the pipeline writes at
``data/jobs/{job_id}/job.json`` and returns it as a validated
:class:`~app.schemas.JobState`. It owns the record's *shape* (via the schema)
but not its *content*: the pipeline writes it, this module only reads it.

What this module deliberately does not do: it does not own the catalog, the
pipeline, the exporter, or any Meta call. The public ``/p/`` product pages are
registered here, but they read what the pipeline and the exporter produced.

Registration order is binding: every API route is registered first, then the
static mount, then the catch-all last.
Reversing that order makes the catch-all swallow every ``/api/*`` request and
answer 404. The catch-all therefore refuses to serve HTML for ``/api/*`` and
for ``/assets/*``.

The public product pages under ``/p/`` are not here yet — the pipeline owns them. Until
then the catch-all answers them with the SPA shell, which is a known and
accepted limitation (spec).

This module also publishes the **complete** contract into
``components.schemas``. A pydantic model reaches that map only when a
route references it, so a model like
:class:`~app.schemas.ReviewedProduct` — the export body the review screen
posts — would be missing from the document that ``apps/web/src/lib/types.ts``
is generated from, and the client would have to hand-write the type, which the
type-contract invariant forbids. The override installed below therefore merges
every schema declared in :mod:`app.schemas` into the route-derived document,
using :func:`pydantic.json_schema.models_json_schema` so a model shared by
several others is emitted once and referenced by ``$ref`` instead of copied
into each parent. A schema a route already published is left untouched: the
route-derived entry is authoritative, and this step only fills gaps. It
publishes a contract — it adds no route, no handler, and no behaviour, so it
is not stub code. Retire the override once every route references every model,
at which point the merge becomes a no-op by construction.

The module never crashes at import time when the built SPA is missing: a
developer running bare ``uvicorn`` still gets a working ``/api/health``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from pydantic.json_schema import models_json_schema

from app import schemas
from app import exporter
from app.exporter import PriceMissingError
from app.schemas import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ExportRequest,
    ExportResponse,
    HealthResponse,
    JobState,
    SellerInfo,
)
from app.settings import settings

logger = logging.getLogger(__name__)

# The built SPA sits next to the application package: /srv/app and /srv/static
# in the image, apps/api/app and apps/api/static in a local checkout. The path
# is derived from this file, never hardcoded.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ASSETS_DIR = STATIC_DIR / "assets"
INDEX_FILE = STATIC_DIR / "index.html"

# Job records live one directory above the application package: /srv/data/jobs
# in the image, apps/api/data/jobs in a checkout.
# Derived from this file's location so both layouts resolve without a setting.
JOBS_DIR = Path(__file__).resolve().parent.parent / "data" / "jobs"
JOB_RECORD_FILENAME = "job.json"
EXPORTS_DIR = exporter.EXPORTS_DIR

app = FastAPI(title="Katalog Kilat API")

# --- complete-contract publication -----------------------------------------
# A pydantic model enters ``components.schemas`` only when a route references
# it, so the document that ``apps/web/src/lib/types.ts`` is
# generated from would otherwise omit the export body and the stage payloads.
# The merge below publishes every schema declared in :mod:`app.schemas` and
# adds no route, no handler, and no behaviour.

# Every reference the published schemas emit points at the OpenAPI component
# map, never at pydantic's ``$defs`` container: a ``#/$defs/...`` reference
# resolves to nothing in an OpenAPI document and the generated TypeScript
# would carry an unresolved alias.
SCHEMA_REF_TEMPLATE = "#/components/schemas/{model}"

# The bound method is captured before the override is installed, so the
# override can call the real route-derived builder instead of recursing into
# itself. ``FastAPI.openapi()`` memoizes its result in ``app.openapi_schema``,
# so this is the document FastAPI itself builds and caches.
_openapi_from_routes = app.openapi


def _declared_models() -> list[type[BaseModel]]:
    """Every pydantic model declared in :mod:`app.schemas`, sorted by name.

    Filtering by ``__module__`` keeps the imported ``BaseModel`` base class
    out. The sort is not cosmetic: the document is dumped to a committed file
    and diffed byte for byte, so an iteration order that varied between
    processes would turn the drift test red at random.
    """
    return sorted(
        (
            value
            for value in vars(schemas).values()
            if isinstance(value, type)
            and issubclass(value, BaseModel)
            and value.__module__ == schemas.__name__
        ),
        key=lambda model: model.__name__,
    )


def _published_schemas() -> dict[str, Any]:
    """Build the component map for every schema declared in :mod:`app.schemas`.

    ``models_json_schema`` walks all models in one pass, so a model shared by
    several parents (``ProductAttributes``, ``FidelityVerdict``,
    ``ErrorDetail``) is emitted once and referenced by ``$ref`` from each
    parent rather than copied into every one of them. The enums a model uses
    arrive in the same pass — ``ExportKind`` included, which is why it is
    published here even though no route carries it yet.
    """
    _mapping, container = models_json_schema(
        [(model, "validation") for model in _declared_models()],
        ref_template=SCHEMA_REF_TEMPLATE,
    )
    # ``models_json_schema`` returns a bare JSON Schema document, so its
    # definitions arrive wrapped in a ``$defs`` container. Only the contents
    # belong in ``components.schemas``; a ``$defs`` key there would make the
    # document invalid OpenAPI.
    nested = container.get("$defs")
    if not isinstance(nested, dict):
        raise RuntimeError(
            "models_json_schema returned no $defs mapping; the complete "
            "OpenAPI contract cannot be published."
        )
    return dict(nested)


def _openapi_with_full_contract() -> dict[str, Any]:
    """Publish the complete contract on top of the route-derived document.

    A schema a route already published is left exactly as FastAPI built it:
    the route-derived entry is authoritative, and this step only fills gaps.
    Iterating in sorted order keeps the merged map stable across processes.
    """
    document = _openapi_from_routes()
    components = document.setdefault("components", {}).setdefault("schemas", {})
    published = _published_schemas()
    for name in sorted(published):
        if name in components:
            continue
        components[name] = published[name]
    return document


app.openapi = _openapi_with_full_contract



@app.exception_handler(RequestValidationError)
async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return _error(400, ErrorCode.INVALID_REQUEST, "Request body failed validation.")


def _error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    """Return the standard error envelope for a failing request.

    ``code`` is the contract the UI translates; ``message`` is English and
    developer-facing, never rendered.
    """
    payload = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))


def _job_record_path(job_id: str) -> Path | None:
    """Resolve ``job_id`` to a record path, or ``None`` if it escapes the root.

    The job id is used as a path segment, so it is resolved and checked against
    ``JOBS_DIR`` rather than trusted: a traversal attempt is a missing record
    (404), never a read outside the job tree and never a 500.
    """
    root = JOBS_DIR.resolve()
    candidate = (JOBS_DIR / job_id / JOB_RECORD_FILENAME).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report process liveness and whether the service runs in mock mode."""
    return HealthResponse(ok=True, mock=settings.meta_mock)


@app.get(
    "/api/jobs/{job_id}",
    response_model=JobState,
    responses={
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
async def get_job(job_id: str) -> JobState | JSONResponse:
    """Return the job record for ``job_id`` as a validated :class:`JobState`.

    A missing directory, a missing record, or a job id that resolves outside
    the job tree is a 404 with the standard envelope. A record that is not
    valid JSON, or does not satisfy the schema, is a 500: the pipeline wrote
    something this contract cannot describe, which is a server-side defect and
    must not escape as an unhandled traceback.
    """
    record_path = _job_record_path(job_id)
    if record_path is None or not record_path.is_file():
        return _error(
            404,
            ErrorCode.NOT_FOUND,
            f"No job record for {job_id!r}.",
        )

    try:
        raw = record_path.read_text(encoding="utf-8")
        return JobState.model_validate(json.loads(raw))
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        # Logged with the job id only: the record may carry seller data, and
        # the response never repeats it.
        logger.error("Unreadable job record for %r: %s", job_id, exc)
        return _error(
            500,
            ErrorCode.INTERNAL_ERROR,
            f"Job record for {job_id!r} is unreadable or does not match the contract.",
        )


# --- export + public product routes -----------------------------------------

import re as _re
import urllib.parse as _urlparse

_MOCK_JOB_RE = _re.compile(r"^mock-\d+-\d+$")


def _exports_record_path(job_id: str, filename: str) -> Path | None:
    root = EXPORTS_DIR.resolve()
    candidate = (EXPORTS_DIR / job_id / filename).resolve()
    if root not in candidate.parents and candidate != root:
        # candidate must be inside exports root
        if not str(candidate).startswith(str(root) + "/"):
            return None
    # containment check
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


@app.post(
    "/api/jobs/{job_id}/export",
    response_model=ExportResponse,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def export_job(job_id: str, body: ExportRequest) -> ExportResponse | JSONResponse:
    if not exporter._safe_component(job_id):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid job id {job_id!r}.")
    if not _MOCK_JOB_RE.fullmatch(job_id):
        record_path = _job_record_path(job_id)
        if record_path is None or not record_path.is_file():
            return _error(404, ErrorCode.NOT_FOUND, f"No job record for {job_id!r}.")
    try:
        files = exporter.run_export(job_id, body.products, body.seller, settings.base_url)
    except PriceMissingError as exc:
        return _error(
            400,
            ErrorCode.PRICE_MISSING,
            f"Missing price for included items: {', '.join(exc.skus)}.",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("Export failed for %r: %s", job_id, exc)
        return _error(500, ErrorCode.EXPORT_FAILED, "Export failed.")
    return ExportResponse(job_id=job_id, files=files)


@app.get(
    "/api/exports/{job_id}/{filename}",
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_export_file(job_id: str, filename: str) -> Response:
    if not exporter._safe_component(job_id) or not exporter._safe_component(filename):
        return _error(400, ErrorCode.INVALID_REQUEST, "Invalid path component.")
    path = _exports_record_path(job_id, filename)
    if path is None or not path.is_file():
        return _error(404, ErrorCode.NOT_FOUND, f"No export file {filename!r}.")
    content_type = "text/csv"
    if filename.endswith(".zip"):
        content_type = "application/zip"
    elif filename.endswith(".txt"):
        content_type = "text/plain; charset=utf-8"
    data = path.read_bytes()
    return Response(content=data, media_type=content_type, headers={"Cache-Control": "public, max-age=3600"})


def _validate_sku(sku: str) -> bool:
    if not exporter._safe_component(sku):
        return False
    # Allow KK-0001 and KK-0001-crop
    if not _re.fullmatch(r"KK-\d{4}(-crop)?", sku):
        return False
    return True


@app.get("/p/{job_id}/{sku}.jpg")
async def get_product_image(job_id: str, sku: str) -> Response:
    if not exporter._safe_component(job_id) or not _validate_sku(sku):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid path component.")
    try:
        data = exporter.resolve_image_bytes(job_id, sku)
    except FileNotFoundError:
        return _error(404, ErrorCode.NOT_FOUND, f"No image for {sku!r}.")
    except Exception as exc:
        logger.error("Image resolve failed for %r/%r: %s", job_id, sku, exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Image unavailable.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/p/{job_id}/{sku}")
async def get_product_page(job_id: str, sku: str) -> Response:
    if not exporter._safe_component(job_id) or not _validate_sku(sku):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid path component.")
    base = settings.base_url.rstrip("/")
    img_url = f"{base}/p/{job_id}/{sku}.jpg"
    record = exporter.read_export_record(job_id)
    name = sku
    price_text = ""
    wa_number = None
    via_chat = False
    price_idr = None
    if record:
        for prod in record.get("products", []):
            if prod.get("sku") == sku or prod.get("sku") == sku.replace("-crop", ""):
                name = prod.get("name", name)
                price_idr = prod.get("price_idr")
                via_chat = bool(prod.get("price_via_chat", False))
                break
        seller = record.get("seller") or {}
        wa_number = seller.get("wa_number")
    if via_chat or price_idr is None:
        price_text = "harga via chat"
    else:
        price_text = exporter.format_price_display(int(price_idr))
    # WhatsApp button
    if wa_number:
        text = f"Halo, saya mau tanya {name} ({sku})"
        wa_url = f"https://wa.me/{wa_number}?text=" + _urlparse.quote(text, safe="")
        button = f'<a href="{wa_url}">WhatsApp</a>'
    else:
        button = "<p>hubungi toko</p>"
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{name}</title></head><body>"
        f'<img src="{img_url}" alt="{name}" width="256" />'
        f"<h1>{name}</h1>"
        f"<p>{price_text}</p>"
        f"{button}"
        "</body></html>"
    )
    return Response(content=html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


# --- static mount and SPA catch-all -----------------------------------------
# Everything below is registered after the API routes above. Do not move it.

# Path prefixes the catch-all must never answer with the HTML shell: an
# unknown API path is a missing route, an unknown asset path a missing file.
RESERVED_PREFIXES = ("api", "assets")


def _not_found(path: str) -> JSONResponse:
    """Return the standard error shape for a path that matches no route."""
    return _error(404, ErrorCode.NOT_FOUND, f"No route matches {path!r}.")


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
