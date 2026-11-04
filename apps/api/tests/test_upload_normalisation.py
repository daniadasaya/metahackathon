"""Upload normalisation tests (offline, no network).

A photo is decoded once, at the door, and what the door stores is the photo of
record: every later stage — the SAM masks, the crops, the cut-outs, the gate
references — works in those pixels, so the frame is decided here and nowhere
else.

Three behaviours are pinned:

* a photo larger than :data:`imaging.MAX_UPLOAD_SIDE` is downscaled, never
  upscaled, and keeps its aspect ratio;
* EXIF orientation is applied, so a portrait phone photo is not stored sideways;
* a file Pillow cannot decode rejects the whole request before a job folder
  exists, naming the offending photo.

A JPEG that already fits and carries no rotation is returned untouched, byte for
byte — that is what keeps every recorded fixture and golden image valid.
"""

from __future__ import annotations

import asyncio
import io
import json

import pytest
from fastapi import BackgroundTasks
from PIL import Image
from starlette.requests import Request

from app import imaging, main, pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _jpeg(size: tuple[int, int] = (800, 600), color=(90, 140, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _png(size: tuple[int, int] = (120, 90), color=(200, 40, 40)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _other(fmt: str, size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 200, 10)).save(buf, format=fmt)
    return buf.getvalue()


def _decode(data: bytes) -> tuple[Image.Image, str]:
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        return im.copy(), (im.format or "")


def _jobs_root(tmp_path, monkeypatch) -> "object":
    """Point the pipeline's data tree at a temporary directory."""
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "data" / "jobs")
    return tmp_path / "data" / "jobs"


def _multipart(photos: list[tuple[str, bytes]], captions: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data body the way the browser posts it."""
    boundary = "----kktestboundary"
    chunks: list[bytes] = []
    for filename, data in photos:
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="photos"; filename="{filename}"\r\n'
                "Content-Type: image/jpeg\r\n\r\n"
            ).encode()
            + data
            + b"\r\n"
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="captions"\r\n\r\n'
        ).encode()
        + json.dumps(captions).encode()
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _request(body: bytes, content_type: str) -> Request:
    """A starlette Request carrying ``body`` as its entire payload."""
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/jobs",
        "raw_path": b"/api/jobs",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


def _create_job(photos: list[tuple[str, bytes]], captions: dict | None = None):
    body, content_type = _multipart(photos, captions or {"general": "", "photos": []})
    tasks = BackgroundTasks()
    result = asyncio.run(main.create_job(_request(body, content_type), tasks))
    return result, tasks


# ---------------------------------------------------------------------------
# The normaliser itself
# ---------------------------------------------------------------------------


def test_an_oversize_photo_is_downscaled_to_the_cap() -> None:
    photo = _jpeg((3200, 1800))

    out = imaging.normalise_upload(photo)
    decoded, fmt = _decode(out)

    assert fmt == "JPEG"
    assert max(decoded.size) == imaging.MAX_UPLOAD_SIDE
    assert decoded.size == (2400, 1350)  # aspect ratio preserved, no rounding drift
    assert len(out) < len(photo)


def test_a_portrait_oversize_photo_keeps_its_orientation() -> None:
    out = imaging.normalise_upload(_jpeg((1800, 3200)))
    decoded, _ = _decode(out)

    assert decoded.size == (1350, 2400)


def test_a_photo_at_the_cap_is_never_upscaled() -> None:
    photo = _jpeg((imaging.MAX_UPLOAD_SIDE, 900))

    out = imaging.normalise_upload(photo)

    assert out == photo


def test_a_small_jpeg_is_returned_byte_for_byte() -> None:
    photo = _jpeg((800, 600))

    assert imaging.normalise_upload(photo) == photo


def test_exif_orientation_is_applied() -> None:
    # Orientation 6 = rotate 90° clockwise on display: a 400x200 frame is a
    # 200x400 photo, and a seller's portrait shot must not be stored sideways.
    exif = Image.Exif()
    exif[0x0112] = 6
    buf = io.BytesIO()
    Image.new("RGB", (400, 200), (30, 60, 90)).save(buf, format="JPEG", exif=exif)

    decoded, _ = _decode(imaging.normalise_upload(buf.getvalue()))

    assert decoded.size == (200, 400)


@pytest.mark.parametrize(
    ("fmt", "suffix"),
    [("PNG", "png"), ("GIF", "gif"), ("BMP", "bmp"), ("TIFF", "tiff")],
)
def test_every_decodable_format_becomes_a_jpeg(fmt: str, suffix: str) -> None:
    out = imaging.normalise_upload(_other(fmt))

    decoded, out_format = _decode(out)

    assert out_format == "JPEG"
    assert decoded.mode == "RGB"
    assert decoded.size == (64, 48)


@pytest.mark.parametrize(
    "payload",
    [
        b"this is not an image, it is a text file with a .jpg extension",
        _jpeg((400, 400))[:200],  # a truncated stream: header fine, data missing
    ],
)
def test_an_undecodable_file_raises(payload: bytes) -> None:
    with pytest.raises(imaging.UnsupportedImage):
        imaging.normalise_upload(payload)


# ---------------------------------------------------------------------------
# The door: POST /api/jobs
# ---------------------------------------------------------------------------


def test_the_job_stores_the_normalised_photo_of_record(tmp_path, monkeypatch) -> None:
    _jobs_root(tmp_path, monkeypatch)
    oversize = _jpeg((3200, 1800))
    small = _jpeg((800, 600))

    result, tasks = _create_job([("big.jpg", oversize), ("small.jpg", small)])

    assert result.progress.photos_total == 2  # type: ignore[union-attr]
    stored = tasks.tasks[0].args[1]
    assert len(stored) == 2
    big, kept = _decode(stored[0]), _decode(stored[1])
    assert big[1] == "JPEG" and max(big[0].size) == imaging.MAX_UPLOAD_SIDE
    assert stored[1] == small  # already fits: untouched, byte for byte


def test_a_decodable_non_jpeg_upload_is_accepted(tmp_path, monkeypatch) -> None:
    _jobs_root(tmp_path, monkeypatch)

    result, tasks = _create_job([("shot.png", _png())])

    assert result.progress.photos_total == 1  # type: ignore[union-attr]
    decoded, fmt = _decode(tasks.tasks[0].args[1][0])
    assert fmt == "JPEG"
    assert decoded.size == (120, 90)


def test_an_undecodable_upload_rejects_the_whole_request(tmp_path, monkeypatch) -> None:
    jobs = _jobs_root(tmp_path, monkeypatch)

    result, tasks = _create_job(
        [("fine.jpg", _jpeg((400, 400))), ("notanimage.jpg", b"plain text, renamed")]
    )

    assert result.status_code == 400  # type: ignore[union-attr]
    payload = json.loads(result.body)  # type: ignore[union-attr]
    assert payload["error"]["code"] == "invalid_request"
    # The message names the offending photo by index and by the name the seller
    # sees, so the fix is obvious without reading a log.
    assert "Photo 2" in payload["error"]["message"]
    assert "notanimage.jpg" in payload["error"]["message"]
    # Nothing was created and nothing was queued: the request died at the door.
    assert not jobs.exists()
    assert tasks.tasks == []


def test_a_truncated_jpeg_is_rejected_with_the_same_answer(tmp_path, monkeypatch) -> None:
    _jobs_root(tmp_path, monkeypatch)

    result, _tasks = _create_job([("cut-short.jpg", _jpeg((600, 600))[:300])])

    assert result.status_code == 400  # type: ignore[union-attr]
    assert "cut-short.jpg" in json.loads(result.body)["error"]["message"]  # type: ignore[union-attr]
