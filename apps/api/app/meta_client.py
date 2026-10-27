"""Meta Model API adapter — the only module that may talk to ``api.meta.ai``.

Single-adapter boundary: no other file imports ``openai``, points ``httpx`` at Meta,
or constructs a Meta URL. The rest of the codebase calls exactly the six
public functions below and nothing else::

    discover_concepts(image_bytes, captions) -> list[Concept]
    segment_concept(image_bytes, concept)    -> list[MaskResult]
    clean_product_image(crop_png, label)    -> bytes
    verify_fidelity(original_png, cleaned)  -> FidelityVerdict
    extract_product(clean_png, context)     -> list[ProductEntry]
    health_probe()                          -> HealthResponse

MOCK MODE (``META_MOCK=true``, the default) is the only verified path today
(2026-10-26). Every function serves the recorded
fixtures under ``apps/api/tests/fixtures/pack_{a,b,c}/`` (see that
directory's README for the envelope). All fixtures are hand-written
placeholders (``placeholder: true``, ``recorded_at: null``); mock-green
proves nothing about the live API (a green mock run proves wiring, not the contract).

Deterministic input → fixture mapping (same input bytes → same output;
every input maps to some fixture — mock never raises "not found").

ONE shared slot scheme: every fixture-routed read derives its
``(pack, photo)`` slot from the call's primary image bytes alone —
``digest = sha256(image_bytes)``, ``digest[0] % 3`` picks ``a``/``b``/``c``,
``digest[1] % 2`` picks photo 1/2. No other argument changes the slot:
captions, concept phrases, and labels never move a call to a different
photo, so Stage A concepts and Stage B masks for the same input photo
always come from the same fixture photo.

* ``discover_concepts`` serves that photo's ``photo{n}_concepts.json``
  payload verbatim. ``captions`` is accepted (the live P1 prompt needs it)
  but ignored for routing.
* ``segment_concept`` serves the same slot's ``photo{n}_masks.json``,
  filtered to records whose ``concept`` matches the requested phrase
  (case-insensitive, stripped). Anything else — including a comma-joined
  phrase, which is never a valid SAM request — matches nothing and yields
  ``[]``, never an error.
* ``extract_product`` accepts the seller-captions wire shape for seller captions: ONE
  JSON string ``{"general": "...", "photos": [...]}`` (anything that does
  not parse as that object is treated as opaque text). It serves the slot
  of its ``clean_png`` bytes from ``photo{n}_entries.json`` — image bytes
  only; ``concept`` and the captions text do not move the slot.
  (``clean_png`` is Stage D output, bytes-different from the Stage A photo,
  so Stage E's slot is deterministic but not the same photo's slot; the
  pipeline, not the adapter, carries product identity across stages.)
* ``verify_fidelity`` takes its pack from the slot of ``original_png`` and
  selects ``fidelity_pass.json`` vs ``fidelity_fail.json`` by parity of
  ``sha256(original + cleaned)`` (even → pass, odd → fail), so both gate
  outcomes stay reachable. The parity bit is the verdict selector, not a
  slot input.
* ``clean_product_image`` has no image fixture (bytes, not JSON). It
  generates a deterministic 640x640 studio PNG with Pillow at runtime,
  seeded by ``sha256(crop_png + label)`` — same input → same bytes.
* ``health_probe`` serves ``pack_a/health.json`` (identical in all packs).

Price Law: the adapter returns ``price_hint_idr`` /
``price_suggestion_idr`` exactly as the fixture carries them. It never
computes, infers, or defaults a price.

LIVE BRANCH (``META_MOCK=false``): the ``segment_concept`` (SAM 3.1) path was
verified live on 2026-10-26 (non-streaming ``POST /v1/responses``,
status=completed, 39 records, parsed with ``meta-sam-parser`` 0.1.0). Other
live functions keep their hypothesis docstrings until probed. The live path
lazy-imports ``openai`` and ``meta-sam-parser`` inside the functions that need
them and raises :class:`MetaLiveUnavailable` when a package (or the key) is
absent; ``requirements.txt`` stays locked at four packages — adding either
package there is a Lead decision. The live branch is unreachable when
``settings.meta_mock`` is true, and tests never execute it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from pydantic import BaseModel, ValidationError

from app.schemas import (
    Concept,
    ExtractContext,
    FidelityVerdict,
    HealthResponse,
    MaskResult,
    ProductEntry,
)
from app.settings import settings

__all__ = [
    "aclose_live_client",
    "discover_concepts",
    "segment_concept",
    "clean_product_image",
    "verify_fidelity",
    "extract_product",
    "health_probe",
]

# Model ids and endpoint from the runbook. SAM request/response shape verified
# live 2026-10-26 against https://dev.meta.ai/docs/sam/* (see _live_segment_concept).
# MUSE_SPARK_MODEL uses the contributor variant: identical capability to 1.3 at roughly 12x lower price (input $0.10/M vs $1.25/M, output $0.20/M vs $4.25/M); inputs and outputs are used to train Meta's models, which is acceptable because every sample photo in this project is generated, not client data.
MUSE_SPARK_MODEL = "muse-spark-1.3-contributor"
SAM_MODEL = "sam-3.1"
MUSE_IMAGE_MODEL = "muse-image-1.0"
META_BASE_URL = "https://api.meta.ai/v1"

_PACKS = ("a", "b", "c")
_PHOTOS = (1, 2)

# Transients worth the runbook's 2s/5s/12s backoff; anything else surfaces.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
_RETRY_DELAYS = (2.0, 5.0, 12.0)

# Pillow canvas for the mock studio image (P3's off-white #F7F7F5).
_MOCK_CLEAN_SIZE = 640
_MOCK_CLEAN_BG = (247, 247, 245)


class MetaLiveUnavailable(RuntimeError):
    """Live Meta calls are impossible in this environment.

    Raised when the live branch is reached without the client dependency,
    without ``MODEL_API_KEY``, or without the SAM streaming parser — the
    three decisions that land with the key. Never raised in mock mode.
    """


class MetaModelNotFound(MetaLiveUnavailable):
    """``404 model_not_found``: an access problem, never retried."""


class MetaMockError(RuntimeError):
    """The mock fixture tree is missing or unreadable.

    This names a deployment defect, never an input problem: any *input*
    maps to some fixture, but a missing tree has nothing to serve.
    """


# ---------------------------------------------------------------------------
# Mock fixture loading
# ---------------------------------------------------------------------------

_fixtures_dir: Path | None = None
_fixture_cache: dict[str, Any] = {}


def _fixtures_root() -> Path:
    """Locate ``tests/fixtures`` in both known layouts.

    Checkout: ``apps/api/tests/fixtures`` (sibling of the ``app`` package).
    Container: ``/srv/apps/api/tests/fixtures`` (the image bakes the tree at
    a different relative path). The search walks upward so neither layout is
    hardcoded.
    """
    global _fixtures_dir
    if _fixtures_dir is not None:
        return _fixtures_dir
    here = Path(__file__).resolve()
    for parent in (here.parent, *here.parents):
        candidate = parent / "tests" / "fixtures"
        if candidate.is_dir():
            _fixtures_dir = candidate
            return candidate
        candidate = parent / "apps" / "api" / "tests" / "fixtures"
        if candidate.is_dir():
            _fixtures_dir = candidate
            return candidate
    raise MetaMockError(
        f"fixture tree not found above {here}; mock mode has nothing to serve"
    )


def _read_payload(pack: str, filename: str) -> Any:
    """Return the ``payload`` of one fixture envelope, cached by path."""
    key = f"pack_{pack}/{filename}"
    if key not in _fixture_cache:
        path = _fixtures_root() / f"pack_{pack}" / filename
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise MetaMockError(f"cannot read fixture {key}: {exc}") from exc
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MetaMockError(f"fixture {key} is not valid JSON: {exc}") from exc
        _fixture_cache[key] = envelope["payload"]
    return _fixture_cache[key]


def _validated(
    model: type[BaseModel], payload: Any, pack: str, filename: str
) -> BaseModel:
    """Validate one payload element, naming the fixture file on failure."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise MetaMockError(
            f"fixture pack_{pack}/{filename} does not validate as "
            f"{model.__name__}: {exc}"
        ) from exc


def _locate(key: bytes) -> tuple[str, int]:
    """Map arbitrary bytes to a deterministic ``(pack, photo)`` slot."""
    digest = hashlib.sha256(key).digest()
    return _PACKS[digest[0] % len(_PACKS)], _PHOTOS[digest[1] % len(_PHOTOS)]


def _captions_text(seller_captions: str) -> str:
    """Accept the seller-captions wire shape: one JSON string, or opaque text.

    ``{"general": "...", "photos": [...]}`` is flattened to plain text for
    the mock location key. Anything that does not parse as that object is
    used verbatim — mock never rejects a captions shape.
    """
    try:
        parsed = json.loads(seller_captions)
    except json.JSONDecodeError:
        return seller_captions
    if not isinstance(parsed, dict):
        return seller_captions
    parts: list[str] = []
    general = parsed.get("general", "")
    if isinstance(general, str) and general.strip():
        parts.append(general)
    photos = parsed.get("photos", [])
    if isinstance(photos, list):
        parts.extend(str(item) for item in photos if isinstance(item, str))
    elif isinstance(photos, str) and photos.strip():
        parts.append(photos)
    return "\n".join(parts) if parts else seller_captions


def _mock_discover_concepts(image_bytes: bytes, captions: str) -> list[Concept]:
    # Captions ride the live P1 prompt; the mock slot is image-bytes-only so
    # Stage B routes to the same photo (one shared slot scheme, see docstring).
    _ = captions
    pack, photo = _locate(image_bytes)
    filename = f"photo{photo}_concepts.json"
    payload = _read_payload(pack, filename)
    return [
        _validated(Concept, item, pack, filename)  # type: ignore[misc]
        for item in payload
    ]


def _mock_segment_concept(image_bytes: bytes, concept: str) -> list[MaskResult]:
    wanted = concept.strip().lower()
    if not wanted:
        return []
    # Location keys on the image bytes alone so Stage B agrees with Stage A
    # for the same input photo; the phrase only filters records inside it.
    pack, photo = _locate(image_bytes)
    filename = f"photo{photo}_masks.json"
    payload = _read_payload(pack, filename)
    results: list[MaskResult] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        if str(item.get("concept", "")).strip().lower() != wanted:
            continue
        validated = _validated(MaskResult, item, pack, filename)
        assert isinstance(validated, MaskResult)
        results.append(validated)
    return results


def _mock_clean_product_image(crop_png: bytes, product_label: str) -> bytes:
    """Deterministic studio PNG: same input → same bytes."""
    seed = hashlib.sha256(
        crop_png + b"\x00" + product_label.encode("utf-8")
    ).digest()
    img = Image.new("RGB", (_MOCK_CLEAN_SIZE, _MOCK_CLEAN_SIZE), _MOCK_CLEAN_BG)
    draw = ImageDraw.Draw(img)
    accent = (seed[0] // 2 + 100, seed[1] // 2 + 100, seed[2] // 2 + 100)
    bar_h = _MOCK_CLEAN_SIZE // 12
    draw.rectangle(
        (0, _MOCK_CLEAN_SIZE - bar_h, _MOCK_CLEAN_SIZE, _MOCK_CLEAN_SIZE),
        fill=accent,
    )
    draw.ellipse(
        (
            _MOCK_CLEAN_SIZE // 4,
            _MOCK_CLEAN_SIZE // 4,
            3 * _MOCK_CLEAN_SIZE // 4,
            3 * _MOCK_CLEAN_SIZE // 4,
        ),
        outline=accent,
        width=3,
    )
    draw.text((24, 24), product_label[:48], fill=(60, 60, 60))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=9)
    return buf.getvalue()


def _mock_verify_fidelity(original_png: bytes, cleaned_png: bytes) -> FidelityVerdict:
    # Pack from the original's slot; the verdict file from pair parity.
    pack, _ = _locate(original_png)
    if not cleaned_png:
        # Pre-cleanup crop check (P4a): mock always passes so offline runs
        # never block before Stage D; the post-check parity below is untouched.
        filename = "fidelity_pass.json"
        payload = _read_payload(pack, filename)
        validated = _validated(FidelityVerdict, payload, pack, filename)
        assert isinstance(validated, FidelityVerdict)
        return validated
    even = hashlib.sha256(original_png + b"\x00" + cleaned_png).digest()[0] % 2 == 0
    # Even digest → pass, odd → fail: both gate outcomes stay reachable.
    filename = "fidelity_pass.json" if even else "fidelity_fail.json"
    payload = _read_payload(pack, filename)
    validated = _validated(FidelityVerdict, payload, pack, filename)
    assert isinstance(validated, FidelityVerdict)
    return validated


def _mock_extract_product(
    clean_png: bytes, context: ExtractContext
) -> list[ProductEntry]:
    # Slot from the image bytes only (one shared slot scheme, see docstring).
    # Stage C bytes differ from the Stage A photo, so the slot is
    # deterministic per clean image, not identical to Stage A's photo slot.
    # The live branch needs the captions and concept for the P5 prompt; the
    # mock slot ignores them, so they are discarded explicitly, not silently.
    _ = context
    pack, photo = _locate(clean_png)
    filename = f"photo{photo}_entries.json"
    payload = _read_payload(pack, filename)
    # Prices pass through exactly as the fixture carries them (Price Law):
    # the adapter never computes, infers, or defaults a price.
    return [
        _validated(ProductEntry, item, pack, filename)  # type: ignore[misc]
        for item in payload
    ]


def _mock_health_probe() -> HealthResponse:
    validated = _validated(HealthResponse, _read_payload("a", "health.json"), "a", "health.json")
    assert isinstance(validated, HealthResponse)
    return validated


# ---------------------------------------------------------------------------
# Live branch — SAM segment path VERIFIED 2026-10-26 against
# https://dev.meta.ai/docs/sam/{overview,segmenting,reading-segmentation,client-libraries}
# with one captured non-streaming response (status=completed, 39 records).
# Live client is a process-lifetime singleton (per-call clients leaked
# unclosed httpx transports — "Event loop is closed" noise at teardown).
# Remaining live functions keep their hypothesis
# docstrings until probed.
# ---------------------------------------------------------------------------


_shared_live_client: Any | None = None


def _live_client() -> Any:
    """Return the shared OpenAI-compatible client, creating it on first use.

    One client per process: it owns a connection pool, so creating one per
    call leaks transports and spams ``Event loop is closed`` errors at loop
    teardown (a leaked-transport finding). Callers never close it; scripts and suite
    teardown use :func:`aclose_live_client`. Raises the same
    :class:`MetaLiveUnavailable` as before when the package or key is absent.
    """
    global _shared_live_client
    if _shared_live_client is None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise MetaLiveUnavailable(
                "live Meta calls need the 'openai' client package, which is not "
                "installed: requirements.txt is locked at four packages and adding "
                "one is a Lead decision (due with the API key)."
            ) from exc
        api_key = settings.model_api_key
        if not api_key:
            raise MetaLiveUnavailable(
                "MODEL_API_KEY is not set; live Meta calls are impossible."
            )
        _shared_live_client = AsyncOpenAI(base_url=META_BASE_URL, api_key=api_key)
    return _shared_live_client


async def aclose_live_client() -> None:
    """Close and drop the shared live client.

    Idempotent. For probe scripts and suite teardown — the running server
    keeps the process-lifetime client open by design. A close failure is
    re-raised with context, never swallowed.
    """
    global _shared_live_client
    client, _shared_live_client = _shared_live_client, None
    if client is not None:
        try:
            await client.close()
        except Exception as exc:
            raise MetaLiveUnavailable(
                f"closing the shared live client failed: {exc}"
            ) from exc


def _is_transient(exc: Exception) -> bool:
    """True for 429/5xx/timeout — the only retried failures (runbook §2)."""
    try:
        from openai import APIConnectionError, APIStatusError, RateLimitError
    except ImportError:
        return isinstance(exc, (TimeoutError, asyncio.TimeoutError))
    if isinstance(exc, (APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError) and exc.status_code in _TRANSIENT_STATUS:
        return True
    return False


def _is_model_not_found(exc: Exception) -> bool:
    """True for ``404 model_not_found``: access problem, never retried."""
    if getattr(exc, "status_code", None) != 404:
        return False
    code = ""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", {})
        if isinstance(error, dict):
            code = str(error.get("code", ""))
    return "model_not_found" in (code + " " + str(exc)).lower()


async def _live_request(
    factory: Callable[[], Coroutine[Any, Any, Any]], *, what: str
) -> Any:
    """Run one live call with 2s/5s/12s backoff on transients only."""
    last: Exception | None = None
    for attempt_index, delay in enumerate((*_RETRY_DELAYS, 0.0)):
        try:
            return await factory()
        except Exception as exc:
            if _is_model_not_found(exc):
                raise MetaModelNotFound(
                    f"{what}: 404 model_not_found — key scope problem, not retried "
                    f": key scope not retried: {exc}"
                ) from exc
            last = exc
            if not _is_transient(exc) or attempt_index == len(_RETRY_DELAYS):
                raise
            await asyncio.sleep(delay)
    assert last is not None
    raise last


def _data_url(image_bytes: bytes) -> str:
    """Encode image bytes as a data URL, sniffing PNG vs JPEG magic."""
    mime = "image/png" if image_bytes[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(image_bytes).decode("ascii")


def _output_text_of(response: Any) -> str:
    """Collect the ``output_text`` lane from a non-streaming response.

    Empty lane (zero matches) returns ``""`` — a valid result, never an
    error. A refused response surfaces verbatim; a failed/incomplete status
    surfaces as an error.
    """
    status = getattr(response, "status", None)
    if status not in (None, "completed"):
        if status == "incomplete":
            raise MetaLiveUnavailable(
                "segment_concept: response incomplete "
                f"({getattr(response, 'incomplete_details', None)})"
            )
        error = getattr(response, "error", None)
        raise MetaLiveUnavailable(
            f"segment_concept: response status {status!r}: {error!r}"
        )
    parts: list[str] = []
    output = getattr(response, "output", None) or []
    for item in output:
        item_type = getattr(item, "type", None)
        if item_type == "refusal":
            raise MetaLiveUnavailable(
                f"segment_concept: model refused: {getattr(item, 'refusal', '')!r}"
            )
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "refusal":
                raise MetaLiveUnavailable(
                    f"segment_concept: model refused: {getattr(part, 'refusal', '')!r}"
                )
            if getattr(part, "type", None) == "output_text":
                parts.append(str(getattr(part, "text", "") or ""))
    text = "".join(parts)
    return text.strip()


async def _parse_sam_output(output_text: str) -> list[Any]:
    """Parse one ``output_text`` grammar line into mask records.

    Lazy-imports the installed ``meta-sam-parser`` package (>=0.0.6 for
    confidence; verified 2026-10-26 on v0.1.0: 39/39 probe records decode
    with zero diagnostics). Feeds the line as a single delta plus synthetic
    lane-done and completed events, mirroring what the streaming parser
    consumes from a live stream.
    """
    if not output_text:
        return []
    try:
        from meta_sam_parser import (
            image_segmentation_format,
            parse_responses_stream,
        )
    except ImportError as exc:
        raise MetaLiveUnavailable(
            "segment_concept: the 'meta-sam-parser' package (needs >=0.0.6 "
            "for confidence) is not installed in this environment."
        ) from exc

    async def _events() -> Any:
        lane = {"item_id": "msg_0", "output_index": 0, "content_index": 0}
        yield {
            "type": "response.output_text.delta",
            "delta": output_text,
            **lane,
        }
        yield {"type": "response.output_text.done", "text": output_text, **lane}
        yield {
            "type": "response.completed",
            "response": {"id": "resp_live", "status": "completed"},
        }

    parsed = parse_responses_stream(_events(), image_segmentation_format())
    result = await parsed.final_result()
    status = getattr(getattr(result, "outcome", None), "status", None)
    if status != "completed":
        raise MetaLiveUnavailable(
            f"segment_concept: SAM output lane did not complete: {status!r}"
        )
    return [r for r in result.records if getattr(r, "kind", None) == "mask"]


def _raster_to_mode1_png(raster: bytes, width: int, height: int) -> bytes:
    """Encode a 0/1 raster as a mode-1 PNG (matches the ``MaskResult`` wire)."""
    if len(raster) != width * height:
        raise MetaLiveUnavailable(
            f"segment_concept: raster length {len(raster)} != {width}x{height}"
        )
    lum = Image.frombytes(
        "L", (width, height), bytes(255 if b else 0 for b in raster)
    )
    img = lum.convert("1")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def _live_discover_concepts(
    image_bytes: bytes, captions: str
) -> list[Concept]:
    """Live Stage A (P1), verified 2026-10-27.

    ``POST /v1/chat/completions``, model ``muse-spark-1.3-contributor``,
    (contributor variant: identical capability to 1.3 at roughly 12x lower price (input $0.10/M vs $1.25/M, output $0.20/M vs $4.25/M); inputs and outputs are used to train Meta's models, which is acceptable because every sample photo in this project is generated, not client data),
    ``temperature=0.4``, ``response_format={"type": "json_object"}``, one
    ``user`` message carrying ``input_text``-equivalent text +
    ``image_url`` data URL (docs:
    https://dev.meta.ai/docs/image-understanding). Two live photos, both
    HTTP 200 first try (~11s each): 10 concepts (snacks photo) and 4
    (phones photo), schema-valid ``Concept[]`` with no comma in any phrase,
    shared ``scene_note`` + joined ``visible_text`` attached to every
    concept. Temp/``json_object``/vision shape exactly as hypothesized.
    """
    client = _live_client()
    data_url = _data_url(image_bytes)

    async def _call() -> Any:
        return await client.chat.completions.create(
            model=MUSE_SPARK_MODEL,
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You analyze photos taken by small online sellers. Your "
                        "job is to prepare the photo for concept segmentation "
                        "by listing the foreground product types clearly "
                        "presented for sale on the main surface."
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "List every distinct foreground product TYPE clearly presented for sale "
                                f"on the main counter, table, or display surface in this photo. Seller captions: {captions!r}.\n"
                                "Rules:\n"
                                "- Output concepts as short English noun phrases: \"smartphone\", \"smartphone box\", "
                                "\"phone case\", \"charging case\", \"power bank\", \"charging cable\", \"sneakers\", "
                                "\"instant noodle cup\". One phrase per visually distinct product type. "
                                "Multiple identical items are ONE concept.\n"
                                "- When sealed retail product boxes are stacked on the main counter, list their box type "
                                "(e.g. \"smartphone box\") rather than \"phone case\".\n"
                                "- Never put brand names, model names, colors, or commas inside a concept phrase.\n"
                                "- ONLY list foreground products on the main selling surface. Ignore distant or blurry "
                                "items hanging on background walls/pegboards/shelves, open brown shipping cartons in the "
                                "back room, plastic display stands/holders, price tags/signs, pens/notebooks, hands, "
                                "furniture, walls, posters, and reflections or items trapped underneath glass counter shelves.\n"
                                "- Transcribe any readable text (brands, model numbers, price tags) verbatim into visible_text.\n"
                                "Return ONLY JSON:\n"
                                "{\"concepts\": [\"...\"], \"scene_note\": \"one sentence describing the scene\", "
                                "\"visible_text\": [\"...\"]}"
                            ),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
        )

    response = await _live_request(_call, what="discover_concepts")
    try:
        document = json.loads(response.choices[0].message.content or "")
        scene_note = str(document["scene_note"])
        visible = document.get("visible_text", [])
        visible_text = ", ".join(str(t) for t in visible) if visible else None
        return [
            Concept(phrase=str(phrase), scene_note=scene_note, visible_text=visible_text)
            for phrase in document["concepts"]
        ]
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise MetaLiveUnavailable(
            f"discover_concepts: live P1 response did not match the hypothesis: {exc}"
        ) from exc


async def _live_segment_concept(
    image_bytes: bytes, concept: str
) -> list[MaskResult]:
    """Live Stage B (P2), verified 2026-10-26.

    Non-streaming ``POST /v1/responses`` (docs: streaming optional), model
    ``sam-3.1``, one user message with ``input_text`` (the phrase) +
    ``input_image`` (data URL), ``metadata={"mask_encoding": "one_bit",
    "include_confidence": "true"}``. The ``output_text`` grammar line starts
    with ``<0f>`` (no ``text`` prefix) and uses ``data=H,W,!payload``
    (height first, base85, ``!`` = one_bit). Parsed with the installed
    ``meta-sam-parser`` package (``parse_responses_stream`` +
    ``image_segmentation_format()`` against synthetic delta/done/completed
    events); masks decoded with its ``decode_mask_to_raster``. Zero matches
    (empty output_text) → ``[]``.
    404 model_not_found surfaces, never retried; 429/5xx/
    timeout retry 2s/5s/12s; other 400s surface verbatim.
    """
    wanted = concept.strip()
    if not wanted:
        return []
    client = _live_client()

    async def _call() -> Any:
        return await client.responses.create(  # type: ignore[attr-defined]
            model=SAM_MODEL,
            input=[
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": wanted},
                        {"type": "input_image", "image_url": _data_url(image_bytes)},
                    ],
                }
            ],
            metadata={"mask_encoding": "one_bit", "include_confidence": "true"},
            stream=False,
        )

    response = await _live_request(_call, what="segment_concept")
    try:
        from meta_sam_parser import decode_mask_to_raster

        output_text = _output_text_of(response)
        records = await _parse_sam_output(output_text)
    except MetaModelNotFound:
        raise
    except MetaLiveUnavailable:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise MetaLiveUnavailable(
            f"segment_concept: live P2 response did not match the verified "
            f"shape: {exc}"
        ) from exc
    results: list[MaskResult] = []
    for record in records:
        try:
            raster = decode_mask_to_raster(record.mask)
        except Exception as exc:
            raise MetaLiveUnavailable(
                f"segment_concept: mask payload of record {record.object_id!r} "
                f"did not decode: {exc}"
            ) from exc
        if len(raster) != record.mask.width * record.mask.height:
            raise MetaLiveUnavailable(
                f"segment_concept: raster length {len(raster)} != "
                f"{record.mask.width}x{record.mask.height}"
            )
        score = float(record.confidence if record.confidence is not None else 0.0)
        if score < 0.70:
            continue
        fg_pixels = sum(1 for b in raster if b)
        mask_area = record.mask.width * record.mask.height
        if mask_area <= 0 or (fg_pixels / mask_area) < 0.38:
            continue
        png = _raster_to_mode1_png(
            raster, record.mask.width, record.mask.height
        )
        results.append(
            MaskResult(
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
        )
    return results


async def _live_clean_product_image(
    crop_png: bytes, product_label: str
) -> bytes:
    """Live Stage D cleanup (P3), verified 2026-10-27.

    ``POST /v1/images/edits``, model ``muse-image-1.0``, multipart
    ``image`` = file object (``crop.png``, ``image/png``) — the runbook's
    open question is answered: **file object, not data URL** on this
    endpoint (docs: https://dev.meta.ai/docs/image-generation — "pass the
    image bytes as ``image``, exactly like OpenAI's ``images.edit``").
    ``n=1``, ``size="1024x1024"`` (aspect ratio only — returned PNG is
    1600x1600 RGB), ``response_format="b64_json"``,
    ``output_format="png"``, ``reasoning_strength`` omitted. HTTP 200
    first try (~27s, 2.3MB PNG). Note: the edit redesigned the product
    (PEANUT→HAZELNUT imagery, garbled brand/fine-print text) — the P4 gate
    below failed it, which is the design working, not an error.
    """
    client = _live_client()
    image_file = io.BytesIO(crop_png)
    image_file.name = "crop.png"

    async def _call() -> Any:
        return await client.images.edit(
            model=MUSE_IMAGE_MODEL,
            image=image_file,
            prompt=(
                "Place this exact product on a clean off-white studio background "
                "(#F7F7F5), soft even lighting, gentle natural shadow beneath it, "
                "centered, front view, e-commerce product photography. Preserve "
                "the product exactly: same shape, same colors, same logos, same "
                "text, same details — do not redesign, restyle, or add or remove "
                "any part of the product. Remove the original background, hands, "
                "plastic display stands or holders, price tags, and any other "
                "non-product objects completely."
            ),
            n=1,
            size="1024x1024",
            response_format="b64_json",
            output_format="png",
        )

    response = await _live_request(_call, what="clean_product_image")
    try:
        return base64.b64decode(response.data[0].b64_json)
    except (AttributeError, IndexError, KeyError, ValueError) as exc:
        raise MetaLiveUnavailable(
            f"clean_product_image: live P3 response did not match the hypothesis: {exc}"
        ) from exc


async def _live_verify_fidelity(
    original_png: bytes, cleaned_png: bytes
) -> FidelityVerdict:
    """Live Stage D gate (P4 v2: P4a pre-cleanup crop gate + P4b post-edit gate).

    ``POST /v1/chat/completions``, model ``muse-spark-1.3-contributor``,
    (contributor variant: identical capability to 1.3 at roughly 12x lower price (input $0.10/M vs $1.25/M, output $0.20/M vs $4.25/M); inputs and outputs are used to train Meta's models, which is acceptable because every sample photo in this project is generated, not client data),
    ``temperature=0.0``, ``response_format={"type": "json_object"}``.
    Two modes on ``cleaned_png``:
    * P4a (``not cleaned_png``): one ``image_url`` (the candidate crop),
      rejects partial/sandwiched/half-hidden fragments, under-glass items,
      background clutter, and merged boxes before Muse Image runs.
    * P4b (``bool(cleaned_png)``): two ``image_url`` data URLs
      (docs: https://dev.meta.ai/docs/image-understanding — up to 50 images
      per request), ground-truth Image 1 vs studio Image 2. A fail is a
      verdict, never an error.
    """
    client = _live_client()
    if not cleaned_png:
        first = _data_url(original_png)

        async def _call() -> Any:
            return await client.chat.completions.create(
                model=MUSE_SPARK_MODEL,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict quality inspector for product photography.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You are a strict product crop inspector for an e-commerce catalog pipeline.\n"
                                    "The image shows the candidate product cut-out (if two panels are shown: LEFT is the "
                                    "original store photo context with a red box marking the candidate product, and RIGHT "
                                    "is the segmented cut-out on a solid bright magenta #FF00FF mask background).\n\n"
                                    "Decide whether the segmented cut-out is a VALID, SINGLE, SUBSTANTIALLY COMPLETE "
                                    "foreground product ready for studio cleanup.\n"
                                    "Set same_product=false, color_match=false, logo_text_match=false (and reject the crop) "
                                    "if ANY of the following is true:\n"
                                    "1. PARTIAL / SANDWICHED / WEDGE-BITTEN FRAGMENT: Another object (such as an upper box in a "
                                    "stack, a price sign/card, or a cable coil) blocks a major face or cuts a large angular wedge "
                                    "or diagonal bite (>12% of the body) out of the side of the product in the RIGHT panel, or "
                                    "only the side walls of a sandwiched middle/bottom box are visible (note: a small rectangular "
                                    "stand-clip cutout notch centered at the very bottom rim of an upright vertical phone is "
                                    "acceptable in this pre-check).\n"
                                    "2. TRAPPED UNDER GLASS / CROSSED BY CABLES: The object is underneath a glass "
                                    "counter/shelf at the bottom edge of the photo with charging cables crossing over its "
                                    "face, or has a hole through its center.\n"
                                    "3. BACKGROUND CLUTTER OR MERGED DISTINCT ITEMS: The cut-out is a distant blurry item "
                                    "hanging on a background wall rack, an open brown shipping carton in the back room, or "
                                    "merges two distinct rigid products/boxes together (note: a coiled bundle of charging "
                                    "cables resting together on the counter counts as ONE valid cable product).\n\n"
                                    "Set same_product=true, color_match=true, logo_text_match=true if the cut-out shows ONE "
                                    "clear foreground product on the main counter with its primary face/body intact (>=75% "
                                    "visible), even if a small corner (<25%) is notched where an adjacent price tag, cable, "
                                    "or stand lip was masked out.\n"
                                    "Return ONLY JSON:\n"
                                    "{\"same_product\": bool, \"color_match\": bool, \"logo_text_match\": bool, "
                                    "\"reason\": \"one short sentence\"}"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": first}},
                        ],
                    },
                ],
            )
    else:
        first = _data_url(original_png)
        second = _data_url(cleaned_png)

        async def _call() -> Any:  # noqa: F811 - mode branch redefines the factory
            return await client.chat.completions.create(
                model=MUSE_SPARK_MODEL,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": "You are a strict quality inspector for product photography.",
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "You are a strict quality and fidelity inspector for e-commerce product photography.\n"
                                    "Image 1 is the ground-truth reference (if two panels are shown: LEFT is the original "
                                    "photo region with a red box around the target item, and RIGHT is the segmented cut-out "
                                    "on magenta).\n"
                                    "Image 2 is the AI-edited studio image.\n\n"
                                    "Check whether Image 2 is a faithful studio version of the EXACT single product shown in Image 1:\n"
                                    "(a) same_product: Image 2 must show one complete, intact single product of the exact same "
                                    "product type, exact same item count (1 item must stay 1 item — never turn 1 box into 2 or 3 "
                                    "stacked boxes, never turn a phone screen into a charging cable), and exact same physical "
                                    "model, camera/port layout, and natural geometry. If Image 2 still has a missing cutout "
                                    "notch, missing corner wedge, or missing chunk taken out of the product's body (such as a "
                                    "missing bottom stand-notch or a missing price-tag wedge in the silhouette of Image 2), OR "
                                    "if Image 1 was a sandwiched box fragment (<70% visible), OR if Image 2 retained a physical "
                                    "plastic display stand/holder or added a second object, same_product MUST be false.\n"
                                    "(b) color_match: The product's body, screen, and accent colors in Image 2 match Image 1 "
                                    "without recoloring.\n"
                                    "(c) logo_text_match: Any visible brand logo or printed text on the product in Image 1 is "
                                    "identical and legible in Image 2 (or true if the product has no visible logo/text in "
                                    "either image).\n\n"
                                    "Be strict: if any check fails, set that boolean to false.\n"
                                    "Return ONLY JSON:\n"
                                    "{\"same_product\": bool, \"color_match\": bool, \"logo_text_match\": bool, "
                                    "\"reason\": \"one short sentence\"}"
                                ),
                            },
                            {"type": "image_url", "image_url": {"url": first}},
                            {"type": "image_url", "image_url": {"url": second}},
                        ],
                    },
                ],
            )

    response = await _live_request(_call, what="verify_fidelity")
    try:
        return FidelityVerdict.model_validate_json(
            response.choices[0].message.content or ""
        )
    except (ValueError, ValidationError) as exc:
        raise MetaLiveUnavailable(
            f"verify_fidelity: live P4 response did not match the hypothesis: {exc}"
        ) from exc


async def _live_extract_product(
    clean_png: bytes, context: ExtractContext
) -> list[ProductEntry]:
    """Live Stage E (P5 + one P6 repair retry), verified 2026-10-27 (live probe).

    ``POST /v1/chat/completions``, model ``muse-spark-1.3-contributor``,
    (contributor variant: identical capability to 1.3 at roughly 12x lower price (input $0.10/M vs $1.25/M, output $0.20/M vs $4.25/M); inputs and outputs are used to train Meta's models, which is acceptable because every sample photo in this project is generated, not client data),
    ``temperature=0.2``, ``response_format={"type": "json_object"}`` +
    pydantic validation, exactly one P6 repair retry on validation failure
    (docs structured output: https://dev.meta.ai/docs/structured-output —
    ``json_schema``/``text.format`` exist but the ``json_object`` +
    validation shape was kept per spec). Two decisive calls, both HTTP 200
    first try: captions WITH "Harga 15000 per pcs" → ``price_hint_idr`` =
    15000 (seller's own words, pre-fillable) and ``price_suggestion_idr``
    = 15000, brand "Koomuak"; captions with NO price → both null,
    ``price_suggestion_idr`` null (not an invention). The price rules bind
    the prompt, not this code — the model returned hints, never decisions.
    One review-fix: raw live ``category`` values ("snack", "chocolate
    wafer bar", "Makanan Ringan") are free-str ``str`` per schemas.py, so
    fixtures normalize them to the P5 schema bucket "Makanan & Minuman".
    """
    client = _live_client()
    data_url = _data_url(clean_png)
    captions_text = _captions_text(context.seller_captions)
    # ExtractContext carries seller_captions (JSON, which bundles the
    # Stage A scene note in its "general" text), concept, and visible_text —
    # schemas.py is frozen, so there is no separate scene_note field. The
    # exact live threading of Stage A's scene note is confirmed against the
    # first live response, not assumed here.
    user_text = (
        f"Seller's own notes/captions: {captions_text!r}. "
        f"Text seen in the original photo: {context.visible_text}. "
        f"Product type: {context.concept!r}. Write the catalog entry for THIS "
        "product as JSON only: name, description_id, description_en, category, "
        "brand, condition, attributes {color, size, variant}, price_hint_idr, "
        "price_suggestion_idr, confidence."
    )

    async def _call(messages: list[dict[str, Any]]) -> Any:
        async def _inner() -> Any:
            return await client.chat.completions.create(
                model=MUSE_SPARK_MODEL,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=messages,
            )

        return await _live_request(_inner, what="extract_product")

    def _messages(extra: str = "") -> list[dict[str, Any]]:
        return [
            {
                "role": "system",
                "content": (
                    "You write catalog entries for Indonesian social-commerce "
                    "sellers. You never invent specifications, prices, or brands "
                    "you cannot see."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text + extra},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]

    response = await _call(_messages())
    try:
        return [ProductEntry.model_validate_json(response.choices[0].message.content or "")]
    except (ValueError, ValidationError) as first_exc:
        repair = await _call(
            _messages(
                f" Your previous answer failed validation: {first_exc}. Return "
                "the same content as corrected JSON only, matching the schema "
                "exactly. No explanation, no markdown fences."
            )
        )
        try:
            return [
                ProductEntry.model_validate_json(repair.choices[0].message.content or "")
            ]
        except (ValueError, ValidationError) as exc:
            raise MetaLiveUnavailable(
                f"extract_product: P6 repair retry also failed validation: {exc}"
            ) from exc


async def _live_health_probe() -> HealthResponse:
    """HYPOTHESIS: reachability + model-access check, no prompt, no content."""
    client = _live_client()

    async def _call() -> Any:
        return await client.models.list()

    await _live_request(_call, what="health_probe")
    return HealthResponse(ok=True, mock=False)


# ---------------------------------------------------------------------------
# Public surface — exactly these six functions
# ---------------------------------------------------------------------------


async def discover_concepts(image_bytes: bytes, captions: str) -> list[Concept]:
    """Stage A: name the product concepts visible in one photo (P1)."""
    if settings.meta_mock:
        return _mock_discover_concepts(image_bytes, captions)
    return await _live_discover_concepts(image_bytes, captions)


async def segment_concept(image_bytes: bytes, concept: str) -> list[MaskResult]:
    """Stage B: segment every instance of ONE concept phrase (P2)."""
    if settings.meta_mock:
        return _mock_segment_concept(image_bytes, concept)
    return await _live_segment_concept(image_bytes, concept)


async def clean_product_image(crop_png: bytes, product_label: str) -> bytes:
    """Stage D: cleanup edit into a studio PNG (P3)."""
    if settings.meta_mock:
        return _mock_clean_product_image(crop_png, product_label)
    return await _live_clean_product_image(crop_png, product_label)


async def verify_fidelity(original_png: bytes, cleaned_png: bytes) -> FidelityVerdict:
    """Stage D gate: strict same-product verdict (P4). A fail is a verdict."""
    if settings.meta_mock:
        return _mock_verify_fidelity(original_png, cleaned_png)
    return await _live_verify_fidelity(original_png, cleaned_png)


async def extract_product(
    clean_png: bytes, context: ExtractContext
) -> list[ProductEntry]:
    """Stage E: catalog entries from the cleaned image + captions (P5/P6)."""
    if settings.meta_mock:
        return _mock_extract_product(clean_png, context)
    return await _live_extract_product(clean_png, context)


async def health_probe() -> HealthResponse:
    """Liveness + mock flag. Sends no prompt, produces no catalog content."""
    if settings.meta_mock:
        return _mock_health_probe()
    return await _live_health_probe()
