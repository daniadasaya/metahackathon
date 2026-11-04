#!/usr/bin/env python3
"""SAM segmentation spike (temporary diagnostic for the live SAM check).

MOCK run (default ``--pack a --photo 1``): loads that photo's concepts
fixture, finds input bytes that route to the same ``(pack, photo)`` slot
through the adapter's own deterministic mapping, calls ``segment_concept``
once per concept phrase, decodes every returned ``mask_png_base64``, prints
per-concept mask counts plus a decode/alignment check per mask, and writes
one overlay PNG per mask — the mask in translucent red over a neutral
synthetic photo canvas sized to the mask's OWN ``width``/``height``, with
the record's bbox drawn in blue.

There is no real photo in mock, so the canvas is deterministic grey, not a
scene: the overlay proves the raster decodes, matches the record's own
dimensions, and sits inside its bbox. It proves nothing about segmentation
quality (a decoded mask is not
a correct mask).

LIVE run (``--live``): runs the real path over every photo in
a local photo directory with
``META_MOCK=false``, one short noun phrase per request (1-2 phrases per
photo, derived from the fixture concept phrases for that scene). Prints
per-concept mask counts, saves overlay PNGs of each mask over the REAL photo
(mask in translucent red + bbox in blue), and can save (see SPIKE_SAVE_RAW) the first response's raw
``output_text`` to ``/tmp/spike-captured-response.txt``. Every overlay
then gets inspected via vision — a decode check alone is not a correctness
claim (visual inspection required).

Environment variables (all optional, LIVE run only):

- ``SPIKE_FOTOS``: directory of ``*.jpg`` photos to sweep
  (default ``/tmp/spike_foto``).
- ``SPIKE_SAVE_RAW``: set to ``1`` to make one extra raw ``sam-3.1`` call
  and write its ``output_text`` to the capture file, only if that file does
  not exist yet.
- ``SPIKE_CAPTURE``: path of that capture file
  (default ``/tmp/spike-captured-response.txt``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

_CANDIDATE_FIXTURE_DIRS: tuple[Path, ...] = (
    HERE / "tests" / "fixtures",  # checkout: apps/api/spike_sam.py
    Path("/srv/apps/api/tests/fixtures"),  # container service root
)

_CANDIDATE_APP_ROOTS: tuple[Path, ...] = (
    HERE,  # checkout: apps/api/ holds app/
    Path("/srv"),  # container: /srv/app
)


def _fixture_dir() -> Path:
    """First fixture tree that exists, checkout layout before container."""
    for candidate in _CANDIDATE_FIXTURE_DIRS:
        if candidate.is_dir():
            return candidate
    raise RuntimeError(
        "fixture tree not found in any of "
        + ", ".join(str(p) for p in _CANDIDATE_FIXTURE_DIRS)
    )


def _app_root() -> Path:
    """First directory that holds an importable ``app`` package."""
    for candidate in _CANDIDATE_APP_ROOTS:
        if (candidate / "app" / "__init__.py").exists():
            return candidate
    raise RuntimeError(
        "app package not found under any of "
        + ", ".join(str(p) for p in _CANDIDATE_APP_ROOTS)
    )


def _ensure_importable() -> None:
    root = str(_app_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_concept_phrases(pack: str, photo: int) -> list[str]:
    """Phrases from the photo's concepts fixture, in file order."""
    path = _fixture_dir() / f"pack_{pack}" / f"photo{photo}_concepts.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load concepts fixture {path}: {exc}") from exc
    phrases: list[str] = []
    for item in document["payload"]:
        phrase = str(item["phrase"])
        if "," in phrase:
            raise RuntimeError(
                f"fixture phrase {phrase!r} contains a comma: never a valid "
                "SAM request (one concept phrase per request)"
            )
        phrases.append(phrase)
    return phrases


def _find_key_bytes(pack: str, photo: int) -> bytes:
    """Deterministic input bytes routing to the ``(pack, photo)`` slot.

    The mock mapping is content-hash based, so the spike searches
    ``spike:{pack}:{photo}:{n}`` until the adapter's own locator lands on
    the wanted slot. Six slots exist; the search is bounded and honest
    about what it does.
    """
    from app.meta_client import _locate

    for attempt in range(10000):
        key = f"spike:{pack}:{photo}:{attempt}".encode("utf-8")
        if _locate(key) == (pack, photo):
            return key
    raise RuntimeError(f"no routing key found for slot {(pack, photo)} in 10000 tries")


def _photo_canvas(width: int, height: int, seed_bytes: bytes, label: str) -> "Image.Image":
    """Neutral synthetic photo canvas: grey gradient, bands, label, no scene."""
    from PIL import Image, ImageDraw

    seed = int.from_bytes(hashlib.sha256(seed_bytes).digest()[:4], "big")
    canvas = Image.new("RGB", (width, height))
    top = 205 - (seed % 20)
    bottom = 168 - ((seed >> 8) % 20)
    rows = Image.new("RGB", (1, height))
    rows.putdata(
        [
            (
                top + (bottom - top) * y // max(height - 1, 1),
                top + (bottom - top) * y // max(height - 1, 1),
                top + (bottom - top) * y // max(height - 1, 1),
            )
            for y in range(height)
        ]
    )
    canvas = rows.resize((width, height))
    draw = ImageDraw.Draw(canvas, "RGBA")
    for band in range(3):
        y0 = (seed >> (band * 7)) % max(height - 40, 1)
        shade = 150 + ((seed >> (band * 5)) % 40)
        draw.rectangle(
            (0, y0, width, y0 + 18 + band * 8), fill=(shade, shade, shade, 255)
        )
    draw.text((12, 10), label[:64], fill=(50, 50, 50, 255))
    return canvas


def _overlay_png(
    mask_b64: str,
    width: int,
    height: int,
    bbox: tuple[int, int, int, int],
    seed_bytes: bytes,
    label: str,
    out_path: Path,
) -> tuple[int, float]:
    """Composite the decoded mask over the canvas; return (white_px, frac_in_bbox).

    Box-local convention (matches ``_live_overlay_png``): the raster maps
    onto its own box, so the canvas is ``(max(width, x2+1), max(height,
    y2+1))``, the raster is pasted at ``(x0, y0)`` as the alpha channel,
    and the bbox rectangle is drawn at ``(x0, y0, x1, y1)``.
    """
    from PIL import Image, ImageDraw

    try:
        mask = Image.open(io.BytesIO(base64.b64decode(mask_b64)))
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"mask record for {label!r} does not decode: {exc}") from exc
    if mask.mode != "1" or mask.size != (width, height):
        raise RuntimeError(
            f"mask record for {label!r} decoded as {mask.mode} {mask.size}, "
            f"record claims 1-bit {(width, height)}"
        )
    lum = mask.convert("L")
    px = lum.load()
    x0, y0, x1, y1 = bbox
    white_total = 0
    white_inside = 0
    for y in range(height):
        for x in range(width):
            if px[x, y]:
                white_total += 1
                if x0 <= x + x0 <= x1 and y0 <= y + y0 <= y1:
                    white_inside += 1
    frac_inside = white_inside / white_total if white_total else 0.0

    canvas_w, canvas_h = max(width, x1 + 1), max(height, y1 + 1)
    canvas = _photo_canvas(canvas_w, canvas_h, seed_bytes, label).convert("RGBA")
    red = Image.new("RGBA", (width, height), (255, 45, 45, 110))
    canvas.paste(red, box=(x0, y0), mask=lum)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((x0, y0, x1, y1), outline=(30, 90, 255, 255), width=2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out_path, format="PNG", optimize=True)
    return white_total, frac_inside


async def _run_mock(pack: str, photo: int, out_dir: Path) -> int:
    """Mock spike: per-concept counts, decode checks, overlay PNGs."""
    from app.meta_client import segment_concept

    phrases = _load_concept_phrases(pack, photo)
    key = _find_key_bytes(pack, photo)
    print(f"spike MOCK pack={pack} photo={photo} phrases={phrases}")
    print(f"spike MOCK routing key={key!r}")

    failures = 0
    total_masks = 0
    for phrase in phrases:
        masks = await segment_concept(key, phrase)
        print(f'concept "{phrase}": {len(masks)} mask(s)')
        total_masks += len(masks)
        for index, mask in enumerate(masks):
            label = f"{pack}/photo{photo}/{phrase}#{index}"
            slug = "".join(ch if ch.isalnum() else "_" for ch in phrase)[:24]
            out_path = out_dir / f"overlay_{pack}_photo{photo}_{slug}_{index}.png"
            if not mask.mask_png_base64:
                print(f"  mask {index}: size={mask.width}x{mask.height} NO RASTER (null)")
                failures += 1
                continue
            try:
                white, frac = _overlay_png(
                    mask.mask_png_base64,
                    mask.width,
                    mask.height,
                    mask.bbox,
                    key + phrase.encode("utf-8") + bytes((index,)),
                    label,
                    out_path,
                )
            except RuntimeError as exc:
                print(f"  mask {index}: FAIL {exc}")
                failures += 1
                continue
            print(
                f"  mask {index}: size={mask.width}x{mask.height} "
                f"bbox={list(mask.bbox)} white_px={white} "
                f"frac_in_bbox={frac:.2f} overlay={out_path}"
            )
    print(f"spike MOCK total={total_masks} mask(s) failures={failures} out={out_dir}")
    return 1 if failures else 0


def _live_overlay_png(
    mask_b64: str,
    photo_path: Path,
    width: int,
    height: int,
    bbox: tuple[int, int, int, int],
    out_path: Path,
) -> tuple[int, float]:
    """Composite a BOX-LOCAL mask over the REAL photo; (white_px, frac_in_bbox).

    SAM rasters are box-local (``width``/``height`` are the raster's own dims,
    smaller than the photo); ``bbox`` carries inclusive wire corners
    (x1,y1,x2,y2) and places the raster at (x1, y1) in source pixels.
    """
    import base64

    from PIL import Image, ImageDraw, ImageOps

    try:
        mask = Image.open(io.BytesIO(base64.b64decode(mask_b64)))
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"mask for {photo_path.name!r} does not decode: {exc}") from exc
    if mask.mode != "1":
        raise RuntimeError(f"mask for {photo_path.name!r} decoded as {mask.mode}, want 1-bit")
    try:
        photo = ImageOps.exif_transpose(Image.open(photo_path).convert("RGBA"))
    except (ValueError, OSError) as exc:
        raise RuntimeError(f"cannot open photo {photo_path}: {exc}") from exc
    photo_w, photo_h = photo.size
    if mask.size != (width, height):
        raise RuntimeError(
            f"mask size {mask.size} != claimed raster {(width, height)} for {photo_path.name}"
        )
    x0, y0, x1, y1 = (int(v) for v in bbox)
    if not (0 <= x0 <= x1 < photo_w and 0 <= y0 <= y1 < photo_h):
        raise RuntimeError(
            f"bbox [{x0},{y0},{x1},{y1}] outside photo {(photo_w, photo_h)} "
            f"for {photo_path.name}"
        )
    if (x1 + 1 - x0, y1 + 1 - y0) != (width, height):
        raise RuntimeError(
            f"bbox size {(x1 + 1 - x0, y1 + 1 - y0)} != raster {(width, height)} "
            f"for {photo_path.name}"
        )
    lum = mask.convert("L")
    full = Image.new("L", (photo_w, photo_h), 0)
    full.paste(lum, (x0, y0))
    white_total = sum(lum.tobytes()) // 255
    region = full.crop((x0, y0, x1 + 1, y1 + 1))
    white_inside = sum(region.tobytes()) // 255
    frac_inside = white_inside / white_total if white_total else 0.0
    red = Image.new("RGBA", (photo_w, photo_h), (255, 45, 45, 110))
    photo.paste(red, mask=full)
    draw = ImageDraw.Draw(photo)
    draw.rectangle((x0, y0, x1, y1), outline=(30, 90, 255, 255), width=3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    photo.convert("RGB").save(out_path, format="PNG", optimize=True)
    return white_total, frac_inside


_CAPTURE_PATH = Path(os.environ.get("SPIKE_CAPTURE", "/tmp/spike-captured-response.txt"))


async def _run_live_on_photo(
    photo_path: Path, phrases: list[str], out_dir: Path, capture: dict[str, bool]
) -> tuple[int, int]:
    """One photo, 1-2 phrases, via the public ``segment_concept`` surface.

    Requires ``META_MOCK=false`` in the environment (``--live`` is run as
    ``META_MOCK=false python spike_sam.py --live``) so the adapter's live
    branch is taken. Returns (masks, failures).
    """
    import base64

    from app.meta_client import segment_concept
    from app import meta_client as _mc

    if _mc.settings.meta_mock:
        print("LIVE: refusing — META_MOCK is true; rerun with META_MOCK=false")
        return 0, 1
    photo_bytes = photo_path.read_bytes()
    masks_total = 0
    failures = 0
    for phrase in phrases:
        try:
            masks = await segment_concept(photo_bytes, phrase)
        except Exception as exc:  # noqa: BLE001 - spike must report, not crash
            print(f'concept "{phrase}": CALL FAILED: {type(exc).__name__}: {str(exc)[:300]}')
            failures += 1
            continue
        print(f'photo {photo_path.name} concept "{phrase}": {len(masks)} mask(s)')
        if not _CAPTURE_PATH.exists() and os.environ.get("SPIKE_SAVE_RAW") == "1":
            try:
                from app.meta_client import (
                    _data_url as _mc_data_url,
                    _live_client,
                    _live_request,
                    _output_text_of,
                )

                _client = _live_client()

                async def _raw_call() -> object:
                    return await _client.responses.create(
                        model="sam-3.1",
                        input=[
                            {
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": phrase},
                                    {
                                        "type": "input_image",
                                        "image_url": _mc_data_url(photo_bytes),
                                    },
                                ],
                            }
                        ],
                        metadata={
                            "mask_encoding": "one_bit",
                            "include_confidence": "true",
                        },
                        stream=False,
                    )

                _resp = await _live_request(_raw_call, what="spike:raw-capture")
                _CAPTURE_PATH.write_text(_output_text_of(_resp), encoding="utf-8")
                print(f"captured raw output_text -> {_CAPTURE_PATH}")
            except Exception as exc:  # noqa: BLE001 - capture is best-effort
                print(f"raw capture skipped: {type(exc).__name__}: {str(exc)[:200]}")
        for index, mask in enumerate(masks):
            if not mask.mask_png_base64:
                print(f"  mask {index}: NO RASTER (null)")
                failures += 1
                continue
            slug = "".join(ch if ch.isalnum() else "_" for ch in phrase)[:20]
            out_path = out_dir / f"live_{photo_path.stem[:24]}_{slug}_{index}.png"
            try:
                white, frac = _live_overlay_png(
                    mask.mask_png_base64, photo_path,
                    mask.width, mask.height,
                    (mask.bbox[0], mask.bbox[1], mask.bbox[2], mask.bbox[3]),
                    out_path,
                )
                area_frac = white / (photo_size(photo_path) or 1)
                print(
                    f"  mask {index}: rev={mask.revision} "
                    f"score={mask.score:.4f} size={mask.width}x{mask.height} "
                    f"bbox={list(mask.bbox)} white_px={white} "
                    f"frac_in_bbox={frac:.2f} area_frac={area_frac:.4f} overlay={out_path}"
                )
                if area_frac < 0.01:
                    print("  NOTE: area <1% of photo (drop: below the 1% area floor)")
                if area_frac > 0.90:
                    print("  NOTE: area >90% of photo (drop: above the 90% area ceiling)")
            except RuntimeError as exc:
                print(f"  mask {index}: FAIL {exc}")
                failures += 1
                continue
            masks_total += 1
    return masks_total, failures


def photo_size(photo_path: Path) -> int:
    """Pixel area of the photo as displayed (EXIF-transposed)."""
    from PIL import Image, ImageOps

    with Image.open(photo_path) as im:
        w, h = ImageOps.exif_transpose(im).size
    return w * h


def _live_phrases(photo_path: Path) -> list[str]:
    """1-2 short noun phrases per photo, from the fixture concepts for that scene."""
    name = photo_path.name.lower()
    if "smartphone" in name and "counte" in name and "shop_counte" in name:
        return ["smartphone", "phone charger"]
    if "phone-shop" in name or "counte" in name and "smartphones" in name:
        return ["smartphone"]
    if "snack" in name and "box" in name:
        return ["instant noodle cup", "biscuit pack"]
    if "snacks" in name:
        return ["potato chips bag", "chocolate bar"]
    if "clothing_rack" in name:
        return ["t-shirt", "denim jacket"]
    if "folded" in name:
        return ["t-shirt", "hoodie"]
    return ["product"]


def _run_live_all(out_dir: Path | None = None) -> int:
    """Live spike over every ``*.jpg`` under ``SPIKE_FOTOS`` (default ``/tmp/spike_foto``)."""
    live_dir = Path(os.environ.get("SPIKE_FOTOS", "/tmp/spike_foto"))
    photos = sorted(live_dir.glob("*.jpg")) if live_dir.is_dir() else []
    if not photos:
        print(f"LIVE: no photos under {live_dir} (copy sample photos there first)")
        return 2
    out_dir = out_dir or Path("/tmp/spike_sam-overlays")
    capture: dict[str, bool] = {}
    total_masks = 0
    total_failures = 0
    calls = 0
    for photo_path in photos:
        phrases = _live_phrases(photo_path)
        print(f"LIVE photo={photo_path.name} phrases={phrases}")
        calls += len(phrases)
        masks, failures = asyncio.run(_run_live_on_photo(photo_path, phrases, out_dir, capture))
        total_masks += masks
        total_failures += failures
    print(f"LIVE total calls={calls} masks={total_masks} failures={total_failures} out={out_dir}")
    cap = calls * 0.0025
    print(f"LIVE est cost: {calls} SAM calls x $0.0025 = ${cap:.4f} (cap $0.40)")
    return 1 if total_failures else 0


def _run_live(out: str) -> int:
    """Live spike entry: full 6-photo sweep (--live takes no pack/photo args)."""
    return _run_live_all(Path(out))


def main(argv: list[str] | None = None) -> int:
    """Parse args and run the mock or live spike."""
    parser = argparse.ArgumentParser(description="SAM segmentation spike.")
    parser.add_argument("--pack", default="a", choices=("a", "b", "c"))
    parser.add_argument("--photo", default=1, type=int, choices=(1, 2))
    parser.add_argument("--out", default="/tmp/spike_sam")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    _ensure_importable()
    if args.live:
        return _run_live(args.out)
    return asyncio.run(_run_mock(args.pack, args.photo, Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
