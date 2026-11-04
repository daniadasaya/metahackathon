
"""Per-job pipeline orchestration for Katalog Kilat (stages A-E)."""
from __future__ import annotations
import asyncio
import io
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image
from typing import Any
from app import exporter, imaging, meta_client, schemas
from app.meta_client import MetaModelNotFound
from app.schemas import (Concept, ErrorCode, ExtractContext, JobProgress, JobStage, JobState, JobStatus, MaskResult, Product, ProductEntry, StageError)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
JOBS_DIR = DATA_DIR / "jobs"
JOB_RECORD_FILENAME = "job.json"
META_CONCURRENCY = 5
JOB_TIME_BUDGET_SECONDS = 600.0
AREA_MIN = 0.01
AREA_MAX = 0.90
IOU_THRESHOLD = 0.85

#: A composite enters the catalog switched on only when its cut-out fills at
#: least this much of its own bounding box. Calibrated on the 31 products of the
#: live job job-18de6a923213: the five visibly torn composites measured 0.517,
#: 0.629, 0.645, 0.735 and 0.772; the sound ones 0.916, 0.921 and 0.926, plus one
#: long tapered tube at 0.642 whose box is mostly empty by shape. 0.80 catches
#: all five torn and switches off one sound product out of 26. ``muse-image``
#: output is never judged here: the gate already passed it, and a diagonal phone
#: in a wide box measures ~0.46 without being damaged.
COMPOSITE_MIN_COVERAGE = 0.80

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
    """Keep one mask per physical product.

    Two rules, applied against everything already kept:

    * **Same-concept duplicates** — the usual IoU test. SAM returns the same
      phone several times across revisions and scores; the highest revision and
      score wins, the rest are dropped.
    * **Cross-concept containment** — a sub-part mask ("phone screen") that sits
      almost entirely inside an already-kept mask for the whole object
      ("smartphone") is the same product seen twice, not a second product. It is
      dropped only when it is both heavily overlapping (IoU > 0.75) and more
      than 90% contained, so a genuinely separate accessory next to the phone is
      never suppressed.
    """
    ordered = sorted(masks, key=lambda m: (m.revision, m.score), reverse=True)
    kept = []
    for m in ordered:
        duplicate = False
        for k in kept:
            overlap = iou(m.bbox, k.bbox)
            if overlap > threshold:
                duplicate = True
                break
            if (
                m.concept != k.concept
                and overlap > 0.75
                and containment(m.bbox, k.bbox) > 0.90
            ):
                duplicate = True
                break
        if duplicate:
            continue
        kept.append(m)
    return kept


def containment(a, b):
    """Fraction of the smaller box that lies inside the larger one.

    IoU punishes a small box inside a large one (a phone screen inside a phone
    scores ~0.4 even though every pixel of it is the phone). Containment answers
    the question the dedupe actually asks: is this the same object, seen at a
    smaller extent?
    """
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    iw = max(0, ix2 - ix1 + 1)
    ih = max(0, iy2 - iy1 + 1)
    inter = iw * ih
    area_a = max(1, (ax2 - ax1 + 1) * (ay2 - ay1 + 1))
    area_b = max(1, (bx2 - bx1 + 1) * (by2 - by1 + 1))
    return inter / min(area_a, area_b)

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

def _photo_area(photo_bytes: bytes) -> int | None:
    """Pixel area of a photo, or ``None`` when it does not decode.

    An unreadable photo must not disable the area filter for its own masks: the
    caller keeps every mask in that case, exactly as before.
    """
    try:
        with Image.open(io.BytesIO(photo_bytes)) as im:
            return im.size[0] * im.size[1]
    except Exception:
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


def _ref_bytes_for(job_id, sku):
    """Ground-truth reference for the fidelity gate, falling back to the crop.

    ``crops/{sku}-ref.png`` is the two-panel image written by Stage C. When it
    is absent — an old job, or a direct ``_run_stage_d_one`` call in a unit test
    — the crop is the best available reference and the gate still runs.
    """
    path = job_dir(job_id) / "crops" / f"{sku}-ref.png"
    if path.is_file():
        return path.read_bytes()
    return _crop_bytes_for(job_id, sku)


def _apply_composite_guard(product: Product, crop_bytes: bytes) -> Product:
    """Switch a product off when its composite renders a torn cut-out.

    The fidelity gate rejecting a Muse Image edit is correct, but the fallback
    rebuilds the studio shot from the *same* mask: when the mask itself ate a
    bite out of the product, the deterministic composite is a faithful picture
    of damaged goods. Such a product still reaches Review — the seller sees it,
    switched off, carrying the ``Foto standar`` badge — but it is not part of
    the catalog until the seller says so.

    A gate-passed edit is never judged here; only the composite path is, and
    the measurement is the cut-out's own alpha coverage. Idempotent: the same
    crop always yields the same answer, so a re-run or a retry cannot flip a
    product back on.
    """
    if product.cleanup_method != schemas.CleanupMethod.COMPOSITE:
        product.included = True
        return product
    product.included = imaging.alpha_coverage(crop_bytes) >= COMPOSITE_MIN_COVERAGE
    return product


async def _run_stage_d_one(job_id, product, crop_bytes, sem, deadline, prev_product=None, errors=None):
    clean_path = job_dir(job_id) / "clean" / f"{product.sku}.png"
    clean_path.parent.mkdir(parents=True, exist_ok=True)
    if clean_path.is_file() and prev_product is not None and getattr(prev_product, "fidelity", None) is not None:
        product.cleanup_method = prev_product.cleanup_method
        product.fidelity = prev_product.fidelity
        return _apply_composite_guard(product, crop_bytes)
    cleaned_bytes = None
    verdict = None
    if clean_path.is_file():
        cached = clean_path.read_bytes()
        if prev_product is not None and getattr(prev_product, "fidelity", None) is None:
            product.cleanup_method = schemas.CleanupMethod.COMPOSITE
            product.fidelity = None
            return _apply_composite_guard(product, crop_bytes)
        cleaned_bytes = cached
    pre_verdict = None
    if cleaned_bytes is None and time.monotonic() <= deadline:
        ref_bytes = _ref_bytes_for(job_id, product.sku)
        try:
            # Step 1: pre-Muse-Image crop gate. Rejects an incomplete, occluded
            # or multi-object crop BEFORE a generative edit is paid for. A
            # rejection is the gate working, not an error: no StageError.
            async with sem:
                pre_verdict = await meta_client.verify_fidelity(ref_bytes, b"")
            if (
                pre_verdict.same_product
                and pre_verdict.color_match
                and pre_verdict.logo_text_match
            ):
                # Step 2: Muse Image edit, then the post-edit gate against the
                # same ground-truth reference the pre-gate saw.
                async with sem:
                    cleaned_bytes = await meta_client.clean_product_image(crop_bytes, product.concept)
                async with sem:
                    verdict = await meta_client.verify_fidelity(ref_bytes, cleaned_bytes)
        except Exception as exc:
            code = ErrorCode.MODEL_NOT_FOUND if isinstance(exc, MetaModelNotFound) else ErrorCode.CLEANUP_FAILED
            if errors is not None:
                errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.CLEANUP, code=code))
            cleaned_bytes = None
            verdict = None

    if (
        cleaned_bytes is not None
        and verdict is not None
        and verdict.same_product
        and verdict.color_match
        and verdict.logo_text_match
    ):
        product.cleanup_method = schemas.CleanupMethod.MUSE_IMAGE
        product.fidelity = verdict
        clean_path.write_bytes(cleaned_bytes)
        return _apply_composite_guard(product, crop_bytes)

    # Everything else — a pre-gate rejection, a post-gate rejection, an adapter
    # failure, or a spent deadline — ends in the deterministic composite, which
    # cannot distort the product. The composite is built exactly once, outside
    # the try above, so a corrupt crop raises here and is boxed by the caller as
    # ONE error instead of two. It is Pillow work over a full cut-out, so it runs
    # in a worker thread: the event loop must answer /api/health while a job is
    # being built.
    cleaned_bytes = await asyncio.to_thread(imaging.composite, crop_bytes)
    product.cleanup_method = schemas.CleanupMethod.COMPOSITE
    product.fidelity = verdict if verdict is not None else pre_verdict
    clean_path.write_bytes(cleaned_bytes)
    return _apply_composite_guard(product, crop_bytes)

def _without_price_prose(entry: ProductEntry) -> ProductEntry:
    """Return ``entry`` with price sentences removed from both descriptions.

    Stage E sometimes restates a price inside the prose ("harga hemat hanya
    Rp 35.000"). That number is never the seller's confirmed value, so it may
    not reach ``entries.json``, Review, or any export. The strip runs here, at
    the one point where an entry is applied to a product, so a cache hit and a
    fresh extraction are normalised identically. Clean prose is returned
    unchanged, so this is idempotent and cheap on the common path.
    """
    description_id = exporter.strip_price_mentions(entry.description_id)
    description_en = exporter.strip_price_mentions(entry.description_en)
    if description_id == entry.description_id and description_en == entry.description_en:
        return entry
    return entry.model_copy(
        update={"description_id": description_id, "description_en": description_en}
    )


# ---------------------------------------------------------------------------
# Price attribution — a hint is only a price the seller tied to THIS product
# ---------------------------------------------------------------------------
#
# Iron Law §1.1: ``price_hint_idr`` is a price the seller wrote for that item,
# or one printed in that item's photo. Stage E reads the whole caption blob, so
# one general sentence — "Skincare Endless Pure Rp 189.000, kaos polos Rp
# 65.000" — used to land as a pre-filled price on every product of the matching
# category: ten unrelated MOCK-UP bottles shipped at 189000 IDR in the live job
# job-18de6a923213, which is exactly the failure Price Law exists to prevent.
#
# The model cannot be trusted to scope a number, so the pipeline checks it
# against the two places a number can be tied to one product:
#
# * the photo's ``visible_text`` (what Stage A read off the pixels), and
# * that photo's own caption (``captions["photos"][index]``).
#
# The general caption is deliberately excluded. A number that appears only
# there fails both tests for every product, so it can never become a hint on
# one product, let alone on two — the strict reading of the rule, and the only
# version that cannot depend on the order the concurrent Stage E calls finish
# in.

#: ``Rp189.000`` and ``Rp. 189.000`` need the marker detached before scanning;
#: the amount itself never starts against a letter.
_CURRENCY_MARKER_RE = re.compile(r"\b(rp|idr)\.?\s*", re.IGNORECASE)

#: One amount token: ``35.000``, ``35000``, ``1,5``, ``189rb``, ``1.5jt``.
_AMOUNT_TOKEN_RE = re.compile(
    r"(?<![\w.])"
    r"(\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"\s*(ribu|ribuan|rb|juta|jutaan|jt|k)?"
    r"(?![\w])",
    re.IGNORECASE,
)

#: Magnitude words that scale an amount into rupiah.
_SCALES = {
    "rb": 1_000,
    "ribu": 1_000,
    "ribuan": 1_000,
    "k": 1_000,
    "jt": 1_000_000,
    "juta": 1_000_000,
    "jutaan": 1_000_000,
}

#: Below this, a number is a size, a capacity or a count, never a price.
MIN_PRICE_AMOUNT = 1_000


def _amount_value(raw: str, magnitude: str | None) -> int:
    """Parse one Indonesian amount token into integer rupiah.

    Dots group thousands, a comma is the decimal (``1.500.000`` → 1500000,
    ``1,5 juta`` → 1500000), and a trailing magnitude word scales the value
    (``189rb`` → 189000). A separator with one or two trailing digits is read
    as a decimal, which is the only reading that keeps ``1.5jt`` at 1.5.
    """
    if "," in raw and "." in raw:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
    elif "," in raw:
        decimal = "," if len(raw.rsplit(",", 1)[1]) <= 2 else ""
    elif "." in raw:
        decimal = "." if len(raw.rsplit(".", 1)[1]) <= 2 else ""
    else:
        decimal = ""
    if decimal:
        whole, fraction = raw.rsplit(decimal, 1)
        value = float(whole.replace(".", "").replace(",", "") + "." + fraction)
    else:
        value = float(raw.replace(".", "").replace(",", ""))
    scale = _SCALES.get((magnitude or "").lower(), 1)
    return int(round(value * scale))


def _price_amounts(text: str | None) -> set[int]:
    """Every amount in ``text`` that could be a rupiah price, normalised.

    ``"Rp 1.500.000"``, ``"1500000"``, ``"1,5 juta"`` and ``"189rb"`` all yield
    the integer they denote, so an attribution test is one set membership
    rather than four spellings. Numbers below :data:`MIN_PRICE_AMOUNT` are
    dropped: ``30 ml``, ``177 ml`` and ``SPF 30`` are not prices.

    A cached ``concepts.json`` is untrusted JSON, so anything that is not text
    carries no amount rather than raising mid-job.
    """
    if not isinstance(text, str) or not text:
        return set()
    spaced = _CURRENCY_MARKER_RE.sub(r"\1 ", text)
    amounts = set()
    for match in _AMOUNT_TOKEN_RE.finditer(spaced):
        value = _amount_value(match.group(1), match.group(2))
        if value >= MIN_PRICE_AMOUNT:
            amounts.add(value)
    return amounts


def _with_attributable_price(
    entry: ProductEntry,
    product: Product,
    concepts_per_photo: list[Any],
    photo_captions: list[Any],
) -> ProductEntry:
    """Keep ``entry.price_hint_idr`` only when it is tied to this product.

    A hint the seller did not tie to the item is demoted, never deleted: it
    moves to ``price_suggestion_idr`` (the grey ``Saran AI`` helper that never
    pre-fills a field and never reaches an export) when no suggestion is
    already there. The seller still sees the number; the seller decides.

    Runs at the one point where an entry is applied to a product, so a fresh
    extraction and a cached ``entries.json`` written by an older build are
    normalised identically. Idempotent: a demoted entry has no hint left to
    demote.
    """
    hint = entry.price_hint_idr
    if hint is None:
        return entry
    attributable = _price_amounts(_visible_text_for(product, concepts_per_photo))
    index = product.source_photo_index
    if isinstance(photo_captions, list) and 0 <= index < len(photo_captions):
        attributable |= _price_amounts(photo_captions[index])
    if hint in attributable:
        return entry
    suggestion = entry.price_suggestion_idr if entry.price_suggestion_idr is not None else hint
    return entry.model_copy(
        update={"price_hint_idr": None, "price_suggestion_idr": suggestion}
    )


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
                # One retry with a scene-relative phrase: appending "product"
                # made SAM segment unrelated products on the counter when the
                # concept genuinely had zero instances.
                async with sem:
                    masks = await meta_client.segment_concept(photo, f"foreground {phrase}")
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
    # Dedupe per photo, then combine: two different photos legitimately show the
    # same box coordinates, and a global pass would delete a real product from
    # the second photo as a "duplicate" of the first.
    mask_to_photo = {}
    deduped = []
    for i, photo_masks in enumerate(masks_per_photo):
        photo_area = await asyncio.to_thread(_photo_area, photos[i])
        area_kept = [
            m for m in photo_masks
            if photo_area is None or area_ok(m, photo_area)
        ]
        for m in dedupe_masks(area_kept, IOU_THRESHOLD):
            mask_to_photo[id(m)] = i
            deduped.append(m)
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
                # Pillow over the full frame: worker thread, never the loop.
                crop_bytes = await asyncio.to_thread(imaging.cut_out, photo, mask)
                crop_path.write_bytes(crop_bytes)
        except Exception:
            errors.append(StageError(photo_index=photo_index, stage=JobStage.CLEANUP, code=ErrorCode.CLEANUP_FAILED))
            continue
        # Ground-truth reference for the Stage D gate: the unmasked photo region
        # beside the cut-out on magenta. Written once per SKU and reused by every
        # retry, so the gate always compares against the original product. A
        # failure here is not fatal: _ref_bytes_for falls back to the crop.
        ref_path = crops_dir / f"{sku}-ref.png"
        if not ref_path.is_file():
            try:
                ref_path.write_bytes(
                    await asyncio.to_thread(
                        imaging.gate_reference, photo, mask, crop_bytes
                    )
                )
            except Exception:
                pass
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
                # No deadline gate here. JOB_TIME_BUDGET_SECONDS exists to stop
                # the expensive generative stage from running forever; Stage E
                # is cheap, and killing it turned a slow job into a catalog of
                # thirty untitled products. If the job is late, extraction still
                # runs — an entry costs seconds, a nameless product costs the
                # seller the whole catalog.
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
                if entry is not None:
                    entries[product.sku] = entry
        except Exception:
            # Unexpected raise (disk, decode) — box it per product.
            errors.append(StageError(photo_index=product.source_photo_index, stage=JobStage.EXTRACT, code=ErrorCode.EXTRACTION_FAILED))
            entry = None
        if entry is not None:
            entry = _without_price_prose(entry)
            entry = _with_attributable_price(entry, product, concepts_per_photo, photo_caps)
            entries[product.sku] = entry
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
        cleaned = await asyncio.to_thread(imaging.composite, crop_bytes)
        clean_path.write_bytes(cleaned)
        product.cleanup_method = schemas.CleanupMethod.COMPOSITE
    else:
        ref_bytes = _ref_bytes_for(job_id, sku)
        verdict = None
        try:
            # Same two-step gate as the pipeline: the crop must pass on its own
            # before a generative edit is requested.
            async with sem:
                pre_verdict = await meta_client.verify_fidelity(ref_bytes, b"")
            if (
                pre_verdict.same_product
                and pre_verdict.color_match
                and pre_verdict.logo_text_match
            ):
                async with sem:
                    cleaned = await meta_client.clean_product_image(crop_bytes, product.concept)
                async with sem:
                    verdict = await meta_client.verify_fidelity(ref_bytes, cleaned)
                if not (
                    verdict.same_product
                    and verdict.color_match
                    and verdict.logo_text_match
                ):
                    cleaned = await asyncio.to_thread(imaging.composite, crop_bytes)
                    product.cleanup_method = schemas.CleanupMethod.COMPOSITE
                else:
                    product.cleanup_method = schemas.CleanupMethod.MUSE_IMAGE
            else:
                # Pre-gate rejected the crop: no edit was attempted.
                cleaned = await asyncio.to_thread(imaging.composite, crop_bytes)
                product.cleanup_method = schemas.CleanupMethod.COMPOSITE
                verdict = pre_verdict
        except Exception:
            cleaned = await asyncio.to_thread(imaging.composite, crop_bytes)
            product.cleanup_method = schemas.CleanupMethod.COMPOSITE
        else:
            product.fidelity = verdict
        clean_path.write_bytes(cleaned)
    product = _apply_composite_guard(product, crop_bytes)
    state.products[idx] = product
    state.updated_at = _now()
    _write_job(job_id, state)
    return state
