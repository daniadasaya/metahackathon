"""Per-job pipeline orchestration for Katalog Kilat (stages A-C in the schema).

Stages fail independently: a failed photo or product appends to ``errors[]``
as ``{photo_index, stage, code}`` and the job continues. Stages are cached on
disk under ``data/jobs/{job_id}/`` so a retry for one product never re-runs
the others. Meta calls go through ``meta_client`` only, capped by a
semaphore.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
import io
from typing import Any

from app import imaging, meta_client, schemas
from app.meta_client import MetaModelNotFound
from app.schemas import (
    Concept,
    ErrorCode,
    JobProgress,
    JobStage,
    JobState,
    JobStatus,
    MaskResult,
    Product,
    StageError,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_DIR = DATA_DIR / "jobs"
JOB_RECORD_FILENAME = "job.json"

# Concurrency + time budget (spec).
META_CONCURRENCY = 5
JOB_TIME_BUDGET_SECONDS = 300.0

# Area filter thresholds .
AREA_MIN = 0.01
AREA_MAX = 0.90
IOU_THRESHOLD = 0.85


def job_dir(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _job_record_path(job_id: str) -> Path | None:
    root = JOBS_DIR.resolve()
    candidate = (JOBS_DIR / job_id / JOB_RECORD_FILENAME).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def write_job_record(job_id: str, state: JobState) -> None:
    """Public alias for the job-record writer."""
    _write_job(job_id, state)

def _write_job(job_id: str, state: JobState) -> None:
    path = _job_record_path(job_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(), encoding="utf-8")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    return imaging.iou(a, b)


def area_ratio(mask: MaskResult, photo_area: int) -> float:
    """Mask bbox area relative to the photo area."""
    x1, y1, x2, y2 = mask.bbox
    bw = max(0, x2 - x1 + 1)
    bh = max(0, y2 - y1 + 1)
    if photo_area <= 0:
        return 0.0
    return (bw * bh) / photo_area


def area_ok(mask: MaskResult, photo_area: int, min_ratio: float = AREA_MIN, max_ratio: float = AREA_MAX) -> bool:
    r = area_ratio(mask, photo_area)
    return min_ratio <= r <= max_ratio


def dedupe_masks(masks: list[MaskResult], threshold: float = IOU_THRESHOLD) -> list[MaskResult]:
    """Dedupe at IoU > threshold, keeping the higher revision (then score)."""
    ordered = sorted(masks, key=lambda m: (m.revision, m.score), reverse=True)
    kept: list[MaskResult] = []
    for m in ordered:
        if any(iou(m.bbox, k.bbox) > threshold for k in kept):
            continue
        kept.append(m)
    return kept


def reading_order(masks: list[MaskResult]) -> list[MaskResult]:
    """Sort masks left-to-right, top-to-bottom by bbox origin."""
    return sorted(masks, key=lambda m: (m.bbox[1], m.bbox[0]))


def assign_skus(masks: list[MaskResult]) -> list[str]:
    return [f"KK-{i + 1:04d}" for i in range(len(masks))]


def _error_code_for(exc: Exception) -> ErrorCode:
    if isinstance(exc, MetaModelNotFound):
        return ErrorCode.MODEL_NOT_FOUND
    return ErrorCode.SEGMENTATION_FAILED


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _concepts_path(job_id: str) -> Path:
    return job_dir(job_id) / "concepts.json"


def _masks_path(job_id: str, photo_index: int) -> Path:
    return job_dir(job_id) / "masks" / f"photo_{photo_index}.json"


def _load_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _load_concepts(job_id: str) -> list[list[dict[str, Any]]] | None:
    raw = _load_json(_concepts_path(job_id))
    if not isinstance(raw, list):
        return None
    return raw


def _save_concepts(job_id: str, data: list[list[dict[str, Any]]]) -> None:
    _save_json(_concepts_path(job_id), data)


def _load_masks(job_id: str, photo_index: int) -> list[MaskResult] | None:
    raw = _load_json(_masks_path(job_id, photo_index))
    if not isinstance(raw, list):
        return None
    out: list[MaskResult] = []
    for item in raw:
        try:
            out.append(MaskResult.model_validate(item))
        except Exception:
            return None
    return out


def _save_masks(job_id: str, photo_index: int, masks: list[MaskResult]) -> None:
    _save_json(
        _masks_path(job_id, photo_index),
        [m.model_dump(mode="json") for m in masks],
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(
    job_id: str,
    photos: list[bytes],
    captions: dict[str, Any],
) -> None:
    """Run stages A-C for one job, updating the on-disk job record."""
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "masks").mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "crops").mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "clean").mkdir(parents=True, exist_ok=True)

    sem = asyncio.Semaphore(META_CONCURRENCY)
    deadline = time.monotonic() + JOB_TIME_BUDGET_SECONDS
    errors: list[StageError] = []

    general = captions.get("general", "") if isinstance(captions, dict) else ""
    photo_caps = captions.get("photos", []) if isinstance(captions, dict) else []
    if not isinstance(photo_caps, list):
        photo_caps = []

    # Initial record.
    state = JobState(
        job_id=job_id,
        status=JobStatus.QUEUED,
        stage=JobStage.CONCEPTS,
        progress=JobProgress(
            photos_total=len(photos),
            photos_done=0,
            products_total=0,
            products_cleaned=0,
        ),
        products=[],
        errors=[],
        created_at=_now(),
        updated_at=_now(),
    )
    _write_job(job_id, state)

    # ---- Stage A: concepts ----
    state.status = JobStatus.RUNNING
    state.stage = JobStage.CONCEPTS
    state.updated_at = _now()
    _write_job(job_id, state)

    concepts_per_photo: list[list[dict[str, Any]]] = []
    cached = _load_concepts(job_id)
    if cached is not None and len(cached) == len(photos):
        concepts_per_photo = cached
    else:
        concepts_per_photo = [[] for _ in photos]
        for i, photo in enumerate(photos):
            if time.monotonic() > deadline:
                break
            try:
                async with sem:
                    concepts = await meta_client.discover_concepts(
                        photo, str(general) + "\n" + str(photo_caps[i] if i < len(photo_caps) else "")
                    )
                concepts_per_photo[i] = [c.model_dump(mode="json") for c in concepts]
            except Exception as exc:
                concepts_per_photo[i] = []
                errors.append(
                    StageError(photo_index=i, stage=JobStage.CONCEPTS, code=ErrorCode.CONCEPTS_FAILED)
                )
                # map model not found
                if isinstance(exc, MetaModelNotFound):
                    errors[-1] = StageError(photo_index=i, stage=JobStage.CONCEPTS, code=ErrorCode.MODEL_NOT_FOUND)
        _save_concepts(job_id, concepts_per_photo)

    # Ensure length.
    while len(concepts_per_photo) < len(photos):
        concepts_per_photo.append([])

    # ---- Stage B: segment ----
    state.stage = JobStage.SEGMENT
    state.updated_at = _now()
    _write_job(job_id, state)

    masks_per_photo: list[list[MaskResult]] = []
    for i, photo in enumerate(photos):
        if time.monotonic() > deadline:
            masks_per_photo.append([])
            continue
        cached_masks = _load_masks(job_id, i)
        if cached_masks is not None:
            masks_per_photo.append(cached_masks)
            continue
        photo_masks: list[MaskResult] = []
        concepts = concepts_per_photo[i] if i < len(concepts_per_photo) else []
        for c in concepts:
            phrase = c.get("phrase", "") if isinstance(c, dict) else getattr(c, "phrase", "")
            if not phrase:
                continue
            try:
                async with sem:
                    masks = await meta_client.segment_concept(photo, str(phrase))
                if not masks:
                    # exactly one retry with a more specific phrase
                    async with sem:
                        masks = await meta_client.segment_concept(photo, str(phrase) + " product")
                photo_masks.extend(masks)
            except Exception as exc:
                code = ErrorCode.MODEL_NOT_FOUND if isinstance(exc, MetaModelNotFound) else ErrorCode.SEGMENTATION_FAILED
                errors.append(StageError(photo_index=i, stage=JobStage.SEGMENT, code=code))
        _save_masks(job_id, i, photo_masks)
        masks_per_photo.append(photo_masks)

    while len(masks_per_photo) < len(photos):
        masks_per_photo.append([])

    # Progress photos_done: count photos that produced at least one mask? Use all processed.
    photos_done = sum(1 for i in range(len(photos)) if time.monotonic() <= deadline or i < len(masks_per_photo))

    # ---- Collect, filter, dedupe, order, SKU ----
    all_masks: list[tuple[int, MaskResult]] = []
    for i, photo_masks in enumerate(masks_per_photo):
        photo_area = None
        try:
            with Image.open(io.BytesIO(photos[i])) as im:
                photo_area = im.size[0] * im.size[1]
        except Exception:
            photo_area = None
        for m in photo_masks:
            if photo_area is not None and not area_ok(m, photo_area):
                continue
            all_masks.append((i, m))

    # Zero-mask concepts don't add; if a photo yielded nothing, no product.
    # Dedupe globally.
    flat_masks = [m for (_, m) in all_masks]
    deduped = dedupe_masks(flat_masks, IOU_THRESHOLD)
    # Reattach photo_index by identity.
    mask_to_photo: dict[int, int] = {}
    for idx, (pi, m) in enumerate(all_masks):
        mask_to_photo[id(m)] = pi
    ordered = reading_order(deduped)
    skus = assign_skus(ordered)

    # ---- Stage C: cut-out ----
    state.stage = JobStage.CLEANUP
    state.updated_at = _now()
    _write_job(job_id, state)

    products: list[Product] = []
    crops_dir = job_dir(job_id) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    for idx, (sku, mask) in enumerate(zip(skus, ordered)):
        photo_index = mask_to_photo.get(id(mask), 0)
        photo = photos[photo_index] if photo_index < len(photos) else b""
        # find concept phrase
        concept_str = mask.concept
        try:
            crop_bytes = imaging.cut_out(photo, mask)
        except Exception:
            errors.append(
                StageError(photo_index=photo_index, stage=JobStage.CLEANUP, code=ErrorCode.CLEANUP_FAILED)
            )
            continue
        crop_path = crops_dir / f"{sku}-crop.png"
        crop_path.write_bytes(crop_bytes)

        bbox = mask.bbox
        product = Product(
            sku=sku,
            job_id=job_id,
            source_photo_index=photo_index,
            concept=concept_str,
            bbox=(int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])),
            crop_url=f"/p/{job_id}/{sku}-crop.jpg",
            clean_url=f"/p/{job_id}/{sku}.jpg",
            cleanup_method=schemas.CleanupMethod.COMPOSITE,
            fidelity=None,
            name=concept_str,
            description_id="",
            description_en="",
            category=concept_str,
            brand=None,
            condition=schemas.Condition.NEW,
            attributes=schemas.ProductAttributes(color=None, size=None, variant=None),
            price_hint_idr=None,
            price_suggestion_idr=None,
            confidence=schemas.Confidence.LOW,
            price_idr=None,
            price_via_chat=False,
            availability=schemas.Availability.IN_STOCK,
            included=True,
        )
        products.append(product)

    # Save masks dir already. Write final state.
    state.status = JobStatus.DONE
    state.stage = JobStage.DONE
    state.progress = JobProgress(
        photos_total=len(photos),
        photos_done=len(photos),
        products_total=len(products),
        products_cleaned=0,
    )
    state.products = products
    state.errors = errors
    state.updated_at = _now()
    _write_job(job_id, state)


# Allow main to import.
__all__ = ["run_pipeline", "job_dir", "JOBS_DIR"]
