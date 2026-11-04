"""Price-prose tests: no generated description may carry an unconfirmed price.

The seller's confirmed ``price_idr`` is the only number a buyer may read. Stage E
sometimes restates a price inside the prose ("harga hemat hanya Rp 35.000"),
which duplicates or contradicts that value on the CSV, the Instagram caption,
the WhatsApp list and the public page. :func:`exporter.strip_price_mentions` is
the deterministic fix; these tests pin its false-positive boundary and prove
every export surface is clean.

Offline, no network, no job folder: every call is a pure function.
"""

from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import replace
from pathlib import Path

import pytest

from app import exporter, main, pipeline, schemas

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def exports_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "exports"
    target.mkdir()
    monkeypatch.setattr(exporter, "EXPORTS_DIR", target)
    monkeypatch.setattr(main, "EXPORTS_DIR", target)
    return target


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(
        main, "settings", replace(main.settings, base_url="https://kilat.example")
    )
    return "https://kilat.example"


def _seller() -> schemas.SellerInfo:
    return schemas.SellerInfo(store_name="Toko Kilat", wa_number="6281234567890")


def _product(
    sku: str = "KK-0001",
    price: int | None = 150000,
    via_chat: bool = False,
    name: str = "Tas Batik",
    description_id: str = "Tas batik motif parang.",
    description_en: str = "Batik tote bag.",
    category: str = "Tas",
    brand: str | None = None,
) -> schemas.ReviewedProduct:
    """An export-legal record: composite cleanup needs no fidelity verdict."""
    return schemas.ReviewedProduct(
        sku=sku,
        job_id="mock-1-2",
        source_photo_index=0,
        concept="bag",
        bbox=(1, 2, 3, 4),
        crop_url=f"/p/mock-1-2/{sku}-crop.jpg",
        clean_url=f"/p/mock-1-2/{sku}.jpg",
        cleanup_method="composite",
        fidelity=None,
        name=name,
        description_id=description_id,
        description_en=description_en,
        category=category,
        brand=brand,
        condition="new",
        attributes={"color": None, "size": None, "variant": None},
        price_hint_idr=None,
        confidence="medium",
        price_idr=price,
        price_via_chat=via_chat,
        availability="in stock",
        included=True,
    )


def _entry(
    description_id: str = "Tas batik motif parang.",
    description_en: str = "Batik tote bag.",
) -> schemas.ProductEntry:
    return schemas.ProductEntry(
        name="Tas Batik",
        description_id=description_id,
        description_en=description_en,
        category="Tas",
        brand=None,
        condition="new",
        attributes={"color": None, "size": None, "variant": None},
        price_hint_idr=None,
        price_suggestion_idr=None,
        confidence="medium",
    )


def _csv_rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _page_body(sku: str = "KK-0001") -> str:
    return asyncio.run(main.get_product_page("mock-1-2", sku)).body.decode()


# ---------------------------------------------------------------------------
# The helper — every spelling that is a price
# ---------------------------------------------------------------------------

PRICE_ONLY = [
    "Rp 35.000",
    "Rp35.000",
    "Rp. 35.000",
    "Rp35rb",
    "35 ribu",
    "harga 35k",
    "hanya 35k",
    "cuma 35k",
    "35.000 rupiah",
    "IDR 35000",
    "35000 IDR",
    "1,5 juta",
    "Rp 1.5jt",
    "USD 25",
    "$25",
    "25 USD",
    "Rp1.500.000",
    "1.500.000 rupiah",
    "HARGA RP 35.000",
    "Rp 2 juta",
]


@pytest.mark.parametrize("text", PRICE_ONLY)
def test_a_price_sentence_is_removed_entirely(text: str) -> None:
    # The only sentence carries a price, so nothing survives: an empty string
    # beats a fragment like "harga hemat".
    assert exporter.strip_price_mentions(text) == ""


# ---------------------------------------------------------------------------
# The helper — numbers that are not prices stay untouched
# ---------------------------------------------------------------------------

NOT_A_PRICE = [
    "30 ml",
    "256GB",
    "6.7 inch",
    "tahun 2024",
    "12 bulan garansi",
    "iPhone 12",
    "Kamera 12MP",
    "Baterai 5000mAh",
    "RAM 8GB",
    "4K video",
    "Baju ukuran L, panjang 72 cm",
    "iPhone 12 Pro Max 256GB",
    "Garansi resmi 12 bulan, baterai 5000mAh, layar 6.7 inch.",
    "Tas batik motif parang.",
]


@pytest.mark.parametrize("text", NOT_A_PRICE)
def test_non_price_numbers_pass_through_unchanged(text: str) -> None:
    assert exporter.strip_price_mentions(text) == text


# ---------------------------------------------------------------------------
# The helper — sentence surgery
# ---------------------------------------------------------------------------


def test_a_price_sentence_is_dropped_and_the_rest_kept() -> None:
    assert (
        exporter.strip_price_mentions(
            "harga hemat Rp 35.000. Chat sekarang untuk tanya ukuran"
        )
        == "Chat sekarang untuk tanya ukuran."
    )


def test_a_single_price_sentence_returns_empty_not_a_fragment() -> None:
    assert exporter.strip_price_mentions("Kondisi bagus, harga Rp 1.500.000.") == ""


def test_the_surviving_sentences_keep_their_order_and_punctuation() -> None:
    assert (
        exporter.strip_price_mentions(
            "HP second Samsung H6 warna biru. Kondisi normal, fungsi lancar. "
            "Bisa COD. Harga Rp 1.500.000."
        )
        == "HP second Samsung H6 warna biru. Kondisi normal, fungsi lancar. Bisa COD."
    )


def test_an_rp_abbreviation_does_not_split_the_sentence() -> None:
    # "Rp." is a currency abbreviation: splitting there would hide the amount
    # from the detector and let the price through.
    assert exporter.strip_price_mentions("Bisa COD, harga Rp. 35.000 nego") == ""
    assert exporter.strip_price_mentions("Harga Rp. 35.000. Bisa COD.") == "Bisa COD."


def test_a_newline_separates_sentences() -> None:
    assert (
        exporter.strip_price_mentions("Line one.\nHarga Rp 35.000.\nLine three.")
        == "Line one. Line three."
    )


def test_empty_and_blank_input_are_safe() -> None:
    assert exporter.strip_price_mentions("") == ""
    assert exporter.strip_price_mentions("   ") == ""


def test_the_helper_is_idempotent() -> None:
    text = "Kemeja denim. Stok terbatas, harga hemat Rp 35.000. Chat sekarang!"
    once = exporter.strip_price_mentions(text)
    assert once == "Kemeja denim. Chat sekarang!"
    assert exporter.strip_price_mentions(once) == once


# ---------------------------------------------------------------------------
# The description rule (ID first, EN second, name last)
# ---------------------------------------------------------------------------


def test_description_prefers_indonesian() -> None:
    assert exporter.description_text("Tas batik.", "Batik bag.") == "Tas batik."


def test_description_falls_back_to_english_when_the_id_is_only_a_price() -> None:
    assert (
        exporter.description_text("Harga Rp 35.000.", "Batik tote bag.")
        == "Batik tote bag."
    )


def test_description_falls_back_to_the_name_when_both_are_prices() -> None:
    assert (
        exporter.description_text(
            "Harga Rp 35.000.", "Only Rp 35,000.", fallback="Tas Batik"
        )
        == "Tas Batik"
    )


# ---------------------------------------------------------------------------
# Export surfaces — a via-chat item carries no prose price anywhere
# ---------------------------------------------------------------------------

VIA_CHAT_PROSE = (
    "Kemeja denim thrift pilihan. Stok terbatas dengan pilihan ukuran campur, "
    "harga hemat hanya Rp 35.000. Chat sekarang untuk tanya ukuran!"
)


def test_a_via_chat_item_leaks_no_prose_price_anywhere(
    exports_dir: Path, base_url: str
) -> None:
    product = _product(
        price=None,
        via_chat=True,
        name="Kemeja Denim",
        description_id=VIA_CHAT_PROSE,
        description_en="Thrift denim shirt. Affordable price at only Rp 35,000.",
    )
    seller = _seller()

    csv_text = exporter.build_csv("mock-1-2", [product], seller, "https://kilat.example")
    captions = exporter.build_captions(
        "mock-1-2", [product], seller, "https://kilat.example"
    )
    whatsapp = exporter.build_whatsapp([product], seller, "https://kilat.example")
    exporter.write_export_record("mock-1-2", [product], seller)
    page = _page_body()

    # Excluded from the feed entirely — the CSV never names the row.
    assert "KK-0001" not in csv_text
    for surface in (csv_text, captions, whatsapp, page):
        assert "35.000" not in surface
        assert "35,000" not in surface
    assert "harga via chat" in captions
    assert "harga via chat" in whatsapp
    assert "harga via chat" in page
    # The surviving prose is still there; only the price sentence is gone.
    assert "Chat sekarang untuk tanya ukuran!" in captions
    assert "Kemeja denim thrift pilihan." in page


def test_a_via_chat_description_that_is_only_a_price_omits_the_prose_block(
    exports_dir: Path, base_url: str
) -> None:
    product = _product(
        price=None,
        via_chat=True,
        name="Kaos Thrift",
        description_id="Harga hanya Rp 35.000.",
        description_en="Only Rp 35,000.",
    )
    seller = _seller()

    captions = exporter.build_captions(
        "mock-1-2", [product], seller, "https://kilat.example"
    )
    exporter.write_export_record("mock-1-2", [product], seller)
    page = _page_body()

    # No blank prose line in the caption, no empty paragraph on the page.
    assert "Kaos Thrift — harga via chat\n📩" in captions
    assert 'class="desc"' not in page
    assert "35.000" not in captions
    assert "35.000" not in page


# ---------------------------------------------------------------------------
# Export surfaces — a priced item shows only the seller's confirmed value
# ---------------------------------------------------------------------------

CONFIRMED_PROSE = (
    "Tas batik motif parang. Kondisi bagus, harga Rp 1.500.000. Siap kirim."
)


def test_a_priced_item_shows_only_the_confirmed_price(
    exports_dir: Path, base_url: str
) -> None:
    product = _product(price=150000, description_id=CONFIRMED_PROSE)
    seller = _seller()

    csv_text = exporter.build_csv("mock-1-2", [product], seller, "https://kilat.example")
    captions = exporter.build_captions(
        "mock-1-2", [product], seller, "https://kilat.example"
    )
    whatsapp = exporter.build_whatsapp([product], seller, "https://kilat.example")
    exporter.write_export_record("mock-1-2", [product], seller)
    page = _page_body()

    # The stale prose number is gone from every surface...
    for surface in (csv_text, captions, whatsapp, page):
        assert "1.500.000" not in surface
        assert "1500000" not in surface
    # ...and the confirmed value is the one a buyer reads.
    rows = _csv_rows(csv_text)
    assert rows[0]["price"] == "150000 IDR"
    assert "Rp150.000" in captions
    assert "*Rp150.000*" in whatsapp
    assert "Rp150.000" in page
    # The rest of the description survives.
    assert rows[0]["description"] == "Tas batik motif parang. Siap kirim."


def test_the_csv_description_falls_back_to_the_name_when_prose_is_a_price(
    exports_dir: Path,
) -> None:
    product = _product(
        price=150000,
        name="Tas Batik",
        description_id="Harga Rp 1.500.000.",
        description_en="Price Rp 1,500,000.",
    )

    rows = _csv_rows(
        exporter.build_csv("mock-1-2", [product], _seller(), "https://kilat.example")
    )

    assert rows[0]["description"] == "Tas Batik"
    assert rows[0]["price"] == "150000 IDR"


def test_the_csv_description_is_never_empty(exports_dir: Path) -> None:
    # Last resort: a nameless record still ships a non-empty description, and
    # the SKU is the only identifier left that is guaranteed to exist.
    product = _product(
        price=150000,
        name="",
        description_id="Harga Rp 1.500.000.",
        description_en="",
    )

    rows = _csv_rows(
        exporter.build_csv("mock-1-2", [product], _seller(), "https://kilat.example")
    )

    assert rows[0]["description"] == "KK-0001"


# ---------------------------------------------------------------------------
# Stage E normalisation — the stale price never reaches entries.json or Review
# ---------------------------------------------------------------------------


def test_stage_e_entries_are_normalised_before_they_reach_the_job_record() -> None:
    entry = _entry(
        description_id="Kemeja denim. Harga hemat hanya Rp 35.000.",
        description_en="Denim shirt. Only Rp 35,000.",
    )

    cleaned = pipeline._without_price_prose(entry)

    assert cleaned.description_id == "Kemeja denim."
    assert cleaned.description_en == "Denim shirt."
    # Identity and taxonomy are untouched — only the prose changes.
    assert cleaned.name == entry.name
    assert cleaned.category == entry.category


def test_stage_e_normalisation_is_a_no_op_on_clean_prose() -> None:
    entry = _entry()

    assert pipeline._without_price_prose(entry) is entry
