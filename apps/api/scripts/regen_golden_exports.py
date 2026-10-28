"""Render the four Demo Safe Mode golden export files from an existing bundle.

The bundle at ``apps/web/public/samples/{pack}/golden/`` keeps its products as
JSON, its images as static JPEGs, and its four export files as static assets
(safe mode must not touch the network). When a change upstream of
the exporter — a taxonomy, a prompt, a formatter — makes those four files stale,
the whole bundle cannot be regenerated: the job that produced it is long wiped
from ``apps/api/data/jobs/``, so ``POST /api/jobs/{id}/export`` has no images to
read.

This script closes that gap. It reads the bundle, renders the four files with
the **real exporter functions** (:func:`exporter.build_csv`,
:func:`exporter.build_captions`, :func:`exporter.build_whatsapp`,
:func:`exporter.build_zip` with its ``images=`` seam), and writes them to a
directory of your choosing. It formats no price, builds no CSV row, and slugs no
hashtag itself — a second formatter is a defect.

It writes **outside** the bundle on purpose: ``apps/web/**`` belongs to the web
client, which installs the rendered files. A ``--out`` inside
``apps/web`` is refused — where that tree exists, which is a checkout; the
image ships no web tree, so there the guard is a documented no-op.

Run from the service root (``/srv`` in the image, ``apps/api/`` in a checkout),
which is also what the script derives its own paths from — ``scripts/`` always
sits exactly one level below it::

    python -m scripts.regen_golden_exports \\
        --golden ../web/public/samples/a/golden \\
        --out /tmp/opencode/golden-a-exports \\
        --job-id job-pack-a-v3 \\
        --base-url http://127.0.0.1:8180

The ``--golden`` and ``--out`` values above are checkout-shaped; both are
resolved against the working directory, so an image run passes absolute paths
(``--golden /web/public/samples/a/golden``).

Price invariant: the bundle deliberately carries ``price_idr: null``
on every record, because the AI price guess must never be exported. The
seller's confirmed prices live in the bundle's own reviewed ``catalog.csv``;
this script reads them from there and refuses to run when a SKU is missing.
``price_suggestion_idr`` is dropped from the payload, never used as a fallback.

Exit code ``0`` when the rendered CSV's ``product_type`` values are all
canonical, its price cells match the reviewed CSV, and the ZIP's root member is
the rendered CSV; ``1`` when any of those three checks fails.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import zipfile
from itertools import zip_longest
from pathlib import Path
from typing import Any

from app import exporter, schemas
from app.meta_client import PRODUCT_CATEGORIES

#: The service root: ``/srv`` in the image, ``apps/api/`` in a checkout. It is
#: always exactly one level above ``scripts/``, which is the only depth both
#: layouts share — a ``parents[3]`` chain raises ``IndexError`` under ``/srv``.
_SERVICE_ROOT = Path(__file__).resolve().parents[1]

#: ``apps/web`` in a checkout; ``/web`` in the image, which ships no web tree.
#: :func:`_refuse_web_target` is a no-op where this directory is absent.
WEB_DIR = _SERVICE_ROOT.parent / "web"

#: The exact names ``apps/web/scripts/generate-goldens.js`` writes and
#: ``apps/web/src/lib/golden.ts`` fetches.
OUTPUT_NAMES = (
    "catalog.csv",
    "photos.zip",
    "captions_instagram.txt",
    "price_list_whatsapp.txt",
)

#: The only price spelling Commerce Manager accepts in a golden CSV cell.
_PRICE_CELL_RE = re.compile(r"^(\d+) IDR$")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the four golden export files from an existing bundle."
    )
    parser.add_argument(
        "--golden",
        required=True,
        help="bundle directory, e.g. ../web/public/samples/a/golden",
    )
    parser.add_argument(
        "--out", required=True, help="directory to write the four files into"
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="job id the CSV links and the ZIP's root member name use",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8180",
        help="public base URL the CSV links point at (default: %(default)s)",
    )
    return parser.parse_args(argv)


def _refuse_web_target(out_dir: Path) -> None:
    """Refuse to write inside ``apps/web``: this script only renders.

    A no-op when :data:`WEB_DIR` does not exist. The image ships no web tree, so
    there is nothing to protect there; the guard only fires where the golden
    bundle actually lives.
    """
    if not WEB_DIR.is_dir():
        return
    web_dir = WEB_DIR.resolve()
    if out_dir == web_dir or web_dir in out_dir.parents:
        raise SystemExit(
            f"refusing to write into {web_dir}: this script only renders the four "
            "files; the web client installs them into the golden bundle"
        )


def _csv_rows(csv_text: str) -> list[dict[str, str]]:
    """Parse CSV text the way ``exporter`` writes it (CRLF, minimal quoting)."""
    return list(csv.DictReader(io.StringIO(csv_text, newline="")))


def _csv_rows_from_path(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _confirmed_prices(csv_path: Path) -> dict[str, int]:
    """``id`` -> integer rupiah from the bundle's already-reviewed CSV.

    This is the seller's confirmed record. A SKU that is
    absent, or a cell that is not ``"<int> IDR"``, stops the run: falling back
    to ``price_suggestion_idr`` would export an AI guess.
    """
    prices: dict[str, int] = {}
    for line_no, row in enumerate(_csv_rows_from_path(csv_path), start=2):
        sku = (row.get("id") or "").strip()
        cell = (row.get("price") or "").strip()
        match = _PRICE_CELL_RE.fullmatch(cell)
        if not sku or match is None:
            raise SystemExit(
                f"{csv_path}:{line_no}: expected a named 'id' and '<int> IDR' in "
                f"'price', got id={sku!r} price={cell!r}"
            )
        prices[sku] = int(match.group(1))
    return prices


def _reviewed_products(
    records: list[dict[str, Any]], prices: dict[str, int]
) -> list[schemas.ReviewedProduct]:
    """Turn bundle records into the exporter's input type.

    ``price_suggestion_idr`` is dropped — the type forbids it, which is the
    price invariant expressed as a type — and ``price_idr`` comes
    from the reviewed CSV. Every other field is carried verbatim, so the
    rendered text can only change where the record itself changed.

    An excluded record (``included is False``) has no CSV row and is never
    exported, so it needs no price: it is accepted with ``price_idr = None`` —
    never a fallback, never a guess. An included record
    missing from the reviewed CSV still stops the run.
    """
    products: list[schemas.ReviewedProduct] = []
    for record in records:
        sku = record["sku"]
        payload = {k: v for k, v in record.items() if k != "price_suggestion_idr"}
        if sku in prices:
            payload["price_idr"] = prices[sku]
        elif record.get("included", True):
            raise SystemExit(
                f"{sku}: no confirmed price in the golden catalog.csv — refusing to "
                "fall back to price_suggestion_idr"
            )
        else:
            payload["price_idr"] = None
        products.append(schemas.ReviewedProduct.model_validate(payload))
    return products


def _image_map(
    golden: Path, products: list[schemas.ReviewedProduct]
) -> dict[str, bytes]:
    """Read the bundle's static JPGs, keyed the way ``build_zip`` asks for them.

    Keyed by :func:`exporter.photo_sku` — the SKU the record's ``clean_url``
    names — because that is the name ``build_zip`` looks up and it is what a
    promoted angle photo needs. A missing file stops the run: a placeholder in
    the golden bundle would ship a fake product photo to the demo.
    """
    images: dict[str, bytes] = {}
    for product in products:
        sku = exporter.photo_sku(product)
        for name in (f"{sku}.jpg", f"{sku}-crop.jpg"):
            if name in images:
                continue
            path = golden / "images" / name
            if not path.is_file():
                raise SystemExit(f"missing golden image: {path} (for {sku})")
            images[name] = path.read_bytes()
    return images


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _text_lines(data: bytes) -> list[str] | None:
    try:
        return data.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _diff_window(old_line: str, new_line: str, pad: int = 26) -> str:
    """Show the first differing stretch of two lines, not their shared prefix."""
    limit = min(len(old_line), len(new_line))
    index = next((i for i in range(limit) if old_line[i] != new_line[i]), limit)
    start = max(0, index - pad)
    lead = "…" if start else ""
    old_slice = old_line[start : index + pad]
    new_slice = new_line[start : index + pad]
    return f"col {index}: {lead}{old_slice!r} -> {lead}{new_slice!r}"


def _diff_note(old: bytes, new: bytes) -> str:
    """One line of evidence for a ``changed`` verdict — never an opinion."""
    if zipfile.is_zipfile(io.BytesIO(old)) and zipfile.is_zipfile(io.BytesIO(new)):
        with zipfile.ZipFile(io.BytesIO(old)) as a, zipfile.ZipFile(io.BytesIO(new)) as b:
            return f"zip members {len(a.namelist())} -> {len(b.namelist())}"
    old_lines, new_lines = _text_lines(old), _text_lines(new)
    if old_lines is None or new_lines is None:
        return f"binary, {len(old)} -> {len(new)} bytes"
    pairs = list(zip_longest(old_lines, new_lines, fillvalue=""))
    differing = [(a, b) for a, b in pairs if a != b]
    if not differing:
        return "identical lines, different trailing bytes"
    first_old, first_new = differing[0]
    return (
        f"{len(differing)}/{len(pairs)} lines differ; "
        f"first {_diff_window(first_old, first_new)}"
    )


def _zip_root_csv(data: bytes) -> tuple[str, bytes]:
    """The ZIP's first member, which is the CSV at root by construction."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        name = zf.namelist()[0]
        return name, zf.read(name)


def _report(
    golden: Path,
    out_dir: Path,
    job_id: str,
    base_url: str,
    products: list[schemas.ReviewedProduct],
    images: dict[str, bytes],
    rendered: dict[str, bytes],
) -> bool:
    """Print the evidence table plus the three hard checks; True if all pass."""
    old_dir = golden / "exports"
    included = exporter.included_products(products)

    print(f"golden   {golden}")
    print(f"job_id   {job_id}")
    print(f"base_url {base_url}")
    print(f"products {len(products)} ({len(included)} included, "
          f"{len(products) - len(included)} merged/excluded)")
    print(f"images   {len(images)} files from images/")
    print(f"out      {out_dir}")
    print()
    print(f"{'file':24} {'bytes':>8}  {'sha256 (new)':64}  {'sha256 (old)':64}  verdict")

    verdicts: dict[str, str] = {}

    for name in OUTPUT_NAMES:
        new = rendered[name]
        old_path = old_dir / name
        old = old_path.read_bytes() if old_path.is_file() else b""
        verdict = "identical" if old_path.is_file() and old == new else "changed"
        verdicts[name] = verdict
        old_hash = _sha256(old) if old_path.is_file() else "missing"
        print(f"{name:24} {len(new):>8}  {_sha256(new)}  {old_hash:64}  {verdict}")
        if verdict == "changed":
            print(f"{'':24} {'':>8}  diff: {_diff_note(old, new)}")

    new_rows = _csv_rows(rendered["catalog.csv"].decode("utf-8"))
    old_rows = _csv_rows_from_path(old_dir / "catalog.csv")
    new_prices = {row["id"]: row["price"] for row in new_rows}
    old_prices = {row["id"]: row["price"] for row in old_rows}
    product_types = sorted({row["product_type"] for row in new_rows})
    subset = set(product_types) <= set(PRODUCT_CATEGORIES)
    prices_match = new_prices == old_prices
    root_name, root_bytes = _zip_root_csv(rendered["photos.zip"])
    root_matches = root_bytes == rendered["catalog.csv"]
    changed = [n for n in OUTPUT_NAMES if verdicts[n] == "changed"]

    print()
    print("checks")
    print(f"  product_type subset of PRODUCT_CATEGORIES: "
          f"{'PASS' if subset else 'FAIL'} ({', '.join(product_types)})")
    print(f"  price cells identical to old catalog.csv:  "
          f"{'PASS' if prices_match else 'FAIL'} ({len(new_prices)}/{len(old_prices)} SKUs)")
    print(f"  zip root member == catalog.csv:            "
          f"{'PASS' if root_matches else 'FAIL'} ({root_name})")
    print(f"  changed files:                             "
          f"{', '.join(changed) if changed else 'none'}")
    print(f"  identical files:                           "
          f"{', '.join(n for n in OUTPUT_NAMES if n not in changed) or 'none'}")
    return subset and prices_match and root_matches


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    golden = Path(args.golden).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    _refuse_web_target(out_dir)

    products_json = golden / "products.json"
    if not products_json.is_file():
        raise SystemExit(f"missing bundle file: {products_json}")
    old_csv_path = golden / "exports" / "catalog.csv"
    if not old_csv_path.is_file():
        raise SystemExit(f"missing reviewed CSV: {old_csv_path}")

    bundle = json.loads(products_json.read_text(encoding="utf-8"))
    prices = _confirmed_prices(old_csv_path)
    products = _reviewed_products(bundle["products"], prices)
    images = _image_map(golden, products)

    # The image gate and the price invariant are checked here, never assumed.
    exporter.validate_price_law(products)
    exporter.validate_image_gate(products)

    # The bundle's own seller (Toko Contoh / 6281234567890).
    seller = schemas.SellerInfo(**bundle["seller"])
    csv_text = exporter.build_csv(
        args.job_id, products, seller, args.base_url, images=images
    )
    rendered: dict[str, bytes] = {
        "catalog.csv": csv_text.encode("utf-8"),
        "photos.zip": exporter.build_zip(
            args.job_id, products, seller, args.base_url, csv_text, images=images
        ),
        "captions_instagram.txt": exporter.build_captions(
            args.job_id, products, seller, args.base_url
        ).encode("utf-8"),
        "price_list_whatsapp.txt": exporter.build_whatsapp(
            products, seller, args.base_url
        ).encode("utf-8"),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_NAMES:
        (out_dir / name).write_bytes(rendered[name])

    passed = _report(
        golden, out_dir, args.job_id, args.base_url, products, images, rendered
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
