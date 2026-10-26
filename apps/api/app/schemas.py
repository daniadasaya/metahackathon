"""Single source of truth for every type that crosses a boundary.

Every payload that enters or leaves the service — request bodies, response
bodies, the job record on disk, the closed error/status/stage enums — is
declared here and nowhere else. ``apps/web/src/lib/types.ts`` is **generated**
from the OpenAPI document this module produces; it is never hand-edited.
Changing a field, an enum member, or an optionality marker here without
re-dumping ``apps/api/openapi.json`` turns the drift test
(``apps/api/tests/test_openapi_drift.py``) red, which is the intended failure:
a field named here becomes a TypeScript field there, and a field omitted here
is a field the web app cannot see.

Three invariants are structural rather than conventional:

* ``Product.price_suggestion_idr`` exists because the review screen renders it
  as grey helper text, but the export body is :class:`ReviewedProduct`, which
  physically does not carry that field. The server cannot export a value it
  never receives.
* ``StageError`` carries no ``message`` field: the UI never renders one, so
  there is nothing to leak and nothing to translate.
* ``ErrorCode`` is a closed enum. A code outside it has no locale key and
  would render as nothing, so an unlisted code is a defect.

Every model forbids unknown keys. A payload with a field the contract does not
know is rejected, never silently dropped.

No field declared by the API contract carries a pydantic default. ``| None``
is a null value that must be sent, not an omitted key: the only two states on
the wire are a value and an explicit ``null``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    """Lifecycle of a job. Polling stops on ``done`` or ``failed``."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class JobStage(StrEnum):
    """Pipeline stage a job is currently working on."""

    CONCEPTS = "concepts"
    SEGMENT = "segment"
    CLEANUP = "cleanup"
    EXTRACT = "extract"
    DONE = "done"


class ErrorCode(StrEnum):
    """Closed set of error codes. The UI maps each one to a locale key.

    Adding a member is a contract change: it needs a locale key in both
    dictionaries and a deliberate edit to the test that pins this set.
    """

    NOT_FOUND = "not_found"
    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    FILE_TOO_LARGE = "file_too_large"
    MODEL_NOT_FOUND = "model_not_found"
    MODEL_UNAVAILABLE = "model_unavailable"
    CONCEPTS_FAILED = "concepts_failed"
    SEGMENTATION_FAILED = "segmentation_failed"
    NO_PRODUCTS_FOUND = "no_products_found"
    CLEANUP_FAILED = "cleanup_failed"
    EXTRACTION_FAILED = "extraction_failed"
    PRICE_MISSING = "price_missing"
    EXPORT_FAILED = "export_failed"
    INTERNAL_ERROR = "internal_error"


class Confidence(StrEnum):
    """How sure Stage E is about the catalog entry it wrote."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Condition(StrEnum):
    """Commerce Manager condition. Lowercase, exactly (Meta Commerce catalog)."""

    NEW = "new"
    USED = "used"


class Availability(StrEnum):
    """Commerce Manager availability. Lowercase, exactly (Meta Commerce catalog)."""

    IN_STOCK = "in stock"
    OUT_OF_STOCK = "out of stock"


class CleanupMethod(StrEnum):
    """Which Stage D path produced the clean image.

    ``muse-image`` means a gate-passed generative edit; ``composite`` means the
    deterministic studio composite that physically cannot distort the product.
    """

    MUSE_IMAGE = "muse-image"
    COMPOSITE = "composite"


class ExportKind(StrEnum):
    """The four export artefacts, one per seller-facing button."""

    CATALOG_CSV = "catalog_csv"
    IMAGES_ZIP = "images_zip"
    CAPTIONS_INSTAGRAM = "captions_instagram"
    PRICE_LIST_WHATSAPP = "price_list_whatsapp"


class FidelityVerdict(BaseModel):
    """Stage D gate decision: did the generative edit preserve the product?"""

    model_config = ConfigDict(extra="forbid")

    same_product: bool
    color_match: bool
    logo_text_match: bool
    reason: str


class ProductAttributes(BaseModel):
    """Variant attributes shown on the review screen.

    All three are present on the wire and ``None`` when the seller has no value
    for them. A missing key is a rejected payload, not an empty variant.
    """

    model_config = ConfigDict(extra="forbid")

    color: str | None
    size: str | None
    variant: str | None


class Product(BaseModel):
    """One catalog product, field for field as the API contract declares.

    Prices are integer rupiah, never negative, never a float.
    ``price_hint_idr`` is the only value that may pre-fill the review field;
    ``price_suggestion_idr`` is AI grey text and never reaches an export;
    ``price_idr`` is the seller's confirmed value and the only exportable one.

    Every one of the 23 fields is **required**. ``| None`` marks a value
    that may be absent — a null ``brand`` or an unpriced product — but never an
    omitted key. The extra ``undefined`` state has no meaning in the product and
    would let a client omit ``price_via_chat`` and be read as ``false``, which
    the price invariant forbids.
    """

    model_config = ConfigDict(extra="forbid")

    sku: str
    job_id: str
    source_photo_index: int
    concept: str
    bbox: tuple[int, int, int, int]
    crop_url: str
    clean_url: str
    cleanup_method: CleanupMethod
    fidelity: FidelityVerdict | None
    name: str
    description_id: str
    description_en: str
    category: str
    brand: str | None
    condition: Condition
    attributes: ProductAttributes
    price_hint_idr: int | None = Field(ge=0)
    price_suggestion_idr: int | None = Field(ge=0)
    confidence: Confidence
    price_idr: int | None = Field(ge=0)
    price_via_chat: bool
    availability: Availability
    included: bool


class ReviewedProduct(BaseModel):
    """A :class:`Product` as the client posts it back for export.

    Every ``Product`` field except ``price_suggestion_idr``. The omission is
    the price invariant expressed as a type: an AI price guess is never
    transmitted to the exporter, so no code path can leak it into the CSV, the
    caption pack, or the WhatsApp list. ``extra="forbid"`` turns a leaked
    suggestion into a rejected request instead of a silently ignored key.

    Every one of its 22 fields is required, for the same reason ``Product``
    is.
    """

    model_config = ConfigDict(extra="forbid")

    sku: str
    job_id: str
    source_photo_index: int
    concept: str
    bbox: tuple[int, int, int, int]
    crop_url: str
    clean_url: str
    cleanup_method: CleanupMethod
    fidelity: FidelityVerdict | None
    name: str
    description_id: str
    description_en: str
    category: str
    brand: str | None
    condition: Condition
    attributes: ProductAttributes
    price_hint_idr: int | None = Field(ge=0)
    confidence: Confidence
    price_idr: int | None = Field(ge=0)
    price_via_chat: bool
    availability: Availability
    included: bool


class JobProgress(BaseModel):
    """Honest counters: ``products_cleaned`` counts what Stage D finished."""

    model_config = ConfigDict(extra="forbid")

    photos_total: int = Field(ge=0)
    photos_done: int = Field(ge=0)
    products_total: int = Field(ge=0)
    products_cleaned: int = Field(ge=0)


class StageError(BaseModel):
    """One failed photo or product. Deliberately has no ``message``."""

    model_config = ConfigDict(extra="forbid")

    photo_index: int
    stage: JobStage
    code: ErrorCode


class JobState(BaseModel):
    """The polling payload and the on-disk job record.

    A job that ends ``failed`` keeps every product it already completed:
    partial output is a feature, not an error state.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: JobStatus
    stage: JobStage
    progress: JobProgress
    products: list[Product]
    errors: list[StageError]
    created_at: datetime
    updated_at: datetime


class ErrorDetail(BaseModel):
    """``code`` is the contract; ``message`` is English for logs only."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str


class ErrorResponse(BaseModel):
    """Standard error envelope for every failing route."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Process liveness plus whether the service is in mock mode."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    mock: bool


class Concept(BaseModel):
    """A Stage A product concept, one per SAM request (never comma-joined)."""

    model_config = ConfigDict(extra="forbid")

    phrase: str
    scene_note: str
    visible_text: str | None = None


class MaskResult(BaseModel):
    """A Stage B mask. The mask carries **its own** box and dimensions.

    A segmentation mask is not necessarily full-frame, so aligning it against
    the source photo's dimensions would silently misplace the cut-out. The
    ``width`` and ``height`` here belong to the mask, not to the photo.

    ``mask_png_base64`` is ``None`` when a surface returns only mask metadata;
    the raster is filled in by the segmentation adapter whenever the model
    returns one.
    """

    model_config = ConfigDict(extra="forbid")

    concept: str
    bbox: tuple[int, int, int, int]
    width: int
    height: int
    revision: int
    score: float
    mask_png_base64: str | None = None


class ProductEntry(BaseModel):
    """Stage E output: the catalog text, without identity or file paths.

    The pipeline assigns ``sku``, ``job_id`` and the image URLs after
    extraction, so they are not part of what the model returns. Every field is
    required: Stage E must name a null ``brand`` or a null price rather
    than omit it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description_id: str
    description_en: str
    category: str
    brand: str | None
    condition: Condition
    attributes: ProductAttributes
    price_hint_idr: int | None = Field(ge=0)
    price_suggestion_idr: int | None = Field(ge=0)
    confidence: Confidence


class ExtractContext(BaseModel):
    """What Stage E is allowed to know beyond the cleaned image."""

    model_config = ConfigDict(extra="forbid")

    seller_captions: str
    concept: str
    visible_text: str | None = None


class SellerInfo(BaseModel):
    """Store name plus WhatsApp number, digits only, international, no ``+``."""

    model_config = ConfigDict(extra="forbid")

    store_name: str
    wa_number: str


class ExportFile(BaseModel):
    """One downloadable artefact produced by an export request."""

    model_config = ConfigDict(extra="forbid")

    kind: ExportKind
    filename: str
    url: str


class ExportRequest(BaseModel):
    """The export body: reviewed products plus the seller's own details."""

    model_config = ConfigDict(extra="forbid")

    products: list[ReviewedProduct]
    seller: SellerInfo


class ExportResponse(BaseModel):
    """The four files an export produced, addressed by URL."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    files: list[ExportFile]
