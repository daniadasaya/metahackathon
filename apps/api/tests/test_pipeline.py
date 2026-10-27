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
