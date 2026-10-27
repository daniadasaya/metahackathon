"""Unit tests for ``app.pipeline`` helpers and cache behaviour (offline)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import pipeline, schemas
from app.schemas import MaskResult


def _mask(concept="x", bbox=(0, 0, 10, 10), w=100, h=100, rev=1, score=0.5, b64=None):
    return MaskResult(
        concept=concept,
        bbox=bbox,
        width=w,
        height=h,
        revision=rev,
        score=score,
        mask_png_base64=b64,
    )


def test_iou_identical_is_one() -> None:
    b = (0, 0, 10, 10)
    assert pipeline.iou(b, b) == 1.0


def test_iou_disjoint_is_zero() -> None:
    assert pipeline.iou((0, 0, 1, 1), (5, 5, 6, 6)) == 0.0


def test_dedupe_keeps_higher_revision() -> None:
    m_low = _mask(bbox=(0, 0, 10, 10), rev=1, score=0.9)
    m_high = _mask(bbox=(0, 0, 10, 10), rev=2, score=0.5)
    out = pipeline.dedupe_masks([m_low, m_high], threshold=0.85)
    assert len(out) == 1
    assert out[0].revision == 2


def test_dedupe_drops_overlapping() -> None:
    a = _mask(bbox=(0, 0, 10, 10), rev=1)
    b = _mask(bbox=(2, 2, 12, 12), rev=1)
    out = pipeline.dedupe_masks([a, b], threshold=0.5)
    assert len(out) == 1


def test_containment_is_fraction_of_smaller_box_inside_larger() -> None:
    outer = (0, 0, 99, 99)
    inner = (10, 10, 19, 19)
    assert pipeline.containment(inner, outer) == 1.0
    assert pipeline.containment(outer, inner) == 1.0
    assert pipeline.containment((0, 0, 9, 9), (0, 0, 9, 9)) == 1.0
    # Half of the smaller box sticks out: 50 of 100 pixels are inside.
    assert pipeline.containment((0, 0, 9, 9), (5, 0, 14, 9)) == pytest.approx(0.5)
    assert pipeline.containment((0, 0, 9, 9), (20, 20, 29, 29)) == 0.0


def test_dedupe_suppresses_cross_concept_subpart() -> None:
    """A phone-screen mask inside an already-kept phone mask is the same product.

    IoU alone cannot see this: a screen covering most of the phone's box scores
    IoU > 0.75 but a smaller one does not, and the containment rule is what
    turns "the same object at a smaller extent" into a drop.
    """
    phone = _mask(concept="smartphone", bbox=(100, 100, 500, 700))
    screen = _mask(concept="phone screen", bbox=(110, 110, 490, 690))
    assert pipeline.iou(phone.bbox, screen.bbox) > 0.75
    assert pipeline.containment(screen.bbox, phone.bbox) > 0.90
    out = pipeline.dedupe_masks([phone, screen])
    assert [m.concept for m in out] == ["smartphone"]


def test_dedupe_keeps_a_separate_accessory_next_to_the_product() -> None:
    """Containment must not swallow a genuinely separate object."""
    phone = _mask(concept="smartphone", bbox=(100, 100, 500, 700))
    case = _mask(concept="phone case", bbox=(600, 100, 800, 400))
    out = pipeline.dedupe_masks([phone, case])
    assert {m.concept for m in out} == {"smartphone", "phone case"}


def test_dedupe_same_concept_still_uses_iou_threshold() -> None:
    a = _mask(concept="smartphone", bbox=(0, 0, 100, 100), rev=1, score=0.9)
    b = _mask(concept="smartphone", bbox=(0, 0, 100, 100), rev=1, score=0.5)
    assert len(pipeline.dedupe_masks([a, b])) == 1


def test_area_filter_bounds() -> None:
    m = _mask(bbox=(0, 0, 9, 9))  # area 100
    assert pipeline.area_ok(m, photo_area=10000) is True
    tiny = _mask(bbox=(0, 0, 0, 0))
    assert pipeline.area_ok(tiny, photo_area=10000) is False
    huge = _mask(bbox=(0, 0, 99, 99))
    assert pipeline.area_ok(huge, photo_area=10000) is False


def test_reading_order_left_to_right_top_to_bottom() -> None:
    m1 = _mask(bbox=(50, 0, 60, 10))
    m2 = _mask(bbox=(0, 0, 10, 10))
    m3 = _mask(bbox=(0, 20, 10, 30))
    ordered = pipeline.reading_order([m1, m2, m3])
    assert [m.bbox for m in ordered] == [(0, 0, 10, 10), (50, 0, 60, 10), (0, 20, 10, 30)]


def test_assign_skus_stable() -> None:
    masks = [_mask() for _ in range(3)]
    assert pipeline.assign_skus(masks) == ["KK-0001", "KK-0002", "KK-0003"]


def test_stage_error_shape_no_message() -> None:
    err = schemas.StageError(photo_index=1, stage=schemas.JobStage.SEGMENT, code=schemas.ErrorCode.SEGMENTATION_FAILED)
    assert set(err.model_dump().keys()) == {"photo_index", "stage", "code"}


def test_concepts_cache_idempotency(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point JOBS_DIR at tmp_path so cache files land there.
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    job_id = "job-cache"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    # Pre-write concepts.json; run_pipeline should not call discover_concepts.
    import json

    payload = [[{"phrase": "smartphone", "scene_note": "s", "visible_text": None}]]
    pipeline._save_concepts(job_id, payload)

    called = {"n": 0}

    async def fake_discover(image_bytes, captions):
        called["n"] += 1
        return []

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    # segment returns [] for all (no masks) -> job done with 0 products.
    async def fake_segment(image_bytes, concept):
        return []

    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)

    async def main():
        await pipeline.run_pipeline(job_id, [b"img"], {"general": "", "photos": []})

    asyncio.run(main())
    assert called["n"] == 0, "cached concepts must not trigger a Meta call"


def test_independent_failure_lands_in_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    job_id = "job-fail"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    async def boom(image_bytes, captions):
        raise RuntimeError("concepts boom")

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", boom)

    async def main():
        await pipeline.run_pipeline(job_id, [b"img"], {"general": "", "photos": []})

    asyncio.run(main())
    import json

    state = pipeline.JobState.model_validate_json((jdir / "job.json").read_text())
    assert state.status.value == "done"
    assert len(state.errors) >= 1
    assert state.errors[0].stage.value == "concepts"
    assert state.errors[0].code.value == "concepts_failed"
    # errors entries have no message field
    assert "message" not in state.errors[0].model_dump()


def test_zero_mask_single_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    job_id = "job-retry"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    calls = {"n": 0}

    async def fake_discover(image_bytes, captions):
        return [schemas.Concept(phrase="smartphone", scene_note="s", visible_text=None)]

    async def fake_segment(image_bytes, concept):
        calls["n"] += 1
        # first call (original phrase) returns []; retry returns one mask
        if calls["n"] == 1:
            return []
        return [
            MaskResult(
                concept="smartphone",
                bbox=(1, 1, 5, 5),
                width=10,
                height=10,
                revision=1,
                score=0.9,
                mask_png_base64=None,
            )
        ]

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)

    async def main():
        await pipeline.run_pipeline(job_id, [b"img"], {"general": "", "photos": []})

    asyncio.run(main())
    assert calls["n"] == 2, "zero-mask result must trigger exactly one retry"


def test_zero_mask_retry_uses_foreground_phrase(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry phrase must be scene-relative, not ``"<concept> product"``.

    Appending ``" product"`` made SAM 3.1 segment unrelated products on the
    counter when the concept genuinely had zero instances, which is how a job
    grew products the seller never photographed.
    """
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    job_id = "job-retry-phrase"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    phrases: list[str] = []

    async def fake_discover(image_bytes, captions):
        return [schemas.Concept(phrase="smartphone", scene_note="s", visible_text=None)]

    async def fake_segment(image_bytes, concept):
        phrases.append(concept)
        return []

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)

    asyncio.run(pipeline.run_pipeline(job_id, [b"img"], {"general": "", "photos": []}))

    assert phrases == ["smartphone", "foreground smartphone"], (
        "the zero-mask retry must ask for the foreground of the concept, "
        "never '<concept> product'"
    )


def test_dedupe_is_per_photo_not_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two photos may legitimately report the same box coordinates.

    A global dedupe deletes the second photo's product as a duplicate of the
    first. Deduping inside each photo and then combining keeps both.
    """
    monkeypatch.setattr(pipeline, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)
    job_id = "job-per-photo"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    same_box = (40, 40, 200, 300)

    async def fake_discover(image_bytes, captions):
        return [schemas.Concept(phrase="smartphone", scene_note="s", visible_text=None)]

    async def fake_segment(image_bytes, concept):
        return [
            MaskResult(
                concept="smartphone", bbox=same_box, width=400, height=400,
                revision=1, score=0.9, mask_png_base64=None,
            )
        ]

    async def fake_clean(crop_png, label):
        from PIL import Image

        buf = __import__("io").BytesIO()
        Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
        return buf.getvalue()

    async def fake_verify(orig, cleaned):
        return schemas.FidelityVerdict(
            same_product=True, color_match=True, logo_text_match=True, reason="ok"
        )

    async def fake_extract(clean_png, context):
        return []

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    from PIL import Image

    photos = []
    for seed in (0, 1):
        buf = __import__("io").BytesIO()
        Image.new("RGB", (400, 400), (100 + seed * 20, 120, 140)).save(buf, format="PNG")
        photos.append(buf.getvalue())

    asyncio.run(pipeline.run_pipeline(job_id, photos, {"general": "", "photos": []}))
    state = pipeline._load_job_state(job_id)
    assert [p.sku for p in state.products] == ["KK-0001", "KK-0002"]
    assert {p.source_photo_index for p in state.products} == {0, 1}
