
"""Per-job pipeline orchestration for Katalog Kilat (stages A-E)."""
from __future__ import annotations
import asyncio
import io
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image
from typing import Any
from app import imaging, meta_client, schemas
from app.meta_client import MetaModelNotFound
from app.schemas import (Concept, ErrorCode, ExtractContext, JobProgress, JobStage, JobState, JobStatus, MaskResult, Product, ProductEntry, StageError)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_DIR = DATA_DIR / "jobs"
JOB_RECORD_FILENAME = "job.json"
META_CONCURRENCY = 5
JOB_TIME_BUDGET_SECONDS = 300.0
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
    _write_job(job_id, state)

def _write_job(job_id: str, state: JobState) -> None:
    path = _job_record_path(job_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(), encoding="utf-8")

def _load_job_state(job_id: str) -> JobState:
    path = _job_record_path(job_id)
    if path is None or not path.is_file():
        raise FileNotFoundError(job_id)
    return JobState.model_validate_json(path.read_text(encoding="utf-8"))

def _now() -> datetime:
    return datetime.now(timezone.utc)

def iou(a, b):
    return imaging.iou(a, b)

def area_ratio(mask, photo_area):
    x1, y1, x2, y2 = mask.bbox
    bw = max(0, x2 - x1 + 1)
    bh = max(0, y2 - y1 + 1)
    if photo_area <= 0:
        return 0.0
    return (bw * bh) / photo_area

def area_ok(mask, photo_area, min_ratio=AREA_MIN, max_ratio=AREA_MAX):
    r = area_ratio(mask, photo_area)
    return min_ratio <= r <= max_ratio

def dedupe_masks(masks, threshold=IOU_THRESHOLD):
    ordered = sorted(masks, key=lambda m: (m.revision, m.score), reverse=True)
    kept = []
    for m in ordered:
        if any(iou(m.bbox, k.bbox) > threshold for k in kept):
            continue
        kept.append(m)
    return kept

def reading_order(masks):
    return sorted(masks, key=lambda m: (m.bbox[1], m.bbox[0]))

def assign_skus(masks):
    return [f"KK-{i + 1:04d}" for i in range(len(masks))]

def _concepts_path(job_id):
    return job_dir(job_id) / "concepts.json"

def _masks_path(job_id, photo_index):
    return job_dir(job_id) / "masks" / f"photo_{photo_index}.json"

def _entries_path(job_id):
    return job_dir(job_id) / "entries.json"

def _load_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

def _save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

def _load_concepts(job_id):
    raw = _load_json(_concepts_path(job_id))
    if not isinstance(raw, list):
        return None
    return raw

def _save_concepts(job_id, data):
    _save_json(_concepts_path(job_id), data)

def _load_masks(job_id, photo_index):
    raw = _load_json(_masks_path(job_id, photo_index))
    if not isinstance(raw, list):
        return None
    out = []
    for item in raw:
        try:
            out.append(MaskResult.model_validate(item))
        except Exception:
            return None
    return out

def _save_masks(job_id, photo_index, masks):
    _save_json(_masks_path(job_id, photo_index), [m.model_dump(mode="json") for m in masks])

def _load_entries(job_id):
    raw = _load_json(_entries_path(job_id))
    if not isinstance(raw, dict):
        return None
    out = {}
    for k, v in raw.items():
        try:
            out[k] = ProductEntry.model_validate(v)
        except Exception:
            return None
    return out

def _save_entries(job_id, entries):
    _save_json(_entries_path(job_id), {k: v.model_dump(mode="json") for k, v in entries.items()})

def _visible_text_for(product, concepts_per_photo):
    pi = product.source_photo_index
    concepts = concepts_per_photo[pi] if (isinstance(concepts_per_photo, list) and pi < len(concepts_per_photo)) else []
    for c in concepts:
        if isinstance(c, dict) and c.get("phrase") == product.concept:
            vt = c.get("visible_text")
            if vt is not None:
                return vt
    for c in concepts:
        if isinstance(c, dict):
            vt = c.get("visible_text")
            if vt is not None:
                return vt
    return None

def _crop_bytes_for(job_id, sku):
    path = job_dir(job_id) / "crops" / f"{sku}-crop.png"
    if path.is_file():
        return path.read_bytes()
    from PIL import Image as _Image
    import io as _io
    img = _Image.new("RGBA", (8, 8), (200, 200, 200, 255))
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

async def _run_stage_d_one(job_id, product, crop_bytes, sem, deadline, prev_product=None, errors=None):
    clean_path = job_dir(job_id) / "clean" / f"{product.sku}.png"
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    if clean_path.is_file() and prev_product is not None and getattr(prev_product, "fidelity", None) is not None:
        product.cleanup_method = prev_product.cleanup_method
        product.fidelity = prev_product.fidelity
        return product
    cleaned_bytes = None
    verdict = None
    if clean_path.is_file():
        cached = clean_path.read_bytes()
        if prev_product is not None and getattr(prev_product, "fidelity", None) is None:
            product.cleanup_method = schemas.CleanupMethod.COMPOSITE
            product.fidelity = None
            return product
        cleaned_bytes = cached
    if cleaned_bytes is None and time.monotonic() <= deadline:
        try:
            async with sem:
                cleaned_bytes = await meta_client.clean_product_image(crop_bytes, product.concept)
            async with sem:
                verdict = await meta_client.verify_fidelity(crop_bytes, cleaned_bytes)
        except Exception as exc:
            code = ErrorCode.MODEL_NOT_FOUND if isinstance(exc, MetaModelNotFound) else ErrorCode.CLEANUP_FAILED
            if errors is not None:
                errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.CLEANUP, code=code))
            cleaned_bytes = None
            verdict = None
    if cleaned_bytes is not None and verdict is not None:
        if verdict.same_product and verdict.color_match and verdict.logo_text_match:
            product.cleanup_method = schemas.CleanupMethod.MUSE_IMAGE
            product.fidelity = verdict
            clean_path.write_bytes(cleaned_bytes)
            return product
        fidelity_fail = verdict
        cleaned_bytes = imaging.composite(crop_bytes)
        product.cleanup_method = schemas.CleanupMethod.COMPOSITE
        product.fidelity = fidelity_fail
        clean_path.write_bytes(cleaned_bytes)
        return product
    if cleaned_bytes is None:
        cleaned_bytes = imaging.composite(crop_bytes)
    product.cleanup_method = schemas.CleanupMethod.COMPOSITE
    if product.fidelity is None:
        product.fidelity = verdict
    clean_path.write_bytes(imaging.composite(crop_bytes))
    return product

async def run_pipeline(job_id, photos, captions):
    job_dir(job_id).mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "masks").mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "crops").mkdir(parents=True, exist_ok=True)
    (job_dir(job_id) / "clean").mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(META_CONCURRENCY)
    deadline = time.monotonic() + JOB_TIME_BUDGET_SECONDS
    errors = []
    general = captions.get("general", "") if isinstance(captions, dict) else ""
    photo_caps = captions.get("photos", []) if isinstance(captions, dict) else []
    if not isinstance(photo_caps, list):
        photo_caps = []
    prev_state = None
    prev_path = _job_record_path(job_id)
    if prev_path is not None and prev_path.is_file():
        try:
            prev_state = JobState.model_validate_json(prev_path.read_text(encoding="utf-8"))
        except Exception:
            prev_state = None
    prev_map = {p.sku: p for p in (prev_state.products if prev_state else [])}
    cached_entries = _load_entries(job_id)
    state = JobState(
        job_id=job_id,
        status=JobStatus.QUEUED,
        stage=JobStage.CONCEPTS,
        progress=JobProgress(photos_total=len(photos), photos_done=0, products_total=0, products_cleaned=0),
        products=[],
        errors=[],
        created_at=_now(),
        updated_at=_now(),
    )
    _write_job(job_id, state)
    state.status = JobStatus.RUNNING
    state.stage = JobStage.CONCEPTS
    state.updated_at = _now()
    _write_job(job_id, state)
    concepts_per_photo = []
    cached = _load_concepts(job_id)
    if cached is not None and len(cached) == len(photos):
        concepts_per_photo = cached
    else:
        concepts_per_photo = [[] for _ in photos]

        async def _concepts_one(i: int, photo: bytes) -> None:
            # One photo's Stage A. A skipped photo (budget spent) is not a
            # failure: no error, concepts stay empty, exactly as the old
            # sequential `break` did.
            if time.monotonic() > deadline:
                return
            caption = str(general) + "\n" + str(photo_caps[i] if i < len(photo_caps) else "")
            try:
                async with sem:
                    concepts = await meta_client.discover_concepts(photo, caption)
            except Exception as exc:
                concepts_per_photo[i] = []
                code = ErrorCode.MODEL_NOT_FOUND if isinstance(exc, MetaModelNotFound) else ErrorCode.CONCEPTS_FAILED
                errors.append(StageError(photo_index=i, stage=JobStage.CONCEPTS, code=code))
                return
            concepts_per_photo[i] = [c.model_dump(mode="json") for c in concepts]

        # Stage A fans out across photos; the shared semaphore caps in-flight
        # Meta calls at META_CONCURRENCY.
        await asyncio.gather(*(_concepts_one(i, p) for i, p in enumerate(photos)))
        _save_concepts(job_id, concepts_per_photo)
    while len(concepts_per_photo) < len(photos):
        concepts_per_photo.append([])
    state.stage = JobStage.SEGMENT
    state.updated_at = _now()
    _write_job(job_id, state)
    masks_per_photo: list[Any] = [None] * len(photos)
    segment_tasks = []
    seg_keys = []  # (photo_index, concept_index) per task, same order as segment_tasks

    async def _segment_one(i: int, photo: bytes, phrase: str):
        # One concept on one photo, including the single zero-mask retry.
        # Returns None when skipped past the deadline: a skipped concept is not
        # an error, but it forbids caching a partial mask set for the photo.
        if time.monotonic() > deadline:
            return None
        try:
            async with sem:
                masks = await meta_client.segment_concept(photo, phrase)
            if not masks:
                async with sem:
                    masks = await meta_client.segment_concept(photo, phrase + " product")
        except Exception as exc:
            code = ErrorCode.MODEL_NOT_FOUND if isinstance(exc, MetaModelNotFound) else ErrorCode.SEGMENTATION_FAILED
            errors.append(StageError(photo_index=i, stage=JobStage.SEGMENT, code=code))
            return []
        return list(masks)

    for i, photo in enumerate(photos):
        cached_masks = _load_masks(job_id, i)
        if cached_masks is not None:
            masks_per_photo[i] = cached_masks
            continue
        concepts = concepts_per_photo[i] if i < len(concepts_per_photo) else []
        for ci, c in enumerate(concepts):
            phrase = c.get("phrase", "") if isinstance(c, dict) else getattr(c, "phrase", "")
            if not phrase:
                continue
            seg_keys.append((i, ci))
            segment_tasks.append(_segment_one(i, photo, str(phrase)))
    # Stage B fans out across every (photo, concept) pair at once; the shared
    # semaphore caps in-flight Meta calls.
    seg_results = await asyncio.gather(*segment_tasks)
    ran_by_photo: dict[int, list] = {}
    for (i, ci), masks in zip(seg_keys, seg_results):
        ran_by_photo.setdefault(i, []).append((ci, masks))
    for i in range(len(photos)):
        if masks_per_photo[i] is not None:
            continue  # cached from a previous run
        photo_masks = []
        complete = True
        for _ci, masks in sorted(ran_by_photo.get(i, []), key=lambda pair: pair[0]):
            if masks is None:
                complete = False
                continue
            photo_masks.extend(masks)
        masks_per_photo[i] = photo_masks
        # Errors already leave masks as [] (still cached, as before); a
        # deadline-skipped concept leaves the photo uncached for a later retry.
        if complete:
            _save_masks(job_id, i, photo_masks)
    masks_per_photo = [m if m is not None else [] for m in masks_per_photo]
    while len(masks_per_photo) < len(photos):
        masks_per_photo.append([])
    all_masks = []
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
    flat_masks = [m for (_, m) in all_masks]
    deduped = dedupe_masks(flat_masks, IOU_THRESHOLD)
    mask_to_photo = {}
    for idx, (pi, m) in enumerate(all_masks):
        mask_to_photo[id(m)] = pi
    ordered = reading_order(deduped)
    skus = assign_skus(ordered)
    state.stage = JobStage.CLEANUP
    state.updated_at = _now()
    _write_job(job_id, state)
    crops_dir = job_dir(job_id) / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)
    products = []
    for idx, (sku, mask) in enumerate(zip(skus, ordered)):
        photo_index = mask_to_photo.get(id(mask), 0)
        photo = photos[photo_index] if photo_index < len(photos) else b""
        concept_str = mask.concept
        crop_path = crops_dir / f"{sku}-crop.png"
        try:
            if crop_path.is_file():
                crop_bytes = crop_path.read_bytes()
            else:
                crop_bytes = imaging.cut_out(photo, mask)
                crop_path.write_bytes(crop_bytes)
        except Exception:
            errors.append(StageError(photo_index=photo_index, stage=JobStage.CLEANUP, code=ErrorCode.CLEANUP_FAILED))
            continue
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
    state.stage = JobStage.CLEANUP
    state.updated_at = _now()
    _write_job(job_id, state)
    entries = dict(cached_entries) if cached_entries else {}
    cleaned_count = 0

    async def _cleanup_one(i: int, product: Product) -> Product:
        # One product's Stage D. _run_stage_d_one already isolates adapter
        # failures; this catch boxes the rest (disk, decode) so one bad product
        # can never cancel its siblings or the job.
        nonlocal cleaned_count
        try:
            crop_bytes = _crop_bytes_for(job_id, product.sku)
            prev_p = prev_map.get(product.sku)
            updated = await _run_stage_d_one(job_id, product, crop_bytes, sem, deadline, prev_product=prev_p, errors=errors)
        except Exception:
            errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.CLEANUP, code=ErrorCode.CLEANUP_FAILED))
            return product
        cleaned_count += 1
        return updated

    # Stage D fans out across products; the shared semaphore caps in-flight
    # Meta calls. SKUs were assigned before this stage, so completion
    # order cannot renumber anything.
    d_results = await asyncio.gather(*(_cleanup_one(i, p) for i, p in enumerate(products)))
    for i, updated in enumerate(d_results):
        products[i] = updated
    state.progress = JobProgress(photos_total=len(photos), photos_done=len(photos), products_total=len(products), products_cleaned=cleaned_count)
    state.products = list(products)
    state.errors = list(errors)
    state.updated_at = _now()
    _write_job(job_id, state)
    state.stage = JobStage.EXTRACT
    state.updated_at = _now()
    _write_job(job_id, state)
    captions_payload = captions if isinstance(captions, dict) else {"general": "", "photos": []}
    captions_json = json.dumps(captions_payload, ensure_ascii=False)

    async def _extract_one(i: int, product: Product) -> Product:
        # One product's Stage E: cache-first per SKU, one adapter call plus the
        # single repair retry. Every failure lands in errors[]; nothing
        # propagates to the gather.
        entry = None
        try:
            if product.sku in entries:
                entry = entries[product.sku]
            else:
                visible_text = _visible_text_for(product, concepts_per_photo)
                context = ExtractContext(seller_captions=captions_json, concept=product.concept, visible_text=visible_text)
                clean_img_path = job_dir(job_id) / "clean" / f"{product.sku}.png"
                clean_img_bytes = clean_img_path.read_bytes() if clean_img_path.is_file() else b""
                if not clean_img_bytes:
                    clean_img_bytes = _crop_bytes_for(job_id, product.sku)
                if time.monotonic() <= deadline:
                    try:
                        async with sem:
                            extracted = await meta_client.extract_product(clean_img_bytes, context)
                        if extracted:
                            entry = extracted[0]
                        else:
                            raise ValueError("empty extraction")
                    except Exception:
                        # One repair retry: re-invoke the adapter once.
                        try:
                            async with sem:
                                extracted = await meta_client.extract_product(clean_img_bytes, context)
                            if extracted:
                                entry = extracted[0]
                            else:
                                raise ValueError("empty extraction")
                        except Exception as second_exc:
                            code = ErrorCode.MODEL_NOT_FOUND if isinstance(second_exc, MetaModelNotFound) else ErrorCode.EXTRACTION_FAILED
                            errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.EXTRACT, code=code))
                            entry = None
                else:
                    errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.EXTRACT, code=ErrorCode.EXTRACTION_FAILED))
                    entry = None
                if entry is not None:
                    entries[product.sku] = entry
        except Exception:
            # Unexpected raise (disk, decode) — box it per product.
            errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.EXTRACT, code=ErrorCode.EXTRACTION_FAILED))
            entry = None
        if entry is not None:
            product.name = entry.name
            product.description_id = entry.description_id
            product.description_en = entry.description_en
            product.category = entry.category
            product.brand = entry.brand
            product.condition = entry.condition
            product.attributes = entry.attributes
            product.price_hint_idr = entry.price_hint_idr
            product.price_suggestion_idr = entry.price_suggestion_idr
            product.confidence = entry.confidence
        return product

    # Stage E fans out across products. entries.json and job.json are
    # written once after the gather: concurrent completions never interleave a
    # write of either file.
    e_results = await asyncio.gather(*(_extract_one(i, p) for i, p in enumerate(products)))
    for i, merged in enumerate(e_results):
        products[i] = merged
    _save_entries(job_id, entries)
    state.products = list(products)
    state.errors = list(errors)
    state.updated_at = _now()
    _write_job(job_id, state)
    state.status = JobStatus.DONE
    state.stage = JobStage.DONE
    state.progress = JobProgress(photos_total=len(photos), photos_done=len(photos), products_total=len(products), products_cleaned=cleaned_count)
    state.products = products
    state.errors = errors
    state.updated_at = _now()
    _write_job(job_id, state)

async def retry_cleanup_product(job_id, sku):
    state = _load_job_state(job_id)
    idx = next((i for i, p in enumerate(state.products) if p.sku == sku), None)
    if idx is None:
        raise KeyError(sku)
    product = state.products[idx]
    state.errors = [e for e in state.errors if not (e.photo_index == product.source_photo_index and e.stage == JobStage.CLEANUP)]
    crop_bytes = _crop_bytes_for(job_id, sku)
    sem = asyncio.Semaphore(META_CONCURRENCY)
    deadline = time.monotonic() + JOB_TIME_BUDGET_SECONDS
    errors = list(state.errors)
    updated = await _run_stage_d_one(job_id, product, crop_bytes, sem, deadline, prev_product=product, errors=errors)
    state.products[idx] = updated
    state.errors = errors
    state.updated_at = _now()
    _write_job(job_id, state)
    return state

async def toggle_cleanup_product(job_id, sku):
    state = _load_job_state(job_id)
    idx = next((i for i, p in enumerate(state.products) if p.sku == sku), None)
    if idx is None:
        raise KeyError(sku)
    product = state.products[idx]
    crop_bytes = _crop_bytes_for(job_id, sku)
    sem = asyncio.Semaphore(META_CONCURRENCY)
    deadline = time.monotonic() + JOB_TIME_BUDGET_SECONDS
    clean_path = job_dir(job_id) / "clean" / f"{sku}.png"
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    if product.cleanup_method == schemas.CleanupMethod.MUSE_IMAGE:
        cleaned = imaging.composite(crop_bytes)
        clean_path.write_bytes(cleaned)
        product.cleanup_method = schemas.CleanupMethod.COMPOSITE
    else:
        try:
            async with sem:
                cleaned = await meta_client.clean_product_image(crop_bytes, product.concept)
            async with sem:
                verdict = await meta_client.verify_fidelity(crop_bytes, cleaned)
        except Exception:
            cleaned = imaging.composite(crop_bytes)
            clean_path.write_bytes(cleaned)
            product.cleanup_method = schemas.CleanupMethod.COMPOSITE
        else:
            if verdict.same_product and verdict.color_match and verdict.logo_text_match:
                clean_path.write_bytes(cleaned)
                product.cleanup_method = schemas.CleanupMethod.MUSE_IMAGE
                product.fidelity = verdict
            else:
                cleaned = imaging.composite(crop_bytes)
                clean_path.write_bytes(cleaned)
                product.cleanup_method = schemas.CleanupMethod.COMPOSITE
                product.fidelity = verdict
    state.products[idx] = product
    state.updated_at = _now()
    _write_job(job_id, state)
    return state
