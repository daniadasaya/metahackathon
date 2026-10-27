"""Offline tests for Stage D/E wiring, fidelity gate, retry/toggle, price law."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from app import imaging, meta_client, pipeline, schemas
from app import schemas
from app.meta_client import MetaModelNotFound
from app.schemas import (
    Condition,
    Confidence,
    FidelityVerdict,
    JobStage,
    MaskResult,
    ProductEntry,
    ProductAttributes,
)


def _mask(concept="smartphone", bbox=(10, 10, 100, 100), w=200, h=200):
    return MaskResult(
        concept=concept,
        bbox=bbox,
        width=w,
        height=h,
        revision=1,
        score=0.9,
        mask_png_base64=None,
    )


def _raster_mask(concept="smartphone", bbox=(10, 10, 100, 100), w=200, h=200):
    """A mask carrying a real raster, sized to its own box like a live mask."""
    import base64

    from PIL import Image, ImageDraw

    x1, y1, x2, y2 = bbox
    raster = Image.new("L", (x2 - x1 + 1, y2 - y1 + 1), 0)
    ImageDraw.Draw(raster).rectangle([0, 0, x2 - x1, y2 - y1], fill=255)
    buf = io.BytesIO()
    raster.save(buf, format="PNG")
    return MaskResult(
        concept=concept,
        bbox=bbox,
        width=x2 - x1 + 1,
        height=y2 - y1 + 1,
        revision=1,
        score=0.9,
        mask_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
    )


def _setup(tmp_path):
    td = tmp_path / "data"
    pipeline.DATA_DIR = td
    pipeline.JOBS_DIR = td / "jobs"
    pipeline.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    return td


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    td = _setup(tmp_path)
    return td / "jobs"


def test_composite_deterministic():
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
    # use a real tiny png
    from PIL import Image
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b = buf.getvalue()
    a1 = imaging.composite(b)
    a2 = imaging.composite(b)
    assert a1 == a2


def test_fidelity_gate_pass_routes_muse_image(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-gatepass"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    # patch meta_client
    async def fake_clean(crop_png, label):
        from PIL import Image
        img = Image.new("RGB", (8, 8), (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(same_product=True, color_match=True, logo_text_match=True, reason="ok")

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)

    from PIL import Image
    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    product = Product(
        sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x",
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="x",
        description_id="", description_en="", category="x", brand=None,
        condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True,
    )
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    sem = asyncio.Semaphore(1)
    import time
    asyncio.run(pipeline._run_stage_d_one(job_id, product, crop_bytes, sem, time.monotonic() + 10))
    assert product.cleanup_method == CleanupMethod.MUSE_IMAGE
    assert product.fidelity is not None and product.fidelity.same_product


def test_fidelity_gate_fail_routes_composite(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-gatefail"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    async def fake_clean(crop_png, label):
        from PIL import Image
        img = Image.new("RGB", (8, 8), (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(same_product=False, color_match=True, logo_text_match=False, reason="redesigned")

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    product = Product(
        sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x",
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="x",
        description_id="", description_en="", category="x", brand=None,
        condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True,
    )
    from PIL import Image
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    crop_bytes = buf.getvalue()
    sem = asyncio.Semaphore(1)
    import time
    asyncio.run(pipeline._run_stage_d_one(job_id, product, crop_bytes, sem, time.monotonic() + 10))
    assert product.cleanup_method == CleanupMethod.COMPOSITE
    assert product.fidelity is not None and product.fidelity.same_product is False


def test_extraction_merge_and_price_law_null(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-extract"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    # write a clean image
    from PIL import Image
    img = Image.new("RGB", (8, 8), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (jdir / "clean" / "KK-0001.png").write_bytes(buf.getvalue())

    async def fake_extract(clean_png, context):
        return [ProductEntry(
            name="Test Product",
            description_id="desc id",
            description_en="desc en",
            category="Makanan & Minuman",
            brand="Acme",
            condition=Condition.USED,
            attributes=ProductAttributes(color="red", size=None, variant=None),
            price_hint_idr=None,
            price_suggestion_idr=None,
            confidence=Confidence.HIGH,
        )]

    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    product = Product(
        sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x",
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="x",
        description_id="", description_en="", category="x", brand=None,
        condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True,
    )
    entries = {}
    captions_payload = {"general": "", "photos": []}
    captions_json = __import__("json").dumps(captions_payload)
    context = schemas.ExtractContext(seller_captions=captions_json, concept="x", visible_text=None)
    # simulate merge
    clean_img_bytes = (jdir / "clean" / "KK-0001.png").read_bytes()
    import time
    sem = asyncio.Semaphore(1)
    async def run():
        async with sem:
            extracted = await pipeline.meta_client.extract_product(clean_img_bytes, context)
        entry = extracted[0]
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
    p = asyncio.run(run())
    assert p.name == "Test Product"
    assert p.price_hint_idr is None
    assert p.price_suggestion_idr is None
    assert p.price_idr is None


def test_toggle_flips_and_persists(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-toggle"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    from PIL import Image
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (jdir / "crops" / "KK-0001-crop.png").write_bytes(buf.getvalue())

    async def fake_clean(crop_png, label):
        img2 = Image.new("RGB", (8, 8), (1, 2, 3))
        b = io.BytesIO()
        img2.save(b, format="PNG")
        return b.getvalue()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(same_product=True, color_match=True, logo_text_match=True, reason="ok")

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    product = Product(
        sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x",
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="x",
        description_id="", description_en="", category="x", brand=None,
        condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True,
    )
    # write job state
    state = schemas.JobState(
        job_id=job_id, status=schemas.JobStatus.DONE, stage=schemas.JobStage.DONE,
        progress=schemas.JobProgress(photos_total=1, photos_done=1, products_total=1, products_cleaned=1),
        products=[product], errors=[], created_at=pipeline._now(), updated_at=pipeline._now(),
    )
    pipeline._write_job(job_id, state)

    # toggle composite -> muse
    st = asyncio.run(pipeline.toggle_cleanup_product(job_id, "KK-0001"))
    assert st.products[0].cleanup_method == CleanupMethod.MUSE_IMAGE
    # persist
    st2 = pipeline._load_job_state(job_id)
    assert st2.products[0].cleanup_method == CleanupMethod.MUSE_IMAGE


def test_toggle_pre_gate_blocks_muse_image_call(tmp_path, monkeypatch):
    """The toggle path runs the same two-step gate as the pipeline.

    A crop that fails the pre-gate must stay on the composite and must not
    reach Muse Image, so flipping a product on cannot smuggle in an ungated
    generative edit.
    """
    _setup(tmp_path)
    job_id = "job-toggle-pregate"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    (jdir / "crops" / "KK-0001-crop.png").write_bytes(_crop_png())

    clean_calls = {"n": 0}

    async def fake_clean(crop_png, label):
        clean_calls["n"] += 1
        return _crop_png()

    async def reject_pre_gate(orig, cleaned):
        assert cleaned == b""
        return FidelityVerdict(
            same_product=False, color_match=True, logo_text_match=True,
            reason="crop is incomplete",
        )

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", reject_pre_gate)

    product = _product(job_id)
    state = schemas.JobState(
        job_id=job_id, status=schemas.JobStatus.DONE, stage=schemas.JobStage.DONE,
        progress=schemas.JobProgress(
            photos_total=1, photos_done=1, products_total=1, products_cleaned=1
        ),
        products=[product], errors=[],
        created_at=pipeline._now(), updated_at=pipeline._now(),
    )
    pipeline._write_job(job_id, state)

    st = asyncio.run(pipeline.toggle_cleanup_product(job_id, "KK-0001"))

    assert clean_calls["n"] == 0, "the pre-gate must block the Muse Image call"
    assert st.products[0].cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert st.products[0].fidelity is not None
    assert st.products[0].fidelity.same_product is False
    assert (jdir / "clean" / "KK-0001.png").is_file()


def test_retry_cleanup_touches_only_its_product(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-retry2"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    from PIL import Image
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (jdir / "crops" / "KK-0001-crop.png").write_bytes(buf.getvalue())
    (jdir / "crops" / "KK-0002-crop.png").write_bytes(buf.getvalue())

    async def fake_clean(crop_png, label):
        img2 = Image.new("RGB", (8, 8), (1, 2, 3))
        b = io.BytesIO()
        img2.save(b, format="PNG")
        return b.getvalue()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(same_product=True, color_match=True, logo_text_match=True, reason="ok")

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    p1 = Product(sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x", bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x", cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="a", description_id="", description_en="", category="x", brand=None, condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None), price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW, price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True)
    p2 = Product(sku="KK-0002", job_id=job_id, source_photo_index=0, concept="y", bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x", cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="b", description_id="", description_en="", category="y", brand=None, condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None), price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW, price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True)
    state = schemas.JobState(job_id=job_id, status=schemas.JobStatus.DONE, stage=schemas.JobStage.DONE, progress=schemas.JobProgress(photos_total=1, photos_done=1, products_total=2, products_cleaned=2), products=[p1, p2], errors=[], created_at=pipeline._now(), updated_at=pipeline._now())
    pipeline._write_job(job_id, state)

    before_p2_method = pipeline._load_job_state(job_id).products[1].cleanup_method
    asyncio.run(pipeline.retry_cleanup_product(job_id, "KK-0001"))
    after = pipeline._load_job_state(job_id)
    assert after.products[0].cleanup_method == CleanupMethod.MUSE_IMAGE
    assert after.products[1].cleanup_method == before_p2_method


def test_error_independence(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-errind"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    from PIL import Image
    img = Image.new("RGBA", (8, 8), (200, 100, 50, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (jdir / "crops" / "KK-0001-crop.png").write_bytes(buf.getvalue())
    (jdir / "crops" / "KK-0002-crop.png").write_bytes(buf.getvalue())

    call_count = {"n": 0}

    async def fake_clean(crop_png, label):
        call_count["n"] += 1
        if label == "bad":
            raise RuntimeError("boom")
        img2 = Image.new("RGB", (8, 8), (1, 2, 3))
        b = io.BytesIO()
        img2.save(b, format="PNG")
        return b.getvalue()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(same_product=True, color_match=True, logo_text_match=True, reason="ok")

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    p1 = Product(sku="KK-0001", job_id=job_id, source_photo_index=0, concept="bad", bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x", cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="a", description_id="", description_en="", category="x", brand=None, condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None), price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW, price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True)
    p2 = Product(sku="KK-0002", job_id=job_id, source_photo_index=0, concept="good", bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x", cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="b", description_id="", description_en="", category="y", brand=None, condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None), price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW, price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True)
    state = schemas.JobState(job_id=job_id, status=schemas.JobStatus.DONE, stage=schemas.JobStage.DONE, progress=schemas.JobProgress(photos_total=1, photos_done=1, products_total=2, products_cleaned=2), products=[p1, p2], errors=[], created_at=pipeline._now(), updated_at=pipeline._now())
    pipeline._write_job(job_id, state)

    # retry the bad one
    asyncio.run(pipeline.retry_cleanup_product(job_id, "KK-0001"))
    st = pipeline._load_job_state(job_id)
    # bad product still present, has error
    assert any(e.stage == JobStage.CLEANUP for e in st.errors)
    # good product untouched
    p2_after = [p for p in st.products if p.sku == "KK-0002"][0]
    assert p2_after.cleanup_method == CleanupMethod.COMPOSITE  # unchanged (not retried)


def test_stage_progression_reaches_done(tmp_path, monkeypatch):
    _setup(tmp_path)
    # use valid image
    from PIL import Image
    img = Image.new("RGB", (500, 500), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()
    job_id = "job-prog"
    pipeline.JOBS_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(pipeline.run_pipeline(job_id, [png], {"general": "T", "photos": []}))
    st = pipeline._load_job_state(job_id)
    assert st.status.value == "done"
    assert st.stage.value == "done"
    assert st.progress.products_cleaned == st.progress.products_total


def test_price_missing_stays_null(tmp_path, monkeypatch):
    _setup(tmp_path)
    job_id = "job-price"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "masks").mkdir(exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)
    from PIL import Image
    img = Image.new("RGB", (8, 8), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    (jdir / "clean" / "KK-0001.png").write_bytes(buf.getvalue())

    async def fake_extract(clean_png, context):
        return [ProductEntry(
            name="P", description_id="d", description_en="e", category="c",
            brand=None, condition=Condition.NEW,
            attributes=ProductAttributes(color=None, size=None, variant=None),
            price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        )]

    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    from app.schemas import Product, ProductAttributes, Availability, Condition, Confidence, CleanupMethod
    product = Product(
        sku="KK-0001", job_id=job_id, source_photo_index=0, concept="x",
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name="x",
        description_id="", description_en="", category="x", brand=None,
        condition=Condition.NEW, attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK, included=True,
    )
    entries = {}
    captions_json = __import__("json").dumps({"general": "", "photos": []})
    context = schemas.ExtractContext(seller_captions=captions_json, concept="x", visible_text=None)
    clean_img_bytes = (jdir / "clean" / "KK-0001.png").read_bytes()
    sem = asyncio.Semaphore(1)

    async def run():
        async with sem:
            extracted = await pipeline.meta_client.extract_product(clean_img_bytes, context)
        entry = extracted[0]
        product.price_hint_idr = entry.price_hint_idr
        product.price_suggestion_idr = entry.price_suggestion_idr
        return product

    p = asyncio.run(run())
    assert p.price_hint_idr is None
    assert p.price_suggestion_idr is None
    assert p.price_idr is None


def test_mock_job_clean_url_served_200():
    """A fresh mock job's clean_url (/p/{job_id}/{sku}.jpg) returns 200."""
    import asyncio

    async def _call(path):
        messages = []
        request_sent = False

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message):
            messages.append(message)

        from app import main as mainmod

        await asyncio.wait_for(mainmod.app(scope, receive, send), timeout=10)
        start = next(m for m in messages if m["type"] == "http.response.start")
        return int(start["status"])

    status = asyncio.run(_call("/p/mock-1-2/KK-0001.jpg"))
    assert status == 200


def test_extract_repair_retry_succeeds(tmp_path, monkeypatch):
    """The repair retry: first extract attempt fails, pipeline retries once, succeeds, merges."""
    _setup(tmp_path)
    from PIL import Image

    img = Image.new("RGB", (500, 500), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    calls = {"n": 0}
    retried = {"flag": False}

    async def fake_extract(clean_png, context):
        calls["n"] += 1
        if calls["n"] == 1:
            retried["flag"] = True
            raise ValueError("simulated validation failure")
        return [ProductEntry(
            name="Repaired Product",
            description_id="d",
            description_en="e",
            category="c",
            brand="B",
            condition=Condition.NEW,
            attributes=ProductAttributes(color=None, size=None, variant=None),
            price_hint_idr=100,
            price_suggestion_idr=200,
            confidence=Confidence.HIGH,
        )]

    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    job_id = "job-repair-ok"
    asyncio.run(pipeline.run_pipeline(job_id, [png], {"general": "T", "photos": []}))
    st = pipeline._load_job_state(job_id)
    assert st.status.value == "done"
    assert retried["flag"], "repair retry must have occurred"
    assert calls["n"] >= 2
    # merged fields from the successful retry
    assert any(p.name == "Repaired Product" for p in st.products)
    # no extract errors
    assert not any(e.stage == JobStage.EXTRACT for e in st.errors)


def test_extract_second_failure_lands_in_errors(tmp_path, monkeypatch):
    """Second consecutive extraction failure -> StageError extract, job continues."""
    _setup(tmp_path)
    from PIL import Image

    img = Image.new("RGB", (500, 500), (200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    async def fake_extract(clean_png, context):
        raise RuntimeError("boom")

    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    job_id = "job-extract-fail"
    asyncio.run(pipeline.run_pipeline(job_id, [png], {"general": "T", "photos": []}))
    st = pipeline._load_job_state(job_id)
    assert st.status.value == "done"
    assert any(e.stage == JobStage.EXTRACT for e in st.errors)
    # products still present (partial output)
    assert len(st.products) > 0
    # price stays null (no invention)
    for p in st.products:
        assert p.price_idr is None


# ---------------------------------------------------------------------------
# Two-step fidelity gate: pre-Muse-Image crop gate, then post-edit gate
# ---------------------------------------------------------------------------


def _product(job_id, sku="KK-0001", concept="smartphone"):
    from app.schemas import (
        Availability,
        CleanupMethod,
        Product,
    )

    return Product(
        sku=sku, job_id=job_id, source_photo_index=0, concept=concept,
        bbox=(0, 0, 1, 1), crop_url="/p/x", clean_url="/p/x",
        cleanup_method=CleanupMethod.COMPOSITE, fidelity=None, name=concept,
        description_id="", description_en="", category=concept, brand=None,
        condition=Condition.NEW,
        attributes=ProductAttributes(color=None, size=None, variant=None),
        price_hint_idr=None, price_suggestion_idr=None, confidence=Confidence.LOW,
        price_idr=None, price_via_chat=False, availability=Availability.IN_STOCK,
        included=True,
    )


def _crop_png():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGBA", (8, 8), (200, 100, 50, 255)).save(buf, format="PNG")
    return buf.getvalue()


def test_pre_gate_rejects_incomplete_crop_before_muse_image_called(tmp_path, monkeypatch):
    """A crop that fails the pre-gate must never reach the generative edit.

    This is the cost and the fidelity guarantee in one assertion: an incomplete
    or occluded crop is caught on the reference image, Muse Image is not called
    at all, and the deterministic composite is the result. A pre-gate rejection
    is the gate working, so it appends no StageError.
    """
    _setup(tmp_path)
    job_id = "job-pregate-reject"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    clean_calls = {"n": 0}

    async def fake_clean(crop_png, label):
        clean_calls["n"] += 1
        return _crop_png()

    async def reject_pre_gate(orig, cleaned):
        # The pre-gate is the only call that carries an empty cleaned image.
        assert cleaned == b""
        return FidelityVerdict(
            same_product=False, color_match=True, logo_text_match=True,
            reason="crop shows two phones and a cable",
        )

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", reject_pre_gate)

    product = _product(job_id)
    errors = []
    asyncio.run(
        pipeline._run_stage_d_one(
            job_id, product, _crop_png(), asyncio.Semaphore(1),
            __import__("time").monotonic() + 10, errors=errors,
        )
    )

    assert clean_calls["n"] == 0, "the pre-gate must run before Muse Image, not after"
    assert product.cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert product.fidelity is not None
    assert product.fidelity.same_product is False
    assert errors == [], "a pre-gate rejection is a gate outcome, not an error"
    # The composite was written, so the SKU still has a legal clean image.
    assert (jdir / "clean" / "KK-0001.png").is_file()


def test_post_gate_rejects_bad_edit_after_pre_gate_passes(tmp_path, monkeypatch):
    """The pre-gate passes, the edit is made, and the post-gate rejects it.

    Both gates are needed: a good crop does not make a faithful edit, which is
    exactly the Muse Image failure mode the product exists to catch.
    """
    _setup(tmp_path)
    job_id = "job-postgate-reject"
    jdir = pipeline.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    (jdir / "crops").mkdir(exist_ok=True)
    (jdir / "clean").mkdir(exist_ok=True)

    clean_calls = {"n": 0}
    verify_calls = {"n": 0}

    async def fake_clean(crop_png, label):
        clean_calls["n"] += 1
        return _crop_png()

    async def staged_gate(orig, cleaned):
        verify_calls["n"] += 1
        if cleaned == b"":
            return FidelityVerdict(
                same_product=True, color_match=True, logo_text_match=True,
                reason="crop is the whole product",
            )
        return FidelityVerdict(
            same_product=False, color_match=False, logo_text_match=False,
            reason="the edit redesigned the product",
        )

    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", staged_gate)

    product = _product(job_id)
    errors = []
    asyncio.run(
        pipeline._run_stage_d_one(
            job_id, product, _crop_png(), asyncio.Semaphore(1),
            __import__("time").monotonic() + 10, errors=errors,
        )
    )

    assert clean_calls["n"] == 1, "a passing pre-gate must reach Muse Image exactly once"
    assert verify_calls["n"] == 2, "one pre-gate call and one post-edit call"
    assert product.cleanup_method == schemas.CleanupMethod.COMPOSITE
    assert product.fidelity is not None
    assert product.fidelity.same_product is False
    assert errors == [], "a rejected edit is a verdict, not an adapter error"


# ---------------------------------------------------------------------------
# Stage C geometry: mask alignment and mask cleanup
# ---------------------------------------------------------------------------


def test_clean_mask_drops_disconnected_sliver_and_cut_out_does_not_stretch():
    """The two Stage C bugs, pinned in one test.

    Bug 1A — mask coordinate stretching. The mask raster maps onto the mask
    record's **own box**. The old code padded the box first and then resized the
    raster over the padded crop, so every background pixel inside the pad became
    opaque: the neighbour object next to the product reappeared inside the
    cut-out. The photo below is built so that the old behaviour is impossible to
    miss — the neighbour sits exactly in the 8% padding ring.

    Bug 1B — unmasked RGB in transparent pixels. The Meta API decodes a crop
    without its alpha channel, so a pixel with ``alpha == 0`` still contributes
    its RGB. Transparent pixels must therefore carry the studio background.
    """
    from PIL import Image, ImageDraw

    from app.schemas import MaskResult

    x1, y1, x2, y2 = 120, 80, 220, 200
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    pad = int(round(0.08 * max(bw, bh)))
    assert pad >= 8, "the padding ring must be wide enough to hold the neighbour"

    # The product fills its box; the neighbour sits inside the padding ring, so
    # only a stretched mask can ever make it opaque.
    photo = Image.new("RGB", (400, 300), (255, 255, 255))
    draw = ImageDraw.Draw(photo)
    draw.rectangle([x1, y1, x2, y2], fill=(200, 20, 20))  # the product
    draw.rectangle([x2 + 2, y1, x2 + pad, y2], fill=(20, 20, 200))  # neighbour
    buf = io.BytesIO()
    photo.save(buf, format="PNG")
    photo_bytes = buf.getvalue()

    # The mask raster covers the product plus a detached sliver in its corner.
    # The sliver is separated from the main block by a background gap, so only
    # the largest-component rule removes it.
    raster = Image.new("L", (bw, bh), 0)
    mask_draw = ImageDraw.Draw(raster)
    mask_draw.rectangle([0, 0, bw - 1, bh - 1], fill=255)  # the product
    mask_draw.rectangle([bw - 3, bh - 3, bw - 1, bh - 1], fill=0)  # gap
    mask_draw.rectangle([bw - 2, bh - 2, bw - 1, bh - 1], fill=255)  # sliver

    # clean_mask keeps only the largest component, so the sliver disappears.
    cleaned = imaging.clean_mask(raster)
    cleaned_px = cleaned.load()
    assert cleaned_px[bw - 1, bh - 1] == 0, "the detached sliver must be dropped"
    assert cleaned_px[bw // 2, bh // 2] == 255, "the product must survive"
    assert cleaned.getbbox() is not None
    assert set(cleaned.tobytes()) <= {0, 255}, "mask must be binary"

    mask_buf = io.BytesIO()
    raster.save(mask_buf, format="PNG")
    import base64

    mask = MaskResult(
        concept="smartphone",
        bbox=(x1, y1, x2, y2),
        width=bw,
        height=bh,
        revision=1,
        score=0.9,
        mask_png_base64=base64.b64encode(mask_buf.getvalue()).decode("ascii"),
    )

    out = imaging.cut_out(photo_bytes, mask)
    cutout = Image.open(io.BytesIO(out)).convert("RGBA")
    cut_px = cutout.load()

    # 1A: the opaque region is the object, and the padding ring around it is
    # transparent. The neighbour inside that ring must never be opaque.
    opaque = [
        (x, y)
        for y in range(cutout.size[1])
        for x in range(cutout.size[0])
        if cut_px[x, y][3] == 255
    ]
    assert opaque, "the cut-out must contain the product"
    opaque_w = max(p[0] for p in opaque) - min(p[0] for p in opaque) + 1
    opaque_h = max(p[1] for p in opaque) - min(p[1] for p in opaque) + 1
    assert opaque_w <= bw, f"alpha is wider than the mask box ({opaque_w} > {bw})"
    assert opaque_h <= bh, f"alpha is taller than the mask box ({opaque_h} > {bh})"
    leaked = [p for p in opaque if cut_px[p][0] < 100 and cut_px[p][2] > 100]
    assert leaked == [], (
        f"the neighbour in the padding ring became opaque: {leaked[:3]}"
    )
    # The product's own colour is untouched: this path may never alter pixels.
    assert cut_px[opaque[0]] == (200, 20, 20, 255)

    # 1B: transparent pixels carry the studio background, never raw photo RGB.
    assert cut_px[0, 0][3] == 0
    assert cut_px[0, 0][:3] == (247, 247, 245)
    transparent_raw = {
        cut_px[x, y][:3]
        for y in range(cutout.size[1])
        for x in range(cutout.size[0])
        if cut_px[x, y][3] == 0
    }
    assert transparent_raw == {(247, 247, 245)}, (
        f"unmasked photo RGB survived in transparent pixels: {transparent_raw}"
    )


def test_cut_out_aligns_bbox_sized_and_full_frame_rasters():
    """Both mask layouts must map onto the mask box 1:1, never stretched.

    A live SAM mask carries a raster sized to its own bbox (``bbox size ==
    raster size``); the hand-written placeholder fixtures carry a full-frame
    raster. Neither may be resized over a padded crop — the full-frame case must
    be *cropped* to the box, which is the same pixels a live mask delivers.
    """
    from PIL import Image, ImageDraw

    from app.schemas import MaskResult

    x1, y1, x2, y2 = 40, 30, 139, 129  # 100 x 100
    bw, bh = x2 - x1 + 1, y2 - y1 + 1

    photo = Image.new("RGB", (300, 300), (255, 255, 255))
    ImageDraw.Draw(photo).rectangle([x1, y1, x2, y2], fill=(10, 200, 10))
    buf = io.BytesIO()
    photo.save(buf, format="PNG")
    photo_bytes = buf.getvalue()

    # Left half of the product only, in both layouts.
    def bbox_raster():
        raster = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(raster).rectangle([0, 0, bw // 2, bh - 1], fill=255)
        return raster

    def full_frame_raster():
        raster = Image.new("L", (300, 300), 0)
        ImageDraw.Draw(raster).rectangle([x1, y1, x1 + bw // 2, y2], fill=255)
        return raster

    def to_mask(raster):
        import base64

        b = io.BytesIO()
        raster.save(b, format="PNG")
        return MaskResult(
            concept="thing", bbox=(x1, y1, x2, y2), width=raster.size[0],
            height=raster.size[1], revision=1, score=0.9,
            mask_png_base64=base64.b64encode(b.getvalue()).decode("ascii"),
        )

    def opaque_geometry(out):
        cutout = Image.open(io.BytesIO(out)).convert("RGBA")
        px = cutout.load()
        pts = [
            (x, y)
            for y in range(cutout.size[1])
            for x in range(cutout.size[0])
            if px[x, y][3] == 255
        ]
        w = max(p[0] for p in pts) - min(p[0] for p in pts) + 1
        h = max(p[1] for p in pts) - min(p[1] for p in pts) + 1
        return w, h

    bbox_out = imaging.cut_out(photo_bytes, to_mask(bbox_raster()))
    frame_out = imaging.cut_out(photo_bytes, to_mask(full_frame_raster()))

    bbox_w, bbox_h = opaque_geometry(bbox_out)
    frame_w, frame_h = opaque_geometry(frame_out)

    # Both describe a half-width, full-height object — not a squeezed frame.
    assert bbox_h == pytest.approx(bh, abs=2)
    assert frame_h == pytest.approx(bh, abs=2)
    assert bbox_w == pytest.approx(bw / 2, abs=2)
    assert frame_w == pytest.approx(bw / 2, abs=2), (
        f"a full-frame raster was stretched instead of cropped: {frame_w} vs {bw // 2}"
    )
def test_gate_reference_builds_two_panels_on_magenta():
    """The gate reference must show the unmasked photo AND the cut-out on magenta.

    A single-panel reference lets a hole, a cable crossing, or a missing half
    pass inspection, which is exactly how two wrong edits got through before.
    """
    from PIL import Image, ImageDraw

    from app.schemas import MaskResult

    width, height = 600, 400
    photo = Image.new("RGB", (width, height), (235, 230, 220))
    ImageDraw.Draw(photo).ellipse([150, 80, 320, 300], fill=(180, 30, 30))
    buf = io.BytesIO()
    photo.save(buf, format="PNG")
    photo_bytes = buf.getvalue()

    x1, y1, x2, y2 = 150, 80, 320, 300
    bw, bh = x2 - x1 + 1, y2 - y1 + 1
    raster = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(raster).ellipse([0, 0, bw - 1, bh - 1], fill=255)
    mask_buf = io.BytesIO()
    raster.save(mask_buf, format="PNG")
    import base64

    mask = MaskResult(
        concept="phone", bbox=(x1, y1, x2, y2), width=bw, height=bh,
        revision=1, score=0.9,
        mask_png_base64=base64.b64encode(mask_buf.getvalue()).decode("ascii"),
    )

    ref = imaging.gate_reference(photo_bytes, mask, None)
    image = Image.open(io.BytesIO(ref))
    assert image.size == (1040, 520)
    assert image.mode == "RGB"
    px = image.load()
    colours = {px[x, y] for y in range(0, 520, 4) for x in range(0, 1040, 4)}
    assert (255, 0, 255) in colours, "the right panel must be solid magenta"
    assert (255, 0, 0) in colours, "the left panel must outline the product in red"


def test_gate_reference_falls_back_instead_of_raising():
    """A gate reference is an input to a verdict: it must never raise.

    Offline unit tests and old jobs call it with placeholder bytes or a mask
    that carries no raster. The fallback is the cut-out it was handed, then a
    valid tiny PNG — never an exception that would kill a job.
    """
    from PIL import Image

    from app.schemas import MaskResult

    mask = MaskResult(
        concept="x", bbox=(0, 0, 10, 10), width=10, height=10,
        revision=1, score=0.9, mask_png_base64=None,
    )
    cutout = _crop_png()

    assert imaging.gate_reference(b"img", mask, cutout) == cutout
    blank = imaging.gate_reference(b"img", mask)
    assert Image.open(io.BytesIO(blank)).size == (8, 8)


def test_ref_bytes_for_falls_back_to_the_crop(tmp_path):
    """``_ref_bytes_for`` resolves the cached reference, then the crop."""
    _setup(tmp_path)
    job_id = "job-ref-fallback"
    crops = pipeline.job_dir(job_id) / "crops"
    crops.mkdir(parents=True, exist_ok=True)

    crop = _crop_png()
    (crops / "KK-0001-crop.png").write_bytes(crop)
    assert pipeline._ref_bytes_for(job_id, "KK-0001") == crop, (
        "with no ref on disk, the crop is the reference"
    )

    ref = b"two-panel-reference"
    (crops / "KK-0001-ref.png").write_bytes(ref)
    assert pipeline._ref_bytes_for(job_id, "KK-0001") == ref, (
        "the cached ref must win once Stage C wrote it"
    )


def test_stage_c_writes_the_reference_for_every_sku(tmp_path, monkeypatch):
    """Stage C must persist ``crops/{sku}-ref.png`` for the Stage D gate."""
    _setup(tmp_path)
    job_id = "job-ref-written"

    async def fake_discover(image_bytes, captions):
        return [schemas.Concept(phrase="smartphone", scene_note="s", visible_text=None)]

    async def fake_segment(image_bytes, concept):
        return [_raster_mask(concept="smartphone", bbox=(40, 40, 160, 260), w=300, h=300)]

    async def fake_clean(crop_png, label):
        return _crop_png()

    async def fake_verify(orig, cleaned):
        return FidelityVerdict(
            same_product=True, color_match=True, logo_text_match=True, reason="ok"
        )

    async def fake_extract(clean_png, context):
        return []

    monkeypatch.setattr(pipeline.meta_client, "discover_concepts", fake_discover)
    monkeypatch.setattr(pipeline.meta_client, "segment_concept", fake_segment)
    monkeypatch.setattr(pipeline.meta_client, "clean_product_image", fake_clean)
    monkeypatch.setattr(pipeline.meta_client, "verify_fidelity", fake_verify)
    monkeypatch.setattr(pipeline.meta_client, "extract_product", fake_extract)

    from PIL import Image, ImageDraw

    photo = Image.new("RGB", (300, 300), (240, 235, 225))
    ImageDraw.Draw(photo).rectangle([40, 40, 160, 260], fill=(30, 60, 200))
    buf = io.BytesIO()
    photo.save(buf, format="PNG")

    asyncio.run(pipeline.run_pipeline(job_id, [buf.getvalue()], {"general": "", "photos": []}))

    state = pipeline._load_job_state(job_id)
    assert state.products, "the job must produce at least one product"
    for product in state.products:
        ref_path = pipeline.job_dir(job_id) / "crops" / f"{product.sku}-ref.png"
        assert ref_path.is_file(), f"{product.sku} has no gate reference"
        ref_image = Image.open(io.BytesIO(ref_path.read_bytes()))
        assert ref_image.size == (1040, 520), "the reference is the two-panel image"


# ---------------------------------------------------------------------------
# Hole-filling cap and the photo-aware contour tightening
# ---------------------------------------------------------------------------


def _mask_from_raster(raster, box):
    import base64

    from app.schemas import MaskResult

    buf = io.BytesIO()
    raster.save(buf, format="PNG")
    x1, y1, x2, y2 = box
    return MaskResult(
        concept="thing", bbox=box, width=raster.size[0], height=raster.size[1],
        revision=1, score=0.9,
        mask_png_base64=base64.b64encode(buf.getvalue()).decode("ascii"),
    )


def test_clean_mask_fills_pinholes_but_never_real_openings():
    """The hole cap is 16 px, not 5% of the component.

    The old rule filled any enclosed background region under 5% of the kept
    component — 13,471 px on a cable coil — which welded the table surface
    inside every loop (``KK-0015``, ``KK-0016``) and closed the camera cutout
    of a phone case (``KK-0012``). Only 4x4 binary noise may be filled now.
    """
    from PIL import Image, ImageDraw

    # A solid block with three enclosed holes: 4 px, 16 px, 400 px.
    raster = Image.new("L", (200, 200), 255)
    draw = ImageDraw.Draw(raster)
    draw.rectangle([40, 40, 41, 41], fill=0)      # 4 px  -> pinhole
    draw.rectangle([80, 80, 83, 83], fill=0)      # 16 px -> at the cap
    draw.rectangle([120, 120, 139, 139], fill=0)  # 400 px -> a real opening

    cleaned = imaging.clean_mask(raster)
    px = cleaned.load()

    assert px[40, 40] == 255, "a 4 px pinhole is noise and must be filled"
    assert px[130, 130] == 0, "a 400 px opening is a real hole and must survive"
    # The cap is exclusive (`size < hole_limit`), so 16 px is the first size
    # that survives. Either way the old 5% rule would have filled all three.
    assert px[81, 81] == 0, "a 16 px region is no longer treated as speckle"


def test_clean_mask_never_fills_a_cable_loop():
    """The measured defect in one assertion: an open loop in a coiled cable.

    ``KK-0016``'s component is ~208,000 px, so the old 5% rule filled 18,346 px
    of table surface across 6 loops. A 60x60 loop is 3,600 px and must survive.
    """
    from PIL import Image, ImageDraw

    raster = Image.new("L", (400, 400), 255)
    ImageDraw.Draw(raster).rectangle([170, 170, 229, 229], fill=0)

    cleaned = imaging.clean_mask(raster)
    assert cleaned.load()[200, 200] == 0, (
        "the table visible inside a cable loop must never become foreground"
    )


def test_tighten_with_photo_removes_the_stand_bracket_tab():
    """Case 1: an upright dense portrait phone loses its bottom-centre tab.

    ``KK-0003`` kept the acrylic stand bracket welded to the bottom chin while
    ``KK-0001`` and ``KK-0002`` had it carved out. The discriminator is the
    bottom-centre opacity probe, so this fixture makes that region opaque.
    """
    from PIL import Image, ImageDraw

    w, h = 200, 320  # h > 1.4 * w
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([10, 5, w - 11, h - 1], fill=255)
    photo = Image.new("RGB", (w, h), (90, 88, 92))

    tightened = imaging._tighten_with_photo(photo, mask)
    px = tightened.load()

    assert px[int(0.30 * w), int(0.95 * h)] == 0, (
        "the stand bracket tab must be cut out of the bottom chin"
    )
    assert px[int(0.50 * w), int(0.50 * h)] == 255, "the body must survive"
    assert px[int(0.50 * w), int(0.10 * h)] == 255, "the top must survive"


def test_tighten_with_photo_leaves_a_carved_stand_notch_alone():
    """Case 1's probe is what separates KK-0003 from KK-0001/KK-0002.

    When SAM already carved the notch, the bottom centre is transparent, the
    probe fails, and the bracket rule must not fire — otherwise the rule would
    eat a product that was already correct.
    """
    from PIL import Image, ImageDraw

    w, h = 200, 320
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rectangle([10, 5, w - 11, h - 1], fill=255)
    # The notch SAM already carved, exactly over the bracket rectangle.
    draw.rectangle([int(0.08 * w), int(0.905 * h), int(0.52 * w), h - 1], fill=0)
    photo = Image.new("RGB", (w, h), (90, 88, 92))

    tightened = imaging._tighten_with_photo(photo, mask)
    px = tightened.load()

    assert px[int(0.30 * w), int(0.95 * h)] == 0, "the notch stays a notch"
    assert px[int(0.70 * w), int(0.95 * h)] == 255, (
        "the right half of the bottom edge is product and must survive"
    )


def test_tighten_with_photo_clips_a_dark_lower_left_cast_shadow():
    """Case 2: a tilted wide phone loses its dark cast shadow (``KK-0005``)."""
    from PIL import Image, ImageDraw

    w, h = 400, 200  # w > 1.7 * h
    mask = Image.new("L", (w, h), 0)
    photo = Image.new("RGB", (w, h), (230, 230, 228))
    draw = ImageDraw.Draw(mask)
    pd = ImageDraw.Draw(photo)

    # A parallelogram tilted top-left -> bottom-right at KK-0005's density
    # (fg ratio 0.52), plus a dark shadow bulge welded to its lower-left edge.
    for y in range(h):
        left = 5 + y
        right = left + 190
        draw.line([(left, y), (right, y)], fill=255)
        pd.line([(left, y), (right, y)], fill=(40, 40, 45))
    for y in range(int(0.45 * h), int(0.90 * h)):
        left = 5 + y
        draw.line([(left - 40, y), (left - 1, y)], fill=255)
        pd.line([(left - 40, y), (left - 1, y)], fill=(12, 12, 14))

    tightened = imaging._tighten_with_photo(photo, mask)
    px = tightened.load()

    assert px[20, int(0.60 * h)] == 0, (
        "the dark cast shadow welded to the lower-left must be clipped"
    )
    assert px[int(0.45 * w), int(0.60 * h)] == 255, "the body must survive"


def test_tighten_with_photo_ignores_a_bright_lower_left_edge():
    """The shadow rule keys on luminance, not on tilt alone.

    ``KK-0004`` is tilted the same way as ``KK-0005`` but its lower-left edge is
    a bright product surface (mean 79.6 vs 31.0), so clipping it would cut the
    product. The rule must not fire.
    """
    from PIL import Image, ImageDraw

    w, h = 400, 200
    mask = Image.new("L", (w, h), 0)
    photo = Image.new("RGB", (w, h), (230, 230, 228))
    draw = ImageDraw.Draw(mask)
    pd = ImageDraw.Draw(photo)

    for y in range(h):
        left = int(10 + y * 1.2)
        right = int(left + 160)
        draw.line([(left, y), (right, y)], fill=255)
        pd.line([(left, y), (right, y)], fill=(200, 200, 205))
    for y in range(int(0.45 * h), int(0.90 * h)):
        left = int(10 + y * 1.2)
        draw.line([(left - 40, y), (left - 1, y)], fill=255)
        pd.line([(left - 40, y), (left - 1, y)], fill=(150, 150, 155))

    tightened = imaging._tighten_with_photo(photo, mask)
    px = tightened.load()
    assert px[int(0.60 * h), 20] == 255, (
        "a bright lower-left edge is product, not shadow, and must survive"
    )


def test_tighten_with_photo_cuts_the_camera_window_of_a_clear_case():
    """Case 3: a bright wide silhouette loses its dark camera opening."""
    from PIL import Image, ImageDraw

    w, h = 400, 180  # w > 1.8 * h
    mask = Image.new("L", (w, h), 0)
    photo = Image.new("RGB", (w, h), (232, 232, 230))
    ImageDraw.Draw(mask).rectangle([5, 5, w - 6, h - 6], fill=255)
    # A dark camera window inside the silhouette, in the upper middle band.
    ImageDraw.Draw(photo).rectangle(
        [int(0.48 * w), int(0.06 * h), int(0.68 * w), int(0.26 * h)],
        fill=(18, 18, 20),
    )

    tightened = imaging._tighten_with_photo(photo, mask)
    px = tightened.load()

    assert px[int(0.58 * w), int(0.16 * h)] == 0, (
        "the dark camera window must be cut out of the silhouette"
    )
    assert px[int(0.50 * w), int(0.60 * h)] == 255, "the case body must survive"


def test_tighten_with_photo_skips_a_solid_bright_product():
    """A white power bank is wide and bright but has no window (``KK-0014``).

    Measured on the real pack: the dark share of the camera-window rectangle is
    0.297 for the clear case ``KK-0012`` and 0.000 for the power bank, which is
    what keeps the translucent-case rule from eating a solid white product.
    """
    from PIL import Image, ImageDraw

    w, h = 400, 180
    mask = Image.new("L", (w, h), 0)
    photo = Image.new("RGB", (w, h), (238, 238, 236))
    ImageDraw.Draw(mask).rectangle([5, 5, w - 6, h - 6], fill=255)

    tightened = imaging._tighten_with_photo(photo, mask)
    assert sum(tightened.histogram()[128:]) == sum(mask.histogram()[128:]), (
        "a solid bright product with no dark opening must pass through untouched"
    )


def test_tighten_with_photo_never_erases_the_mask():
    """A pass that would empty the mask is discarded, not applied."""
    from PIL import Image, ImageDraw

    w, h = 200, 320
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([0, 0, w - 1, h - 1], fill=255)
    photo = Image.new("RGB", (w, h), (10, 10, 10))

    tightened = imaging._tighten_with_photo(photo, mask)
    assert tightened.getbbox() is not None, "the product may never be erased"


def test_cut_out_applies_the_tightening_and_gate_reference_matches_it():
    """``cut_out`` and ``gate_reference`` must share one tightened mask.

    The right panel of ``-ref.png`` is the ground truth the Stage D gate
    inspects; if it carried a different alpha from the shipped crop, the gate
    would judge an image the seller never sees.
    """
    from PIL import Image, ImageDraw

    w, h = 220, 340
    photo = Image.new("RGB", (w, h), (236, 234, 230))
    ImageDraw.Draw(photo).rectangle([20, 10, w - 21, h - 1], fill=(92, 90, 94))
    buf = io.BytesIO()
    photo.save(buf, format="PNG")

    raster = Image.new("L", (w, h), 0)
    ImageDraw.Draw(raster).rectangle([20, 10, w - 21, h - 1], fill=255)
    mask = _mask_from_raster(raster, (0, 0, w - 1, h - 1))

    crop = Image.open(io.BytesIO(imaging.cut_out(buf.getvalue(), mask))).convert("RGBA")
    alpha = crop.split()[-1]
    assert alpha.getpixel((int(0.30 * crop.width), crop.height - 3)) == 0, (
        "cut_out must apply the stand-bracket pass, not just clean_mask"
    )

    ref = Image.open(io.BytesIO(imaging.gate_reference(buf.getvalue(), mask, None)))
    assert ref.size == (1040, 520)


def test_tighten_is_skipped_below_the_minimum_side():
    """Small crops keep the old behaviour: geometry ratios are noise there."""
    from PIL import Image, ImageDraw

    w, h = 60, 100  # below TIGHTEN_MIN_SIDE
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([2, 2, w - 3, h - 1], fill=255)
    photo = Image.new("RGB", (w, h), (90, 88, 92))

    out = imaging.cut_out(
        _png_bytes(photo), _mask_from_raster(mask, (0, 0, w - 1, h - 1))
    )
    crop = Image.open(io.BytesIO(out)).convert("RGBA")
    alpha = crop.split()[-1]
    assert alpha.getpixel((int(0.30 * crop.width), crop.height - 2)) == 255, (
        "a 60x100 crop must not be tightened"
    )


def _png_bytes(image):
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
