"""Offline adapter tests for ``app.meta_client`` (mock mode only).

Every call goes through the adapter; no network is opened. These tests pin
determinism (same input bytes -> same output), schema validity, and reachability
of both fidelity verdicts.
"""

from __future__ import annotations

import asyncio
import builtins
import copy
import json
import socket
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from app import meta_client, schemas


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.settings import Settings

    monkeypatch.setattr(meta_client, "settings", Settings(model_api_key=None, base_url="http://127.0.0.1:8180", meta_mock=True))


@pytest.fixture
def _block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket as _socket

    real_socket = _socket.socket

    def _guarded_socket(family=_socket.AF_INET, *args: Any, **kwargs: Any) -> Any:
        if family in (_socket.AF_INET, _socket.AF_INET6):
            raise RuntimeError("network access blocked in offline test")
        return real_socket(family, *args, **kwargs)

    def _blocked(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("network access blocked in offline test")

    monkeypatch.setattr(socket, "socket", _guarded_socket)
    monkeypatch.setattr(socket, "create_connection", _blocked)


def test_health_probe_schema_and_determinism(_block_network: None) -> None:
    import asyncio

    a = asyncio.run(meta_client.health_probe())
    b = asyncio.run(meta_client.health_probe())
    assert isinstance(a, schemas.HealthResponse)
    assert a == b
    assert a.ok is True


def test_discover_concepts_schema_and_determinism(_block_network: None) -> None:
    import asyncio

    img = b"photo-bytes-for-concepts" * 10
    a = asyncio.run(meta_client.discover_concepts(img, '{"general":"hi","photos":[]}'))
    b = asyncio.run(meta_client.discover_concepts(img, "ignored"))
    assert isinstance(a, list)
    assert all(isinstance(c, schemas.Concept) for c in a)
    assert len(a) > 0
    # determinism on image bytes (captions ignored for slot)
    assert [c.model_dump() for c in a] == [c.model_dump() for c in b]


def test_segment_concept_schema_and_determinism(_block_network: None) -> None:
    import asyncio

    img = b"photo-bytes-for-segment" * 10
    concepts = asyncio.run(meta_client.discover_concepts(img, ""))
    assert concepts
    phrase = concepts[0].phrase
    a = asyncio.run(meta_client.segment_concept(img, phrase))
    b = asyncio.run(meta_client.segment_concept(img, phrase))
    assert isinstance(a, list)
    assert all(isinstance(m, schemas.MaskResult) for m in a)
    assert [m.model_dump() for m in a] == [m.model_dump() for m in b]


def test_clean_product_image_determinism(_block_network: None) -> None:
    import asyncio

    crop = b"crop-bytes" * 5
    a = asyncio.run(meta_client.clean_product_image(crop, "label"))
    b = asyncio.run(meta_client.clean_product_image(crop, "label"))
    assert isinstance(a, (bytes, bytearray))
    assert a == b
    assert a[:8] == b"\x89PNG\r\n\x1a\n"


def test_verify_fidelity_both_verdicts(_block_network: None) -> None:
    import asyncio
    import hashlib

    original = b"original-bytes" * 5
    seen: set[bool] = set()
    # Vary the cleaned bytes to flip the parity selector.
    for i in range(20):
        cleaned = (b"cleaned-bytes-" + str(i).encode()) * 5
        verdict = asyncio.run(meta_client.verify_fidelity(original, cleaned))
        assert isinstance(verdict, schemas.FidelityVerdict)
        # same_product is the gate's pass/fail summary proxy; collect all-three.
        passed = verdict.same_product and verdict.color_match and verdict.logo_text_match
        seen.add(passed)
        if len(seen) == 2:
            break
    assert seen == {True, False}, "both fidelity verdicts must be reachable"


def test_extract_product_schema_and_determinism(_block_network: None) -> None:
    import asyncio

    # clean png bytes route to a slot; use a real png-ish header.
    clean = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    ctx = schemas.ExtractContext(seller_captions='{"general":"hi","photos":[]}', concept="smartphone", visible_text=None)
    a = asyncio.run(meta_client.extract_product(clean, ctx))
    b = asyncio.run(meta_client.extract_product(clean, ctx))
    assert isinstance(a, list)
    assert all(isinstance(e, schemas.ProductEntry) for e in a)
    assert len(a) > 0
    assert [e.model_dump() for e in a] == [e.model_dump() for e in b]


def test_segment_concept_no_match_returns_empty(_block_network: None) -> None:
    import asyncio

    img = b"photo-bytes-for-segment" * 10
    result = asyncio.run(meta_client.segment_concept(img, "nonexistent-concept-xyz"))
    assert result == []


# ---------------------------------------------------------------------------
# Shared live-client lifecycle (regression pins)
#
# `_live_client()` builds one process-level AsyncOpenAI and every caller
# reuses it; `aclose_live_client()` drops it. These tests prove the pin
# offline: `openai.AsyncOpenAI` is faked on the `openai` module (the real
# import happens inside the function) and the key is forced via a patched
# `settings` — no network, no real key. Each test resets the module global
# so suite ordering cannot leak state.
# ---------------------------------------------------------------------------


class _FakeAsyncOpenAI:
    """Stand-in for ``openai.AsyncOpenAI`` recording construction kwargs."""

    constructions: int = 0
    close_error: Exception | None = None

    def __init__(self, base_url: str, api_key: str) -> None:
        _FakeAsyncOpenAI.constructions += 1
        self.base_url = base_url
        self.api_key = api_key
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        error = _FakeAsyncOpenAI.close_error
        if error is not None:
            raise error


class _FakeOpenAIPackage:
    """Stands in for the ``openai`` module, exporting the fake client."""

    AsyncOpenAI = _FakeAsyncOpenAI


@pytest.fixture
def _live_lifecycle(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Force live-capable settings, fake the openai package, reset the global.

    Saves the prior ``_shared_live_client`` and restores it in ``finally``, so
    suite ordering cannot leak state.
    """
    import app.settings as settings_module

    monkeypatch.setattr(
        meta_client,
        "settings",
        settings_module.Settings(
            model_api_key="test-key-offline", base_url="http://127.0.0.1:8180", meta_mock=False
        ),
    )
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIPackage())
    prior = meta_client._shared_live_client
    meta_client._shared_live_client = None
    _FakeAsyncOpenAI.constructions = 0
    _FakeAsyncOpenAI.close_error = None
    try:
        yield None
    finally:
        meta_client._shared_live_client = prior
        _FakeAsyncOpenAI.close_error = None
        _FakeAsyncOpenAI.constructions = 0


def test_live_client_returns_same_instance(_live_lifecycle: None) -> None:
    first = meta_client._live_client()
    second = meta_client._live_client()
    assert first is second
    assert isinstance(first, _FakeAsyncOpenAI)
    assert first.base_url == meta_client.META_BASE_URL
    assert first.api_key == "test-key-offline"
    assert _FakeAsyncOpenAI.constructions == 1, (
        "the second _live_client() call must reuse the shared client, not "
        "construct another — per-call construction leaked transports"
    )


def test_live_client_without_key_raises_and_caches_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.settings as settings_module

    monkeypatch.setattr(
        meta_client,
        "settings",
        settings_module.Settings(
            model_api_key=None, base_url="http://127.0.0.1:8180", meta_mock=True
        ),
    )
    # Fake the package too, so the key branch is what fires even on a host
    # where the real openai is not installed.
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIPackage())
    prior = meta_client._shared_live_client
    meta_client._shared_live_client = None
    try:
        with pytest.raises(meta_client.MetaLiveUnavailable, match="MODEL_API_KEY"):
            meta_client._live_client()
        assert meta_client._shared_live_client is None
    finally:
        meta_client._shared_live_client = prior


def test_live_client_without_openai_package_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.settings as settings_module

    monkeypatch.setattr(
        meta_client,
        "settings",
        settings_module.Settings(
            model_api_key="test-key-offline", base_url="http://127.0.0.1:8180", meta_mock=False
        ),
    )

    real_import = builtins.__import__

    def _missing_openai(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openai":
            raise ImportError("openai package faked as missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _missing_openai)

    prior = meta_client._shared_live_client
    meta_client._shared_live_client = None
    try:
        with pytest.raises(meta_client.MetaLiveUnavailable, match="openai"):
            meta_client._live_client()
        assert meta_client._shared_live_client is None
    finally:
        meta_client._shared_live_client = prior


def test_aclose_drops_and_next_call_builds_new(
    _live_lifecycle: None,
) -> None:
    first = meta_client._live_client()
    assert _FakeAsyncOpenAI.constructions == 1
    asyncio.run(meta_client.aclose_live_client())
    assert first.close_calls == 1
    assert meta_client._shared_live_client is None
    second = meta_client._live_client()
    assert second is not first
    assert _FakeAsyncOpenAI.constructions == 2


def test_aclose_is_idempotent_when_nothing_shared(
    _live_lifecycle: None,
) -> None:
    asyncio.run(meta_client.aclose_live_client())  # no raise
    asyncio.run(meta_client.aclose_live_client())  # still no raise
    assert meta_client._shared_live_client is None


def test_aclose_close_failure_propagates(
    _live_lifecycle: None,
) -> None:
    client = meta_client._live_client()
    _FakeAsyncOpenAI.close_error = RuntimeError("transport already destroyed")
    with pytest.raises(meta_client.MetaLiveUnavailable, match="closing the shared live client"):
        asyncio.run(meta_client.aclose_live_client())
    assert client.close_calls == 1
    # dropped even though close failed — the error is not retried or swallowed
    assert meta_client._shared_live_client is None


# ---------------------------------------------------------------------------
# Stage E category taxonomy: the wire enum, the normalizer, and
# the live parse path. Everything here is offline: the live client is faked and
# a real socket fails the test.
# ---------------------------------------------------------------------------


def test_stage_e_schema_carries_the_taxonomy_enum() -> None:
    schema = meta_client._stage_e_schema()
    enum = schema["properties"]["category"]["enum"]

    assert enum == list(meta_client.PRODUCT_CATEGORIES)
    assert len(enum) == 14
    # Spot-check the canonical spelling, independent of the constant itself:
    # an English or slugified taxonomy would still satisfy the equality above.
    assert "Handphone & Tablet" in enum
    assert "Aksesoris HP" in enum


def test_stage_e_schema_never_mutates_the_pydantic_model() -> None:
    schema = meta_client._stage_e_schema()

    # The regression pin: the enum lives in the outbound wire copy only. A
    # future ``ProductEntry.model_json_schema()["properties"]["category"]
    # ["enum"] = ...`` would push the enum into apps/api/openapi.json and break
    # the openapi drift test.
    model_schema = schemas.ProductEntry.model_json_schema()
    assert "enum" not in model_schema["properties"]["category"]

    # And the copy differs from the frozen model in exactly that one place.
    expected = copy.deepcopy(model_schema)
    expected["properties"]["category"]["enum"] = list(meta_client.PRODUCT_CATEGORIES)
    assert schema == expected

    # Two calls hand out independent dicts: mutating one cannot poison the other.
    other = meta_client._stage_e_schema()
    assert other["properties"]["category"] is not schema["properties"]["category"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Live-observed Stage E answers before the taxonomy landed.
        ("smartphone", "Handphone & Tablet"),
        ("smartphone box", "Handphone & Tablet"),
        ("phone case", "Aksesoris HP"),
        ("charging case", "Audio"),
        ("accessories", "Aksesoris HP"),
        # Indonesian and English synonyms.
        ("casing", "Aksesoris HP"),
        ("tempered glass", "Aksesoris HP"),
        ("charging cable", "Aksesoris HP"),
        ("power bank", "Aksesoris HP"),
        ("Aksesori Ponsel", "Aksesoris HP"),
        ("", "Lainnya"),
        ("t-shirt", "Pakaian"),
        ("sepatu", "Sepatu"),
        ("tas", "Tas"),
        ("mie instan", "Makanan & Minuman"),
        ("skincare", "Kecantikan"),
        ("tv", "Elektronik Lainnya"),
    ],
)
def test_normalize_category_maps_known_values(raw: str, expected: str) -> None:
    assert meta_client._normalize_category(raw, "") == expected


def test_normalize_category_ordering_traps() -> None:
    # The accessory rows sit above the phone row: "case" must win over "phone",
    # or every phone case in the catalog would be sold as a smartphone.
    assert meta_client._normalize_category("phone case", "") == "Aksesoris HP"
    # "aksesori" must win over "ponsel" for the same reason.
    assert meta_client._normalize_category("Aksesori Ponsel", "") == "Aksesoris HP"
    # And the charging-case row sits above both: a TWS case is audio, not a
    # phone accessory and not a phone.
    assert meta_client._normalize_category("charging case", "") == "Audio"


@pytest.mark.parametrize("canonical", meta_client.PRODUCT_CATEGORIES)
def test_normalize_category_is_idempotent_on_every_taxonomy_entry(
    canonical: str,
) -> None:
    assert meta_client._normalize_category(canonical, "") == canonical
    # A canonical value must not be re-read as a concept either.
    assert meta_client._normalize_category(canonical, "gizmo") == canonical


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "gizmo",
        "   ",
        "???",
        "handphone & tablet",
        "AKSESORIS HP",
        "smartphone box",
        "an unmapped english concept",
        "HP",
        "tv",
    ],
)
def test_normalize_category_is_closed_over_the_taxonomy(raw: str) -> None:
    assert meta_client._normalize_category(raw, "") in meta_client.PRODUCT_CATEGORIES
    assert meta_client._normalize_category(raw, "gizmo") in meta_client.PRODUCT_CATEGORIES


def test_normalize_category_falls_back_to_the_stage_a_concept() -> None:
    assert meta_client._normalize_category("gizmo", "smartphone") == "Handphone & Tablet"
    assert meta_client._normalize_category("", "sepatu") == "Sepatu"
    # An unmapped pair has nowhere left to go.
    assert meta_client._normalize_category("gizmo", "gizmo") == "Lainnya"


class _RecordingResponses:
    """Stands in for ``client.responses``: records kwargs, replays canned text."""

    def __init__(self, answers: list[str]) -> None:
        self.answers = answers
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        index = min(len(self.calls), len(self.answers)) - 1
        return _fake_response(self.answers[index])


class _RecordingClient:
    """Minimal stand-in for the shared ``AsyncOpenAI`` client."""

    def __init__(self, answers: list[str]) -> None:
        self.responses = _RecordingResponses(answers)


def _fake_response(json_text: str) -> Any:
    """A non-streaming Responses payload shaped for ``_output_text_of``."""
    return types.SimpleNamespace(
        status="completed",
        output=[
            types.SimpleNamespace(
                type="message",
                phase=None,
                content=[types.SimpleNamespace(type="output_text", text=json_text)],
            )
        ],
    )


def _entry_payload(**overrides: Any) -> dict[str, Any]:
    """One valid ``ProductEntry`` payload, overridable field by field."""
    payload: dict[str, Any] = {
        "name": "Casing Pelindung iPhone 14 Pro",
        "description_id": "Casing pelindung transparan untuk iPhone 14 Pro.",
        "description_en": "Transparent protective case for iPhone 14 Pro.",
        "category": "phone case",
        "brand": None,
        "condition": "new",
        "attributes": {"color": "clear", "size": None, "variant": None},
        "price_hint_idr": 45000,
        "price_suggestion_idr": None,
        "confidence": "high",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def _live_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force the live Stage E branch with a fake key and no real client."""
    from app.settings import Settings

    monkeypatch.setattr(
        meta_client,
        "settings",
        Settings(
            model_api_key="test-key-offline",
            base_url="http://127.0.0.1:8180",
            meta_mock=False,
        ),
    )


def test_live_extract_product_sends_the_enum_and_normalises_the_answer(
    _live_extract: None, _block_network: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _RecordingClient([json.dumps(_entry_payload(category="smartphone"))])
    monkeypatch.setattr(meta_client, "_live_client", lambda: client)
    ctx = schemas.ExtractContext(
        seller_captions='{"general":"HP second, casing 45rb","photos":[]}',
        concept="smartphone",
        visible_text="Rp 1.500.000",
    )

    entries = asyncio.run(
        meta_client.extract_product(b"\x89PNG\r\n\x1a\n" + b"x" * 64, ctx)
    )

    assert len(client.responses.calls) == 1
    wire_format = client.responses.calls[0]["text"]["format"]
    assert wire_format["type"] == "json_schema"
    assert wire_format["name"] == "ProductEntry"
    assert wire_format["schema"]["properties"]["category"]["enum"] == list(
        meta_client.PRODUCT_CATEGORIES
    )

    assert len(entries) == 1
    assert entries[0].category == "Handphone & Tablet"
    # The normalizer rewrites the category and nothing else.
    assert entries[0].name == "Casing Pelindung iPhone 14 Pro"
    assert entries[0].price_hint_idr == 45000


def test_live_extract_product_repair_path_also_normalises(
    _live_extract: None, _block_network: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalid = _entry_payload()
    del invalid["category"]  # required field missing -> repair retry
    client = _RecordingClient(
        [
            json.dumps(invalid),
            # Unmapped raw value: only the Stage A concept can place it.
            json.dumps(_entry_payload(category="gizmo")),
        ]
    )
    monkeypatch.setattr(meta_client, "_live_client", lambda: client)
    ctx = schemas.ExtractContext(
        seller_captions="casing 45rb", concept="phone case", visible_text=None
    )

    entries = asyncio.run(
        meta_client.extract_product(b"\x89PNG\r\n\x1a\n" + b"x" * 64, ctx)
    )

    assert len(client.responses.calls) == 2, "exactly one repair retry"
    assert len(entries) == 1
    assert entries[0].category == "Aksesoris HP"


# ---------------------------------------------------------------------------
# Pack C mock slots: the two-call Stage B sequence, offline.
#
# `pipeline.py` sends a bare concept phrase and, only when that answers zero
# masks, one retry with `foreground {phrase}`. Pack C's restored mask fixtures
# hold retry records, so this pins the sequence the pipeline actually performs:
# which phrases hit on the first call, which are empty and recover on the
# retry, and which are empty upstream (recorded as empty, not patched).
# ---------------------------------------------------------------------------


def _bytes_for_slot(pack: str, photo: int) -> bytes:
    """Image bytes whose deterministic mock slot is exactly ``(pack, photo)``.

    ``_locate`` hashes the bytes, so a slot is addressable only by search: one
    candidate in six lands on a given slot. 10 000 attempts is far past the
    point where a miss means anything but a broken locator.
    """
    for attempt in range(10_000):
        candidate = f"mock-slot:{pack}:{photo}:{attempt}".encode("utf-8")
        if meta_client._locate(candidate) == (pack, photo):
            return candidate
    raise AssertionError(
        f"no byte string maps to mock slot ({pack!r}, {photo}) in 10 000 attempts"
    )


def test_pack_c_two_call_mask_sequence(_block_network: None) -> None:
    import asyncio

    photo1 = _bytes_for_slot("c", 1)
    photo2 = _bytes_for_slot("c", 2)

    def _segment(image: bytes, phrase: str) -> list[schemas.MaskResult]:
        masks = asyncio.run(meta_client.segment_concept(image, phrase))
        for mask in masks:
            assert isinstance(mask, schemas.MaskResult)
            assert isinstance(mask.mask_png_base64, str) and mask.mask_png_base64, (
                f"{phrase!r} returned a mask with no raster; mock mode must serve "
                "the recorded mask_png_base64"
            )
        return masks

    def _labels(masks: list[schemas.MaskResult]) -> set[str]:
        return {mask.concept for mask in masks}

    # --- Photo 1: one concept hits bare, one needs the retry. ---
    snack = _segment(photo1, "snack bag")
    assert len(snack) == 16, f"bare 'snack bag' returned {len(snack)} masks, expected 16"
    assert _labels(snack) == {"snack bag"}

    assert _segment(photo1, "wafer packet") == [], "bare 'wafer packet' must answer zero"
    wafer = _segment(photo1, "foreground wafer packet")
    assert len(wafer) == 6, f"retry returned {len(wafer)} masks, expected 6"
    assert _labels(wafer) == {"foreground wafer packet"}

    # --- Photo 2: three bare hits, four retry hits, one upstream empty. ---
    for phrase, expected in (
        ("seaweed snack bag", 10),
        ("potato chip bag", 4),
        ("cassava chip bag", 5),
    ):
        masks = _segment(photo2, phrase)
        assert len(masks) == expected, (
            f"bare {phrase!r} returned {len(masks)} masks, expected {expected}"
        )
        assert _labels(masks) == {phrase}

    for phrase, expected in (
        ("milk carton", 2),
        ("biscuit pack", 1),
        ("wafer pack", 10),
        ("peanut snack bag", 5),
    ):
        assert _segment(photo2, phrase) == [], f"bare {phrase!r} must answer zero"
        retry = _segment(photo2, f"foreground {phrase}")
        assert len(retry) == expected, (
            f"retry for {phrase!r} returned {len(retry)} masks, expected {expected}"
        )
        assert _labels(retry) == {f"foreground {phrase}"}

    # Upstream returned nothing for this phrase on either call; the mock keeps
    # it empty rather than inventing a record the live capture never had.
    assert _segment(photo2, "chocolate bar") == []
    assert _segment(photo2, "foreground chocolate bar") == []
