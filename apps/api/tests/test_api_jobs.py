"""Route tests for ``GET /api/jobs/{job_id}`` and ``GET /api/health``.

The poll route reads ``data/jobs/{job_id}/job.json`` and returns it as a
validated :class:`~app.schemas.JobState`. Three outcomes are contractual:

* no record (or a job id that escapes the job tree) → 404 with the closed
  error envelope, code ``not_found``;
* a valid record → 200 with the record, round-tripped;
* a record that is not JSON or does not satisfy the schema → 500 with code
  ``internal_error``, never an unhandled traceback.

Why this file carries a tiny ASGI caller instead of ``TestClient``: in this
image ``starlette`` is 1.9.0, whose ``TestClient`` imports ``httpx2`` (falling
back to ``httpx``) and raises ``RuntimeError`` when neither is installed —
neither is, and adding one is a new dependency the stack does not allow. The
helper below drives the same ASGI application object over the ASGI protocol, so the
routes, the response models, the exception handlers and the error envelope are
all exercised for real, with no dependency added.

Every test is offline and writes only into a ``tmp_path``; nothing touches
``apps/api/data/``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import main
from app.schemas import HealthResponse, JobState


@dataclass(frozen=True)
class AsgiResponse:
    """The parts of an HTTP response these tests assert on."""

    status_code: int
    body: bytes

    def json(self) -> object:
        """Decode the body as JSON."""
        return json.loads(self.body)


async def _call_asgi(method: str, path: str) -> AsgiResponse:
    """Drive ``app`` over ASGI and collect the response.

    The first ``receive`` returns the request; every later one reports a
    disconnect. Without that second answer a handler that reads the body twice
    would await forever, which turns a broken route into a hanging test suite
    instead of a failing assertion.
    """
    messages: list[dict[str, object]] = []
    request_sent = False

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
    }

    async def receive() -> dict[str, object]:
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    await asyncio.wait_for(main.app(scope, receive, send), timeout=10)

    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"")  # type: ignore[arg-type]
        for m in messages
        if m["type"] == "http.response.body"
    )
    return AsgiResponse(status_code=int(start["status"]), body=body)  # type: ignore[arg-type]


def _get(path: str) -> AsgiResponse:
    """Perform a GET against the application."""
    return asyncio.run(_call_asgi("GET", path))


def _write_record(jobs_dir: Path, job_id: str, payload: object) -> None:
    """Write a job record, creating the job folder."""
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(json.dumps(payload), encoding="utf-8")


def _valid_job_state_payload(job_id: str = "job-1") -> dict[str, object]:
    """A fully valid ``JobState`` payload, as ``model_dump(mode="json")`` makes it."""
    state = JobState.model_validate(
        {
            "job_id": job_id,
            "status": "done",
            "stage": "done",
            "progress": {
                "photos_total": 6,
                "photos_done": 6,
                "products_total": 2,
                "products_cleaned": 2,
            },
            "products": [
                {
                    "sku": "KK-0001",
                    "job_id": job_id,
                    "source_photo_index": 0,
                    "concept": "smartphone",
                    "bbox": [10, 20, 110, 220],
                    "crop_url": f"/p/{job_id}/KK-0001-crop.png",
                    "clean_url": f"/p/{job_id}/KK-0001.png",
                    "cleanup_method": "muse-image",
                    "fidelity": {
                        "same_product": True,
                        "color_match": True,
                        "logo_text_match": True,
                        "reason": "unchanged",
                    },
                    "name": "Samsung Galaxy S23",
                    "description_id": "Ponsel bekas kondisi mulus.",
                    "description_en": "Used phone in great condition.",
                    "category": "Handphone & Tablet",
                    "brand": "Samsung",
                    "condition": "used",
                    "attributes": {"color": "black", "size": None, "variant": "8/256"},
                    "price_hint_idr": 4500000,
                    "price_suggestion_idr": 4700000,
                    "confidence": "high",
                    "price_idr": 4400000,
                    "price_via_chat": False,
                    "availability": "in stock",
                    "included": True,
                }
            ],
            "errors": [{"photo_index": 3, "stage": "segment", "code": "segmentation_failed"}],
            "created_at": "2026-10-26T14:00:00+07:00",
            "updated_at": "2026-10-26T14:02:00+07:00",
        }
    )
    return state.model_dump(mode="json")


@pytest.fixture
def jobs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``JOBS_DIR`` at a temporary directory.

    ``main._job_record_path`` reads the module global at call time, so
    monkeypatching the attribute redirects the route without restarting the
    app, and no test can write into ``apps/api/data/``.
    """
    target = tmp_path / "data" / "jobs"
    target.mkdir(parents=True)
    monkeypatch.setattr(main, "JOBS_DIR", target)
    return target


# --- 1. a missing record is a 404 with the closed envelope -----------------


def test_missing_job_record_is_not_found(jobs_dir: Path) -> None:
    """No directory, no record: 404 with code ``not_found``."""
    response = _get("/api/jobs/nope")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "not_found"
    assert "message" in body["error"]


# --- 2. a valid record round-trips ----------------------------------------


def test_valid_job_record_is_returned_and_round_trips(jobs_dir: Path) -> None:
    """A record written by the pipeline comes back as a validated ``JobState``."""
    payload = _valid_job_state_payload()
    _write_record(jobs_dir, "job-1", payload)

    response = _get("/api/jobs/job-1")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "done"
    assert body["stage"] == "done"
    assert len(body["products"]) == 1
    assert body["products"][0]["sku"] == "KK-0001"
    # The AI price guess stays on the poll payload; only the export body drops
    # it, and this asserts the poll payload still carries it.
    assert body["products"][0]["price_suggestion_idr"] == 4700000
    assert body["errors"] == [
        {"photo_index": 3, "stage": "segment", "code": "segmentation_failed"}
    ]
    # The response is what the schema says it is, not merely JSON-shaped.
    assert JobState.model_validate(body) == JobState.model_validate(payload)


# --- 3. a malformed record is a 500, not a traceback ----------------------


def test_malformed_job_record_is_internal_error(jobs_dir: Path) -> None:
    """A record missing required fields is a server defect, reported as such."""
    _write_record(jobs_dir, "broken", {"job_id": "broken"})

    response = _get("/api/jobs/broken")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_non_json_job_record_is_internal_error(jobs_dir: Path) -> None:
    """Unparseable JSON is the same defect and must not raise out of the route."""
    job_dir = jobs_dir / "garbage"
    job_dir.mkdir()
    (job_dir / "job.json").write_text("{not json", encoding="utf-8")

    response = _get("/api/jobs/garbage")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_record_with_an_unknown_key_is_internal_error(jobs_dir: Path) -> None:
    """``extra="forbid"`` applies to the record on disk, not only to the wire."""
    payload = _valid_job_state_payload()
    payload["surprise"] = "value"
    _write_record(jobs_dir, "job-1", payload)

    response = _get("/api/jobs/job-1")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


# --- 4. a job id cannot read outside the job tree -------------------------


def test_traversal_job_id_cannot_read_outside_the_job_tree(jobs_dir: Path) -> None:
    """``..`` resolves to a real record one level up; the route must still 404.

    ``jobs_dir`` is ``<tmp>/data/jobs``, so ``..`` points at ``<tmp>/data``. A
    valid record is planted exactly there: if the route resolved the path
    without a containment check, this test would return 200.
    """
    _write_record(jobs_dir.parent, "job.json", _valid_job_state_payload("escaped"))

    response = _get("/api/jobs/..")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# --- 5. health is a declared schema ---------------------------------------


def test_health_returns_the_declared_shape() -> None:
    """``GET /api/health`` answers ``{"ok": true, "mock": true}`` offline."""
    response = _get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "mock": True}
    assert HealthResponse.model_validate(body).mock is True
