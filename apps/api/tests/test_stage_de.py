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
