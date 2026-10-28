"""Exporter unit tests (offline, no network)."""

from __future__ import annotations

import io
import csv
import json
import asyncio
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from app import exporter, main, schemas


@pytest.fixture
def exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(exporter, "EXPORTS_DIR", target)
    monkeypatch.setattr(main, "EXPORTS_DIR", target)
    return target


def _product(
    sku="KK-0001",
    price=150000,
    via_chat=False,
    included=True,
    brand=None,
    cleanup_method="muse-image",
    fidelity=None,
    concept="bag",
):
    if cleanup_method == "muse-image" and fidelity is None:
        fidelity = {  # A fully positive verdict is the only legal muse-image record.
            "same_product": True,
            "color_match": True,
            "logo_text_match": True,
            "reason": "unchanged",
        }
    return schemas.ReviewedProduct(
        sku=sku,
        job_id="mock-1-2",
        source_photo_index=0,
        concept=concept,
        bbox=(1, 2, 3, 4),
        crop_url=f"/p/mock-1-2/{sku}-crop.jpg",
        clean_url=f"/p/mock-1-2/{sku}.jpg",
        cleanup_method=cleanup_method,
        fidelity=fidelity,
        name="Tas Batik",
        description_id="Tas batik motif parang.",
        description_en="Batik tote bag.",
        category="Handphone & Tablet",
        brand=brand,
        condition="new",
        attributes={"color": None, "size": None, "variant": None},
        price_hint_idr=None,
        confidence="medium",
        price_idr=price if not via_chat else 99999,
        price_via_chat=via_chat,
        availability="in stock",
        included=included,
    )


def test_import_has_no_side_effects() -> None:
    import app.exporter  # noqa: F401


def test_slugify() -> None:
    assert exporter.slugify_hashtag("Handphone & Tablet") == "handphone_tablet"


def test_format_price() -> None:
    assert exporter.format_price_wire(1500000) == "1500000 IDR"
    assert exporter.format_price_display(1500000) == "Rp1.500.000"


def test_csv_excludes_via_chat_and_included_false(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, None),
        _product("KK-0002", 0, True, True, "Acme"),
        _product("KK-0003", 50000, False, False, None),
    ]
    # via-chat item has residual price_idr set; force it
    products[1].price_idr = 99999
    csv_text = exporter.build_csv("mock-1-2", products, seller, "http://example.com")
    assert "KK-0001" in csv_text
    assert "KK-0002" not in csv_text
    assert "KK-0003" not in csv_text
    assert "150000 IDR" in csv_text
    assert " 0 IDR" not in csv_text
    assert "\r\n" in csv_text
    # brand fallback to shop name
    assert "Toko Kilat" in csv_text


def test_price_missing_raises() -> None:
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    p = _product("KK-0009", None, False, True, None)
    p.price_idr = None
    with pytest.raises(exporter.PriceMissingError) as ei:
        exporter.validate_price_law([p])
    assert ei.value.skus == ["KK-0009"]


def test_run_export_price_missing_returns_400_via_route(exports_dir: Path) -> None:
    # covered by route tests; ensure run_export raises
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    p = _product("KK-0009", None, False, True, None)
    p.price_idr = None
    with pytest.raises(exporter.PriceMissingError):
        exporter.run_export("mock-1-2", [p], seller, "http://example.com")


# --- the image gate: no ungated generative image reaches an export ----------


def test_ungated_muse_image_without_verdict_raises() -> None:
    # Hand-crafted illegal record: generative method, no fidelity verdict at all.
    p = _product("KK-0001")
    p.fidelity = None
    with pytest.raises(exporter.UngatedImageError) as ei:
        exporter.validate_image_gate([p])
    assert ei.value.skus == ["KK-0001"]


def test_ungated_muse_image_with_failing_verdict_raises() -> None:
    p = _product(
        "KK-0002",
        cleanup_method="muse-image",
        fidelity={"same_product": False, "color_match": True, "logo_text_match": True, "reason": "redesigned"},
    )
    with pytest.raises(exporter.UngatedImageError) as ei:
        exporter.validate_image_gate([p])
    assert ei.value.skus == ["KK-0002"]


def test_ungated_muse_image_with_partial_verdict_raises() -> None:
    p = _product(
        "KK-0003",
        cleanup_method="muse-image",
        fidelity={"same_product": True, "color_match": False, "logo_text_match": True, "reason": "hue shifted"},
    )
    with pytest.raises(exporter.UngatedImageError):
        exporter.validate_image_gate([p])


def test_composite_products_export_legally(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    # Composite is unconditionally legal — with or without any verdict.
    products = [
        _product("KK-0001", cleanup_method="composite", fidelity=None),
        _product(
            "KK-0002",
            cleanup_method="composite",
            fidelity={"same_product": False, "color_match": False, "logo_text_match": False, "reason": "gate rejected"},
        ),
    ]
    files = exporter.run_export("mock-1-2", products, seller, "http://example.com")
    assert len(files) == 4


def test_gate_passed_muse_image_exports_legally(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    products = [_product("KK-0001")]
    files = exporter.run_export("mock-1-2", products, seller, "http://example.com")
    assert len(files) == 4


def test_ungated_check_skips_excluded_products() -> None:
    p = _product("KK-0004", included=False)
    p.fidelity = None
    exporter.validate_image_gate([p])  # must not raise


def test_run_export_blocks_ungated_record(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    p = _product("KK-0001")
    p.fidelity = None
    with pytest.raises(exporter.UngatedImageError):
        exporter.run_export("mock-1-2", [p], seller, "http://example.com")


def test_run_export_creates_four_files(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, None)]
    files = exporter.run_export("mock-1-2", products, seller, "http://example.com")
    assert len(files) == 4
    out = exports_dir / "mock-1-2"
    assert (out / "katalog-meta.csv").is_file()
    assert (out / "katalog-kilat-images.zip").is_file()
    # zip contains csv + jpg + crop
    with zipfile.ZipFile(out / "katalog-kilat-images.zip") as zf:
        names = zf.namelist()
        assert "katalog-mock-1-2.csv" in names
        assert "KK-0001.jpg" in names
        assert "KK-0001-crop.jpg" in names


def test_whatsapp_truncation(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="S", wa_number="1")
    products = [_product(f"KK-{i:04d}", 1000, False, True, None) for i in range(1, 33)]
    text = exporter.build_whatsapp(products, seller, "http://example.com")
    assert "+2 produk lainnya di katalog" in text
    # only 30 numbered lines
    assert "30. " in text
    assert "31. " not in text
    assert "https://wa.me/?text=" in text


def test_caption_hashtag_slug() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="1")
    products = [_product("KK-0001", 150000, False, True, None)]
    text = exporter.build_captions("mock-1-2", products, seller, "http://example.com")
    assert "#jualhandphone_tablet" in text
    assert "#katalogkilat" in text


def test_placeholder_deterministic() -> None:
    a = exporter.resolve_image_bytes("mock-1-2", "KK-0001")
    b = exporter.resolve_image_bytes("mock-1-2", "KK-0001")
    assert a == b
    assert a[:2] == b"\xff\xd8"  # jpeg


# ---------------------------------------------------------------------------
# Crop path: live pipeline spelling ``crops/{sku}-crop.png``
# ---------------------------------------------------------------------------


def _live_crop_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job_id: str) -> Path:
    """Point ``JOBS_DIR`` at a tmp tree and return its ``crops/`` directory."""
    jobs = tmp_path / "jobs"
    crops = jobs / job_id / "crops"
    crops.mkdir(parents=True)
    monkeypatch.setattr(exporter, "JOBS_DIR", jobs)
    return crops


def test_crop_path_resolves_live_pipeline_crop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crops = _live_crop_job(tmp_path, monkeypatch, "job-live-1")
    crop_file = crops / "KK-0001-crop.png"
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(crop_file, format="PNG")

    # Both call shapes reach the one file the pipeline wrote.
    assert exporter.crop_path("job-live-1", "KK-0001") == crop_file
    assert exporter.crop_path("job-live-1", "KK-0001-crop") == crop_file
    assert exporter.crop_available("job-live-1", "KK-0001") is True
    assert exporter.crop_available("job-live-1", "KK-0001-crop") is True

    # And the public JPG route resolves it instead of 404-ing.
    jpg = exporter.resolve_image_bytes("job-live-1", "KK-0001-crop")
    assert jpg[:2] == b"\xff\xd8"


def test_crop_path_prefers_plain_spelling_when_both_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crops = _live_crop_job(tmp_path, monkeypatch, "job-live-2")
    plain = crops / "KK-0001.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(plain, format="PNG")
    Image.new("RGB", (8, 8), (4, 5, 6)).save(crops / "KK-0001-crop.png", format="PNG")

    assert exporter.crop_path("job-live-2", "KK-0001") == plain


def test_crop_available_false_when_no_crop_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crops = _live_crop_job(tmp_path, monkeypatch, "job-live-3")

    assert exporter.crop_available("job-live-3", "KK-0001") is False
    # No file either way: the plain spelling is returned for the caller to check.
    assert exporter.crop_path("job-live-3", "KK-0001") == crops / "KK-0001.png"


def test_csv_live_job_links_the_pipeline_crop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crops = _live_crop_job(tmp_path, monkeypatch, "job-live-4")
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(crops / "KK-0001-crop.png", format="PNG")
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")

    csv_text = exporter.build_csv(
        "job-live-4", [_product("KK-0001", concept="smartphone")], seller, "http://example.com"
    )
    rows = list(csv.reader(io.StringIO(csv_text)))
    additional = rows[1][exporter.CSV_COLUMNS.index("additional_image_link")]
    assert additional == "http://example.com/p/job-live-4/KK-0001-crop.jpg"


# ---------------------------------------------------------------------------
# Multi-photo per SKU: merged angle photos (``concept = "{base}@merge:{sku}"``)
# ---------------------------------------------------------------------------


def _merged(sku: str, parent_sku: str, **kwargs) -> schemas.ReviewedProduct:
    """A child angle photo merged into ``parent_sku``: excluded + marked."""
    return _product(sku, included=False, concept=f"smartphone@merge:{parent_sku}", **kwargs)


def test_merged_parent_sku_reads_the_marker() -> None:
    assert exporter.merged_parent_sku(_merged("KK-0002", "KK-0001")) == "KK-0001"
    # An included product is never a child, marker or not.
    assert exporter.merged_parent_sku(_product("KK-0001", concept="smartphone@merge:KK-0009")) is None
    # No marker at all.
    assert exporter.merged_parent_sku(_product("KK-0001", concept="smartphone")) is None


def test_merged_parent_sku_rejects_self_and_empty_target() -> None:
    assert exporter.merged_parent_sku(_product("KK-0001", included=False, concept="bag@merge:KK-0001")) is None
    assert exporter.merged_parent_sku(_product("KK-0002", included=False, concept="bag@merge:   ")) is None


def test_merged_children_map_orders_children_by_parent() -> None:
    products = [
        _product("KK-0001", concept="smartphone"),
        _merged("KK-0002", "KK-0001"),
        _merged("KK-0003", "KK-0001"),
        _product("KK-0009", concept="headphone"),
        _merged("KK-0010", "KK-0077"),  # parent absent from the payload -> dropped
    ]
    children = exporter.merged_children_map(products)

    assert set(children) == {"KK-0001", "KK-0009"}
    assert [c.sku for c in children["KK-0001"]] == ["KK-0002", "KK-0003"]
    assert children["KK-0009"] == []


def test_csv_merges_angle_photos_into_additional_image_link(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, "Acme", concept="smartphone"),
        _merged("KK-0002", "KK-0001"),
        _merged("KK-0003", "KK-0001"),
    ]

    csv_text = exporter.build_csv("mock-1-2", products, seller, "http://example.com")
    rows = list(csv.reader(io.StringIO(csv_text)))

    assert rows[0] == exporter.CSV_COLUMNS
    assert len(rows) == 2  # header + one merged catalog entry
    assert rows[1][0] == "KK-0001"
    additional = rows[1][exporter.CSV_COLUMNS.index("additional_image_link")]
    assert additional.split(",") == [
        "http://example.com/p/mock-1-2/KK-0002.jpg",
        "http://example.com/p/mock-1-2/KK-0003.jpg",
        "http://example.com/p/mock-1-2/KK-0001-crop.jpg",
        "http://example.com/p/mock-1-2/KK-0002-crop.jpg",
        "http://example.com/p/mock-1-2/KK-0003-crop.jpg",
    ]
    assert [r[0] for r in rows[1:]] == ["KK-0001"]


def test_csv_single_photo_row_is_unchanged_by_merging() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, "Acme", concept="smartphone")]

    csv_text = exporter.build_csv("mock-1-2", products, seller, "http://example.com")
    rows = list(csv.reader(io.StringIO(csv_text)))

    assert rows[1][exporter.CSV_COLUMNS.index("additional_image_link")] == (
        "http://example.com/p/mock-1-2/KK-0001-crop.jpg"
    )


def test_photo_sku_reads_the_promoted_url() -> None:
    p = _product("KK-0001")
    assert exporter.photo_sku(p) == "KK-0001"  # /p/mock-1-2/KK-0001.jpg
    p.clean_url = "/p/mock-1-2/KK-0002.jpg"
    assert exporter.photo_sku(p) == "KK-0002"
    p.clean_url = "/p/mock-1-2/KK-0003-crop.png"
    assert exporter.photo_sku(p) == "KK-0003"
    # No parsable SKU -> the record's own sku is the answer.
    p.clean_url = "https://cdn.example.com/whatever.jpg"
    assert exporter.photo_sku(p) == "KK-0001"


def test_csv_promoted_angle_photo_becomes_the_main_image() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    parent = _product("KK-0001", 150000, False, True, "Acme", concept="smartphone")
    child = _merged("KK-0002", "KK-0001")
    # The seller promoted the angle photo to main: the two image URLs swap.
    parent.clean_url, child.clean_url = "/p/mock-1-2/KK-0002.jpg", "/p/mock-1-2/KK-0001.jpg"
    parent.crop_url, child.crop_url = "/p/mock-1-2/KK-0002-crop.jpg", "/p/mock-1-2/KK-0001-crop.jpg"

    csv_text = exporter.build_csv("mock-1-2", [parent, child], seller, "http://example.com")
    rows = list(csv.reader(io.StringIO(csv_text)))

    row = rows[1]
    # Identity is still the parent SKU — only the picture moved.
    assert row[exporter.CSV_COLUMNS.index("id")] == "KK-0001"
    assert row[exporter.CSV_COLUMNS.index("link")] == "http://example.com/p/mock-1-2/KK-0001"
    assert row[exporter.CSV_COLUMNS.index("image_link")] == "http://example.com/p/mock-1-2/KK-0002.jpg"
    additional = row[exporter.CSV_COLUMNS.index("additional_image_link")].split(",")
    assert additional[0] == "http://example.com/p/mock-1-2/KK-0001.jpg"


def test_zip_promoted_angle_photo_writes_the_swapped_images(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    parent = _product("KK-0001", 150000, False, True, "Acme", concept="smartphone")
    child = _merged("KK-0002", "KK-0001")
    parent.clean_url, child.clean_url = "/p/mock-1-2/KK-0002.jpg", "/p/mock-1-2/KK-0001.jpg"

    csv_text = exporter.build_csv("mock-1-2", [parent, child], seller, "http://example.com")
    data = exporter.build_zip("mock-1-2", [parent, child], seller, "http://example.com", csv_text)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()

    assert names[0] == "katalog-mock-1-2.csv"
    for sku in ("KK-0001", "KK-0002"):
        assert f"{sku}.jpg" in names
        assert f"{sku}-crop.jpg" in names
    assert len(names) == len(set(names))


def test_product_page_shows_the_promoted_photo(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    parent = _product("KK-0001", 150000, False, True, "Acme", concept="smartphone")
    child = _merged("KK-0002", "KK-0001")
    parent.clean_url, child.clean_url = "/p/mock-1-2/KK-0002.jpg", "/p/mock-1-2/KK-0001.jpg"
    exporter.write_export_record("mock-1-2", [parent, child], seller)

    response = asyncio.run(main.get_product_page("mock-1-2", "KK-0001"))
    body = response.body.decode()

    img_srcs = [chunk.split('"')[0] for chunk in body.split('<img src="')[1:]]
    assert img_srcs[0].endswith("/p/mock-1-2/KK-0002.jpg")  # promoted main photo
    assert img_srcs[1].endswith("/p/mock-1-2/KK-0001.jpg")  # the angle it replaced
    assert "KK-0001" in body  # identity in the heading is untouched


def test_csv_caps_additional_image_link_at_twenty() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, "Acme", concept="smartphone")]
    products += [_merged(f"KK-{i:04d}", "KK-0001") for i in range(2, 20)]  # 18 children -> 36 urls

    csv_text = exporter.build_csv("mock-1-2", products, seller, "http://example.com")
    rows = list(csv.reader(io.StringIO(csv_text)))
    urls = rows[1][exporter.CSV_COLUMNS.index("additional_image_link")].split(",")

    assert len(urls) == 20
    assert urls == list(dict.fromkeys(urls))  # no duplicates


def test_price_law_ignores_merged_children_without_price() -> None:
    products = [
        _product("KK-0001", 150000, concept="smartphone"),
        _merged("KK-0002", "KK-0001", price=None),
        _merged("KK-0003", "KK-0001", price=None),
    ]

    assert [p.price_idr for p in products[1:]] == [None, None]
    exporter.validate_price_law(products)  # must not raise


def test_gate_checks_merged_children_without_verdict() -> None:
    child = _merged("KK-0002", "KK-0001")
    child.fidelity = None

    with pytest.raises(exporter.UngatedImageError) as ei:
        exporter.validate_image_gate([_product("KK-0001", concept="smartphone"), child])
    assert ei.value.skus == ["KK-0002"]


def test_gate_checks_merged_children_with_failing_verdict() -> None:
    child = _merged(
        "KK-0002",
        "KK-0001",
        fidelity={"same_product": False, "color_match": True, "logo_text_match": True, "reason": "redesigned"},
    )

    with pytest.raises(exporter.UngatedImageError) as ei:
        exporter.validate_image_gate([_product("KK-0001", concept="smartphone"), child])
    assert ei.value.skus == ["KK-0002"]


def test_gate_allows_gate_passed_merged_children() -> None:
    exporter.validate_image_gate(
        [_product("KK-0001", concept="smartphone"), _merged("KK-0002", "KK-0001")]
    )  # fully positive verdict on the child -> legal


def test_gate_skips_merged_children_of_an_excluded_parent() -> None:
    parent = _product("KK-0001", included=False, concept="smartphone")
    child = _merged("KK-0002", "KK-0001")
    child.fidelity = None

    exporter.validate_image_gate([parent, child])  # neither is exported -> no raise


def test_zip_includes_merged_angle_photos(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, "Acme", concept="smartphone"),
        _merged("KK-0002", "KK-0001"),
        _merged("KK-0003", "KK-0001"),
    ]
    csv_text = exporter.build_csv("mock-1-2", products, seller, "http://example.com")

    data = exporter.build_zip("mock-1-2", products, seller, "http://example.com", csv_text)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()

    assert names[0] == "katalog-mock-1-2.csv"
    for sku in ("KK-0001", "KK-0002", "KK-0003"):
        assert f"{sku}.jpg" in names
        assert f"{sku}-crop.jpg" in names
    assert len(names) == len(set(names))  # no duplicate zip entries


def test_run_export_with_merged_children_writes_four_files(exports_dir: Path) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, "Acme", concept="smartphone"),
        _merged("KK-0002", "KK-0001", price=None),
    ]

    files = exporter.run_export("mock-1-2", products, seller, "http://example.com")

    assert len(files) == 4
    out = exports_dir / "mock-1-2"
    # CRLF survives on disk; ``read_text`` would translate it away.
    assert (out / "katalog-meta.csv").read_bytes().count(b"\r\n") == 2  # header + one entry
    with zipfile.ZipFile(out / "katalog-kilat-images.zip") as zf:
        assert "KK-0002.jpg" in zf.namelist()


# ---------------------------------------------------------------------------
# build_zip image-source seam: golden regeneration supplies its own JPGs
# ---------------------------------------------------------------------------


def test_build_zip_reads_images_from_the_supplied_map() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, "Acme", concept="smartphone"),
        _product("KK-0002", 75000, False, True, None, concept="smartphone box"),
    ]
    # A non-mock job id on purpose: a mock id would let resolve_image_bytes
    # invent a placeholder and the test would pass without touching the map.
    csv_text = exporter.build_csv("job-golden-1", products, seller, "http://example.com")
    images = {
        "KK-0001.jpg": b"\xff\xd8primary-one",
        "KK-0001-crop.jpg": b"\xff\xd8crop-one",
        "KK-0002.jpg": b"\xff\xd8primary-two",
        "KK-0002-crop.jpg": b"\xff\xd8crop-two",
    }

    data = exporter.build_zip(
        "job-golden-1", products, seller, "http://example.com", csv_text, images=images
    )

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == [
            "katalog-job-golden-1.csv",
            "KK-0001.jpg",
            "KK-0001-crop.jpg",
            "KK-0002.jpg",
            "KK-0002-crop.jpg",
        ]
        assert zf.read("katalog-job-golden-1.csv") == csv_text.encode("utf-8")
        for name, payload in images.items():
            assert zf.read(name) == payload  # verbatim bytes, never re-encoded


def test_build_zip_incomplete_image_map_raises_naming_the_sku() -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0007", 75000, False, True, None)]
    csv_text = exporter.build_csv("job-golden-2", products, seller, "http://example.com")

    with pytest.raises(KeyError) as ei:
        exporter.build_zip(
            "job-golden-2",
            products,
            seller,
            "http://example.com",
            csv_text,
            images={"KK-0007-crop.jpg": b"\xff\xd8crop"},
        )

    message = str(ei.value)
    assert "KK-0007" in message
    assert "incomplete" in message


# ---------------------------------------------------------------------------
# build_csv crop-source seam: golden regeneration supplies its own map
# ---------------------------------------------------------------------------


def _additional_cell(csv_text: str) -> str:
    """The ``additional_image_link`` cell of the single data row."""
    rows = list(csv.reader(io.StringIO(csv_text)))
    return rows[1][exporter.CSV_COLUMNS.index("additional_image_link")]


def test_build_csv_reads_crop_availability_from_the_supplied_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, "Acme", concept="smartphone")]
    # No job folder at all: the map alone decides.
    monkeypatch.setattr(exporter, "JOBS_DIR", tmp_path / "jobs")
    images = {"KK-0001.jpg": b"\xff\xd8primary", "KK-0001-crop.jpg": b"\xff\xd8crop"}

    csv_text = exporter.build_csv(
        "job-golden-3", products, seller, "http://example.com", images=images
    )

    assert _additional_cell(csv_text) == "http://example.com/p/job-golden-3/KK-0001-crop.jpg"


def test_build_csv_omits_a_crop_absent_from_the_supplied_map(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, "Acme", concept="smartphone")]
    # The job folder *does* hold the crop; the map does not name it. The map
    # wins: an absent key is a legal "no crop", never a dangling link.
    crops = _live_crop_job(tmp_path, monkeypatch, "job-golden-4")
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(crops / "KK-0001-crop.png", format="PNG")

    csv_text = exporter.build_csv(
        "job-golden-4",
        products,
        seller,
        "http://example.com",
        images={"KK-0001.jpg": b"\xff\xd8primary"},
    )

    assert _additional_cell(csv_text) == ""


def test_build_csv_seam_keeps_merged_angle_shot_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [
        _product("KK-0001", 150000, False, True, "Acme", concept="smartphone"),
        _merged("KK-0002", "KK-0001"),
        _merged("KK-0003", "KK-0001"),
    ]
    monkeypatch.setattr(exporter, "JOBS_DIR", tmp_path / "jobs")  # map only
    skus = ("KK-0001", "KK-0002", "KK-0003")
    images = {f"{sku}.jpg": b"\xff\xd8primary" for sku in skus}
    images |= {f"{sku}-crop.jpg": b"\xff\xd8crop" for sku in skus}

    csv_text = exporter.build_csv(
        "job-golden-5", products, seller, "http://example.com", images=images
    )

    # Angle shots first, then the primary's crop, then the children's crops.
    assert _additional_cell(csv_text) == (
        "http://example.com/p/job-golden-5/KK-0002.jpg,"
        "http://example.com/p/job-golden-5/KK-0003.jpg,"
        "http://example.com/p/job-golden-5/KK-0001-crop.jpg,"
        "http://example.com/p/job-golden-5/KK-0002-crop.jpg,"
        "http://example.com/p/job-golden-5/KK-0003-crop.jpg"
    )


def test_build_csv_without_the_seam_still_reads_the_job_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crops = _live_crop_job(tmp_path, monkeypatch, "job-live-5")
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(crops / "KK-0001-crop.png", format="PNG")
    seller = schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")
    products = [_product("KK-0001", 150000, False, True, "Acme", concept="smartphone")]

    # No ``images`` keyword at all: ``None`` keeps the job-folder behaviour.
    csv_text = exporter.build_csv("job-live-5", products, seller, "http://example.com")

    assert _additional_cell(csv_text) == "http://example.com/p/job-live-5/KK-0001-crop.jpg"


# ---------------------------------------------------------------------------
# The regeneration script's own path derivation: scripts/ is one level below
# the service root in both layouts
# ---------------------------------------------------------------------------


def test_regen_script_derives_web_dir_from_the_service_root() -> None:
    """``WEB_DIR`` must not assume a checkout's depth.

    The image runs the module from ``/srv/scripts/``, whose ``.parents`` chain is
    ``[/srv, /]`` — a ``parents[3]`` lookup raises ``IndexError`` at import time,
    before argparse. ``scripts/`` sits exactly one level below the service root
    in both layouts, so the constant is derived from ``parents[1]``.
    """
    import scripts
    import scripts.regen_golden_exports as regen

    service_root = Path(scripts.__file__).resolve().parents[1]
    assert regen.WEB_DIR == service_root.parent / "web"
    assert regen.WEB_DIR.name == "web"


# ---------------------------------------------------------------------------
# The regeneration script's price gate: an excluded record needs no price
# ---------------------------------------------------------------------------


def _bundle_record(
    sku: str, *, included: bool, concept: str = "smartphone"
) -> dict[str, object]:
    """One ``products.json`` record, complete enough for ``ReviewedProduct``."""
    return {
        "sku": sku,
        "job_id": "golden-x",
        "source_photo_index": 0,
        "concept": concept,
        "bbox": [1, 2, 3, 4],
        "crop_url": f"/samples/x/golden/images/{sku}-crop.jpg",
        "clean_url": f"/samples/x/golden/images/{sku}.jpg",
        "cleanup_method": "composite",
        "fidelity": None,
        "name": "Tas Batik",
        "description_id": "Tas batik motif parang.",
        "description_en": "Batik tote bag.",
        "category": "Aksesoris HP",
        "brand": None,
        "condition": "new",
        "attributes": {"color": None, "size": None, "variant": None},
        "price_hint_idr": None,
        "price_suggestion_idr": 275000,
        "confidence": "medium",
        "price_idr": None,
        "price_via_chat": False,
        "availability": "in stock",
        "included": included,
    }


def test_regen_accepts_a_priceless_excluded_record_and_still_refuses_an_included_one() -> None:
    """Merged/excluded records have no CSV row, so they cannot need a price.

    Packs b/c/d carry such records; before this, ``_reviewed_products`` aborted
    on the first one and the script could only ever render Pack A.
    """
    import scripts.regen_golden_exports as regen

    records = [
        _bundle_record("KK-0001", included=True),
        _bundle_record("KK-0002", included=False, concept="smartphone@merge:KK-0001"),
    ]

    products = regen._reviewed_products(records, {"KK-0001": 150000})

    assert [p.sku for p in products] == ["KK-0001", "KK-0002"]
    assert products[0].price_idr == 150000  # the seller's confirmed value
    assert products[1].price_idr is None  # excluded: no row, no price, no guess
    # ``price_suggestion_idr`` is dropped for both — ReviewedProduct forbids it,
    # so a leaked suggestion would have raised above.
    assert all(not hasattr(p, "price_suggestion_idr") for p in products)

    with pytest.raises(SystemExit) as ei:
        regen._reviewed_products([_bundle_record("KK-0003", included=True)], {})
    assert "KK-0003" in str(ei.value)
    assert "no confirmed price" in str(ei.value)

