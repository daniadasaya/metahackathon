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

LIVE run (``--live``): attempts the real path in a fresh interpreter with
``META_MOCK=false`` and surfaces ``MetaLiveUnavailable`` until the key and
the client dependency land. That is honest, not a stub: the live branch is
really entered, and it raises before any network call exists.
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
    """Composite the decoded mask over the canvas; return (white_px, frac_in_bbox)."""
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
                if x0 <= x < x1 and y0 <= y < y1:
                    white_inside += 1
    frac_inside = white_inside / white_total if white_total else 0.0

    canvas = _photo_canvas(width, height, seed_bytes, label).convert("RGBA")
    red = Image.new("RGBA", (width, height), (255, 45, 45, 110))
    canvas.paste(red, mask=lum)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(
        (x0, y0, max(x1 - 1, x0), max(y1 - 1, y0)), outline=(30, 90, 255, 255), width=2
    )
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


def _run_live() -> int:
    """Live spike: attempt the real path, surface MetaLiveUnavailable.

    Runs in a fresh interpreter with ``META_MOCK=false`` so the snapshotted
    settings really take the live branch. The live branch raises before any
    socket exists, so this performs zero network calls.
    """
    probe = (
        "import asyncio; "
        "from app import meta_client as m; "
        "print('mock flag:', m.settings.meta_mock); "
        "print('masks:', len(asyncio.run(m.segment_concept(b'live-probe', 'smartphone'))))"
    )
    env = dict(os.environ, META_MOCK="false")
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=str(_app_root()),
        env=env,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 and "MetaLiveUnavailable" in combined:
        last = next(
            (
                line.strip()
                for line in reversed(combined.splitlines())
                if "MetaLiveUnavailable" in line
            ),
            "MetaLiveUnavailable",
        )
        print(f"LIVE RESULT: {last}")
        print("live unavailable until the key + client dependency land (expected MOCK-era outcome)")
        return 3
    print(proc.stdout, end="")
    print(proc.stderr, end="", file=sys.stderr)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    """Parse args and run the mock or live spike."""
    parser = argparse.ArgumentParser(description="SAM segmentation spike.")
    parser.add_argument("--pack", default="a", choices=("a", "b", "c"))
    parser.add_argument("--photo", default=1, type=int, choices=(1, 2))
    parser.add_argument("--out", default="/tmp/opencode/spike_sam")
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args(argv)

    _ensure_importable()
    if args.live:
        return _run_live()
    return asyncio.run(_run_mock(args.pack, args.photo, Path(args.out)))


if __name__ == "__main__":
    sys.exit(main())
