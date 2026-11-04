"""Event-loop responsiveness and the Stage E deadline (offline, no network).

Two production defects are pinned here, both measured rather than asserted by
inspection:

* **The loop must keep turning while imaging runs.** Pillow work is CPU-bound
  and used to execute directly on the event loop, so ``/api/health`` timed out
  for minutes while a job was in Stage C. The detector is a heartbeat task: it
  sleeps 10 ms and records the longest gap between wake-ups. A synchronous
  imaging call left on the loop blocks that gap for the whole call; a call moved
  to ``asyncio.to_thread`` does not.

* **Stage E is not governed by the generative deadline.** ``JOB_TIME_BUDGET_SECONDS``
  exists to stop the expensive generative stage, not to skip the cheap one that
  names the products. A job whose budget is spent still extracts, because a
  nameless catalog is worse than a late one.
"""

from __future__ import annotations

import asyncio
import base64
import io
import time

from PIL import Image, ImageDraw

from app import imaging, main, pipeline, schemas
from app.schemas import (
    Confidence,
    Condition,
    FidelityVerdict,
    MaskResult,
    ProductAttributes,
    ProductEntry,
)

PASS_VERDICT = dict(same_product=True, color_match=True, logo_text_match=True, reason="ok")

#: How long the slow imaging stub blocks its thread.
SLOW_CALL_SECONDS = 0.3

#: A loop that turns normally shows gaps of a few milliseconds. A single
#: synchronous 0.3 s call on the loop shows a gap of at least 0.3 s, so anything
#: under this is "the loop kept turning" with a wide margin.
MAX_LOOP_GAP_SECONDS = 0.15


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "data" / "jobs")


def _photo_png(size: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (size, size), (180, 160, 140)).save(buf, format="PNG")
    return buf.getvalue()


def _mask(concept: str, bbox: tuple[int, int, int, int], photo: int = 400) -> MaskResult:
    raster = Image.new("L", (photo, photo), 0)
    ImageDraw.Draw(raster).rectangle(list(bbox), fill=255)
    buf = io.BytesIO()
    raster.save(buf, format="PNG")
    return MaskResult(
        concept=concept,
        bbox=bbox,
        width=photo,
        height=photo,
        revision=1,
        score=0.9,
        mask_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
    )


def _entry(name: str = "Named Product") -> ProductEntry:
    return ProductEntry(
        name=name,
        description_id="d",
        description_en="e",
        category="Handphone & Tablet",
        brand=None,
        condition=Condition.NEW,
        attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None,
        price_suggestion_idr=None,
        confidence=Confidence.HIGH,
    )


def _patch_adapters(monkeypatch, *, masks: int = 1, entry: ProductEntry | None = None):
    async def fake_discover(image_bytes, captions):
        return [schemas.Concept(phrase="smartphone", scene_note="s", visible_text=None)]

    async def fake_segment(image_bytes, concept):
        box = (40, 40, 200, 200) if masks == 1 else (40, 40, 200, 200)
        return [_mask(concept, box)]

    async def fast_clean(crop_png, label):
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), (5, 5, 5)).save(buf, format="PNG")
        return buf.getvalue()

    async def fast_verify(original, cleaned):
        return FidelityVerdict(**PASS_VERDICT)

    async def fast_extract(clean_png, context):
        return [entry or _entry()]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fast_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fast_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fast_extract)


# ---------------------------------------------------------------------------
# The event loop keeps answering while imaging runs
# ---------------------------------------------------------------------------


def test_imaging_never_blocks_the_event_loop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _patch_adapters(monkeypatch)

    real_cut_out = imaging.cut_out
    calls: list[int] = []

    def slow_cut_out(image_bytes, mask):
        # The shape of the real defect: a synchronous Pillow pass that holds
        # whichever thread runs it for a noticeable time.
        calls.append(1)
        time.sleep(SLOW_CALL_SECONDS)
        return real_cut_out(image_bytes, mask)

    monkeypatch.setattr(pipeline.imaging, "cut_out", slow_cut_out)

    async def scenario() -> tuple[float, float]:
        stop = asyncio.Event()

        async def heartbeat() -> float:
            """Longest gap between wake-ups: the loop's own pulse."""
            worst = 0.0
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                worst = max(worst, now - last)
                last = now
            return worst

        async def probe_health() -> float:
            """The trivial await a phone in the Progress screen performs."""
            worst = 0.0
            while not stop.is_set():
                started = time.monotonic()
                response = await main.health()
                worst = max(worst, time.monotonic() - started)
                assert response.ok is True
                await asyncio.sleep(0.01)
            return worst

        beat = asyncio.create_task(heartbeat())
        probes = asyncio.create_task(probe_health())
        await pipeline.run_pipeline("loop-1", [_photo_png()], {"general": "t", "photos": []})
        stop.set()
        return await beat, await probes

    worst_gap, worst_health = asyncio.run(scenario())

    # The slow path really ran, or the measurement proves nothing.
    assert calls == [1]
    assert worst_gap < MAX_LOOP_GAP_SECONDS, (
        f"the event loop was starved for {worst_gap:.3f}s by a "
        f"{SLOW_CALL_SECONDS}s synchronous imaging call"
    )
    assert worst_health < MAX_LOOP_GAP_SECONDS, (
        f"/api/health took {worst_health:.3f}s while a job was running"
    )


def test_the_composite_fallback_also_runs_off_the_loop(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    # A pre-gate rejection sends every product to the deterministic composite.
    _patch_adapters(monkeypatch)

    async def reject(original, cleaned):
        return FidelityVerdict(
            same_product=False, color_match=False, logo_text_match=False, reason="no"
        )

    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", reject)

    real_composite = imaging.composite
    calls: list[int] = []

    def slow_composite(cutout_png):
        calls.append(1)
        time.sleep(SLOW_CALL_SECONDS)
        return real_composite(cutout_png)

    monkeypatch.setattr(pipeline.imaging, "composite", slow_composite)

    async def scenario() -> float:
        stop = asyncio.Event()

        async def heartbeat() -> float:
            worst = 0.0
            last = time.monotonic()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.monotonic()
                worst = max(worst, now - last)
                last = now
            return worst

        beat = asyncio.create_task(heartbeat())
        await pipeline.run_pipeline("loop-2", [_photo_png()], {"general": "t", "photos": []})
        stop.set()
        return await beat

    worst_gap = asyncio.run(scenario())

    assert calls == [1]
    assert worst_gap < MAX_LOOP_GAP_SECONDS, (
        f"the event loop was starved for {worst_gap:.3f}s by a "
        f"{SLOW_CALL_SECONDS}s synchronous composite"
    )


# ---------------------------------------------------------------------------
# Stage E survives a spent deadline
# ---------------------------------------------------------------------------


def test_stage_e_extracts_even_when_the_budget_is_spent(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    job_id = "e-after-deadline"
    photo = _photo_png()

    # Seed a cached Stage A and Stage B so the job reaches Stage C without a
    # single model call, then spend the budget before the run starts.
    pipeline._save_concepts(
        job_id,
        [[{"phrase": "smartphone", "scene_note": "s", "visible_text": None}]],
    )
    pipeline._save_masks(job_id, 0, [_mask("smartphone", (40, 40, 200, 200))])
    monkeypatch.setattr(pipeline, "JOB_TIME_BUDGET_SECONDS", -1.0)

    extracted: list[str] = []

    async def fake_extract(clean_png, context):
        extracted.append(context.concept)
        return [_entry("Kaos Polos Katun")]

    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    asyncio.run(pipeline.run_pipeline(job_id, [photo], {"general": "t", "photos": []}))

    state = pipeline._load_job_state(job_id)
    assert len(state.products) == 1
    product = state.products[0]
    # Stage D was governed by the deadline and fell back to the composite...
    assert product.cleanup_method == schemas.CleanupMethod.COMPOSITE
    # ...and Stage E ran anyway, so the catalog is not a list of untitled items.
    assert extracted == ["smartphone"]
    assert product.name == "Kaos Polos Katun"
    assert product.description_id == "d"
    assert product.category == "Handphone & Tablet"
    assert [e for e in state.errors if e.stage == schemas.JobStage.EXTRACT] == []
    assert state.status == schemas.JobStatus.DONE


def test_stage_e_still_reports_a_real_failure(tmp_path, monkeypatch):
    """Removing the deadline gate must not remove the error path."""
    _setup(tmp_path, monkeypatch)
    job_id = "e-failure"
    pipeline._save_concepts(
        job_id,
        [[{"phrase": "smartphone", "scene_note": "s", "visible_text": None}]],
    )
    pipeline._save_masks(job_id, 0, [_mask("smartphone", (40, 40, 200, 200))])
    monkeypatch.setattr(pipeline, "JOB_TIME_BUDGET_SECONDS", -1.0)

    async def failing_extract(clean_png, context):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(pipeline.meta_client, "extract_product", failing_extract)

    asyncio.run(pipeline.run_pipeline(job_id, [_photo_png()], {"general": "t", "photos": []}))

    state = pipeline._load_job_state(job_id)
    codes = [e.code for e in state.errors if e.stage == schemas.JobStage.EXTRACT]
    # One error per product: the repair retry is part of the same product's
    # attempt, not a second product.
    assert codes == [schemas.ErrorCode.EXTRACTION_FAILED]
    assert state.status == schemas.JobStatus.DONE
