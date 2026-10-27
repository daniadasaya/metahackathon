"""Deterministic image work for Katalog Kilat (Pillow only, no model calls).

Turns a SAM mask into a product cut-out and, when needed, the studio composite
fallback. Every pixel here is arithmetic on purpose: the composite exists because
a generative edit can lie about a product, and arithmetic cannot.
"""

from __future__ import annotations

import base64
import io
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageOps

__all__ = [
    "cut_out",
    "cutout",
    "composite",
    "export_jpg",
    "png_to_jpg",
    "to_jpg",
    "decode_mask",
    "iou",
]


def decode_mask(mask: Any) -> Image.Image:
    """Decode a mask record's raster to a single-channel (L) image.

    The raster is read at the mask's own dimensions; callers must never assume
    it matches the photo's dimensions.
    """
    b64 = getattr(mask, "mask_png_base64", None)
    if b64 is None and isinstance(mask, dict):
        b64 = mask.get("mask_png_base64")
    if not b64:
        # No raster: return an empty L image sized to the record's own dims.
        w = getattr(mask, "width", None)
        h = getattr(mask, "height", None)
        if w is None and isinstance(mask, dict):
            w = mask.get("width", 1)
            h = mask.get("height", 1)
        return Image.new("L", (int(w or 1), int(h or 1)), 0)
    raw = base64.b64decode(b64)
    with Image.open(io.BytesIO(raw)) as im:
        return im.convert("L")


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
    return int(w), int(h)


def cut_out(image_bytes: bytes, mask: Any) -> bytes:
    """Mask raster -> RGBA cut-out, bbox crop with 8% padding, anti-aliased edge.

    Returns PNG bytes at source resolution. Never upscales, never colour-corrects.
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
    pad = int(round(0.08 * max(bw, bh)))

    px1 = max(0, x1 - pad)
    py1 = max(0, y1 - pad)
    px2 = min(pw - 1, x2 + pad)
    py2 = min(ph - 1, y2 + pad)

    crop = photo.crop((px1, py1, px2 + 1, py2 + 1))
    crop_w, crop_h = crop.size

    # Decode the mask at its own dimensions, then resize to the crop with NEAREST.
    mask_img = decode_mask(mask).resize((crop_w, crop_h), Image.NEAREST)
    mask_l = mask_img.convert("L")
    alpha = mask_l.point(lambda v: 255 if v > 127 else 0)
    # Anti-alias the edge on the alpha channel only.
    alpha = alpha.filter(ImageFilter.GaussianBlur(radius=1))

    crop.putalpha(alpha)

    # Trim fully transparent rows/columns (safety; bbox already trims).
    # Find bounding box of non-transparent pixels.
    bbox = crop.getbbox()
    if bbox is not None:
        crop = crop.crop(bbox)

    buf = io.BytesIO()
    crop.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


cutout = cut_out


def composite(cutout_png_bytes: bytes) -> bytes:
    """Deterministic studio composite: 1024x1024, #F7F7F5, 88% scale, soft shadow.

    Physically incapable of altering the product pixels.
    """
    frame = 1024
    with Image.open(io.BytesIO(cutout_png_bytes)) as im:
        cutout = im.convert("RGBA")

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

    bg_color = (247, 247, 245, 255)
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
    out = Image.new("RGB", (frame, frame), (247, 247, 245))
    out.paste(canvas, mask=canvas.split()[-1])

    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


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


# Keep ImageOps import used (avoid lint) - not strictly needed.
_ = ImageOps
