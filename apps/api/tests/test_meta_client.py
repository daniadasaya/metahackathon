"""Offline adapter tests for ``app.meta_client`` (mock mode only).

Every call goes through the adapter; no network is opened. These tests pin
determinism (same input bytes -> same output), schema validity, and reachability
of both fidelity verdicts.
"""

from __future__ import annotations

import asyncio
import builtins
import socket
import sys
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
