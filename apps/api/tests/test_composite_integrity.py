"""A composite built from a torn mask never enters the catalog switched on.

Stage D's fidelity gate correctly rejects a Muse Image edit that damages the
product, but the deterministic fallback rebuilds the studio shot from the *same*
mask. When the mask itself ate a bite out of the product, the composite is a
faithful picture of damaged goods: in the live job ``job-18de6a923213`` five of
31 studio images shipped torn (KK-0009, KK-0018, KK-0022, KK-0023, KK-0027).

:func:`pipeline._apply_composite_guard` measures the cut-out's alpha coverage
and switches such a product off by default. The seller still sees it in Review
with the ``Foto standar`` badge and can switch it back on; the catalog does not
ship it unasked.

Calibration (the 31 products of that job, alpha coverage of the crop against
its own bounding box):

===============  =========  =====================================
composite        coverage   verdict by eye
===============  =========  =====================================
KK-0023          0.517      torn (right half of the tube missing)
KK-0022          0.629      torn (wedge out of the bottle base)
KK-0009          0.645      torn (hole through the bottle)
KK-0018          0.735      torn (notch out of the cap)
KK-0027          0.772      torn (notch out of the base)
KK-0013          0.642      sound — a long tapered tube, empty box by shape
KK-0017          0.916      sound
KK-0020          0.921      sound
KK-0007          0.926      sound
===============  =========  =====================================

``COMPOSITE_MIN_COVERAGE = 0.80`` catches all five and switches off one sound
product of 26. ``muse-image`` output is never judged here: the gate passed it,
and a diagonal phone in a wide box measures ~0.46 without being damaged.

Offline: Pillow geometry, no network, no model call.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app import imaging, pipeline, schemas
from app.schemas import FidelityVerdict, Product, ProductAttributes


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _bottle_crop(bitten: bool) -> bytes:
    """A 100x300 bottle whose bounding box is the full frame either way.

    The bitten variant punches a 40x180 notch out of the bottom-right corner,
    exactly the geometry of KK-0018 and KK-0027: the silhouette loses 24% of its
    area while its bounding box does not shrink, which is what makes the tear
    measurable. 22_800 of 30_000 pixels survive, a coverage of 0.76.
    """
    image = Image.new("RGBA", (100, 300), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 99, 299), fill=(180, 90, 60, 255))
    if bitten:
        draw.rectangle((60, 120, 99, 299), fill=(0, 0, 0, 0))
    return _png(image)


def _product(job_id: str = "job-composite") -> Product:
    return Product(
        sku="KK-0001",
        job_id=job_id,
        source_photo_index=0,
        concept="cosmetic bottle",
        bbox=(0, 0, 99, 299),
        crop_url=f"/p/{job_id}/KK-0001-crop.jpg",
        clean_url=f"/p/{job_id}/KK-0001.jpg",
        cleanup_method=schemas.CleanupMethod.COMPOSITE,
        fidelity=None,
        name="Botol",
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


def _verdict(passes: bool) -> FidelityVerdict:
    return FidelityVerdict(
        same_product=passes,
        color_match=passes,
        logo_text_match=passes,
        reason="ok" if passes else "the mask ate a bite out of the product",
    )


@pytest.fixture
def job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    job_id = "job-composite"
    jdir = pipeline.job_dir(job_id)
    for sub in ("masks", "crops", "clean"):
        (jdir / sub).mkdir(parents=True, exist_ok=True)
    return job_id


# ---------------------------------------------------------------------------
# The measurement
# ---------------------------------------------------------------------------


def test_a_solid_cutout_fills_its_box() -> None:
    assert imaging.alpha_coverage(_bottle_crop(bitten=False)) == pytest.approx(1.0)


def test_a_bite_through_the_silhouette_lowers_the_coverage() -> None:
    # 22_800 of the 30_000 pixels in the bounding box survive the wedge.
    assert imaging.alpha_coverage(_bottle_crop(bitten=True)) == pytest.approx(0.76)


def test_a_fully_transparent_cutout_has_no_coverage() -> None:
    assert imaging.alpha_coverage(_png(Image.new("RGBA", (10, 10), (0, 0, 0, 0)))) == 0.0


def test_the_threshold_sits_between_a_tear_and_a_sound_composite() -> None:
    assert imaging.alpha_coverage(_bottle_crop(bitten=True)) < pipeline.COMPOSITE_MIN_COVERAGE
    assert imaging.alpha_coverage(_bottle_crop(bitten=False)) >= pipeline.COMPOSITE_MIN_COVERAGE


# ---------------------------------------------------------------------------
# The Stage D decision
# ---------------------------------------------------------------------------


def _run(job_id: str, crop: bytes, passes: bool, prev: Product | None = None) -> Product:
    async def fake_clean(crop_png: bytes, label: str) -> bytes:
        return _png(Image.new("RGB", (8, 8), (1, 2, 3)))

    async def fake_verify(orig: bytes, cleaned: bytes) -> FidelityVerdict:
        return _verdict(passes)

    original_clean = pipeline.meta_client.clean_product_image
    original_verify = pipeline.meta_client.verify_fidelity
    pipeline.meta_client.clean_product_image = fake_clean
    pipeline.meta_client.verify_fidelity = fake_verify
    try:
        product = _product(job_id)
        asyncio.run(
            pipeline._run_stage_d_one(
                job_id,
                product,
                crop,
                asyncio.Semaphore(1),
                float("inf"),
                prev_product=prev,
            )
        )
    finally:
        pipeline.meta_client.clean_product_image = original_clean
        pipeline.meta_client.verify_fidelity = original_verify
    return product


def test_a_torn_composite_is_switched_off(job: str) -> None:
    product = _run(job, _bottle_crop(bitten=True), passes=False)

    assert product.cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert product.included is False


def test_a_sound_composite_stays_included(job: str) -> None:
    product = _run(job, _bottle_crop(bitten=False), passes=False)

    assert product.cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert product.included is True


def test_a_gate_passed_edit_is_never_judged_by_coverage(job: str) -> None:
    # A diagonal phone in a wide box measures ~0.46 and is perfectly sound:
    # the guard must only ever look at the composite path.
    product = _run(job, _bottle_crop(bitten=True), passes=True)

    assert product.cleanup_method == schemas.CleanupMethod.MUSE_IMAGE
    assert product.included is True


def test_a_rerun_keeps_a_torn_product_switched_off(job: str) -> None:
    first = _run(job, _bottle_crop(bitten=True), passes=False)
    assert first.included is False

    # Second pass over the cached clean image, as a re-run does.
    second = _run(job, _bottle_crop(bitten=True), passes=False, prev=first)

    assert second.cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert second.included is False


# ---------------------------------------------------------------------------
# The retry / toggle path
# ---------------------------------------------------------------------------


def _store(job_id: str, product: Product) -> None:
    (pipeline.job_dir(job_id) / "crops" / f"{product.sku}-crop.png").write_bytes(
        _bottle_crop(bitten=True)
    )
    pipeline._write_job(
        job_id,
        schemas.JobState(
            job_id=job_id,
            status=schemas.JobStatus.DONE,
            stage=schemas.JobStage.DONE,
            progress=schemas.JobProgress(
                photos_total=1, photos_done=1, products_total=1, products_cleaned=1
            ),
            products=[product],
            errors=[],
            created_at=pipeline._now(),
            updated_at=pipeline._now(),
        ),
    )


def test_toggling_a_torn_composite_to_a_gate_passed_edit_switches_it_back_on(
    job: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_clean(crop_png: bytes, label: str) -> bytes:
        return _png(Image.new("RGB", (8, 8), (1, 2, 3)))

    async def passing(orig: bytes, cleaned: bytes) -> FidelityVerdict:
        return _verdict(True)

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", passing)

    torn = _product(job)
    torn.included = False
    _store(job, torn)

    state = asyncio.run(pipeline.toggle_cleanup_product(job, "KK-0001"))

    assert state.products[0].cleanup_method == schemas.CleanupMethod.MUSE_IMAGE
    assert state.products[0].included is True


def test_toggling_a_sound_edit_down_to_a_torn_composite_switches_it_off(job: str) -> None:
    sound = _product(job)
    sound.cleanup_method = schemas.CleanupMethod.MUSE_IMAGE
    _store(job, sound)

    state = asyncio.run(pipeline.toggle_cleanup_product(job, "KK-0001"))

    assert state.products[0].cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert state.products[0].included is False
