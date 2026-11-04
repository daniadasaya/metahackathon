"""Stage B offload: same answers, and the event loop keeps turning (offline).

A live ``segment_concept`` blocked the event loop for 5.77 s — 5.69 s of it the
SAM streaming parse, with each mask decoding in 1.6-2.1 s — so ``/api/health``
was frozen for the whole of Stage B. The parse now runs in a worker thread
(``_parse_sam_output`` wraps ``_parse_sam_output_inner`` via
``asyncio.to_thread``), each mask record is decoded by the synchronous
``_mask_record_to_result`` on a worker thread, and the data-URL encodes are
offloaded too.

Two things are pinned here, both measured rather than asserted by inspection:

* **Same answer.** The offloaded path must return exactly what the inline loop
  it replaced returned: the ``translate`` table must equal the per-pixel
  ``255 if b else 0`` generator, ``bytes.count(1)`` must equal
  ``sum(1 for b in raster if b)``, and a decoded record must produce an equal
  ``MaskResult`` (or the same ``None`` rejection).

* **Loop stays alive.** A 20 ms heartbeat records the longest gap between
  wake-ups while a large SAM grammar line is parsed and while the live segment
  path runs. A parse left on the loop shows a gap equal to the parse; an
  offloaded one does not.

The grammar line is synthesized in the format the recorded fixtures were
captured in — ``<0f>`` header, ``<|box;x1,y1,x2,y2,w,h|>`` plus
``<|mask;x=0;y=0;data=H,W,!payload;c=...|>`` — and encoded with the package's
own range encoder, so ``decode_mask_to_raster`` accepts it as canonical.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import random
import threading
import time
import types
from collections.abc import Awaitable, Callable
from typing import Any

import meta_sam_parser
import pytest
from PIL import Image

from app import meta_client

#: A loop that turns normally shows gaps of a few milliseconds. A synchronous
#: parse of the mask below shows a gap of seconds, so anything under this is
#: "the loop kept turning" with a wide margin.
MAX_LOOP_GAP_SECONDS = 0.15

#: The heartbeat's own period, per the offload contract.
HEARTBEAT_SECONDS = 0.02

#: Half the live mask in each dimension (2400x1600 -> 3.84 MP, 1.6-2.1 s to
#: decode). 0.96 MP keeps the parse a multi-second CPU burn — enough that a
#: synchronous parse is unmistakable — without making the offline suite slow.
BIG_MASK = (1200, 800)

#: One instance of each Stage B filter, so a decoded record and the loop it
#: replaced are compared on both sides of every branch.
PASS_SCORE = 0.93
LOW_SCORE = 0.55


# ---------------------------------------------------------------------------
# Oracles: the implementations the offload replaced, verbatim
# ---------------------------------------------------------------------------


def _old_raster_to_mode1_png(raster: bytes, width: int, height: int) -> bytes:
    """Pre-offload ``_raster_to_mode1_png``: a per-pixel Python generator."""
    lum = Image.frombytes(
        "L", (width, height), bytes(255 if b else 0 for b in raster)
    )
    img = lum.convert("1")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _old_fg_count(raster: bytes) -> int:
    """Pre-offload foreground count: a Python-level scan of the raster."""
    return sum(1 for b in raster if b)


def _old_mask_record_to_result(record: Any, wanted: str) -> Any:
    """The inline per-record loop ``_mask_record_to_result`` replaced."""
    from meta_sam_parser import decode_mask_to_raster

    raster = decode_mask_to_raster(record.mask)
    score = float(record.confidence if record.confidence is not None else 0.0)
    if score < 0.70:
        return None
    fg_pixels = _old_fg_count(raster)
    mask_area = record.mask.width * record.mask.height
    if mask_area <= 0 or (fg_pixels / mask_area) < 0.38:
        return None
    png = _old_raster_to_mode1_png(raster, record.mask.width, record.mask.height)
    return meta_client.MaskResult(
        concept=wanted,
        bbox=(
            int(record.bounds.left),
            int(record.bounds.top),
            int(record.bounds.right) - 1,
            int(record.bounds.bottom) - 1,
        ),
        width=record.mask.width,
        height=record.mask.height,
        revision=record.revision,
        score=float(score),
        mask_png_base64=base64.b64encode(png).decode("ascii"),
    )


# ---------------------------------------------------------------------------
# Grammar-line synthesis (the shape the recorded fixtures were captured in)
# ---------------------------------------------------------------------------


def _canonical_payload(raster: bytes, width: int, height: int) -> str:
    """Encode a 0/1 raster with the parser's own range encoder.

    ``meta_sam_parser`` exposes no public encoder, and ``one_bit`` payloads are
    validated by re-encoding on decode, so the package's encoder is the only
    way to produce a line the parser accepts as canonical.
    """
    from meta_sam_parser import _mask_codec

    return _mask_codec._encode_raster(raster, width, height)


def _grammar_line(
    raster: bytes,
    width: int,
    height: int,
    *,
    confidence: float = PASS_SCORE,
    object_id: int = 1,
) -> str:
    """One SAM output line: frame header, one record, box then mask token."""
    payload = _canonical_payload(raster, width, height)
    return (
        f"<0f>{object_id}"
        f"<|box;x1=0;y1=0;x2={width - 1};y2={height - 1};w={width};h={height}|>"
        f"<|mask;x=0;y=0;data={height},{width},{payload};c={confidence}|>"
    )


def _bits(count: int, *, seed: int = 7, density: int = 2) -> bytes:
    """A deterministic 0/1 raster: one pixel in every ``density`` is set."""
    rng = random.Random(seed)
    return bytes(1 if rng.randrange(density) == 0 else 0 for _ in range(count))


@functools.lru_cache(maxsize=1)
def _big_line() -> str:
    """The large mask used by both liveness checks (built once per process)."""
    width, height = BIG_MASK
    return _grammar_line(_bits(width * height, seed=11), width, height)


# ---------------------------------------------------------------------------
# Heartbeat: how long the loop is starved, measured
# ---------------------------------------------------------------------------


async def _measure(
    work: Callable[[], Awaitable[Any]],
) -> tuple[float, float, Any]:
    """Run ``work`` under a heartbeat; return (worst gap, elapsed, result)."""
    stop = asyncio.Event()

    async def heartbeat() -> float:
        worst = 0.0
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            now = time.monotonic()
            worst = max(worst, now - last)
            last = now
        return worst

    beat = asyncio.create_task(heartbeat())
    # Let the pulse tick before the work starts: a heartbeat that had not been
    # scheduled yet would report a zero gap for work that blocked the loop
    # before its first wake-up, and the check would pass vacuously.
    await asyncio.sleep(HEARTBEAT_SECONDS * 2)
    started = time.monotonic()
    try:
        value = await work()
    finally:
        elapsed = time.monotonic() - started
        stop.set()
        worst_gap = await beat
    return worst_gap, elapsed, value


# ---------------------------------------------------------------------------
# (a) The offload returns byte-identical output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("width", "height", "raster"),
    [
        (1, 1, b"\x00"),
        (1, 1, b"\x01"),
        (32, 32, bytes(32 * 32)),
        (32, 32, bytes([1]) * (32 * 32)),
        (64, 48, _bits(64 * 48, seed=1)),
        (200, 150, _bits(200 * 150, seed=2)),
        (400, 300, _bits(400 * 300, seed=3)),
    ],
    ids=["1x1-zero", "1x1-one", "all-zero", "all-one", "random-64", "random-200",
         "large-400"],
)
def test_mode1_png_matches_the_generator_oracle(
    width: int, height: int, raster: bytes
) -> None:
    assert len(raster) == width * height
    new = meta_client._raster_to_mode1_png(raster, width, height)
    old = _old_raster_to_mode1_png(raster, width, height)
    assert new == old
    # The bytes are a real mode-1 PNG of the same bits, not merely equal to
    # the oracle because both are broken.
    img = Image.open(io.BytesIO(new))
    assert img.mode == "1" and img.size == (width, height)
    assert img.convert("L").tobytes() == bytes(255 if b else 0 for b in raster)


def test_translate_table_covers_the_whole_byte_alphabet() -> None:
    """``bytes.translate`` must agree with ``255 if b else 0`` for every byte."""
    raster = bytes(range(256)) * 4
    width, height = 32, 32
    assert meta_client._raster_to_mode1_png(raster, width, height) == (
        _old_raster_to_mode1_png(raster, width, height)
    )


def test_raster_length_mismatch_still_raises() -> None:
    with pytest.raises(meta_client.MetaLiveUnavailable) as caught:
        meta_client._raster_to_mode1_png(b"\x00\x01\x01", 2, 2)
    assert "raster length 3 != 2x2" in str(caught.value)


@pytest.mark.parametrize(
    ("raster", "label"),
    [
        (b"", "empty"),
        (bytes(1024), "all-zero"),
        (bytes([1]) * 1024, "all-one"),
        (b"\x00", "single-zero"),
        (b"\x01", "single-one"),
        (_bits(1000, seed=4), "random-1000"),
        (_bits(1_000_000, seed=5), "large-1M"),
    ],
    ids=["empty", "all-zero", "all-one", "single-zero", "single-one", "random-1000",
         "large-1M"],
)
def test_fg_count_matches_the_python_scan(raster: bytes, label: str) -> None:
    assert raster.count(1) == _old_fg_count(raster)


def test_fg_count_and_png_agree_on_a_one_bit_raster() -> None:
    """A raster of only 0/1 is where the two implementations must coincide."""
    raster = _bits(5000, seed=6)
    assert set(raster) <= {0, 1}
    assert raster.count(1) == _old_fg_count(raster)
    assert meta_client._raster_to_mode1_png(raster, 100, 50) == (
        _old_raster_to_mode1_png(raster, 100, 50)
    )


@pytest.mark.parametrize(
    ("raster", "confidence", "label"),
    [
        (_bits(60 * 40, seed=8), PASS_SCORE, "kept"),
        (_bits(60 * 40, seed=8), LOW_SCORE, "low-score-dropped"),
        (_bits(60 * 40, seed=8), 0.70, "score-exactly-0.70-kept"),
        (_bits(60 * 40, seed=9, density=50), PASS_SCORE, "low-coverage-dropped"),
        (bytes([1]) * 60 * 40, PASS_SCORE, "full-coverage-kept"),
    ],
    ids=[
        "kept",
        "low-score-dropped",
        "score-exactly-0.70-kept",
        "low-coverage-dropped",
        "full-coverage-kept",
    ],
)
def test_mask_record_matches_the_inline_loop(
    raster: bytes, confidence: float, label: str
) -> None:
    line = _grammar_line(raster, 60, 40, confidence=confidence)
    records = asyncio.run(meta_client._parse_sam_output(line))
    assert len(records) == 1

    new = meta_client._mask_record_to_result(records[0], "smartphone")
    old = _old_mask_record_to_result(records[0], "smartphone")
    if old is None:
        assert new is None
    else:
        assert new is not None
        assert new.model_dump() == old.model_dump()
        assert new.concept == "smartphone"
        assert new.bbox == (0, 0, 59, 39)
        assert new.width == 60 and new.height == 40
        assert new.revision == 1
        assert new.mask_png_base64 == old.mask_png_base64


# ---------------------------------------------------------------------------
# (b) The loop keeps turning while the parse burns CPU
# ---------------------------------------------------------------------------


def test_parse_sam_output_does_not_block_the_event_loop() -> None:
    line = _big_line()

    async def work() -> list[Any]:
        return await meta_client._parse_sam_output(line)

    worst_gap, elapsed, records = asyncio.run(_measure(work))

    assert len(records) == 1
    assert records[0].mask.width == BIG_MASK[0]
    # The measurement means something only if the parse really was expensive.
    assert elapsed > 1.0, f"the synthesized parse took only {elapsed:.3f}s"
    assert worst_gap < MAX_LOOP_GAP_SECONDS, (
        f"the event loop was starved for {worst_gap:.3f}s by a {elapsed:.3f}s "
        f"SAM parse"
    )


def _fake_response(line: str) -> types.SimpleNamespace:
    """The non-streaming ``/v1/responses`` shape ``_output_text_of`` reads."""
    return types.SimpleNamespace(
        status="completed",
        output=[
            types.SimpleNamespace(
                type="message",
                phase=None,
                content=[types.SimpleNamespace(type="output_text", text=line)],
            )
        ],
    )


class _FakeResponses:
    def __init__(self, line: str) -> None:
        self._line = line
        self.calls = 0

    async def create(self, **kwargs: Any) -> types.SimpleNamespace:
        self.calls += 1
        return _fake_response(self._line)


class _FakeClient:
    def __init__(self, line: str) -> None:
        self.responses = _FakeResponses(line)


def test_live_segment_path_does_not_block_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = _big_line()
    client = _FakeClient(line)
    monkeypatch.setattr(meta_client, "_live_client", lambda: client)

    async def work() -> list[Any]:
        return await meta_client._live_segment_concept(
            b"\x89PNG\r\n\x1a\n" + b"0" * 64, "smartphone"
        )

    worst_gap, elapsed, results = asyncio.run(_measure(work))

    assert client.responses.calls == 1
    assert len(results) == 1
    result = results[0]
    assert result.concept == "smartphone"
    assert result.bbox == (0, 0, BIG_MASK[0] - 1, BIG_MASK[1] - 1)
    assert result.score == pytest.approx(PASS_SCORE)
    assert result.mask_png_base64
    assert elapsed > 1.0, f"the synthesized segment took only {elapsed:.3f}s"
    assert worst_gap < MAX_LOOP_GAP_SECONDS, (
        f"the event loop was starved for {worst_gap:.3f}s by a {elapsed:.3f}s "
        f"live segment path"
    )


def test_the_parse_runs_on_a_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structural pin: the parse must not run on the caller's loop thread."""
    seen: list[int] = []

    def fake_parse(events: Any, fmt: Any) -> Any:
        seen.append(threading.get_ident())

        class _Parsed:
            async def final_result(self) -> Any:
                return types.SimpleNamespace(
                    outcome=types.SimpleNamespace(status="completed"), records=[]
                )

        return _Parsed()

    monkeypatch.setattr(meta_sam_parser, "parse_responses_stream", fake_parse)

    assert asyncio.run(meta_client._parse_sam_output("<0f>")) == []
    assert seen and seen[0] != threading.get_ident()


# ---------------------------------------------------------------------------
# (c) The wrapper propagates the lane verdict and the empty case unchanged
# ---------------------------------------------------------------------------


def test_parse_sam_output_propagates_a_not_completed_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_parse(events: Any, fmt: Any) -> Any:
        class _Parsed:
            async def final_result(self) -> Any:
                return types.SimpleNamespace(
                    outcome=types.SimpleNamespace(status="incomplete"), records=[]
                )

        return _Parsed()

    monkeypatch.setattr(meta_sam_parser, "parse_responses_stream", fake_parse)

    with pytest.raises(meta_client.MetaLiveUnavailable) as caught:
        asyncio.run(meta_client._parse_sam_output("<0f>"))
    # Unchanged: the same class, not a subclass and not a re-wrap.
    assert type(caught.value) is meta_client.MetaLiveUnavailable
    assert "did not complete" in str(caught.value)


def test_parse_sam_output_returns_empty_for_empty_text() -> None:
    """Zero matches is a valid Stage B result, not an error."""
    assert asyncio.run(meta_client._parse_sam_output("")) == []


def test_parse_sam_output_reports_a_missing_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The thread hop must not turn an import failure into a bare ImportError."""
    import builtins

    real_import = builtins.__import__

    def blocked(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "meta_sam_parser":
            raise ImportError("blocked for the test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(meta_client.MetaLiveUnavailable) as caught:
        asyncio.run(meta_client._parse_sam_output("<0f>"))
    assert "meta-sam-parser" in str(caught.value)
