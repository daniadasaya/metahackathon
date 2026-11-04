"""Price attribution: a hint belongs to one product or it is not a hint.

The live job ``job-18de6a923213`` shipped ten MOCK-UP bottles at ``189000 IDR``
because one general caption sentence — "Skincare Endless Pure Rp 189.000, kaos
polos Rp 65.000" — was applied at category level to products it never named.
:func:`pipeline._with_attributable_price` keeps a hint only when the amount
appears in that product's own photo text (``visible_text``) or in that photo's
own caption, and demotes everything else to the grey AI suggestion.

Offline, no network: every case is a pure function over a ``ProductEntry``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app import pipeline, schemas
from app.schemas import MaskResult, ProductEntry, ProductAttributes

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: The general caption the seller typed for the live job. It names two things;
#: the job produced 31 products across six photos.
LIVE_GENERAL_CAPTION = "Skincare Endless Pure Rp 189.000, kaos polos Rp 65.000"

#: The ten products of photo 3 (index 3) that inherited the 189000 hint. None
#: of them is Endless Pure: the photo's own visible text reads MOCK-UP.
LIVE_PHOTO_3_PRODUCTS = (
    ("KK-0009", "cosmetic bottle"),
    ("KK-0013", "cosmetic tube"),
    ("KK-0016", "cosmetic jar"),
    ("KK-0018", "cosmetic tube"),
    ("KK-0022", "cosmetic bottle"),
    ("KK-0023", "cosmetic bottle"),
    ("KK-0024", "cosmetic bottle"),
    ("KK-0026", "cosmetic bottle"),
    ("KK-0027", "cosmetic bottle"),
    ("KK-0028", "cosmetic jar"),
)

LIVE_PHOTO_3_VISIBLE_TEXT = "MOCK-UP, MOCK - UP, COSMETIC"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _product(photo_index: int = 0, concept: str = "cosmetic bottle") -> schemas.Product:
    return schemas.Product(
        sku="KK-0001",
        job_id="job-hint",
        source_photo_index=photo_index,
        concept=concept,
        bbox=(0, 0, 10, 10),
        crop_url="/p/job-hint/KK-0001-crop.jpg",
        clean_url="/p/job-hint/KK-0001.jpg",
        cleanup_method=schemas.CleanupMethod.COMPOSITE,
        fidelity=None,
        name=concept,
        description_id="",
        description_en="",
        category="Kecantikan",
        brand=None,
        condition=schemas.Condition.NEW,
        attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None,
        price_suggestion_idr=None,
        confidence=schemas.Confidence.LOW,
        price_idr=None,
        price_via_chat=False,
        availability=schemas.Availability.IN_STOCK,
        included=True,
    )


def _entry(hint: int | None, suggestion: int | None = None) -> ProductEntry:
    return ProductEntry(
        name="Botol Pump",
        description_id="Botol pump.",
        description_en="Pump bottle.",
        category="Kecantikan",
        brand=None,
        condition=schemas.Condition.NEW,
        attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=hint,
        price_suggestion_idr=suggestion,
        confidence=schemas.Confidence.MEDIUM,
    )


def _concepts(*visible_texts: str | None) -> list[list[dict[str, str | None]]]:
    return [
        [{"phrase": "cosmetic bottle", "scene_note": "s", "visible_text": text}]
        for text in visible_texts
    ]


# ---------------------------------------------------------------------------
# The amount parser — four spellings, one integer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("Rp 1.500.000", 1_500_000),
        ("Rp1.500.000", 1_500_000),
        ("Rp. 1.500.000", 1_500_000),
        ("1500000", 1_500_000),
        ("1.500.000 rupiah", 1_500_000),
        ("1,5 juta", 1_500_000),
        ("1.5jt", 1_500_000),
        ("189rb", 189_000),
        ("Rp 189.000", 189_000),
        ("Rp35rb", 35_000),
        ("35 ribu", 35_000),
        ("IDR 35000", 35_000),
        ("35k", 35_000),
        ("Rp 25.000 - 50.000", 25_000),
    ],
)
def test_an_amount_is_normalised_to_integer_rupiah(text: str, amount: int) -> None:
    assert amount in pipeline._price_amounts(text)


@pytest.mark.parametrize(
    "text",
    [
        "MOCK-UP, MOCK - UP, COSMETIC",
        "30 ml",
        "15ml",
        "177 ml e",
        "256GB",
        "6.7 inch",
        "iPhone 12 Pro Max 256GB",
        "",
    ],
)
def test_a_size_capacity_or_label_is_not_a_price(text: str) -> None:
    assert pipeline._price_amounts(text) == set()


def test_a_multi_price_tag_yields_every_amount() -> None:
    # Pack B's photo 2 carries a hand-written price list in the frame.
    assert pipeline._price_amounts("OBRAL, Rp 25.000 - 50.000, PILIH SENDIRI") == {
        25_000,
        50_000,
    }


def test_no_text_has_no_amounts() -> None:
    assert pipeline._price_amounts(None) == set()


# ---------------------------------------------------------------------------
# Attribution — kept only where the seller tied the number to this product
# ---------------------------------------------------------------------------


def test_a_general_caption_price_is_demoted_to_the_suggestion() -> None:
    entry = _entry(hint=189_000)
    product = _product()

    kept = pipeline._with_attributable_price(
        entry, product, _concepts(LIVE_PHOTO_3_VISIBLE_TEXT), [""]
    )

    assert kept.price_hint_idr is None
    assert kept.price_suggestion_idr == 189_000


def test_a_demoted_price_never_overwrites_an_existing_suggestion() -> None:
    entry = _entry(hint=189_000, suggestion=210_000)

    kept = pipeline._with_attributable_price(entry, _product(), _concepts(None), [""])

    assert kept.price_hint_idr is None
    assert kept.price_suggestion_idr == 210_000


def test_a_price_in_the_products_own_photo_caption_is_kept() -> None:
    entry = _entry(hint=150_000)
    product = _product(photo_index=1)

    kept = pipeline._with_attributable_price(
        entry, product, _concepts(None, None), ["", "Tas batik ini Rp 150.000 ya"]
    )

    assert kept.price_hint_idr == 150_000
    assert kept.price_suggestion_idr is None


@pytest.mark.parametrize(
    ("visible_text", "hint"),
    [
        ("Rp 1.500.000, SAMSUNG, H6", 1_500_000),
        ("1500000", 1_500_000),
        ("1,5 juta", 1_500_000),
        ("189rb", 189_000),
    ],
)
def test_a_price_printed_in_the_photo_is_kept_in_every_spelling(
    visible_text: str, hint: int
) -> None:
    kept = pipeline._with_attributable_price(
        _entry(hint=hint), _product(), _concepts(visible_text), [""]
    )

    assert kept.price_hint_idr == hint


def test_a_price_from_another_photos_caption_is_not_attributable() -> None:
    entry = _entry(hint=189_000)
    product = _product(photo_index=0)

    kept = pipeline._with_attributable_price(
        entry, product, _concepts(None, None), ["", "Botol ini Rp 189.000"]
    )

    assert kept.price_hint_idr is None
    assert kept.price_suggestion_idr == 189_000


def test_a_product_without_a_hint_is_returned_untouched() -> None:
    entry = _entry(hint=None, suggestion=90_000)

    kept = pipeline._with_attributable_price(entry, _product(), _concepts(None), [""])

    assert kept is entry


def test_attribution_is_idempotent() -> None:
    once = pipeline._with_attributable_price(
        _entry(hint=189_000), _product(), _concepts(LIVE_PHOTO_3_VISIBLE_TEXT), [""]
    )
    twice = pipeline._with_attributable_price(
        once, _product(), _concepts(LIVE_PHOTO_3_VISIBLE_TEXT), [""]
    )

    assert twice == once


# ---------------------------------------------------------------------------
# The live job replay — the number the seller wrote once never becomes a hint
# ---------------------------------------------------------------------------


def test_the_live_job_replay_demotes_all_ten_products() -> None:
    """Replays the photo-3 slice of ``job-18de6a923213`` as recorded on disk.

    Every one of the ten products carried ``price_hint_idr = 189000``; the
    photo's own text says MOCK-UP and the per-photo captions are empty, so the
    number is attributable to none of them.
    """
    concepts = [[{"phrase": "cosmetic bottle", "scene_note": "s",
                  "visible_text": LIVE_PHOTO_3_VISIBLE_TEXT}]] * 6
    photo_captions = [""] * 6
    general = LIVE_GENERAL_CAPTION

    results = []
    for sku, concept in LIVE_PHOTO_3_PRODUCTS:
        product = _product(photo_index=3, concept=concept)
        product.sku = sku
        results.append(
            pipeline._with_attributable_price(
                _entry(hint=189_000, suggestion=189_000),
                product,
                concepts,
                photo_captions,
            )
        )

    assert [entry.price_hint_idr for entry in results] == [None] * 10
    assert [entry.price_suggestion_idr for entry in results] == [189_000] * 10
    # The property the rule exists to guarantee: a number that appears only in
    # the general caption is a hint on zero products, so never on two.
    assert 189_000 in pipeline._price_amounts(general)
    assert sum(1 for entry in results if entry.price_hint_idr == 189_000) == 0


def test_the_same_number_survives_where_the_photo_prints_it() -> None:
    """The control for the replay: attribution is not a blanket demotion."""
    kept = pipeline._with_attributable_price(
        _entry(hint=189_000),
        _product(photo_index=3),
        _concepts(None, None, None, "MOCK-UP, Rp 189.000"),
        [""],
    )

    assert kept.price_hint_idr == 189_000


# ---------------------------------------------------------------------------
# Recorded packs — every hint the photo can attribute survives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pack", "photo", "expected"),
    [
        # Three phones priced from a Rp 1.500.000 tag in the frame; the phone
        # case beside them carries no price.
        ("pack_a", "photo1", [1_500_000, 1_500_000, 1_500_000, None]),
        # A PRICE LIST board: no entry claims a price from it.
        ("pack_a", "photo2", [None, None, None, None]),
        # "Rp 35.000" printed on the rack.
        ("pack_b", "photo1", [35_000] * 7),
        # "OBRAL, Rp 25.000 - 50.000, PILIH SENDIRI" in the frame.
        ("pack_b", "photo2", [25_000, 25_000]),
        # Groceries: no price in the photo, no hint in the entry.
        ("pack_c", "photo1", [None] * 5),
        ("pack_c", "photo2", [None] * 5),
    ],
)
def test_a_recorded_pack_keeps_every_hint_the_photo_can_attribute(
    pack: str, photo: str, expected: list[int | None]
) -> None:
    """The rule must not cost a recorded pack a price the seller printed.

    Each pack's ``visible_text`` is the text Stage A read off the frame, so a
    hint that survives here is one a buyer could read on the photo. The concept
    per product is taken from the recorded concept list in order; every concept
    of a photo shares that photo's ``visible_text``.
    """
    concepts = json.loads((FIXTURES / pack / f"{photo}_concepts.json").read_text())["payload"]
    entries = json.loads((FIXTURES / pack / f"{photo}_entries.json").read_text())["payload"]

    kept_hints = []
    for index, raw in enumerate(entries):
        entry = ProductEntry.model_validate(raw)
        concept = concepts[min(index, len(concepts) - 1)]["phrase"]
        product = _product(photo_index=0, concept=concept)
        kept = pipeline._with_attributable_price(entry, product, [concepts], [""])
        kept_hints.append(kept.price_hint_idr)

    assert kept_hints == expected


# ---------------------------------------------------------------------------
# The re-run path — a cached entry cannot reintroduce the bad hint
# ---------------------------------------------------------------------------


def test_a_cached_entry_is_validated_before_it_reaches_the_job_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``entries.json`` written by an older build is corrected on re-run.

    The hint is read from disk, validated, and written back demoted, so a
    second run cannot resurrect it either.
    """
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    job_id = "job-cached-hint"
    jdir = pipeline.job_dir(job_id)
    for sub in ("masks", "crops", "clean"):
        (jdir / sub).mkdir(parents=True, exist_ok=True)

    pipeline._save_concepts(
        job_id,
        [[{"phrase": "cosmetic bottle", "scene_note": "s",
           "visible_text": LIVE_PHOTO_3_VISIBLE_TEXT}]],
    )
    pipeline._save_entries(job_id, {"KK-0001": _entry(hint=189_000)})

    async def no_concepts(image_bytes, captions):
        raise AssertionError("cached concepts must not call Stage A")

    async def no_extract(clean_png, context):
        raise AssertionError("a cached entry must not call Stage E")

    async def one_mask(image_bytes, concept):
        # A real raster, sized to its own box like a live SAM mask: a mask with
        # no raster cuts out a fully transparent crop.
        raster = Image.new("L", (161, 261), 255)
        raster_buf = io.BytesIO()
        raster.save(raster_buf, format="PNG")
        return [
            MaskResult(
                concept="cosmetic bottle",
                bbox=(40, 40, 200, 300),
                width=161,
                height=261,
                revision=1,
                score=0.9,
                mask_png_base64=base64.b64encode(raster_buf.getvalue()).decode("ascii"),
            )
        ]

    async def reject_edit(orig, cleaned):
        return schemas.FidelityVerdict(
            same_product=False, color_match=False, logo_text_match=False, reason="torn"
        )

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", no_concepts)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", one_mask)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", reject_edit)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", no_extract)

    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (120, 140, 160)).save(buf, format="PNG")

    asyncio.run(
        pipeline.run_pipeline(
            job_id,
            [buf.getvalue()],
            {"general": LIVE_GENERAL_CAPTION, "photos": [""]},
        )
    )

    product = pipeline._load_job_state(job_id).products[0]
    assert product.price_hint_idr is None
    assert product.price_suggestion_idr == 189_000
    # A synthetic cut-out is a sound composite: the guard must not touch it.
    assert product.included is True

    on_disk = pipeline._load_entries(job_id)
    assert on_disk is not None
    assert on_disk["KK-0001"].price_hint_idr is None
    assert on_disk["KK-0001"].price_suggestion_idr == 189_000
