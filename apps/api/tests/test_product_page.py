"""Public product page tests (offline, no network).

``GET /p/{job_id}/{sku}`` is the page a buyer opens from a shared link, so it
is a product surface of its own: it renders at 360px, carries only the price
the seller confirmed (or the words "harga via chat"), escapes seller-authored
text, and never invents a WhatsApp number.

Every test here runs against the real route through ``main.get_product_page``,
with the export record written to a temporary ``EXPORTS_DIR``. No server, no
network.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image

from app import exporter, main, schemas


@pytest.fixture
def exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the export-record store at a temporary directory."""
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(exporter, "EXPORTS_DIR", target)
    monkeypatch.setattr(main, "EXPORTS_DIR", target)
    return target


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Pin ``BASE_URL`` so an absolute URL in the page is a known string."""
    monkeypatch.setattr(
        main, "settings", replace(main.settings, base_url="https://kilat.example")
    )
    return "https://kilat.example"


def _seller(store_name: str = "Toko Kilat", wa_number: str = "6281234567890") -> schemas.SellerInfo:
    return schemas.SellerInfo(store_name=store_name, wa_number=wa_number)


def _product(
    sku: str = "KK-0001",
    price: int | None = 150000,
    via_chat: bool = False,
    name: str = "Tas Batik",
    description_id: str = "Tas batik motif parang.",
    description_en: str = "Batik tote bag.",
    category: str = "Handphone & Tablet",
    brand: str | None = None,
    concept: str = "bag",
    clean_url: str | None = None,
    included: bool = True,
    attributes: dict[str, str | None] | None = None,
) -> schemas.ReviewedProduct:
    """A minimal export-legal record: composite cleanup needs no fidelity verdict."""
    return schemas.ReviewedProduct(
        sku=sku,
        job_id="mock-1-2",
        source_photo_index=0,
        concept=concept,
        bbox=(1, 2, 3, 4),
        crop_url=f"/p/mock-1-2/{sku}-crop.jpg",
        clean_url=clean_url or f"/p/mock-1-2/{sku}.jpg",
        cleanup_method="composite",
        fidelity=None,
        name=name,
        description_id=description_id,
        description_en=description_en,
        category=category,
        brand=brand,
        condition="new",
        attributes=attributes or {"color": None, "size": None, "variant": None},
        price_hint_idr=None,
        confidence="medium",
        price_idr=price,
        price_via_chat=via_chat,
        availability="in stock",
        included=included,
    )


def _page(job_id: str = "mock-1-2", sku: str = "KK-0001") -> main.Response:
    return asyncio.run(main.get_product_page(job_id, sku))


def _body(job_id: str = "mock-1-2", sku: str = "KK-0001") -> str:
    return _page(job_id, sku).body.decode()


def _img_srcs(body: str) -> list[str]:
    return [chunk.split('"')[0] for chunk in body.split('<img src="')[1:]]


# ---------------------------------------------------------------------------
# Document shell
# ---------------------------------------------------------------------------


def test_page_is_self_contained_and_mobile_ready(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller())

    response = _page()
    body = response.body.decode()

    assert response.media_type == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "public, max-age=3600"
    assert '<html lang="id">' in body
    assert '<meta name="viewport" content="width=device-width,initial-scale=1">' in body
    assert "<style>" in body and "</style>" in body
    # No script, no stylesheet link, no font: nothing to fetch but the JPEGs.
    assert "<script" not in body
    assert "<link" not in body
    # Every absolute URL the document points at is our own host or WhatsApp.
    urls = re.findall(r'(?:href|src|content)="(https?://[^"]+)"', body)
    assert urls
    assert {urlparse(url).netloc for url in urls} == {"kilat.example", "wa.me"}
    # A share page that cannot be loaded over a slow phone connection is useless.
    assert len(response.body) < 12_000


def test_metadata_previews_the_product_in_whatsapp(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller())

    body = _body()

    assert "<title>Tas Batik · Toko Kilat</title>" in body
    assert '<meta property="og:title" content="Tas Batik · Toko Kilat">' in body
    assert '<meta property="og:description" content="Rp150.000 · Toko Kilat">' in body
    assert '<meta property="og:image" content="https://kilat.example/p/mock-1-2/KK-0001.jpg">' in body
    assert '<meta property="og:url" content="https://kilat.example/p/mock-1-2/KK-0001">' in body
    assert '<meta name="theme-color" content="#ffffff" media="(prefers-color-scheme:light)">' in body
    assert '<meta name="theme-color" content="#0e0f13" media="(prefers-color-scheme:dark)">' in body


# ---------------------------------------------------------------------------
# Price Law: three fields, three separate assertions
# ---------------------------------------------------------------------------


def test_confirmed_price_is_rendered(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product(price=150000)], _seller())

    body = _body()

    assert '<p class="price">Rp150.000</p>' in body
    assert "harga via chat" not in body


def test_via_chat_item_never_prints_its_residual_number(exports_dir: Path, base_url: str) -> None:
    # A via-chat record may still carry a stale price_idr; the flag wins.
    exporter.write_export_record("mock-1-2", [_product(price=99999, via_chat=True)], _seller())

    body = _body()

    assert "harga via chat" in body
    assert "Rp" not in body
    assert "99.999" not in body


def test_null_price_without_the_flag_is_via_chat(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product(price=None, via_chat=False)], _seller())

    body = _body()

    assert "harga via chat" in body
    assert "Rp" not in body


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_seller_authored_text_is_escaped(exports_dir: Path, base_url: str) -> None:
    product = _product(name='<script>alert("x")</script> Kemeja')
    exporter.write_export_record(
        "mock-1-2", [product], _seller(store_name="Toko <b>Kilat</b> & Co")
    )

    body = _body()

    assert "<script" not in body
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; Kemeja" in body
    assert "Toko &lt;b&gt;Kilat&lt;/b&gt; &amp; Co" in body
    # The same text reaches the WhatsApp message URL-encoded, never raw.
    assert "%3Cscript%3E" in body


# ---------------------------------------------------------------------------
# Call to action
# ---------------------------------------------------------------------------


def test_whatsapp_button_links_the_existing_wa_url(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller())

    body = _body()

    assert (
        'href="https://wa.me/6281234567890?text=Halo%2C%20saya%20mau%20tanya%20Tas%20Batik'
        "%20%28KK-0001%29\"" in body
    )
    assert 'rel="noopener"' in body
    assert "Pesan via WhatsApp" in body
    assert 'class="quiet"' not in body


def test_no_number_renders_a_quiet_block_and_no_link(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller(wa_number=""))

    body = _body()

    assert "wa.me" not in body
    assert '<p class="quiet">Hubungi toko</p>' in body
    assert 'class="cta"' not in body


# ---------------------------------------------------------------------------
# Gallery
# ---------------------------------------------------------------------------


def test_merged_angles_become_carousel_slides(exports_dir: Path, base_url: str) -> None:
    parent = _product("KK-0001", clean_url="/p/mock-1-2/KK-0002.jpg")
    child = _product("KK-0002", concept="bag@merge:KK-0001", included=False,
                     clean_url="/p/mock-1-2/KK-0001.jpg")
    exporter.write_export_record("mock-1-2", [parent, child], _seller())

    body = _body()

    assert body.count('<div class="slide">') == 2
    assert "2 foto" in body
    srcs = _img_srcs(body)
    assert srcs[0].endswith("/p/mock-1-2/KK-0002.jpg")  # promoted main photo
    assert srcs[1].endswith("/p/mock-1-2/KK-0001.jpg")  # the angle it replaced
    assert 'aria-label="Galeri foto produk"' in body


def test_single_photo_renders_no_carousel_chrome(exports_dir: Path, base_url: str) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller())

    body = _body()

    assert body.count('<div class="slide">') == 1
    assert 'class="count"' not in body
    assert "Galeri foto produk" not in body


# ---------------------------------------------------------------------------
# Optional sections
# ---------------------------------------------------------------------------


def test_description_and_facts_render_when_the_record_carries_them(
    exports_dir: Path, base_url: str
) -> None:
    product = _product(brand="Acme", attributes={"color": "Biru", "size": "M", "variant": None})
    exporter.write_export_record("mock-1-2", [product], _seller())

    body = _body()

    assert '<p class="desc">Tas batik motif parang.</p>' in body
    assert "<li>Handphone &amp; Tablet</li>" in body
    assert "<li>Acme</li>" in body
    assert "<li>Biru</li>" in body
    assert "<li>M</li>" in body
    assert "<li>Baru</li>" in body


def test_absent_optional_sections_are_omitted(exports_dir: Path, base_url: str) -> None:
    # Category, brand and the variant attributes may all be empty; the
    # condition is a required field, so it is the one fact that always ships.
    product = _product(description_id="", description_en="", category="")
    exporter.write_export_record("mock-1-2", [product], _seller())

    body = _body()

    assert 'class="desc"' not in body
    assert '<ul class="facts"><li>Baru</li></ul>' in body


# ---------------------------------------------------------------------------
# Missing records
# ---------------------------------------------------------------------------


def test_unknown_sku_on_a_known_job_renders_a_graceful_page(
    exports_dir: Path, base_url: str
) -> None:
    exporter.write_export_record("mock-1-2", [_product()], _seller())

    body = _body(sku="KK-0007")

    assert "Produk belum tersedia" in body
    assert "KK-0007" in body
    assert "<style>" in body
    assert "wa.me" not in body  # a stale link offers nothing it cannot honour
    assert "harga via chat" not in body


def test_unknown_job_renders_the_same_graceful_page(exports_dir: Path, base_url: str) -> None:
    body = _body(job_id="job-absent", sku="KK-0001")

    assert "Produk belum tersedia" in body
    assert "KK-0001" in body
    assert 'name="viewport"' in body


# ---------------------------------------------------------------------------
# The image route itself is untouched
# ---------------------------------------------------------------------------


def test_image_route_still_returns_the_artefact_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = tmp_path / "jobs"
    clean = jobs / "job-live-9" / "clean"
    clean.mkdir(parents=True)
    Image.new("RGB", (12, 12), (10, 20, 30)).save(clean / "KK-0001.png", format="PNG")
    monkeypatch.setattr(exporter, "JOBS_DIR", jobs)

    response = asyncio.run(main.get_product_image("job-live-9", "KK-0001"))

    assert response.media_type == "image/jpeg"
    assert response.body[:2] == b"\xff\xd8"
    assert response.body == exporter.resolve_image_bytes("job-live-9", "KK-0001")
    assert response.headers["cache-control"] == "public, max-age=3600"
