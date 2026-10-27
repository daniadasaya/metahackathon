"""Exporter unit tests (offline, no network)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

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
        concept="bag",
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
