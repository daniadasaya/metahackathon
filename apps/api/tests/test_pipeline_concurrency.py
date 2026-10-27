"""Offline concurrency tests for the pipeline.

Every Meta call site in ``run_pipeline`` must run its per-item work
concurrently, bounded by ONE shared semaphore (``META_CONCURRENCY`` = 5), with
per-item error isolation intact. The regression detector is a max-in-flight
counter over patched adapter calls — never a wall-clock assertion, which
cannot tell sequential from concurrent code without being flaky.

``enter()`` runs before any ``await`` inside each fake and the first
``sem.acquire()`` of an uncontended asyncio.Semaphore does not suspend, so N
tasks launched together guarantee ``peak == min(N, cap)``; a regression back
to await-in-a-sequential-loop collapses the peak to 1 and fails the assertion.

All fakes are offline: META_MOCK=true, no network, no live calls.
"""

from __future__ import annotations

import asyncio
import base64
import io

import pytest
from PIL import Image, ImageDraw

from app import pipeline, schemas
from app.schemas import (
    Confidence,
    Condition,
    FidelityVerdict,
    MaskResult,
    ProductAttributes,
    ProductEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup(tmp_path, monkeypatch):
    """Point the pipeline's data dir at tmp_path (restored after the test)."""
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "data" / "jobs")


def _photo_png(size=400, seed=0):
    img = Image.new("RGB", (size, size), ((180 + seed * 13) % 255, 160, 140))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mask(concept, bbox, photo_w=400, photo_h=400):
    """A mask carrying a real raster so imaging.cut_out yields a real crop."""
    raster = Image.new("L", (photo_w, photo_h), 0)
    draw = ImageDraw.Draw(raster)
    draw.rectangle([bbox[0], bbox[1], bbox[2], bbox[3]], fill=255)
    buf = io.BytesIO()
    raster.save(buf, format="PNG")
    return MaskResult(
        concept=concept,
        bbox=bbox,
        width=photo_w,
        height=photo_h,
        revision=1,
        score=0.9,
        mask_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
    )


def _grid_box(n, cols=4, cell=80, pad=20, gap=90):
    """n-th disjoint box in a grid — no two overlap, so global dedupe keeps all."""
    x = pad + (n % cols) * gap
    y = pad + (n // cols) * gap
    return (x, y, x + cell, y + cell)


def _png_bytes(size=8, color=(1, 2, 3)):
    img = Image.new("RGB", (size, size), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _entry(name="Named Product"):
    return ProductEntry(
        name=name,
        description_id="d",
        description_en="e",
        category="c",
        brand=None,
        condition=Condition.NEW,
        attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None,
        price_suggestion_idr=None,
        confidence=Confidence.HIGH,
    )


PASS_VERDICT = dict(same_product=True, color_match=True, logo_text_match=True, reason="ok")


class InFlight:
    """Max-in-flight counter over patched adapter call(s)."""

    def __init__(self):
        self.cur = 0
        self.peak = 0
        self.calls = 0

    def enter(self):
        self.cur += 1
        self.calls += 1
        if self.cur > self.peak:
            self.peak = self.cur

    def leave(self):
        self.cur -= 1


def _tracked(tracker, result_fn, delay=0.05):
    """Wrap a sync result function in a counted, sleeping async adapter fake."""

    async def wrapped(*args, **kwargs):
        tracker.enter()
        try:
            await asyncio.sleep(delay)
            return result_fn(*args, **kwargs)
        finally:
            tracker.leave()

    return wrapped


def _concepts(*phrases):
    return [schemas.Concept(phrase=p, scene_note="s", visible_text=None) for p in phrases]


# ---------------------------------------------------------------------------
# Regression detectors: peak >= 2 fails if the stage goes back to sequential
# ---------------------------------------------------------------------------


def test_stage_a_discover_concepts_run_concurrently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    photos = [_photo_png(seed=s) for s in range(4)]
    tracker = InFlight()

    async def no_masks(image_bytes, concept):
        return []

    monkeypatch.setattr(
        pipeline.meta_client,
        "discover_concepts",
        _tracked(tracker, lambda image_bytes, captions: _concepts("smartphone")),
    )
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", no_masks)

    asyncio.run(pipeline.run_pipeline("conc-a", photos, {"general": "t", "photos": []}))
    assert tracker.calls == 4
    assert tracker.peak >= 2, (
        f"Stage A ran sequentially: max in flight {tracker.peak}; "
        "per-photo discover_concepts calls must overlap"
    )
    assert tracker.peak <= pipeline.META_CONCURRENCY


def test_stage_b_segment_runs_concurrently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    photos = [_photo_png(seed=s) for s in range(2)]
    tracker = InFlight()
    boxes = {"n": 0}

    def segment_result(image_bytes, concept):
        box = _grid_box(boxes["n"])
        boxes["n"] += 1
        return [_mask(concept, box)]

    async def fast_clean(crop_png, label):
        return _png_bytes()

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    async def fast_extract(clean_png, context):
        return [_entry()]

    async def fast_discover(image_bytes, captions):
        return _concepts("alpha", "beta", "gamma")

    async def fast_clean(crop_png, label):
        return _png_bytes()

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    async def fast_extract(clean_png, context):
        return [_entry()]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fast_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", _tracked(tracker, segment_result))
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fast_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fast_extract)

    asyncio.run(pipeline.run_pipeline("conc-b", photos, {"general": "t", "photos": []}))
    # 2 photos x 3 concepts = 6 segment calls, one task each.
    assert tracker.calls == 6
    assert tracker.peak >= 2, (
        f"Stage B ran sequentially: max in flight {tracker.peak}; "
        "per-concept segment_concept calls must overlap"
    )
    assert tracker.peak <= pipeline.META_CONCURRENCY
    # SKU assignment must not depend on completion order: 6 disjoint masks in
    # reading order (top row left-to-right, then the second row).
    state = pipeline._load_job_state("conc-b")
    assert [p.sku for p in state.products] == [f"KK-{i:04d}" for i in range(1, 7)]


def test_stage_d_cleanup_runs_concurrently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    tracker = InFlight()

    async def fake_discover(image_bytes, captions):
        return _concepts("smartphone")

    async def fake_segment(image_bytes, concept):
        return [_mask(concept, _grid_box(n)) for n in range(4)]

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    async def fast_extract(clean_png, context):
        return [_entry()]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    monkeypatch.setattr(
        pipeline.meta_client,
        "clean_product_image",
        _tracked(tracker, lambda crop_png, label: _png_bytes()),
    )
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fast_extract)

    asyncio.run(pipeline.run_pipeline("conc-d", [_photo_png()], {"general": "t", "photos": []}))
    assert tracker.calls == 4
    assert tracker.peak >= 2, (
        f"Stage D ran sequentially: max in flight {tracker.peak}; "
        "per-product clean_product_image calls must overlap"
    )
    assert tracker.peak <= pipeline.META_CONCURRENCY
    state = pipeline._load_job_state("conc-d")
    assert all(p.cleanup_method == schemas.CleanupMethod.MUSE_IMAGE for p in state.products)


def test_stage_e_extract_runs_concurrently(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    tracker = InFlight()

    async def fake_discover(image_bytes, captions):
        return _concepts("smartphone")

    async def fake_segment(image_bytes, concept):
        return [_mask(concept, _grid_box(n)) for n in range(4)]

    async def fast_clean(crop_png, label):
        return _png_bytes()

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fast_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(
        pipeline.meta_client,
        "extract_product",
        _tracked(tracker, lambda clean_png, context: [_entry()]),
    )

    asyncio.run(pipeline.run_pipeline("conc-e", [_photo_png()], {"general": "t", "photos": []}))
    assert tracker.calls == 4
    assert tracker.peak >= 2, (
        f"Stage E ran sequentially: max in flight {tracker.peak}; "
        "per-product extract_product calls must overlap"
    )
    assert tracker.peak <= pipeline.META_CONCURRENCY
    state = pipeline._load_job_state("conc-e")
    assert all(p.name == "Named Product" for p in state.products)


# ---------------------------------------------------------------------------
# The one shared semaphore: demand above the cap must never exceed it
# ---------------------------------------------------------------------------


def test_stage_d_shared_semaphore_caps_in_flight_calls(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    tracker = InFlight()

    async def fake_discover(image_bytes, captions):
        return _concepts("smartphone")

    async def fake_segment(image_bytes, concept):
        return [_mask(concept, _grid_box(n)) for n in range(12)]

    async def fast_extract(clean_png, context):
        return [_entry()]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    # clean AND verify share ONE tracker and the ONE pipeline semaphore: with
    # 12 products both are saturated, so the peak is exactly META_CONCURRENCY.
    monkeypatch.setattr(
        pipeline.meta_client,
        "clean_product_image",
        _tracked(tracker, lambda crop_png, label: _png_bytes()),
    )
    monkeypatch.setattr(
        pipeline.meta_client,
        "verify_fidelity",
        _tracked(tracker, lambda orig, cleaned: FidelityVerdict(**PASS_VERDICT)),
    )
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fast_extract)

    asyncio.run(pipeline.run_pipeline("conc-cap", [_photo_png()], {"general": "t", "photos": []}))
    assert tracker.calls == 24  # 12 cleans + 12 verifies
    assert tracker.peak == pipeline.META_CONCURRENCY


# ---------------------------------------------------------------------------
# Error isolation under concurrency: one bad item never cancels its siblings
# ---------------------------------------------------------------------------


def test_stage_d_one_bad_product_does_not_cancel_siblings(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def fake_discover(image_bytes, captions):
        return _concepts("alpha", "beta", "gamma")

    async def segment_result(image_bytes, concept):
        boxes = {"alpha": _grid_box(0), "beta": _grid_box(1), "gamma": _grid_box(2)}
        return [_mask(concept, boxes[concept])]

    async def fast_extract(clean_png, context):
        return [_entry()]

    calls = {"clean": 0}

    async def flaky_clean(crop_png, label):
        calls["clean"] += 1
        if label == "beta":
            raise RuntimeError("cleanup boom")
        return _png_bytes()

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", segment_result)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", flaky_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fast_extract)

    asyncio.run(pipeline.run_pipeline("conc-iso-d", [_photo_png()], {"general": "t", "photos": []}))
    state = pipeline._load_job_state("conc-iso-d")
    assert state.status.value == "done"
    assert calls["clean"] == 3, "the failing product must not prevent its siblings' calls"
    by_sku = {p.sku: p for p in state.products}
    assert set(by_sku) == {"KK-0001", "KK-0002", "KK-0003"}
    assert by_sku["KK-0002"].cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert by_sku["KK-0001"].cleanup_method == schemas.CleanupMethod.MUSE_IMAGE
    assert by_sku["KK-0003"].cleanup_method == schemas.CleanupMethod.MUSE_IMAGE
    cleanup_errors = [e for e in state.errors if e.stage.value == "cleanup"]
    assert len(cleanup_errors) == 1
    assert cleanup_errors[0].code.value == "cleanup_failed"
    assert cleanup_errors[0].photo_index == 0
    assert "message" not in cleanup_errors[0].model_dump()


def test_stage_d_unexpected_raise_is_boxed_per_product(tmp_path, monkeypatch):
    """A non-adapter raise (corrupt crop bytes) is boxed by the gather wrapper:
    one StageError for the broken product, siblings complete, job completes."""
    _setup(tmp_path, monkeypatch)
    original_crop = pipeline._crop_bytes_for

    def flaky_crop(job_id_arg, sku):
        if sku == "KK-0002":
            return b"this is not a png"
        return original_crop(job_id_arg, sku)

    async def fake_discover(image_bytes, captions):
        return _concepts("alpha", "beta", "gamma")

    async def segment_result(image_bytes, concept):
        boxes = {"alpha": _grid_box(0), "beta": _grid_box(1), "gamma": _grid_box(2)}
        return [_mask(concept, boxes[concept])]

    async def fast_clean(crop_png, label):
        return _png_bytes()

    async def gate_fail(orig, cleaned):
        return FidelityVerdict(same_product=False, color_match=False, logo_text_match=False, reason="bad edit")

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", segment_result)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fast_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", gate_fail)
    monkeypatch.setattr(pipeline, "_crop_bytes_for", flaky_crop)

    asyncio.run(pipeline.run_pipeline("conc-box-d", [_photo_png()], {"general": "t", "photos": []}))
    state = pipeline._load_job_state("conc-box-d")
    assert state.status.value == "done"
    assert {p.sku for p in state.products} == {"KK-0001", "KK-0002", "KK-0003"}
    cleanup_errors = [e for e in state.errors if e.stage.value == "cleanup"]
    assert len(cleanup_errors) == 1
    assert cleanup_errors[0].code.value == "cleanup_failed"
    # KK-0001 and KK-0003 still got their (gate-failed -> composite) result.
    by_sku = {p.sku: p for p in state.products}
    assert by_sku["KK-0002"].cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert by_sku["KK-0001"].fidelity is not None
    assert by_sku["KK-0003"].fidelity is not None


def test_stage_e_one_bad_product_does_not_cancel_siblings(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    async def fake_discover(image_bytes, captions):
        return _concepts("alpha", "beta", "gamma")

    async def segment_result(image_bytes, concept):
        boxes = {"alpha": _grid_box(0), "beta": _grid_box(1), "gamma": _grid_box(2)}
        return [_mask(concept, boxes[concept])]

    async def fast_clean(crop_png, label):
        return _png_bytes()

    async def fast_verify(orig, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    extract_calls = {"n": 0}

    async def flaky_extract(clean_png, context):
        extract_calls["n"] += 1
        if context.concept == "beta":
            raise RuntimeError("extraction boom")
        return [_entry(f"Named {context.concept}")]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", segment_result)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fast_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", flaky_extract)

    asyncio.run(pipeline.run_pipeline("conc-iso-e", [_photo_png()], {"general": "t", "photos": []}))
    state = pipeline._load_job_state("conc-iso-e")
    assert state.status.value == "done"
    by_sku = {p.sku: p for p in state.products}
    assert by_sku["KK-0001"].name == "Named alpha"
    assert by_sku["KK-0003"].name == "Named gamma"
    # The failing product keeps its defaults (name == concept, no invented price).
    assert by_sku["KK-0002"].name == "beta"
    assert by_sku["KK-0002"].price_idr is None
    # The repair retry still fires for the failing product only: 1 + 2 + 1.
    assert extract_calls["n"] == 4
    extract_errors = [e for e in state.errors if e.stage.value == "extract"]
    assert len(extract_errors) == 1
    assert extract_errors[0].code.value == "extraction_failed"


# ---------------------------------------------------------------------------
# Idempotency, deadline, and constants under the concurrent shape
# ---------------------------------------------------------------------------


def test_rerun_reuses_every_cached_stage_under_concurrency(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    photos = [_photo_png(seed=s) for s in range(2)]
    boxes = {"n": 0}

    def segment_result(image_bytes, concept):
        box = _grid_box(boxes["n"])
        boxes["n"] += 1
        return [_mask(concept, box)]

    first = tuple(InFlight() for _ in range(4))

    async def run(tag, trackers):
        a, b, c, e = trackers
        monkeypatch.setattr(
            pipeline.meta_client,
            "discover_concepts",
            _tracked(a, lambda image_bytes, captions: _concepts("alpha", "beta"), delay=0.01),
        )
        monkeypatch.setattr(pipeline.meta_client, "segment_concept", _tracked(b, segment_result, delay=0.01))
        monkeypatch.setattr(
            pipeline.meta_client,
            "clean_product_image",
            _tracked(c, lambda crop_png, label: _png_bytes(), delay=0.01),
        )
        monkeypatch.setattr(
            pipeline.meta_client,
            "verify_fidelity",
            _tracked(c, lambda orig, cleaned: FidelityVerdict(**PASS_VERDICT), delay=0.01),
        )
        monkeypatch.setattr(
            pipeline.meta_client,
            "extract_product",
            _tracked(e, lambda clean_png, context: [_entry("Named")], delay=0.01),
        )
        await pipeline.run_pipeline(tag, photos, {"general": "t", "photos": []})

    asyncio.run(run("conc-cache", first))
    state1 = pipeline._load_job_state("conc-cache")
    assert [p.sku for p in state1.products] == [f"KK-{i:04d}" for i in range(1, 5)]
    a1, b1, c1, e1 = first
    assert (a1.calls, b1.calls, c1.calls, e1.calls) == (2, 4, 8, 4)

    # Second run of the same job: every stage must come from disk cache.
    second = tuple(InFlight() for _ in range(4))
    asyncio.run(run("conc-cache", second))
    a2, b2, c2, e2 = second
    assert (a2.calls, b2.calls, c2.calls, e2.calls) == (0, 0, 0, 0), (
        "a re-run must reuse cached concepts, masks, clean images and entries — zero Meta calls"
    )
    state2 = pipeline._load_job_state("conc-cache")
    assert [p.sku for p in state2.products] == [f"KK-{i:04d}" for i in range(1, 5)]
    assert all(p.name == "Named" for p in state2.products)
    assert all(p.cleanup_method == schemas.CleanupMethod.MUSE_IMAGE for p in state2.products)


def test_no_meta_call_after_deadline_and_job_still_completes(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(pipeline, "JOB_TIME_BUDGET_SECONDS", -1.0)  # deadline already spent
    tracker = InFlight()

    monkeypatch.setattr(
        pipeline.meta_client,
        "discover_concepts",
        _tracked(tracker, lambda image_bytes, captions: _concepts("smartphone")),
    )
    for name in ("segment_concept", "clean_product_image", "verify_fidelity", "extract_product"):
        monkeypatch.setattr(
            pipeline.meta_client,
            name,
            _tracked(tracker, lambda *args, **kwargs: []),
        )

    asyncio.run(pipeline.run_pipeline("conc-deadline", [_photo_png()], {"general": "t", "photos": []}))
    state = pipeline._load_job_state("conc-deadline")
    assert state.status.value == "done"
    assert tracker.calls == 0, "no Meta call may start after the job budget is spent"
    assert state.errors == []


def test_budget_and_concurrency_constants_unchanged():
    """The budget and the cap keep their documented values."""
    assert pipeline.META_CONCURRENCY == 5
    assert pipeline.JOB_TIME_BUDGET_SECONDS == 300.0
