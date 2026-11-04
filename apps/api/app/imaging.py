"""Deterministic image work for Katalog Kilat (Pillow only, no model calls).

Turns a SAM mask into a product cut-out and, when needed, the studio composite
fallback. Every pixel here is arithmetic on purpose: the composite exists because
a generative edit can lie about a product, and arithmetic cannot.

Two coordinate rules govern everything below, and each one was a shipped bug
before it was a rule:

* The mask raster is aligned **1:1 to the mask record's own box**, never to a
  padded crop. Stretching the raster over the 8% padding drags the neighbouring
  product, the acrylic stand and the table surface into the alpha channel.
* A pixel with ``alpha == 0`` still carries RGB. Meta's image decoder drops the
  alpha channel (``convert("RGB")``), so unmasked neighbours survive inside the
  crop box unless the transparent pixels are neutralised to the studio
  background before the crop leaves this module.
"""

from __future__ import annotations

import base64
import io
from collections import deque
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

__all__ = [
    "cut_out",
    "cutout",
    "composite",
    "alpha_coverage",
    "export_jpg",
    "png_to_jpg",
    "to_jpg",
    "decode_mask",
    "clean_mask",
    "gate_reference",
    "normalise_upload",
    "iou",
]

#: Studio background, the same off-white the composite paints.
STUDIO_BG = (247, 247, 245)

#: Longest side a photo of record may carry. Every mask coordinate, crop and
#: cut-out is computed in this photo's pixels, so an upload is downscaled once,
#: at the door, instead of every stage paying for a 24-megapixel frame.
MAX_UPLOAD_SIDE = 2400

#: JPEG quality for a re-encoded upload: high enough that the downscale is the
#: only visible change.
UPLOAD_JPEG_QUALITY = 90

#: EXIF tag 274, the orientation flag a phone camera writes.
_EXIF_ORIENTATION = 0x0112

#: Threshold between mask background (0) and foreground (1).
MASK_THRESHOLD = 127

#: Maps a raw mask byte to 1 (foreground) or 0 (background). Used with
#: ``bytes.translate`` so the padded binary grid is built in C, not per pixel.
_BIN_LUT = bytes(1 if v > MASK_THRESHOLD else 0 for v in range(256))


def decode_mask(mask: Any) -> Image.Image:
    """Decode a mask record's raster to a single-channel (L) image.

    The raster is read at the mask's own dimensions; callers must never assume
    it matches the photo's dimensions. A corrupt or unreadable stream yields an
    empty raster of the record's size rather than raising: a placeholder mask
    must degrade to "nothing to cut out", never kill the job.
    """
    b64 = getattr(mask, "mask_png_base64", None)
    if b64 is None and isinstance(mask, dict):
        b64 = mask.get("mask_png_base64")
    if not b64:
        w, h = _mask_dims(mask)
        return Image.new("L", (w, h), 0)
    try:
        raw = base64.b64decode(b64)
        with Image.open(io.BytesIO(raw)) as im:
            return im.convert("L")
    except Exception:
        w, h = _mask_dims(mask)
        return Image.new("L", (w, h), 0)


def _bbox(mask: Any) -> tuple[int, int, int, int]:
    bbox = getattr(mask, "bbox", None)
    if bbox is None and isinstance(mask, dict):
        bbox = mask["bbox"]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    return x1, y1, x2, y2


def _mask_dims(mask: Any) -> tuple[int, int]:
    w = getattr(mask, "width", None)
    h = getattr(mask, "height", None)
    if w is None and isinstance(mask, dict):
        w = mask.get("width")
        h = mask.get("height")
    return int(w or 1), int(h or 1)


def _align_mask(mask: Any, box: tuple[int, int, int, int]) -> Image.Image:
    """Decode a mask and align it to its own box **without ever stretching it**.

    A live SAM mask carries a raster sized exactly to its bbox (``bbox size ==
    raster size``), so ``resize`` to the box is a no-op and the
    mapping is 1:1. The hand-written placeholder fixtures instead carry a
    full-frame raster, where resizing to the box would squeeze the whole frame
    into the product — so that case is *cropped* to the box, which is the same
    pixels a live mask would have delivered.

    Anything else falls back to ``NEAREST`` resize, never to a padded size: the
    padding is applied later, as a transparent margin, and can never enter the
    alpha channel.
    """
    raster = decode_mask(mask)
    rw, rh = raster.size
    x1, y1, x2, y2 = box
    bw = x2 - x1 + 1
    bh = y2 - y1 + 1
    if (rw, rh) == (bw, bh):
        return raster
    if rw >= x2 + 1 and rh >= y2 + 1:
        return raster.crop((x1, y1, x2 + 1, y2 + 1))
    return raster.resize((bw, bh), Image.NEAREST)


# ---------------------------------------------------------------------------
# Mask cleanup
# ---------------------------------------------------------------------------


def _offsets(stride: int, connectivity: int) -> tuple[int, ...]:
    """Neighbour index offsets for a padded grid of width ``stride``."""
    if connectivity == 4:
        return (-1, 1, -stride, stride)
    return (
        -stride - 1,
        -stride,
        -stride + 1,
        -1,
        1,
        stride - 1,
        stride,
        stride + 1,
    )


def _flood(
    grid: bytearray,
    marks: list[int] | bytearray,
    start: int,
    mark: int,
    offsets: tuple[int, ...],
    target: int = 1,
    members: list[int] | None = None,
) -> int:
    """BFS one connected component of ``target`` pixels; return its size.

    ``marks`` doubles as the visited map and as the component label store: a
    pixel is part of this component exactly when ``marks[i] == mark``. The grid
    is padded by one background pixel on every side, so no neighbour index can
    fall outside it and the inner loop needs no bounds arithmetic.
    """
    marks[start] = mark
    queue = deque([start])
    popleft = queue.popleft
    push = queue.append
    size = 0
    while queue:
        i = popleft()
        size += 1
        for off in offsets:
            j = i + off
            if grid[j] == target and not marks[j]:
                marks[j] = mark
                push(j)
                if members is not None:
                    members.append(j)
    return size


def clean_mask(mask_l: Image.Image) -> Image.Image:
    """Deterministic mask cleanup: one product, no slivers, no glare holes.

    Four operations, in order, each one fixing a defect seen in a real cut-out:

    1. **Largest 8-connected component.** A SAM mask can carry detached slivers
       of a neighbouring object; keeping only the biggest component drops them
       (``KK-0003``'s bottom-left sliver, stray background islands).
    2. **Pinhole filling only.** A 4-connected background region that does not
       touch the crop border and is at most 16 pixels (4x4) is binary noise, not
       a hole in the silhouette. The cap is deliberately tiny: the old 5%-of-
       component rule filled the open gaps between cable loops (``KK-0016``) and
       the camera cutout of a phone case, dragging the table surface and the
       neighbouring phone edge into the alpha channel. A real opening is
       never filled, and exterior concavities (a stand cutout) touch the border
       and are preserved anyway.
    3. **Binary rewrite.** ``255`` for foreground, ``0`` for background, so the
       alpha channel carries no half tones before the anti-alias step.
    4. **1px erosion** (only for masks at least 24px on the short side), which
       shaves the one-pixel background bleed SAM leaves on the boundary. An
       erosion that would erase the mask entirely is discarded.

    Pure Pillow and ``collections.deque``; no model call, no network.
    """
    mask_l = mask_l.convert("L")
    w, h = mask_l.size
    if w <= 0 or h <= 0:
        return Image.new("L", (max(1, w), max(1, h)), 0)

    binary = mask_l.point(lambda v: 255 if v > MASK_THRESHOLD else 0)
    if binary.getbbox() is None:
        # Nothing to clean: an empty (or placeholder) mask stays empty.
        return binary

    # One background pixel of padding on every side, so a neighbour index can
    # never fall outside the grid and "touches the border" is the same question
    # as "reaches the padding ring".
    stride = w + 2
    n = stride * (h + 2)
    grid = bytearray(n)
    flat = binary.tobytes()
    for y in range(h):
        dst = (y + 1) * stride + 1
        grid[dst:dst + w] = flat[y * w:(y + 1) * w].translate(_BIN_LUT)

    off8 = _offsets(stride, 8)
    off4 = _offsets(stride, 4)

    # 1. Largest 8-connected foreground component.
    seen = bytearray(n)
    best_start = -1
    best_size = 0
    for start in range(n):
        if not grid[start] or seen[start]:
            continue
        size = _flood(grid, seen, start, 1, off8)
        if size > best_size:
            best_size = size
            best_start = start
    if best_start < 0:
        return binary

    keep = bytearray(n)
    _flood(grid, keep, best_start, 1, off8)

    # 2. Fill pinholes only: <= 16 px (4x4) of binary noise. A larger enclosed
    #    background region is a real opening — a cable loop, a camera cutout —
    #    and filling it welds the table surface into the product (``KK-0016``).
    exterior = bytearray(n)
    _flood(grid, exterior, 0, 1, off4, target=0)
    filled = bytearray(n)
    hole_limit = min(16, max(1, int(0.0005 * best_size)))
    has_holes = False
    for start in range(n):
        if grid[start] or exterior[start]:
            continue
        hole: list[int] = [start]
        size = _flood(grid, exterior, start, 1, off4, target=0, members=hole)
        if size < hole_limit:
            has_holes = True
            for idx in hole:
                filled[idx] = 1

    # 3. Binary rewrite, done in C: slice the padded rows out, threshold the
    #    component label with a 256-entry LUT, then OR the filled holes in.
    keep_flat = bytearray(w * h)
    filled_flat = bytearray(w * h)
    for y in range(h):
        src = (y + 1) * stride + 1
        dst = y * w
        keep_flat[dst:dst + w] = keep[src:src + w]
        if has_holes:
            filled_flat[dst:dst + w] = filled[src:src + w]

    cleaned = Image.frombytes("L", (w, h), bytes(keep_flat)).point(
        lambda v: 255 if v else 0
    )
    if has_holes:
        holes = Image.frombytes("L", (w, h), bytes(filled_flat)).point(
            lambda v: 255 if v else 0
        )
        cleaned = ImageChops.lighter(cleaned, holes)

    # 4. Contour tightening.
    if min(w, h) >= 24:
        eroded = cleaned.filter(ImageFilter.MinFilter(3))
        if eroded.getbbox() is not None:
            cleaned = eroded
    return cleaned


# ---------------------------------------------------------------------------
# Photo-aware contour tightening
# ---------------------------------------------------------------------------

#: Minimum short side (px) before the photo-aware pass is allowed to run. Below
#: this the geometry ratios are noise and a 5px mode filter eats the product.
TIGHTEN_MIN_SIDE = 120


def _leftmost(px: Any, y: int, w: int) -> int | None:
    """Leftmost foreground ``x`` on row ``y``, or ``None`` for an empty row."""
    for x in range(w):
        if px[x, y] > MASK_THRESHOLD:
            return x
    return None


def _rightmost(px: Any, y: int, w: int) -> int | None:
    """Rightmost foreground ``x`` on row ``y``, or ``None`` for an empty row."""
    for x in range(w - 1, -1, -1):
        if px[x, y] > MASK_THRESHOLD:
            return x
    return None


def _fg_ratio(mask_l: Image.Image) -> float:
    """Fraction of the crop that is foreground, from the 256-bin histogram."""
    hist = mask_l.histogram()
    total = mask_l.size[0] * mask_l.size[1]
    if total <= 0:
        return 0.0
    fg = sum(hist[MASK_THRESHOLD + 1:])
    return fg / total


def _median_fg_luminance(lum: Image.Image, mask_l: Image.Image) -> int:
    """Median luminance of the foreground pixels (0 when there are none)."""
    hist = lum.histogram(mask=mask_l)
    total = sum(hist)
    if total <= 0:
        return 0
    half = total / 2
    running = 0
    for value, count in enumerate(hist):
        running += count
        if running >= half:
            return value
    return 255


def _dark_window_share(tight_photo: Image.Image, mask_l: Image.Image) -> float:
    """Share of the camera-window rectangle that is dark foreground.

    A clear case has a hole through which the table shows: dark pixels *inside*
    the silhouette, in the upper middle. A solid white product (a power bank)
    has none, which is what separates the two shapes (``KK-0012`` 0.297 vs
    ``KK-0014`` 0.000).
    """
    w, h = mask_l.size
    y0, y1 = int(0.03 * h), min(h, int(0.29 * h) + 1)
    x0, x1 = int(0.45 * w), min(w, int(0.71 * w) + 1)
    area = (y1 - y0) * (x1 - x0)
    if area <= 0:
        return 0.0
    lum = tight_photo.convert("L")
    mask_px = mask_l.load()
    lum_px = lum.load()
    dark = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if mask_px[x, y] > MASK_THRESHOLD and lum_px[x, y] < 128:
                dark += 1
    return dark / area


def _tighten_with_photo(tight_photo: Image.Image, mask_l: Image.Image) -> Image.Image:
    """Shave background that SAM welded to the product silhouette.

    Three deterministic shapes, each keyed on the product's own geometry and the
    pixels behind it — never on a model, never on a network call. Each shape
    exists because a measured defect on a named SKU demanded it:

    1. **Upright portrait phone on a stand** (``h > 1.4 * w``, dense silhouette;
       ``KK-0003``). SAM merged the translucent acrylic stand bracket into the
       bottom chin instead of carving the notch it carved on ``KK-0001`` and
       ``KK-0002``, and left a bump on the right bezel. When the bottom centre is
       still mostly opaque the bracket tab is zeroed, the right bezel is
       straightened along the chord between its top and bottom extents, and a
       ``ModeFilter(5)`` smooths the remaining stair-steps.
    2. **Tilted wide dark phone with a cast shadow** (``w > 1.7 * h``; ``KK-0005``).
       A strong top-left → bottom-right tilt plus a dark lower-left boundary is a
       cast shadow, not the product: the left boundary is clipped to the straight
       perspective chord between the two measured extents.
    3. **Clear/translucent case** (``w > 1.8 * h`` and a bright silhouette;
       ``KK-0012``). The dark interior camera window in the upper middle is
       background showing through, and the bottom rim is the contact shadow
       strip; both are removed, then a ``ModeFilter(7)`` cleans the jagged edge.
       Tested *before* case 2: a translucent case is also wide and dense, so the
       shadow case's outer gate would otherwise swallow it.

    Returns an ``"L"`` mask of the same size. A pass that would erase the mask
    is discarded and the input is returned unchanged — losing the product is
    worse than keeping a shadow, because the composite cannot restore it.
    """
    mask_l = mask_l.convert("L")
    w, h = mask_l.size
    if w <= 0 or h <= 0 or mask_l.getbbox() is None:
        return mask_l

    lum = tight_photo.convert("L")
    ratio = _fg_ratio(mask_l)
    original = mask_l

    # -- Case 1: upright portrait phone on a stand -------------------------
    if h > 1.4 * w and ratio > 0.65:
        px = mask_l.load()
        y_lo = int(0.91 * h)
        x_lo = int(0.15 * w)
        x_hi = int(0.50 * w)
        probe = 0
        opaque = 0
        for y in range(y_lo, h):
            for x in range(x_lo, x_hi):
                probe += 1
                if px[x, y] > MASK_THRESHOLD:
                    opaque += 1
        if probe and opaque / probe > 0.70:
            for y in range(int(0.905 * h), h):
                for x in range(int(0.08 * w), int(0.52 * w)):
                    px[x, y] = 0

        yt = int(0.06 * h)
        yb = int(0.92 * h)
        xr_top = _rightmost(px, yt, w)
        xr_bot = _rightmost(px, yb, w)
        if xr_top is not None and xr_bot is not None:
            span = max(1, yb - yt)
            for y in range(yt, yb + 1):
                edge = round(xr_top + (y - yt) / span * (xr_bot - xr_top)) + 1
                for x in range(edge + 1, w):
                    px[x, y] = 0
        mask_l = mask_l.filter(ImageFilter.ModeFilter(5))

    # -- Case 3: clear/translucent case with a camera window ---------------
    #    Evaluated before Case 2: a translucent case is also wide and dense, so
    #    the cast-shadow case's outer gate would otherwise swallow it (KK-0012).
    #    A bright silhouette alone is not enough — a white power bank (KK-0014)
    #    is equally wide and bright — so the shape must also carry a genuinely
    #    dark opening where the table shows through (>=8% of the window rect;
    #    KK-0012 measures 29.7%, KK-0014 measures 0.0%).
    elif (
        w > 1.8 * h
        and ratio > 0.55
        and _median_fg_luminance(lum, mask_l) > 155
        and _dark_window_share(tight_photo, mask_l) >= 0.08
    ):
        px = mask_l.load()
        lum_px = lum.load()
        window = Image.new("L", (w, h), 0)
        win_px = window.load()
        for y in range(int(0.03 * h), min(h, int(0.29 * h) + 1)):
            for x in range(int(0.45 * w), min(w, int(0.71 * w) + 1)):
                if px[x, y] > MASK_THRESHOLD and lum_px[x, y] < 128:
                    win_px[x, y] = 255
        window = window.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
        mask_l = ImageChops.subtract(mask_l, window)

        # Contact-shadow rim: 5 passes over the lower boundary, dark pixels only.
        y_start = int(0.35 * h)
        for _ in range(5):
            box = mask_l.getbbox()
            if box is None:
                break
            # getbbox() is exclusive on the right/bottom edges; make it inclusive.
            bx1, by1, bx2, by2 = box[0], box[1], box[2] - 1, box[3] - 1
            px = mask_l.load()
            doomed: list[tuple[int, int]] = []
            for y in range(max(by1, y_start), by2 + 1):
                for x in range(bx1, bx2 + 1):
                    if px[x, y] <= MASK_THRESHOLD or lum_px[x, y] >= 105:
                        continue
                    if (
                        x == 0
                        or y == 0
                        or x == w - 1
                        or y == h - 1
                        or px[x - 1, y] <= MASK_THRESHOLD
                        or px[x + 1, y] <= MASK_THRESHOLD
                        or px[x, y - 1] <= MASK_THRESHOLD
                        or px[x, y + 1] <= MASK_THRESHOLD
                    ):
                        doomed.append((x, y))
            if not doomed:
                break
            for x, y in doomed:
                px[x, y] = 0
        mask_l = mask_l.filter(ImageFilter.ModeFilter(7))

    # -- Case 2: tilted wide dark phone with a cast shadow -----------------
    elif w > 1.7 * h and ratio > 0.50:
        px = mask_l.load()
        yt = int(0.10 * h)
        yb = int(0.91 * h)
        xl_top = _leftmost(px, yt, w)
        xl_bot = _leftmost(px, yb, w)
        xr_20 = _rightmost(px, int(0.20 * h), w)
        xr_80 = _rightmost(px, int(0.80 * h), w)
        tilted = (
            xl_top is not None
            and xl_bot is not None
            and xr_20 is not None
            and xr_80 is not None
            and xl_bot - xl_top > int(0.55 * h)
            and xr_80 - xr_20 > int(0.40 * h)
        )
        if tilted:
            lum_px = lum.load()
            samples: list[int] = []
            for y in range(int(0.45 * h), int(0.90 * h)):
                taken = 0
                for x in range(w):
                    if px[x, y] > MASK_THRESHOLD:
                        samples.append(lum_px[x, y])
                        taken += 1
                        if taken == 6:
                            break
            if samples and sum(samples) / len(samples) < 45:
                span = max(1, yb - yt)
                for y in range(yt, yb + 1):
                    chord = round(
                        xl_top + (y - yt) / span * (xl_bot + 10 - xl_top)
                    )
                    for x in range(0, min(chord, w)):
                        px[x, y] = 0
                mask_l = mask_l.filter(ImageFilter.ModeFilter(5))

    if mask_l.getbbox() is None:
        return original
    return mask_l


# ---------------------------------------------------------------------------
# Cut-out
# ---------------------------------------------------------------------------


def cut_out(image_bytes: bytes, mask: Any) -> bytes:
    """Mask raster -> RGBA cut-out: tight crop, cleaned alpha, 8% transparent pad.

    Returns PNG bytes at source resolution. Never upscales, never colour-corrects.

    The mask is decoded and resized to the mask record's own box — the crop is
    cut from that same box, so the raster and the pixels line up 1:1. Padding is
    applied *afterwards* as a transparent margin on a canvas, which is why it
    can never pull a neighbouring object into the alpha channel.
    """
    with Image.open(io.BytesIO(image_bytes)) as im:
        photo = im.convert("RGBA")

    pw, ph = photo.size
    x1, y1, x2, y2 = _bbox(mask)
    # Intersect the claimed box with the image bounds.
    x1 = max(0, min(pw - 1, x1))
    y1 = max(0, min(ph - 1, y1))
    x2 = max(0, min(pw - 1, x2))
    y2 = max(0, min(ph - 1, y2))
    if x2 < x1:
        x2 = x1
    if y2 < y1:
        y2 = y1

    bw = x2 - x1 + 1
    bh = y2 - y1 + 1
    tight_photo = photo.crop((x1, y1, x2 + 1, y2 + 1))

    # 1:1 with the mask's own box. Never resized to a padded crop.
    cleaned_mask = clean_mask(_align_mask(mask, (x1, y1, x2, y2)))

    # Photo-aware tightening: shave the stand bracket, the cast shadow and the
    # translucent-case rim that SAM welded into the silhouette.
    if min(bw, bh) >= TIGHTEN_MIN_SIDE and cleaned_mask.getbbox() is not None:
        cleaned_mask = _tighten_with_photo(tight_photo, cleaned_mask)

    tb = cleaned_mask.getbbox()
    if tb is not None:
        # Trim fully transparent rows/columns before padding.
        tx1, ty1, tx2, ty2 = tb
        tight_photo = tight_photo.crop(tb)
        cleaned_mask = cleaned_mask.crop(tb)
        bx1 = x1 + tx1
        by1 = y1 + ty1
        bx2 = x1 + tx2 - 1
        by2 = y1 + ty2 - 1
    else:
        # Placeholder mask (no raster): keep the tight box as it came.
        bx1, by1, bx2, by2 = x1, y1, x2, y2

    tw, th = tight_photo.size
    has_alpha = cleaned_mask.getbbox() is not None

    # Anti-alias the edge on the alpha channel only.
    alpha = (
        cleaned_mask.filter(ImageFilter.GaussianBlur(radius=1))
        if has_alpha
        else cleaned_mask
    )

    # Neutralise RGB where the pixel is transparent: Meta's decoder drops the
    # alpha channel, and raw unmasked RGB inside the crop box is how the
    # neighbouring product reappears in the "cleaned" image.
    neutral_bg = Image.new("RGB", (tw, th), STUDIO_BG)
    neutral_bg.paste(tight_photo.convert("RGB"), mask=alpha)
    tight_rgba = neutral_bg.convert("RGBA")
    tight_rgba.putalpha(alpha)

    if has_alpha:
        # 8% of the larger tight dimension, as a transparent margin clamped to
        # the source photo so the pad never invents pixels.
        pad = int(round(0.08 * max(tw, th)))
        pad_left = min(pad, bx1)
        pad_top = min(pad, by1)
        pad_right = min(pad, pw - 1 - bx2)
        pad_bottom = min(pad, ph - 1 - by2)
        out_w = tw + pad_left + pad_right
        out_h = th + pad_top + pad_bottom
        canvas = Image.new("RGBA", (out_w, out_h), STUDIO_BG + (0,))
        canvas.paste(tight_rgba, (pad_left, pad_top))
    else:
        canvas = tight_rgba

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


cutout = cut_out


# ---------------------------------------------------------------------------
# Studio composite
# ---------------------------------------------------------------------------


def composite(cutout_png_bytes: bytes) -> bytes:
    """Deterministic studio composite: 1024x1024, #F7F7F5, 88% scale, soft shadow.

    Physically incapable of altering the product pixels.
    """
    frame = 1024
    with Image.open(io.BytesIO(cutout_png_bytes)) as im:
        cutout = im.convert("RGBA")

    # Trim the transparent 8% margin first: the 88% scaling and the shadow must
    # measure the product, not the padding.
    bbox = cutout.getbbox()
    if bbox is not None:
        cutout = cutout.crop(bbox)

    cw, ch = cutout.size
    target = int(frame * 0.88)
    scale = target / max(cw, ch) if max(cw, ch) else 1.0
    if scale > 2.0:
        scale = 2.0
    new_w = max(1, int(cw * scale))
    new_h = max(1, int(ch * scale))
    resized = cutout.resize((new_w, new_h), Image.LANCZOS)

    x = (frame - new_w) // 2
    # Slightly more space above than below: shift the product down a touch.
    y = (frame - new_h) // 2 + int(frame * 0.03)

    bg_color = STUDIO_BG + (255,)
    canvas = Image.new("RGBA", (frame, frame), bg_color)

    # Shadow: ellipse under the product's base.
    shadow = Image.new("RGBA", (frame, frame), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)
    sw = new_w
    sh = max(4, int(new_w * 0.12))
    sx = x
    sy = y + new_h - sh // 2
    # Clip ellipse to frame bounds.
    ex1 = max(0, sx)
    ey1 = max(0, sy)
    ex2 = min(frame, sx + sw)
    ey2 = min(frame, sy + sh)
    if ex2 > ex1 and ey2 > ey1:
        draw.ellipse((ex1, ey1, ex2, ey2), fill=(0, 0, 0, int(255 * 0.15)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=25))

    canvas = Image.alpha_composite(canvas, shadow)
    canvas.alpha_composite(resized, (x, y))

    # Flatten onto the off-white background.
    out = Image.new("RGB", (frame, frame), STUDIO_BG)
    out.paste(canvas, mask=canvas.split()[-1])

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def alpha_coverage(png_bytes: bytes) -> float:
    """How much of its own bounding box a cut-out's opaque silhouette fills.

    A mask that bites a chunk out of a product leaves a silhouette filling far
    less of the box it occupies, and :func:`composite` renders that hole
    faithfully: the deterministic fallback is honest about the damage, and the
    damage is still there. This ratio is the pipeline's only evidence of the
    tear once Stage C has run, measured on the exact PNG the composite is built
    from.

    The cut-out carries the 8% padding margin, so a tight product scores below
    1.0; the pipeline's threshold is calibrated against real cut-outs rather
    than against this function. A fully transparent image scores ``0.0``.
    """
    with Image.open(io.BytesIO(png_bytes)) as im:
        alpha = im.convert("RGBA").getchannel("A")
    solid = alpha.point(lambda value: 255 if value > MASK_THRESHOLD else 0)
    box = solid.getbbox()
    if box is None:
        return 0.0
    box_area = (box[2] - box[0]) * (box[3] - box[1])
    if box_area <= 0:
        return 0.0
    return solid.histogram()[255] / box_area


# ---------------------------------------------------------------------------
# Fidelity gate reference
# ---------------------------------------------------------------------------


def _blank_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (32, 32, 32)).save(buf, format="PNG")
    return buf.getvalue()


def gate_reference(
    image_bytes: bytes, mask: Any, cutout_png_bytes: bytes | None = None
) -> bytes:
    """Build the two-panel ground-truth PNG the Stage D gate inspects.

    Left is the **unmasked** photo around the tightened box with a red rectangle
    on the product, so the inspector can see what the mask left out. Right is
    the tightened cut-out on solid magenta, so a missing half, a cable crossing
    the product, or a hole in the silhouette is unmistakable.

    Never raises: an undecodable photo or a placeholder mask falls back to the
    cut-out it was given, then to a valid 8x8 PNG, because a gate reference is
    an input to a verdict and a broken input must not become a job failure.
    """

    def _fallback() -> bytes:
        if cutout_png_bytes:
            return cutout_png_bytes
        return _blank_png()

    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            photo = im.convert("RGB")
    except Exception:
        return _fallback()

    pw, ph = photo.size
    x1, y1, x2, y2 = _bbox(mask)
    x1 = max(0, min(pw - 1, x1))
    y1 = max(0, min(ph - 1, y1))
    x2 = max(0, min(pw - 1, x2))
    y2 = max(0, min(ph - 1, y2))
    if x2 < x1:
        x2 = x1
    if y2 < y1:
        y2 = y1

    bw = x2 - x1 + 1
    bh = y2 - y1 + 1
    cleaned = clean_mask(_align_mask(mask, (x1, y1, x2, y2)))
    # Same photo-aware pass as ``cut_out``: the right panel must show exactly the
    # alpha the cut-out carries, or the gate inspects a mask that never ships.
    if min(bw, bh) >= TIGHTEN_MIN_SIDE and cleaned.getbbox() is not None:
        cleaned = _tighten_with_photo(
            photo.crop((x1, y1, x2 + 1, y2 + 1)), cleaned
        )
    tb = cleaned.getbbox()
    if tb is None:
        return _fallback()

    tx1, ty1, tx2, ty2 = tb
    bx1 = x1 + tx1
    by1 = y1 + ty1
    bx2 = x1 + tx2 - 1
    by2 = y1 + ty2 - 1
    tw = tx2 - tx1
    th = ty2 - ty1
    if tw <= 0 or th <= 0:
        return _fallback()

    # LEFT: 35% context around the tightened box, with the box outlined.
    pad = int(round(0.35 * max(tw, th)))
    cx1 = max(0, bx1 - pad)
    cy1 = max(0, by1 - pad)
    cx2 = min(pw - 1, bx2 + pad)
    cy2 = min(ph - 1, by2 + pad)
    ctx = photo.crop((cx1, cy1, cx2 + 1, cy2 + 1))
    ImageDraw.Draw(ctx).rectangle(
        (bx1 - cx1, by1 - cy1, bx2 - cx1, by2 - cy1),
        outline=(255, 0, 0),
        width=max(3, max(tw, th) // 80),
    )

    # RIGHT: the tightened cut-out on magenta.
    mag = Image.new("RGB", (tw, th), (255, 0, 255))
    mag.paste(
        photo.crop((bx1, by1, bx2 + 1, by2 + 1)).convert("RGB"),
        mask=cleaned.crop(tb),
    )

    ctx.thumbnail((512, 512), Image.LANCZOS)
    mag.thumbnail((512, 512), Image.LANCZOS)

    canvas = Image.new("RGB", (1040, 520), (32, 32, 32))
    canvas.paste(ctx, ((512 - ctx.width) // 2, (520 - ctx.height) // 2))
    canvas.paste(mag, (528 + (512 - mag.width) // 2, (520 - mag.height) // 2))

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# JPG export
# ---------------------------------------------------------------------------


def export_jpg(png_bytes: bytes) -> bytes:
    """PNG -> JPG, quality 88, flattened onto white, EXIF stripped."""
    with Image.open(io.BytesIO(png_bytes)) as im:
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            out = bg
        else:
            out = im.convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
    return buf.getvalue()


png_to_jpg = export_jpg
to_jpg = export_jpg


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection over union for two (x1,y1,x2,y2) boxes."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1 + 1)
    ih = max(0, iy2 - iy1 + 1)
    inter = iw * ih
    area_a = max(1, (ax2 - ax1 + 1) * (ay2 - ay1 + 1))
    area_b = max(1, (bx2 - bx1 + 1) * (by2 - by1 + 1))
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


# ---------------------------------------------------------------------------
# Upload normalisation
# ---------------------------------------------------------------------------


class UnsupportedImage(ValueError):
    """An upload Pillow cannot decode.

    Raised on the request path so the whole job is rejected before a job folder
    exists: a renamed text file, a truncated stream and a corrupt header are one
    answer to the caller, never a half-created catalog.
    """


def normalise_upload(image_bytes: bytes) -> bytes:
    """Decode an upload and return the JPEG that becomes the photo of record.

    Everything downstream — the SAM masks, the crops, the cut-outs, the gate
    references — is computed in this photo's pixels, so this is the one place
    where the frame is decided:

    * the bytes must decode, or :class:`UnsupportedImage` is raised and the
      request is rejected;
    * EXIF orientation is applied, so a portrait phone photo is stored the way
      the seller saw it instead of lying on its side;
    * the long side is capped at :data:`MAX_UPLOAD_SIDE` with LANCZOS and never
      upscaled, because a 6400-pixel frame makes every pure-Pillow stage
      several times more expensive for pixels the catalog never shows.

    A JPEG that already fits and carries no rotation is returned **untouched**,
    byte for byte: re-encoding it would change pixels for no benefit and would
    move every recorded fixture.
    """
    try:
        with Image.open(io.BytesIO(image_bytes)) as im:
            im.load()  # force the decode: a truncated stream fails here
            fmt = (im.format or "").upper()
            orientation = int(im.getexif().get(_EXIF_ORIENTATION, 1) or 1)
            width, height = im.size
            if (
                fmt == "JPEG"
                and orientation == 1
                and max(width, height) <= MAX_UPLOAD_SIDE
            ):
                return image_bytes
            oriented = ImageOps.exif_transpose(im)
            rgb = oriented.convert("RGB")
    except Exception as exc:
        raise UnsupportedImage(str(exc)) from exc

    long_side = max(rgb.size)
    if long_side > MAX_UPLOAD_SIDE:
        scale = MAX_UPLOAD_SIDE / long_side
        target = (
            max(1, round(rgb.width * scale)),
            max(1, round(rgb.height * scale)),
        )
        rgb = rgb.resize(target, Image.LANCZOS)

    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=UPLOAD_JPEG_QUALITY, optimize=True)
    return buf.getvalue()

