"""Offline adapter tests for ``app.meta_client`` (mock mode only).

Every call goes through the adapter; no network is opened. These tests pin
determinism (same input bytes -> same output), schema validity, and reachability
of both fidelity verdicts.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest

from app import meta_client, schemas
from app.meta_client import settings


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
