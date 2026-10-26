"""Exporter: the single producer of the four Katalog Kilat artefacts.

This module is importable without a running server and has no side effects at
import time. It owns:

* the money formatters — one module, two documented
  functions, no duplicates anywhere else in the codebase;
* the CSV generator (Meta Commerce catalog specification);
* the Instagram caption pack (caption rules);
* the WhatsApp price list (WhatsApp list rules);
* the photo ZIP assembly;
* the deterministic Pillow placeholder images used for ``mock-*`` jobs;
* the conversion of on-disk PNG artefacts into the public JPG (image export
  rules).

Image filename convention this module defines (the pipeline's write contract):
``data/jobs/{job_id}/clean/{sku}.png`` for the cleaned studio image and
``data/jobs/{job_id}/crops/{sku}.png`` for the original crop. ``sku`` is used
verbatim, so a crop is stored as ``crops/KK-0001-crop.png``. A missing file is
a 404 on the public route, never a 500.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from app import schemas

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# ``apps/api/data`` in a checkout, ``/srv/data`` in the image: derived from
# this file's location so both layouts resolve.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_DIR = DATA_DIR / "jobs"
EXPORTS_DIR = DATA_DIR / "exports"

MOCK_JOB_RE = re.compile(r"^mock-\d+-\d+$")

CSV_COLUMNS = [
    "id",
    "title",
    "description",
    "availability",
    "condition",
    "price",
    "link",
    "image_link",
    "brand",
    "product_type",
    "additional_image_link",
]

MAX_WA_ITEMS = 30


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class PriceMissingError(Exception):
    """Raised when an included, non-chat item has no confirmed price.

    ``skus`` lists the blocking SKUs in request order so the route can name
    them in the ``price_missing`` message.
    """

    def __init__(self, skus: list[str]) -> None:
        super().__init__("price_missing: " + ", ".join(skus))
        self.skus = skus


# ---------------------------------------------------------------------------
# Job-id helpers
# ---------------------------------------------------------------------------


def is_mock_job_id(job_id: str) -> bool:
    """Return ``True`` for a client-simulated job id (``mock-*``)."""
    return bool(MOCK_JOB_RE.fullmatch(job_id))


def _safe_component(value: str) -> bool:
    """A path component must be non-empty and contain no traversal or slash."""
    if not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value or "\x00" in value:
        return False
    return True


def clean_path(job_id: str, sku: str) -> Path:
    """On-disk path of the cleaned studio image for ``sku``."""
    return JOBS_DIR / job_id / "clean" / f"{sku}.png"


def crop_path(job_id: str, sku: str) -> Path:
    """On-disk path of the original crop for ``sku``."""
    return JOBS_DIR / job_id / "crops" / f"{sku}.png"


def _is_crop_sku(sku: str) -> bool:
    return sku.endswith("-crop")


def _base_sku(sku: str) -> str:
    # Crop artefacts are stored under the crop sku verbatim (see module docstring).
    return sku


def crop_available(job_id: str, sku: str) -> bool:
    """Whether a crop image can be served for ``sku``."""
    if is_mock_job_id(job_id):
        return True
    return crop_path(job_id, sku).is_file()


# ---------------------------------------------------------------------------
# Money formatting — the only two formatters in the codebase
# ---------------------------------------------------------------------------


def format_price_wire(price: int) -> str:
    """Wire/CSV format: ``1500000 IDR`` (integer, space, ISO code)."""
    return f"{int(price)} IDR"


def format_price_display(price: int) -> str:
    """Display format with Indonesian thousands separators: ``Rp1.500.000``."""
    return "Rp" + f"{int(price):,}".replace(",", ".")


# ---------------------------------------------------------------------------
# Slugification (hashtags must be slug-safe)
# ---------------------------------------------------------------------------


def slugify_hashtag(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace to underscores.

    ``Handphone & Tablet`` -> ``handphone_tablet`` (not ``handphone&tablet``).
    """
    out: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            out.append(ch)
        else:
            out.append(" ")
    collapsed = "_".join(part for part in "".join(out).split())
    return collapsed.strip("_")


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------


def _placeholder_jpg(sku: str, job_id: str, *, crop: bool = False) -> bytes:
    """Deterministic Pillow placeholder, same input -> same bytes."""
    seed = 0
    for ch in f"{job_id}:{sku}:{'crop' if crop else 'clean'}":
        seed = (seed * 31 + ord(ch)) & 0xFFFFFFFF

    size = 512 if crop else 1024
    bg = (248, 248, 246) if not crop else (235, 235, 233)
    img = Image.new("RGB", (size, size), bg)
    draw = ImageDraw.Draw(img)

    # A simple deterministic accent bar derived from the seed.
    accent = ((seed >> 16) & 0x7F) + 100, ((seed >> 8) & 0x7F) + 100, (seed & 0x7F) + 100
    bar_h = size // 12
    draw.rectangle((0, size - bar_h, size, size), fill=accent)

    label = sku
    # Pillow's default bitmap font is small; scale by drawing multiple times is
    # not worth it. Keep the label readable-ish.
    text_x = size // 16
    text_y = size // 2
    draw.text((text_x, text_y), label, fill=(60, 60, 60))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True, progressive=True)
    return buf.getvalue()


def resolve_image_bytes(job_id: str, sku: str) -> bytes:
    """Return JPG bytes for ``sku`` under ``job_id``.

    Mock jobs get a deterministic placeholder; real jobs read the on-disk PNG
    artefact and flatten onto white at quality 88.
    Raises ``FileNotFoundError`` when a real job's artefact is missing.
    """
    crop = _is_crop_sku(sku)
    if is_mock_job_id(job_id):
        return _placeholder_jpg(sku, job_id, crop=crop)

    path = crop_path(job_id, sku) if crop else clean_path(job_id, sku)
    if not path.is_file():
        raise FileNotFoundError(str(path))

    with Image.open(path) as im:
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


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def included_products(products: list[schemas.ReviewedProduct]) -> list[schemas.ReviewedProduct]:
    return [p for p in products if p.included]


def _blocking_skus(products: list[schemas.ReviewedProduct]) -> list[str]:
    out: list[str] = []
    for p in products:
        if not p.included:
            continue
        if p.price_via_chat:
            continue
        if p.price_idr is None:
            out.append(p.sku)
    return out


def validate_price_law(products: list[schemas.ReviewedProduct]) -> None:
    """Raise :class:`PriceMissingError` if any included non-chat item lacks a price."""
    skus = _blocking_skus(products)
    if skus:
        raise PriceMissingError(skus)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def build_csv(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
) -> str:
    """Render the Commerce Manager CSV (catalog-feed §1–§3)."""
    base = base_url.rstrip("/")
    rows: list[list[str]] = []
    for p in included_products(products):
        if p.price_via_chat:
            continue
        if p.price_idr is None:
            # Should have been caught by validate_price_law; skip defensively.
            continue
        brand = p.brand if p.brand else seller.store_name
        description = p.description_id if p.description_id.strip() else p.description_en
        crop_url = f"{base}/p/{job_id}/{p.sku}-crop.jpg" if crop_available(job_id, p.sku) else ""
        additional = crop_url if crop_available(job_id, p.sku) else ""
        rows.append(
            [
                p.sku,
                p.name[:200],
                description,
                p.availability.value,
                p.condition.value,
                format_price_wire(p.price_idr),
                f"{base}/p/{job_id}/{p.sku}",
                f"{base}/p/{job_id}/{p.sku}.jpg",
                brand,
                p.category,
                additional,
            ]
        )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Caption pack
# ---------------------------------------------------------------------------


def build_captions(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
) -> str:
    """Render the combined Instagram caption pack."""
    base = base_url.rstrip("/")
    blocks: list[str] = []
    for p in included_products(products):
        if p.price_via_chat or p.price_idr is None:
            price_line = "harga via chat"
        else:
            price_line = format_price_display(p.price_idr)
        cat_slug = slugify_hashtag(p.category)
        brand_slug = slugify_hashtag(p.brand if p.brand else seller.store_name)
        block = (
            f"{p.name} — {price_line}\n"
            f"{p.description_id if p.description_id.strip() else p.description_en}\n"
            f"📩 Order via WhatsApp: {seller.wa_number}\n"
            f"#jual{cat_slug} #{brand_slug} #katalogkilat\n"
            "———\n"
        )
        blocks.append(block)
    return "\n".join(blocks) if blocks else ""


# ---------------------------------------------------------------------------
# WhatsApp price list
# ---------------------------------------------------------------------------


def build_whatsapp(
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
) -> str:
    """Render the WhatsApp price list."""
    base = base_url.rstrip("/")
    included = included_products(products)
    total = len(included)
    shown = included[:MAX_WA_ITEMS]
    remaining = total - len(shown)

    lines: list[str] = []
    lines.append(f"*{seller.store_name} — Daftar Harga*")
    lines.append(f"_Update: {date.today().isoformat()}_")
    lines.append("")

    for i, p in enumerate(shown, start=1):
        if p.price_via_chat or p.price_idr is None:
            lines.append(f"{i}. {p.name} — harga via chat")
        else:
            lines.append(f"{i}. {p.name} — *{format_price_display(p.price_idr)}*")

    lines.append("")
    lines.append("Order: balas pesan ini atau tap produk di katalog kami 🙏")

    if remaining > 0:
        lines.append(f"+{remaining} produk lainnya di katalog")

    text = "\n".join(lines)

    # URL-encoded share link appended as a footer line.
    import urllib.parse

    encoded = urllib.parse.quote(text, safe="")
    share_url = f"https://wa.me/?text={encoded}"

    return text + f"\n\n{share_url}"



# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------


def build_zip(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
    csv_text: str,
) -> bytes:
    """Assemble the photo ZIP with JPGs, crops, and the CSV at root."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"katalog-{job_id}.csv", csv_text)
        for p in included_products(products):
            sku = p.sku
            jpg = resolve_image_bytes(job_id, sku)
            zf.writestr(f"{sku}.jpg", jpg)
            if crop_available(job_id, sku):
                crop_jpg = resolve_image_bytes(job_id, f"{sku}-crop")
                zf.writestr(f"{sku}-crop.jpg", crop_jpg)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Export record persistence (for /p HTML page)
# ---------------------------------------------------------------------------


def export_record_path(job_id: str) -> Path:
    return EXPORTS_DIR / job_id / "export.json"


def write_export_record(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
) -> None:
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORTS_DIR / job_id).mkdir(parents=True, exist_ok=True)
    payload = {
        "products": [p.model_dump(mode="json") for p in products],
        "seller": seller.model_dump(mode="json"),
    }
    export_record_path(job_id).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def read_export_record(job_id: str) -> dict[str, Any] | None:
    path = export_record_path(job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_export(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
) -> list[schemas.ExportFile]:
    """Validate, generate, persist, and return the four export files."""
    validate_price_law(products)

    csv_text = build_csv(job_id, products, seller, base_url)
    captions_text = build_captions(job_id, products, seller, base_url)
    whatsapp_text = build_whatsapp(products, seller, base_url)
    zip_bytes = build_zip(job_id, products, seller, base_url, csv_text)

    out_dir = EXPORTS_DIR / job_id
    out_dir.mkdir(parents=True, exist_ok=True)

    files: list[tuple[schemas.ExportKind, str, bytes, str]] = [
        (schemas.ExportKind.CATALOG_CSV, "katalog-meta.csv", csv_text.encode("utf-8"), "text/csv"),
        (schemas.ExportKind.IMAGES_ZIP, "katalog-kilat-images.zip", zip_bytes, "application/zip"),
        (schemas.ExportKind.CAPTIONS_INSTAGRAM, f"katalog-{job_id}-captions.txt", captions_text.encode("utf-8"), "text/plain"),
        (schemas.ExportKind.PRICE_LIST_WHATSAPP, f"katalog-{job_id}-whatsapp.txt", whatsapp_text.encode("utf-8"), "text/plain"),
    ]

    export_files: list[schemas.ExportFile] = []
    base = base_url.rstrip("/")
    for kind, filename, data, _ct in files:
        (out_dir / filename).write_bytes(data)
        export_files.append(
            schemas.ExportFile(
                kind=kind,
                filename=filename,
                url=f"{base}/api/exports/{job_id}/{filename}",
            )
        )

    write_export_record(job_id, products, seller)
    return export_files
