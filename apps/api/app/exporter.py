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

The crop file has two spellings in the wild: the live pipeline writes
``crops/{sku}-crop.png``, older fixtures wrote ``crops/{sku}.png``.
:func:`crop_path` resolves either, which is what makes
``crop_available(job_id, "KK-0001")`` and
``resolve_image_bytes(job_id, "KK-0001-crop")`` agree on one file.

**Merged angle photos.** A seller may merge several photos of the same item
into one catalog entry. The client expresses that as a pair of fields on the
*child* record: ``included = False`` (it is not a row of its own) and
``concept = "{base_concept}@merge:{parent_sku}"``. :func:`merged_parent_sku`
and :func:`merged_children_map` are the only readers of that convention; the
child's studio image and crop then ride along in the parent's
``additional_image_link`` and in the photo ZIP. The same gate still applies to
them — a merged photo is exported, so it must be gate-passed like any other.

Promoting one of those angles to be the main photo swaps ``clean_url`` and
``crop_url`` between the parent and the child. :func:`photo_sku` then names
the image to export from the URL, while ``sku`` keeps the catalog identity.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from collections.abc import Mapping
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

#: Marker a merged child appends to its ``concept`` (see module docstring).
MERGE_MARKER = "@merge:"

#: Commerce Manager's ceiling for ``additional_image_link``.
MAX_ADDITIONAL_IMAGES = 20


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


class UngatedImageError(Exception):
    """Raised when an included product claims a generative cleanup without a gate pass.

    Every exported image is a gate-passed Muse Image
    output or the deterministic composite. A record with ``muse-image`` and
    no fully positive fidelity verdict cannot exist if the pipeline is
    correct, so it blocks the export with the offending SKUs named — never
    silently downgraded to composite, which would hide the upstream defect.
    """

    def __init__(self, skus: list[str]) -> None:
        super().__init__("ungated_generative_image: " + ", ".join(skus))
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
    """On-disk path of the original crop for ``sku``.

    The live pipeline writes ``crops/{sku}-crop.png``; older fixtures wrote
    ``crops/{sku}.png``. Both spellings resolve here so
    ``crop_available(job_id, "KK-0001")`` and
    ``resolve_image_bytes(job_id, "KK-0001-crop")`` land on the same file.
    When neither exists the plain spelling is returned, so the caller's
    ``is_file()`` check stays the single source of truth.
    """
    crops = JOBS_DIR / job_id / "crops"
    plain = crops / f"{sku}.png"
    if plain.is_file():
        return plain
    if not _is_crop_sku(sku):
        hyphenated = crops / f"{sku}-crop.png"
        if hyphenated.is_file():
            return hyphenated
    return plain


def _is_crop_sku(sku: str) -> bool:
    return sku.endswith("-crop")


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

#: The SKU an image URL points at: ``/p/{job}/KK-0001.jpg``, ``KK-0001-crop.png``.
_SKU_IN_URL_RE = re.compile(r"/(KK-\d{4})(?:-crop)?\.(?:jpg|png)$")


def photo_sku(product: schemas.ReviewedProduct) -> str:
    """Return the underlying image SKU referenced by ``product.clean_url``.

    A seller may promote a merged angle photo to be the main photo of a SKU.
    The client expresses that by swapping ``clean_url`` (and ``crop_url``)
    between the parent and the child, so the image to export is named by the
    URL, not by the record's own ``sku``. The record's ``sku`` stays the
    catalog identity — only the picture moves.
    """
    m = _SKU_IN_URL_RE.search(product.clean_url)
    return m.group(1) if m else product.sku


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


def merged_parent_sku(product: schemas.ReviewedProduct) -> str | None:
    """Return target parent SKU when ``product`` is merged as an additional photo."""
    if product.included or MERGE_MARKER not in product.concept:
        return None
    _, _, target = product.concept.rpartition(MERGE_MARKER)
    target = target.strip()
    if not target or target == product.sku:
        return None
    return target


def merged_children_map(
    products: list[schemas.ReviewedProduct],
) -> dict[str, list[schemas.ReviewedProduct]]:
    """Map each included parent SKU to its ordered list of merged child products."""
    included_skus = {p.sku for p in products if p.included}
    children: dict[str, list[schemas.ReviewedProduct]] = {sku: [] for sku in included_skus}
    for p in products:
        parent = merged_parent_sku(p)
        if parent is not None and parent in children:
            children[parent].append(p)
    return children


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


def _gate_passed(p: schemas.ReviewedProduct) -> bool:
    """A generative cleanup is export-legal only with a fully positive verdict.

    Mirrors the pipeline's own write condition (stage D) and the review
    screen's gate display: ``same_product``, ``color_match`` and
    ``logo_text_match`` must all be true.
    """
    v = p.fidelity
    return v is not None and v.same_product and v.color_match and v.logo_text_match


def validate_image_gate(products: list[schemas.ReviewedProduct]) -> None:
    """Raise :class:`UngatedImageError` if an exported item claims an ungated image.

    Composite is always export-legal. ``muse-image`` is legal only when the
    record carries a fully positive fidelity verdict.

    A merged child is exported too — its studio image is written to
    ``additional_image_link`` and into the photo ZIP — so it is checked on the
    same terms as an included product even though ``included`` is ``False``.
    """
    included_skus = {p.sku for p in products if p.included}
    exported = [p for p in products if p.included or merged_parent_sku(p) in included_skus]
    skus = [p.sku for p in exported if p.cleanup_method == schemas.CleanupMethod.MUSE_IMAGE and not _gate_passed(p)]
    if skus:
        raise UngatedImageError(skus)


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------


def _unique_capped(urls: list[str], limit: int) -> list[str]:
    """Deduplicate preserving first-seen order, then cap at ``limit`` entries."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def build_csv(
    job_id: str,
    products: list[schemas.ReviewedProduct],
    seller: schemas.SellerInfo,
    base_url: str,
    *,
    images: Mapping[str, bytes] | None = None,
) -> str:
    """Render the Commerce Manager CSV.

    ``images`` (keyword-only) supplies crop availability from a mapping
    keyed ``"{sku}.jpg"`` / ``"{sku}-crop.jpg"`` instead of reading the job
    folder. It exists for golden-bundle regeneration: the Demo Safe Mode bundle
    keeps its images as static JPEGs under ``apps/web/public/samples/{pack}/
    golden/images/`` and the job folder that produced them is long wiped, so
    :func:`crop_available` and :func:`resolve_image_bytes` cannot serve them.
    When ``images`` is given neither is consulted at all: a SKU has a crop
    exactly when ``"{sku}-crop.jpg"`` is a key, and an absent key is a legal
    "no crop", never an incomplete map. ``None`` (the default) keeps the
    job-folder behaviour byte-identical.
    """
    base = base_url.rstrip("/")
    children_by_parent = merged_children_map(products)

    def has_crop(sku: str) -> bool:
        """Whether the crop URL for ``sku`` may be linked."""
        if images is None:
            return crop_available(job_id, sku)
        return f"{sku}-crop.jpg" in images

    rows: list[list[str]] = []
    for p in included_products(products):
        if p.price_via_chat:
            continue
        if p.price_idr is None:
            # Should have been caught by validate_price_law; skip defensively.
            continue
        brand = p.brand if p.brand else seller.store_name
        description = p.description_id if p.description_id.strip() else p.description_en
        children = children_by_parent.get(p.sku, [])
        # The image is named by the URL, not the record: a promoted angle photo
        # means ``clean_url`` names another SKU (see :func:`photo_sku`).
        primary_img_sku = photo_sku(p)
        # Order: merged angle shots first (the reason the seller merged them),
        # then the primary's original crop, then the children's crops.
        additional_urls: list[str] = [f"{base}/p/{job_id}/{photo_sku(c)}.jpg" for c in children]
        if has_crop(primary_img_sku):
            additional_urls.append(f"{base}/p/{job_id}/{primary_img_sku}-crop.jpg")
        for c in children:
            child_img_sku = photo_sku(c)
            if has_crop(child_img_sku):
                additional_urls.append(f"{base}/p/{job_id}/{child_img_sku}-crop.jpg")
        additional = ",".join(_unique_capped(additional_urls, MAX_ADDITIONAL_IMAGES))
        rows.append(
            [
                p.sku,
                p.name[:200],
                description,
                p.availability.value,
                p.condition.value,
                format_price_wire(p.price_idr),
                f"{base}/p/{job_id}/{p.sku}",
                f"{base}/p/{job_id}/{primary_img_sku}.jpg",
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
    *,
    images: Mapping[str, bytes] | None = None,
) -> bytes:
    """Assemble the photo ZIP with JPGs, crops, and the CSV at root.

    Merged children ride along: the seller asked for one catalog entry, but
    every angle they merged is still a photo they own, so it ships in the ZIP
    under its own SKU. A promoted angle photo is written under the SKU its
    ``clean_url`` names (:func:`photo_sku`), so the ZIP always matches the
    CSV's links. An entry is written once, never twice.

    ``images`` (keyword-only) supplies the JPG bytes from a mapping keyed
    ``"{sku}.jpg"`` / ``"{sku}-crop.jpg"`` instead of reading the job folder.
    It exists for golden-bundle regeneration: the Demo Safe Mode bundle keeps
    its images as static JPEGs under ``apps/web/public/samples/{pack}/golden/
    images/`` and the job folder that produced them is long wiped, so
    :func:`resolve_image_bytes` cannot serve them. When ``images`` is given,
    :func:`resolve_image_bytes` and :func:`crop_available` are not consulted at
    all; a missing primary entry raises ``KeyError`` naming the SKU instead of
    skipping the photo or substituting a placeholder. ``None`` (the default)
    keeps the job-folder behaviour byte-identical.
    """
    children_by_parent = merged_children_map(products)

    def image_bytes(sku: str) -> bytes:
        """Primary JPG bytes for ``sku``: the supplied map, else the job folder."""
        if images is None:
            return resolve_image_bytes(job_id, sku)
        key = f"{sku}.jpg"
        try:
            return images[key]
        except KeyError:
            raise KeyError(
                f"build_zip: the supplied image map is incomplete — no {key!r} "
                f"for SKU {sku}"
            ) from None

    def crop_bytes(sku: str) -> bytes | None:
        """Crop JPG bytes for ``sku``, or ``None`` when this source has no crop."""
        if images is None:
            if not crop_available(job_id, sku):
                return None
            return resolve_image_bytes(job_id, f"{sku}-crop")
        return images.get(f"{sku}-crop.jpg")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"katalog-{job_id}.csv", csv_text)
        written: set[str] = set()

        def write_entry(sku: str) -> None:
            if sku in written:
                return
            written.add(sku)
            zf.writestr(f"{sku}.jpg", image_bytes(sku))
            crop = crop_bytes(sku)
            if crop is not None:
                zf.writestr(f"{sku}-crop.jpg", crop)

        for p in included_products(products):
            write_entry(photo_sku(p))
            for child in children_by_parent.get(p.sku, []):
                write_entry(photo_sku(child))
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
    validate_image_gate(products)

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
