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

The public product pages under ``/p/`` — ``GET /p/{job_id}/{sku}.jpg`` and
``GET /p/{job_id}/{sku}`` — are registered above the static mount, so the
catch-all never answers them with the SPA shell.

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

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError
from pydantic.json_schema import models_json_schema

from app import exporter
from app import imaging
from app import pipeline
from app import schemas
from app.exporter import PriceMissingError
from app.schemas import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    ExportRequest,
    ExportResponse,
    HealthResponse,
    JobProgress,
    JobStage,
    JobState,
    JobStatus,
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


def _read_multipart(
    body: bytes, content_type: str
) -> tuple[list[bytes], list[str], str | None]:
    """Split a ``multipart/form-data`` body into photos, names and captions.

    Standard library only, no extra dependency. This is pure CPU over the whole
    upload, so :func:`create_job` runs it in a worker thread; the function itself
    stays synchronous and testable. A body the parser cannot read raises, and the
    caller answers 400 without creating a job.
    """
    from email import policy
    from email.parser import BytesParser

    header_block = ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode()
    msg = BytesParser(policy=policy.default).parsebytes(header_block + body)

    photo_bytes: list[bytes] = []
    photo_names: list[str] = []
    captions_raw: str | None = None
    for part in (msg.get_payload() if msg.is_multipart() else []):
        if not hasattr(part, "get_param"):
            continue
        disp = part.get("Content-Disposition", "")
        name = None
        filename = None
        for token in disp.split(";"):
            token = token.strip()
            if token.startswith("name="):
                name = token[5:].strip().strip('"')
            elif token.startswith("filename="):
                filename = token[9:].strip().strip('"')
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        if name == "photos":
            photo_bytes.append(payload)
            # The name is what the seller recognises in a rejection message.
            photo_names.append(filename or f"photo {len(photo_bytes)}")
        elif name == "captions":
            captions_raw = payload.decode("utf-8")
    return photo_bytes, photo_names, captions_raw


@app.post(
    "/api/jobs",
    response_model=JobState,
    responses={400: {"model": ErrorResponse}},
)
async def create_job(
    request: Request,
    background_tasks: BackgroundTasks,
) -> JobState | JSONResponse:
    """Create a job and run the pipeline in the background."""
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type:
        return _error(400, ErrorCode.INVALID_REQUEST, "Expected multipart/form-data.")

    body = await request.body()
    try:
        # The parser walks every byte of the upload: ~300 ms on a 9 MB pack,
        # long enough to freeze the poll route if it ran on the event loop.
        photo_bytes, photo_names, captions_raw = await asyncio.to_thread(
            _read_multipart, body, content_type
        )
    except UnicodeDecodeError:
        return _error(400, ErrorCode.INVALID_REQUEST, "captions must be UTF-8.")
    except Exception:
        return _error(400, ErrorCode.INVALID_REQUEST, "Malformed multipart body.")

    if not photo_bytes:
        return _error(400, ErrorCode.INVALID_REQUEST, "At least one photo is required.")
    if len(photo_bytes) > 30:
        return _error(400, ErrorCode.INVALID_REQUEST, "Too many photos (max 30).")
    for data in photo_bytes:
        if not data:
            return _error(400, ErrorCode.INVALID_REQUEST, "Empty photo upload.")
        if len(data) > 10 * 1024 * 1024:
            return _error(400, ErrorCode.FILE_TOO_LARGE, "Photo exceeds 10 MB.")

    if captions_raw is None:
        return _error(400, ErrorCode.INVALID_REQUEST, "Missing captions field.")
    try:
        parsed = json.loads(captions_raw)
    except (json.JSONDecodeError, TypeError):
        return _error(400, ErrorCode.INVALID_REQUEST, "captions must be a JSON string.")
    if not isinstance(parsed, dict):
        return _error(400, ErrorCode.INVALID_REQUEST, "captions must be a JSON object.")
    general = parsed.get("general", "")
    photos_caps = parsed.get("photos", [])
    if not isinstance(general, str):
        general = str(general)
    if not isinstance(photos_caps, list):
        return _error(400, ErrorCode.INVALID_REQUEST, "captions.photos must be a list.")
    photos_caps = [str(x) for x in photos_caps]
    captions_payload = {"general": general, "photos": photos_caps}

    # The door: every upload is decoded, oriented and capped at MAX_UPLOAD_SIDE
    # once, here, before a job folder exists. An undecodable file rejects the
    # whole request — a job with one dead photo would spend minutes producing
    # a catalog nobody can ship. The decode is Pillow work, so it runs off the
    # event loop like every other CPU-bound call in this service.
    normalised = await asyncio.gather(
        *(asyncio.to_thread(imaging.normalise_upload, data) for data in photo_bytes),
        return_exceptions=True,
    )
    photos_of_record: list[bytes] = []
    for index, result in enumerate(normalised):
        if isinstance(result, BaseException):
            return _error(
                400,
                ErrorCode.INVALID_REQUEST,
                f"Photo {index + 1} ({photo_names[index]}) is not a decodable image.",
            )
        photos_of_record.append(result)
    photo_bytes = photos_of_record

    job_id = f"job-{uuid.uuid4().hex[:12]}"
    from datetime import datetime as _dt, timezone as _tz

    now = _dt.now(_tz.utc)
    initial = JobState(
        job_id=job_id,
        status=JobStatus.QUEUED,
        stage=JobStage.CONCEPTS,
        progress=JobProgress(
            photos_total=len(photo_bytes),
            photos_done=0,
            products_total=0,
            products_cleaned=0,
        ),
        products=[],
        errors=[],
        created_at=now,
        updated_at=now,
    )
    pipeline.write_job_record(job_id, initial)

    background_tasks.add_task(pipeline.run_pipeline, job_id, photo_bytes, captions_payload)

    return initial


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


# --- retry / toggle cleanup --------------------------------------------------

def _safe_job_and_sku(job_id: str, sku: str) -> bool:
    if not exporter._safe_component(job_id):
        return False
    if not exporter._safe_component(sku):
        return False
    return True


@app.post(
    "/api/jobs/{job_id}/products/{sku}/retry-cleanup",
    response_model=JobState,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def retry_cleanup_job(job_id: str, sku: str) -> JobState | JSONResponse:
    if not _safe_job_and_sku(job_id, sku):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid path component.")
    try:
        state = await pipeline.retry_cleanup_product(job_id, sku)
    except FileNotFoundError:
        return _error(404, ErrorCode.NOT_FOUND, f"No job record for {job_id!r}.")
    except KeyError:
        return _error(404, ErrorCode.NOT_FOUND, f"No product {sku!r} in job {job_id!r}.")
    except Exception as exc:
        logger.error("retry-cleanup failed for %r/%r: %s", job_id, sku, exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Retry cleanup failed.")
    return state


@app.post(
    "/api/jobs/{job_id}/products/{sku}/toggle-cleanup",
    response_model=JobState,
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def toggle_cleanup_job(job_id: str, sku: str) -> JobState | JSONResponse:
    if not _safe_job_and_sku(job_id, sku):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid path component.")
    try:
        state = await pipeline.toggle_cleanup_product(job_id, sku)
    except FileNotFoundError:
        return _error(404, ErrorCode.NOT_FOUND, f"No job record for {job_id!r}.")
    except KeyError:
        return _error(404, ErrorCode.NOT_FOUND, f"No product {sku!r} in job {job_id!r}.")
    except Exception as exc:
        logger.error("toggle-cleanup failed for %r/%r: %s", job_id, sku, exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Toggle cleanup failed.")
    return state


# --- export + public product routes -----------------------------------------

import re as _re
import html as _html
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
        # Building four files is Pillow + zip work over the whole catalog: it
        # runs off the event loop so a download never freezes the poll route.
        files = await asyncio.to_thread(
            exporter.run_export, job_id, body.products, body.seller, settings.base_url
        )
    except PriceMissingError as exc:
        return _error(
            400,
            ErrorCode.PRICE_MISSING,
            f"Missing price for included items: {', '.join(exc.skus)}.",
        )
    except exporter.UngatedImageError as exc:
        # A record claims a generative cleanup without a
        # gate pass — an upstream pipeline defect, surfaced, not papered over.
        logger.error(
            "Export blocked for %r: ungated generative image for %s",
            job_id,
            ", ".join(exc.skus),
        )
        return _error(500, ErrorCode.EXPORT_FAILED, "Export blocked: ungated generative image.")
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
        # Fall back to a deterministic placeholder so a missing on-disk
        # artefact never surfaces as a broken image in Review.
        data = exporter._placeholder_jpg(sku, job_id, crop=False)
    except Exception as exc:
        logger.error("Image resolve failed for %r/%r: %s", job_id, sku, exc)
        return _error(500, ErrorCode.INTERNAL_ERROR, "Image unavailable.")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})


# --- public product page ----------------------------------------------------
# The page a buyer opens from a shared link: one self-contained document, one
# inline stylesheet, no script, no font, no image host, no request of any kind
# beyond the product JPEGs. Every interpolated value goes through :func:`_esc`
# because the export record carries seller-authored text (name, description,
# store name) and this page is public.

# The palette mirrors apps/web/src/styles/tokens.css. ``--accent`` is spent on
# the primary button and on the focus ring, nothing else.
_PRODUCT_PAGE_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{color-scheme:light;--bg:#fff;--surf:#f3f4f7;--ink:#111218;--ink2:#505560;--muted:#5a5f6b;--line:#e6e8ee;--accent:#4f46ef;--on-accent:#fff}
@media (prefers-color-scheme:dark){:root{color-scheme:dark;--bg:#0e0f13;--surf:#1a1c22;--ink:#f5f5f8;--ink2:#c4c6d0;--muted:#b4b6c1;--line:#343741;--accent:#a6a1ff;--on-accent:#111125}}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:400 15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:480px;margin:0 auto;padding:16px 16px calc(96px + env(safe-area-inset-bottom))}
.hero{position:relative;margin:0 0 16px}
.track{display:flex;gap:8px;overflow-x:auto;scroll-snap-type:x mandatory;border-radius:20px;scrollbar-width:none;-webkit-overflow-scrolling:touch}
.track::-webkit-scrollbar{display:none}
.track:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.slide{position:relative;flex:0 0 100%;scroll-snap-align:start;aspect-ratio:1/1;border-radius:20px;overflow:hidden;background:var(--surf)}
.slide::after{content:"";position:absolute;inset:0;border-radius:20px;box-shadow:inset 0 0 0 1px var(--line);pointer-events:none}
.slide img{display:block;width:100%;height:100%;object-fit:contain}
.count{position:absolute;right:10px;bottom:10px;padding:3px 9px;border-radius:999px;background:rgba(17,18,24,.7);color:#fff;font-size:11px;font-weight:600;letter-spacing:.02em}
.chip{display:inline-block;max-width:100%;margin:0 0 6px;padding:4px 10px;border-radius:999px;background:var(--surf);color:var(--ink2);font-size:12px;font-weight:600;overflow-wrap:anywhere}
h1{margin:0 0 8px;font-size:22px;line-height:1.25;font-weight:800;letter-spacing:-.02em;overflow-wrap:anywhere}
.price{margin:0;font-size:26px;line-height:1.2;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.price-chat{font-size:17px;font-weight:700;color:var(--ink2)}
.desc{margin:16px 0 0;color:var(--ink2);line-height:1.6;white-space:pre-line;overflow-wrap:anywhere}
.facts{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0 0;padding:0;list-style:none}
.facts li{padding:5px 10px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:12px;font-weight:600;overflow-wrap:anywhere}
.foot{margin:24px 0 0;color:var(--muted);font-size:12px;text-align:center}
.bar{position:fixed;left:0;right:0;bottom:0;border-top:1px solid var(--line);background:var(--bg);padding:10px 16px calc(10px + env(safe-area-inset-bottom))}
.bar-in{max-width:480px;margin:0 auto}
.cta,.quiet{display:flex;align-items:center;justify-content:center;height:52px;border-radius:14px;font-size:16px;font-weight:700}
.cta{background:var(--accent);color:var(--on-accent);text-decoration:none}
.cta:active{opacity:.85}
.cta:focus-visible{outline:2px solid var(--ink);outline-offset:2px}
.quiet{margin:0;background:var(--surf);color:var(--muted);font-weight:600}
.empty{margin:24px 0 0;padding:28px 20px;border-radius:20px;background:var(--surf);text-align:center}
.wrap-empty{display:flex;flex-direction:column;justify-content:center;min-height:100vh;padding-bottom:16px}
.wrap-empty .empty{margin:0}
.empty-sku{margin:0 0 8px;color:var(--muted);font-size:12px;font-weight:700;letter-spacing:.06em}
.empty h1{margin:0;font-size:20px}
.empty-note{margin:8px 0 0;color:var(--ink2);font-size:14px}
"""


def _esc(value: object) -> str:
    """Escape one interpolated value for HTML text or a quoted attribute."""
    return _html.escape(str(value), quote=True)


def _page_head(
    *,
    title: str,
    description: str,
    page_url: str,
    site_name: str,
    image_url: str | None,
) -> str:
    """Render the document head: link-preview metadata, then the inline sheet.

    ``og:image`` is emitted only when there is a real artefact to point at, and
    it is absolute by construction — a relative URL previews as nothing.
    """
    image = f'<meta property="og:image" content="{_esc(image_url)}">\n' if image_url else ""
    return (
        '<!doctype html>\n<html lang="id">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f"<title>{_esc(title)}</title>\n"
        '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme:light)">\n'
        '<meta name="theme-color" content="#0e0f13" media="(prefers-color-scheme:dark)">\n'
        f'<meta name="description" content="{_esc(description)}">\n'
        '<meta property="og:type" content="product">\n'
        f'<meta property="og:site_name" content="{_esc(site_name)}">\n'
        f'<meta property="og:title" content="{_esc(title)}">\n'
        f'<meta property="og:description" content="{_esc(description)}">\n'
        f'<meta property="og:url" content="{_esc(page_url)}">\n'
        f"{image}"
        f"<style>{_PRODUCT_PAGE_CSS}</style>\n"
        "</head>\n"
    )


def _product_page_html(
    *,
    job_id: str,
    sku: str,
    name: str,
    price_text: str,
    price_is_text: bool,
    store_name: str,
    description: str,
    facts: list[str],
    image_urls: list[str],
    wa_url: str | None,
    base: str,
) -> str:
    """Render the storefront page for one found product.

    ``image_urls[0]`` is the primary shot (the promoted angle when the seller
    promoted one); the rest are merged angles and ride in the same scroll-snap
    track, so a swipe walks every photo the seller merged.
    """
    page_url = f"{base}/p/{job_id}/{sku}"
    site_name = store_name or "Katalog Kilat"
    title = f"{name} · {store_name}" if store_name else f"{name} · Katalog Kilat"
    og_description = f"{price_text} · {store_name}" if store_name else price_text

    slides: list[str] = []
    for index, url in enumerate(image_urls):
        alt = name if index == 0 else f"{name} — foto {index + 1}"
        lazy = " loading='lazy'" if index else ""
        slides.append(
            f'<div class="slide"><img src="{_esc(url)}" alt="{_esc(alt)}"{lazy} decoding="async"></div>'
        )
    slides_html = "".join(slides)
    track_attrs = ' tabindex="0" aria-label="Galeri foto produk"' if len(image_urls) > 1 else ""
    count = (
        f'<span class="count">{len(image_urls)} foto</span>' if len(image_urls) > 1 else ""
    )
    chip = f'<span class="chip">{_esc(store_name)}</span>\n' if store_name else ""
    description_html = f'<p class="desc">{_esc(description)}</p>\n' if description else ""
    facts_items = "".join(f"<li>{_esc(fact)}</li>" for fact in facts)
    facts_html = f'<ul class="facts">{facts_items}</ul>\n' if facts_items else ""
    price_class = "price price-chat" if price_is_text else "price"
    if wa_url:
        action = (
            f'<a class="cta" href="{_esc(wa_url)}" rel="noopener" target="_blank">'
            "Pesan via WhatsApp</a>"
        )
    else:
        action = '<p class="quiet">Hubungi toko</p>'

    return (
        _page_head(
            title=title,
            description=og_description,
            page_url=page_url,
            site_name=site_name,
            image_url=image_urls[0],
        )
        + "<body>\n"
        f'<main class="wrap" data-sku="{_esc(sku)}">\n'
        '<div class="hero">\n'
        f'<div class="track"{track_attrs}>{slides_html}</div>\n'
        f"{count}\n"
        "</div>\n"
        f"{chip}"
        f"<h1>{_esc(name)}</h1>\n"
        f'<p class="{price_class}">{_esc(price_text)}</p>\n'
        f"{description_html}"
        f"{facts_html}"
        '<p class="foot">Dibuat dengan Katalog Kilat</p>\n'
        "</main>\n"
        f'<div class="bar"><div class="bar-in">{action}</div></div>\n'
        "</body>\n</html>\n"
    )


def _missing_product_page_html(*, job_id: str, sku: str, base: str) -> str:
    """Render the graceful page for a SKU the export record does not carry.

    A shared link outlives the job it pointed at. The page says so in a styled
    card, names the SKU the visitor was given, and offers no action it cannot
    honour — no invented number, no fabricated price.
    """
    note = "Tautan ini mungkin sudah kedaluwarsa, atau produknya sudah tidak dijual lagi."
    return (
        _page_head(
            title=f"{sku} · Katalog Kilat",
            description="Produk belum tersedia.",
            page_url=f"{base}/p/{job_id}/{sku}",
            site_name="Katalog Kilat",
            image_url=None,
        )
        + "<body>\n"
        f'<main class="wrap wrap-empty" data-sku="{_esc(sku)}">\n'
        '<div class="empty">\n'
        f'<p class="empty-sku">{_esc(sku)}</p>\n'
        "<h1>Produk belum tersedia</h1>\n"
        f'<p class="empty-note">{_esc(note)}</p>\n'
        "</div>\n"
        '<p class="foot">Dibuat dengan Katalog Kilat</p>\n'
        "</main>\n"
        "</body>\n</html>\n"
    )


@app.get("/p/{job_id}/{sku}")
async def get_product_page(job_id: str, sku: str) -> Response:
    if not exporter._safe_component(job_id) or not _validate_sku(sku):
        return _error(400, ErrorCode.INVALID_REQUEST, f"Invalid path component.")
    base = settings.base_url.rstrip("/")
    base_sku = sku.replace("-crop", "")
    record = exporter.read_export_record(job_id)
    img_sku = sku
    product: dict[str, Any] | None = None
    extra_image_urls: list[str] = []
    seller: dict[str, Any] = {}
    if record:
        record_products = record.get("products", [])
        for prod in record_products:
            if prod.get("sku") == sku or prod.get("sku") == base_sku:
                product = prod
                # A promoted angle photo names its image in clean_url, not in sku.
                m = exporter._SKU_IN_URL_RE.search(str(prod.get("clean_url") or ""))
                if m:
                    img_sku = m.group(1)
                break
        # Merged angle photos: excluded children whose concept targets this SKU
        # (``{concept}@merge:{parent_sku}``). The buyer sees every angle the
        # seller merged, not just the primary shot.
        for prod in record_products:
            if prod.get("included", True):
                continue
            child_sku = prod.get("sku")
            concept = str(prod.get("concept") or "")
            if not child_sku or child_sku == base_sku:
                continue
            if concept.endswith(f"{exporter.MERGE_MARKER}{base_sku}"):
                m = exporter._SKU_IN_URL_RE.search(str(prod.get("clean_url") or ""))
                child_img_sku = m.group(1) if m else child_sku
                extra_image_urls.append(f"{base}/p/{job_id}/{child_img_sku}.jpg")
        raw_seller = record.get("seller")
        seller = raw_seller if isinstance(raw_seller, dict) else {}

    if product is None:
        # A job or SKU with no export record is a missing page, not a live one:
        # crawlers, link previews and a stale shared link must read the same
        # answer. The body stays the styled "Produk belum tersedia" document,
        # and it is never cached, so a later export makes the link work at once.
        return Response(
            content=_missing_product_page_html(job_id=job_id, sku=sku, base=base),
            status_code=404,
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    name = str(product.get("name") or sku)
    price_idr = product.get("price_idr")
    via_chat = bool(product.get("price_via_chat", False))
    # Price Law: the seller's confirmed value, or the words "harga via chat".
    # A hint, a suggestion and a zero never reach this page.
    if via_chat or price_idr is None:
        price_text = "harga via chat"
    else:
        price_text = exporter.format_price_display(int(price_idr))
    store_name = str(seller.get("store_name") or "").strip()
    wa_number = str(seller.get("wa_number") or "").strip()
    wa_url = None
    if wa_number:
        text = f"Halo, saya mau tanya {name} ({sku})"
        wa_url = f"https://wa.me/{wa_number}?text=" + _urlparse.quote(text, safe="")
    attributes = product.get("attributes")
    attributes = attributes if isinstance(attributes, dict) else {}
    condition = {"new": "Baru", "used": "Bekas"}.get(str(product.get("condition") or ""))
    candidates = (
        product.get("category"),
        product.get("brand"),
        attributes.get("color"),
        attributes.get("size"),
        attributes.get("variant"),
        condition,
        "Stok habis" if product.get("availability") == "out of stock" else None,
    )
    facts = [str(value).strip() for value in candidates if value and str(value).strip()]
    # Price Law: the prose is stripped of any price sentence, so the page
    # shows the seller's confirmed value and nothing else. An empty result
    # omits the description block rather than rendering a blank paragraph.
    description = exporter.description_text(
        str(product.get("description_id") or ""),
        str(product.get("description_en") or ""),
    )
    html = _product_page_html(
        job_id=job_id,
        sku=sku,
        name=name,
        price_text=price_text,
        price_is_text=via_chat or price_idr is None,
        store_name=store_name,
        description=description,
        facts=facts,
        image_urls=[f"{base}/p/{job_id}/{img_sku}.jpg", *extra_image_urls],
        wa_url=wa_url,
        base=base,
    )
    return Response(content=html, media_type="text/html; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})


# --- static mount and SPA catch-all -----------------------------------------
# Everything below is registered after the API routes above. Do not move it.

# Path prefixes the catch-all must never answer with the HTML shell: an
# unknown API path is a missing route, an unknown asset path a missing file.
RESERVED_PREFIXES = ("api", "assets", "samples")


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
    if (STATIC_DIR / "samples").is_dir():
        app.mount(
            "/samples",
            StaticFiles(directory=STATIC_DIR / "samples", check_dir=False),
            name="samples",
        )
    if not STATIC_DIR.is_dir():
        logger.warning(
            "Built SPA not found at %s; only the API routes are served. "
            "Run the web build to produce it.",
            STATIC_DIR,
        )
    elif not ASSETS_DIR.is_dir():
        logger.warning(
            "Built SPA assets not found at %s; /assets will answer 404.",
            ASSETS_DIR,
        )
    if STATIC_DIR.is_dir() and not (STATIC_DIR / "samples").is_dir():
        logger.warning(
            "Sample assets not found at %s; /samples will answer 404.",
            STATIC_DIR / "samples",
        )


_mount_assets()


def _root_static_file(full_path: str) -> Path | None:
    """Return the build-root file named by ``full_path``, or ``None``.

    Only a single path segment qualifies. The candidate is resolved and must
    sit directly in the resolved ``STATIC_DIR`` (no traversal, no
    subdirectory, no symlink pointing elsewhere) and be a regular file.
    ``index.html`` is left to the shell fallback.
    """
    if not full_path or "/" in full_path or full_path == INDEX_FILE.name:
        return None
    try:
        candidate = (STATIC_DIR / full_path).resolve()
        if candidate.parent != STATIC_DIR.resolve() or not candidate.is_file():
            return None
    except (OSError, ValueError):
        return None
    return candidate


@app.api_route(
    "/{full_path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    response_model=None,
    include_in_schema=False,
)
async def spa_catch_all(full_path: str, request: Request) -> Response:
    """Serve the SPA shell for unknown non-API paths, 404 for unknown API paths.

    A deep link such as ``/review`` must survive a browser refresh, so any
    unknown non-API path returns ``index.html``. An unknown ``/api/*`` path
    must never look like a working route, so it returns the standard error
    shape instead of HTML. The same holds for ``/assets/*``: a missing file
    is a 404, never the HTML shell.

    A single-segment path naming a regular file directly inside the build
    directory (``/favicon.svg``) is served as that file, for GET and HEAD only.
    """
    if full_path.split("/", 1)[0] in RESERVED_PREFIXES:
        return _not_found(full_path)
    if request.method in ("GET", "HEAD"):
        static_file = _root_static_file(full_path)
        if static_file is not None:
            return FileResponse(static_file)
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE)
    return _not_found(full_path)
