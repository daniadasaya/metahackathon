"""Contract tests for ``app.schemas`` — the boundary type source of truth.

These tests pin the *shape* of the contract, not a value inside it. Each one
fails when a field is renamed, added, dropped, made optional, or made
nullable, and when an enum member appears or disappears. That is deliberate:
``apps/web/src/lib/types.ts`` is generated from the OpenAPI document these
models produce, so a silent shape change is a silent client break.

The price-invariant test is the most important one here. ``ReviewedProduct`` is
the export body and physically lacks ``price_suggestion_idr``; a future edit
that re-adds the field fails at
``test_reviewed_product_omits_the_ai_price_suggestion`` before it can leak an
AI price guess into a CSV.

Every test is offline and touches no file.
"""

from __future__ import annotations

from enum import Enum

import pytest
from pydantic import BaseModel, ValidationError

from app import schemas
from app.main import app
from app.schemas import (
    Availability,
    CleanupMethod,
    Condition,
    Confidence,
    ErrorCode,
    ExportRequest,
    ExportResponse,
    JobStage,
    JobStatus,
    Product,
    ProductAttributes,
    ProductEntry,
    ReviewedProduct,
    StageError,
)

# The exact contract field list, in order. Order is part of the contract: it is the
# order the review screen and the CSV generator walk.
PRODUCT_FIELDS_IN_ORDER = [
    "sku",
    "job_id",
    "source_photo_index",
    "concept",
    "bbox",
    "crop_url",
    "clean_url",
    "cleanup_method",
    "fidelity",
    "name",
    "description_id",
    "description_en",
    "category",
    "brand",
    "condition",
    "attributes",
    "price_hint_idr",
    "price_suggestion_idr",
    "confidence",
    "price_idr",
    "price_via_chat",
    "availability",
    "included",
]

REVIEWED_FIELDS_IN_ORDER = [
    name for name in PRODUCT_FIELDS_IN_ORDER if name != "price_suggestion_idr"
]

PRICE_FIELDS = ("price_hint_idr", "price_suggestion_idr", "price_idr")


def _all_models() -> list[type[BaseModel]]:
    """Every pydantic model declared in ``app.schemas``.

    Filtered by ``__module__`` so the imported ``BaseModel`` base class itself
    is not collected — it carries no ``extra`` setting and would fail the loop
    for a reason unrelated to the contract.
    """
    return [
        value
        for value in vars(schemas).values()
        if isinstance(value, type)
        and issubclass(value, BaseModel)
        and value is not BaseModel
        and value.__module__ == schemas.__name__
    ]


def _product_payload(**overrides: object) -> dict[str, object]:
    """A minimal valid ``Product`` payload, with optional overrides."""
    payload: dict[str, object] = {
        "sku": "KK-0001",
        "job_id": "job-1",
        "source_photo_index": 0,
        "concept": "smartphone",
        "bbox": [10, 20, 110, 220],
        "crop_url": "/p/job-1/KK-0001-crop.png",
        "clean_url": "/p/job-1/KK-0001.png",
        "cleanup_method": "muse-image",
        "fidelity": {
            "same_product": True,
            "color_match": True,
            "logo_text_match": True,
            "reason": "unchanged",
        },
        "name": "Samsung Galaxy S23",
        "description_id": "Ponsel bekas kondisi mulus.",
        "description_en": "Used phone in great condition.",
        "category": "Handphone & Tablet",
        "brand": "Samsung",
        "condition": "used",
        "attributes": {"color": "black", "size": None, "variant": "8/256"},
        "price_hint_idr": 4500000,
        "price_suggestion_idr": 4700000,
        "confidence": "high",
        "price_idr": 4400000,
        "price_via_chat": False,
        "availability": "in stock",
        "included": True,
    }
    payload.update(overrides)
    return payload


# --- 1. closed enums ---------------------------------------------------------


def test_error_code_is_closed_at_fourteen_members() -> None:
    """A code outside the enum has no locale key, so the set is pinned."""
    assert {member.value for member in ErrorCode} == {
        "not_found",
        "invalid_request",
        "unsupported_file_type",
        "file_too_large",
        "model_not_found",
        "model_unavailable",
        "concepts_failed",
        "segmentation_failed",
        "no_products_found",
        "cleanup_failed",
        "extraction_failed",
        "price_missing",
        "export_failed",
        "internal_error",
    }
    assert len(ErrorCode) == 14


def test_job_status_is_closed_at_four_members() -> None:
    """Polling stops on ``done`` or ``failed``; nothing else exists."""
    assert {member.value for member in JobStatus} == {
        "queued",
        "running",
        "done",
        "failed",
    }
    assert len(JobStatus) == 4


def test_job_stage_is_closed_at_five_members() -> None:
    """The five pipeline stages."""
    assert {member.value for member in JobStage} == {
        "concepts",
        "segment",
        "cleanup",
        "extract",
        "done",
    }
    assert len(JobStage) == 5


def test_confidence_is_closed_at_three_members() -> None:
    """Stage E confidence is one of three values."""
    assert {member.value for member in Confidence} == {"high", "medium", "low"}
    assert len(Confidence) == 3


def test_condition_is_closed_at_two_members() -> None:
    """Commerce Manager condition: lowercase, exactly."""
    assert {member.value for member in Condition} == {"new", "used"}
    assert len(Condition) == 2


def test_availability_is_closed_at_two_members() -> None:
    """Commerce Manager availability: lowercase, with a space."""
    assert {member.value for member in Availability} == {"in stock", "out of stock"}
    assert len(Availability) == 2


def test_cleanup_method_is_closed_at_two_members() -> None:
    """Stage D is either a gate-passed edit or the deterministic composite."""
    assert {member.value for member in CleanupMethod} == {"muse-image", "composite"}
    assert len(CleanupMethod) == 2


def test_export_kind_is_closed_at_four_members() -> None:
    """Four outputs, one click each."""
    assert {member.value for member in schemas.ExportKind} == {
        "catalog_csv",
        "images_zip",
        "captions_instagram",
        "price_list_whatsapp",
    }
    assert len(schemas.ExportKind) == 4


# --- 2. the price invariant as a structural guarantee ----------------------


def test_reviewed_product_omits_the_ai_price_suggestion() -> None:
    """The price invariant as a type: the export body cannot carry an AI guess."""
    assert "price_suggestion_idr" not in ReviewedProduct.model_fields
    for field in ("price_hint_idr", "price_idr"):
        assert field in ReviewedProduct.model_fields
    assert "price_via_chat" in ReviewedProduct.model_fields


# --- 3. the two product shapes differ by exactly one field ------------------


def test_reviewed_product_is_product_minus_the_suggestion() -> None:
    """One field dropped, nothing renamed, nothing added."""
    assert ReviewedProduct.model_fields.keys() == (
        Product.model_fields.keys() - {"price_suggestion_idr"}
    )
    assert list(ReviewedProduct.model_fields) == REVIEWED_FIELDS_IN_ORDER


# --- 4. a leaked suggestion is a rejected request, not a dropped key --------


def test_export_request_rejects_a_product_carrying_a_suggestion() -> None:
    """``extra="forbid"`` turns a leaked AI price into a validation failure."""
    product = Product.model_validate(_product_payload())
    assert product.price_suggestion_idr == 4700000

    with pytest.raises(ValidationError):
        ExportRequest.model_validate(
            {
                "products": [product.model_dump(mode="json")],
                "seller": {"store_name": "Toko Kilat", "wa_number": "6281234567890"},
            }
        )


def test_export_request_accepts_the_same_product_without_the_suggestion() -> None:
    """The reviewed body validates once the forbidden field is gone."""
    product = Product.model_validate(_product_payload())
    reviewed = product.model_dump(mode="json")
    reviewed.pop("price_suggestion_idr")

    request = ExportRequest.model_validate(
        {
            "products": [reviewed],
            "seller": {"store_name": "Toko Kilat", "wa_number": "6281234567890"},
        }
    )
    assert len(request.products) == 1
    assert request.products[0].price_idr == 4400000
    assert request.seller.wa_number == "6281234567890"


# --- 5. the product field list matches the contract, in order ---------------


def test_product_fields_match_the_canonical_list_in_order() -> None:
    """Field for field as the API contract declares, in order."""
    assert list(Product.model_fields) == PRODUCT_FIELDS_IN_ORDER
    assert len(Product.model_fields) == 23


# --- 5b. every contract field is required, nullable where the contract says null ---

# The four models whose fields all come from the contract. Every field on them is
# required: ``| None`` is a value that must be sent, never an omitted key.
FULLY_REQUIRED_MODELS = (Product, ReviewedProduct, ProductAttributes, ProductEntry)


@pytest.mark.parametrize(
    "model", FULLY_REQUIRED_MODELS, ids=[m.__name__ for m in FULLY_REQUIRED_MODELS]
)
def test_every_field_is_required_on_the_model(model: type[BaseModel]) -> None:
    """No pydantic default survives on a contract field.

    A default makes the field optional in JSON Schema, which is emitted into
    ``apps/web/src/lib/types.ts`` as ``brand?: string | null``. The extra
    ``undefined`` state has no meaning in the product, and an omitted
    ``price_via_chat`` would be read as ``false`` instead of rejected — the
    price invariant must never infer from silence. Looping rather than
    enumerating means a field added later cannot quietly escape the rule.
    """
    optional = [
        name for name, field in model.model_fields.items() if not field.is_required()
    ]

    assert not optional, (
        f"{model.__name__} declares {len(optional)} field(s) with a default, "
        f"which makes them optional on the wire: {optional}"
    )


@pytest.mark.parametrize(
    "model", FULLY_REQUIRED_MODELS, ids=[m.__name__ for m in FULLY_REQUIRED_MODELS]
)
def test_the_generated_json_schema_agrees_on_required(model: type[BaseModel]) -> None:
    """The OpenAPI document, not only pydantic, must list every field as required.

    ``types.ts`` is generated from the document, so a field that pydantic
    treats as required but the document omits from ``required`` would still
    arrive in TypeScript as optional. This is the assertion that actually
    protects the client.
    """
    app.openapi_schema = None
    published = app.openapi()["components"]["schemas"][model.__name__]

    assert set(published["required"]) == set(model.model_fields), (
        f"{model.__name__}: {sorted(set(model.model_fields) - set(published['required']))} "
        "missing from the document's required list"
    )
    assert len(published["properties"]) == len(model.model_fields)


def test_the_two_product_shapes_are_required_at_the_expected_field_counts() -> None:
    """The counts that matter: 23 product fields, 22 once the suggestion is gone."""
    assert len(Product.model_fields) == 23
    assert len(ReviewedProduct.model_fields) == 22
    assert len(ProductAttributes.model_fields) == 3
    assert len(ProductEntry.model_fields) == 10


def test_an_omitted_nullable_field_is_rejected_not_defaulted() -> None:
    """The regression this fixes: silence is not a value.

    ``price_via_chat`` is the one that matters — a client that forgets it must
    be rejected rather than have the export infer ``False`` and emit a price
    the seller never confirmed. ``brand`` and ``price_idr`` prove the
    same rule for the nullable fields.
    """
    for field in ("price_via_chat", "included", "brand", "price_idr", "fidelity"):
        payload = _product_payload()
        del payload[field]
        with pytest.raises(ValidationError):
            Product.model_validate(payload)


def test_an_explicit_null_is_accepted_on_a_nullable_field() -> None:
    """``| None`` still means null is legal — it just has to be sent."""
    product = Product.model_validate(
        _product_payload(brand=None, price_idr=None, fidelity=None)
    )
    assert product.brand is None
    assert product.price_idr is None
    assert product.fidelity is None


# --- 6. price fields are integer rupiah ------------------------------------


@pytest.mark.parametrize("field", PRICE_FIELDS)
def test_negative_rupiah_is_rejected(field: str) -> None:
    """Integer rupiah, never negative."""
    with pytest.raises(ValidationError):
        Product.model_validate(_product_payload(**{field: -1}))


@pytest.mark.parametrize("field", PRICE_FIELDS)
def test_fractional_rupiah_is_rejected(field: str) -> None:
    """No decimals. ``150000.5`` is not a rupiah amount."""
    with pytest.raises(ValidationError):
        Product.model_validate(_product_payload(**{field: 150000.5}))


@pytest.mark.parametrize("field", PRICE_FIELDS)
def test_zero_rupiah_is_accepted(field: str) -> None:
    """``0`` is a legitimate value; the boundary is below zero, not at it."""
    product = Product.model_validate(_product_payload(**{field: 0}))
    assert getattr(product, field) == 0


# --- 7. a stage error carries no rendered message --------------------------


def test_stage_error_has_no_message_field() -> None:
    """The UI never renders one, so there is nothing to carry."""
    assert set(StageError.model_fields) == {"photo_index", "stage", "code"}


# --- 8. an unlisted error code cannot be constructed -----------------------


def test_unknown_error_code_is_rejected() -> None:
    """A free-form code is a defect, so it must not validate."""
    with pytest.raises(ValidationError):
        schemas.ErrorDetail(code="something_new", message="nope")


# --- 9. every model forbids unknown keys -----------------------------------


def test_every_model_forbids_extra_keys() -> None:
    """A payload with a field the contract does not know is rejected."""
    models = _all_models()
    assert len(models) >= 18, f"expected every declared model, found {len(models)}"
    for model in models:
        assert model.model_config.get("extra") == "forbid", (
            f"{model.__name__} must set ConfigDict(extra='forbid')"
        )


def test_extra_keys_are_rejected_on_a_nested_payload() -> None:
    """``extra="forbid"`` reaches nested models, not only the outer one."""
    with pytest.raises(ValidationError):
        Product.model_validate(
            _product_payload(
                attributes={"color": "black", "size": None, "variant": None, "x": 1}
            )
        )


# --- 10. the remaining boundary shapes ------------------------------------


def test_mask_result_carries_its_own_dimensions() -> None:
    """A mask is not necessarily full-frame: box and size belong to the mask."""
    mask = schemas.MaskResult(
        concept="smartphone",
        bbox=(0, 0, 640, 480),
        width=640,
        height=480,
        revision=1,
        score=0.93,
    )
    assert mask.mask_png_base64 is None
    assert (mask.width, mask.height) == (640, 480)


def test_export_response_is_addressed_by_url() -> None:
    """The exporter returns links, not bytes, so the SPA downloads from them."""
    response = ExportResponse(
        job_id="job-1",
        files=[
            schemas.ExportFile(
                kind=schemas.ExportKind.CATALOG_CSV,
                filename="katalog-meta.csv",
                url="/api/exports/job-1/katalog-meta.csv",
            )
        ],
    )
    assert response.files[0].kind is schemas.ExportKind.CATALOG_CSV


def test_product_entry_has_no_identity_or_paths() -> None:
    """Stage E writes text only; the pipeline assigns sku, job_id and urls."""
    entry_fields = set(schemas.ProductEntry.model_fields)
    for forbidden in ("sku", "job_id", "crop_url", "clean_url", "cleanup_method", "fidelity"):
        assert forbidden not in entry_fields


# --- 11. the whole contract reaches components.schemas --------------------


def _declared_enums() -> list[type[Enum]]:
    """Every ``StrEnum`` declared in ``app.schemas``, filtered by module."""
    return [
        value
        for value in vars(schemas).values()
        if isinstance(value, type)
        and issubclass(value, Enum)
        and value.__module__ == schemas.__name__
    ]


def test_every_declared_schema_is_published_in_openapi() -> None:
    """The generated ``types.ts`` must see every model and enum.

    A model reaches ``components.schemas`` only when a route references it, so
    without the override in ``app.main`` the export body the review screen
    posts (``ExportRequest``) would be absent from the document until a route
    references it — and the client would have to hand-write the type, which
    the type-contract invariant forbids. This test pins the published set.

    ``app.openapi_schema`` is cleared first: FastAPI memoizes the document, so
    without the reset this assertion would inspect whatever a previous test
    happened to leave in the cache and could never fail.
    """
    app.openapi_schema = None
    published = set(app.openapi()["components"]["schemas"])

    declared_models = {model.__name__ for model in _all_models()}
    declared_enums = {enum_type.__name__ for enum_type in _declared_enums()}
    declared = declared_models | declared_enums
    missing = sorted(declared - published)

    assert not missing, (
        f"{len(missing)} of {len(declared)} declared schemas are missing from "
        f"components.schemas: {missing}"
    )
    print(
        f"published {len(declared)} declared schemas "
        f"({len(declared_models)} models + {len(declared_enums)} enums); "
        f"{len(published)} components in total, including FastAPI's own "
        "validation models"
    )


def test_published_refs_point_at_components_not_defs() -> None:
    """A ``#/$defs/...`` reference resolves to nothing in an OpenAPI document.

    The merge in ``app.main`` asks pydantic for ``#/components/schemas/{model}``
    references, so a ``$defs`` container left behind (or a ref that still
    points into it) would break every generated TypeScript alias that follows
    it.
    """
    app.openapi_schema = None
    document = app.openapi()

    assert "$defs" not in document, "the document must not carry a top-level $defs"

    def _walk(node: object) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "$ref" and isinstance(value, str):
                    found.append(value)
                else:
                    found.extend(_walk(value))
        elif isinstance(node, list):
            for item in node:
                found.extend(_walk(item))
        return found

    refs = _walk(document)
    assert refs, "the document carries no $ref at all, which cannot be right"
    bad = sorted({ref for ref in refs if not ref.startswith("#/components/schemas/")})
    assert not bad, f"references outside components.schemas: {bad}"


def test_a_route_published_schema_is_not_overwritten() -> None:
    """The route-derived entry is authoritative; the merge only fills gaps.

    ``JobState`` is referenced by ``GET /api/jobs/{job_id}``, so FastAPI built
    it from the route. If the merge replaced it, the two descriptions would
    have to agree by luck rather than by construction.
    """
    from fastapi.openapi.utils import get_openapi

    app.openapi_schema = None
    merged = app.openapi()["components"]["schemas"]

    route_only = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        routes=app.routes,
    )["components"]["schemas"]

    assert "JobState" in route_only, "the route no longer publishes JobState"
    assert merged["JobState"] == route_only["JobState"]
