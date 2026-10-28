"""Validate every fixture under ``tests/fixtures/`` against ``app.schemas``.

This is a committed gate, not a scratch check. It runs offline, inside the
image, under ``docker exec katalogkilat-api pytest apps/api/tests -q``. Its job
is to make three separate failure modes impossible to ship quietly:

* **A fixture that no longer matches the contract.** ``apps/api/tests/fixtures``
  is the recorded-response surface the ``meta_client`` adapter replays
  under ``META_MOCK=true``. A fixture whose payload drifts from
  ``schemas.py`` turns every mock run into a test of the fixture, not of the
  pipeline, and mock-green stops meaning anything.
* **An invention that looks like evidence.** The committed set is recorded:
  every file carries ``placeholder: false`` and the ISO-8601 ``recorded_at`` of
  the live response it came from, under a ``source`` that names the capture and
  a ``model_id`` the surface can actually come from.
  ``test_all_fixtures_are_recorded`` pins that for the whole inventory, so no
  fixture can quietly revert to ``placeholder: true`` / ``recorded_at: null``.
  The envelope's shape rule still holds in both directions — a placeholder
  pairs ``true`` with ``null``, a recording pairs ``false`` with a timestamp —
  and that is what makes a hand-written payload impossible to confuse with a
  captured Meta response.
* **A fixture set that would let a wrong pipeline pass.** Masks carry their own
  geometry, so a set where every mask is full-frame would let an
  implementation that wrongly assumes the photo's dimensions pass by accident.
  The per-file geometry test refuses that shape of fixture.

Nothing here calls Meta, opens a socket, or imports ``meta_client``.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from app.schemas import (
    Concept,
    FidelityVerdict,
    HealthResponse,
    MaskResult,
    ProductEntry,
)

# Resolved relative to this file, never hardcoded: the tests live at
# ``/srv/apps/api/tests`` inside the image and at ``apps/api/tests`` in a
# checkout. Both resolve correctly through ``__file__``.
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

PACKS = ("a", "b", "c")

SURFACES = ("concepts", "masks", "entries", "fidelity", "health")

# The envelope documented in ``fixtures/README.md``. ``source`` is part of it:
# it names where the payload came from, which is the only thing that separates a
# hand-written placeholder from a recorded response besides ``recorded_at``.
ENVELOPE_KEYS = (
    "placeholder",
    "recorded_at",
    "pack",
    "surface",
    "source",
    "model_id",
    "payload",
)

# surface -> (schema, payload_is_a_list). Arrays validate element by element so
# a failure names the offending index and file rather than the whole payload.
SCHEMA_BY_SURFACE: dict[str, tuple[type[BaseModel], bool]] = {
    "concepts": (Concept, True),
    "masks": (MaskResult, True),
    "entries": (ProductEntry, True),
    "fidelity": (FidelityVerdict, False),
    "health": (HealthResponse, False),
}

# surface -> the model ids a fixture of that surface may claim to have been
# recorded from. ``muse-spark-1.3-contributor`` is the same model reached
# through the hackathon contributor route. ``health`` is a local liveness probe,
# not a model call, so its only honest ``model_id`` is null — any other value
# would claim a model was called when nothing was.
MODEL_IDS_BY_SURFACE: dict[str, frozenset[str | None]] = {
    "concepts": frozenset({"muse-spark-1.3", "muse-spark-1.3-contributor"}),
    "masks": frozenset({"sam-3.1"}),
    "entries": frozenset({"muse-spark-1.3", "muse-spark-1.3-contributor"}),
    "fidelity": frozenset({"muse-spark-1.3", "muse-spark-1.3-contributor"}),
    "health": frozenset({None}),
}

# Fields that belong to the seller's review, not to the model's output.
# ``ProductEntry`` has ``extra="forbid"``, so their presence already fails
# validation; asserting it separately exists so a future fixture author reads
# *why* instead of decoding a pydantic message.
SELLER_ONLY_ENTRY_FIELDS = ("price_idr", "price_via_chat")

# Price fields Stage E may return, and only as a non-negative integer or null.
ENTRY_PRICE_FIELDS = ("price_hint_idr", "price_suggestion_idr")

# Products per sample pack.
PRODUCT_COUNT_BY_PACK = {
    "a": (5, 8),
    "b": (6, 10),
    "c": (8, 12),
}

# The committed inventory, written out rather than inferred from the glob:
# 3 packs × (2 photos × concepts/masks/entries, 2 fidelity outcomes, 1 health)
# = 27 files. Spelling it out turns a missing or extra fixture into a named
# failure instead of a silently shorter loop.
FIXTURE_NAMES_PER_PACK = (
    "photo1_concepts.json",
    "photo2_concepts.json",
    "photo1_masks.json",
    "photo2_masks.json",
    "photo1_entries.json",
    "photo2_entries.json",
    "fidelity_pass.json",
    "fidelity_fail.json",
    "health.json",
)

EXPECTED_FIXTURE_NAMES = frozenset(
    f"pack_{pack}/{name}" for pack in PACKS for name in FIXTURE_NAMES_PER_PACK
)


# --------------------------------------------------------------------------
# Helpers — importable, so the bite proof can point them at a corrupt file.
# --------------------------------------------------------------------------


def _fail(path: Path, message: str) -> None:
    """Raise a validation failure that names the file it came from."""
    raise AssertionError(f"{path}: {message}")


def fixture_files(directory: Path = FIXTURES_DIR) -> list[Path]:
    """Every ``*.json`` under ``directory``, sorted for a stable test order."""
    return sorted(directory.rglob("*.json"))


def load_fixture(path: Path) -> dict[str, Any]:
    """Read one fixture and require it to be a JSON object."""
    raw = path.read_text(encoding="utf-8")
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:  # noqa: TRY003 - reported with the path
        _fail(path, f"is not valid JSON: {exc}")
    if not isinstance(document, dict):
        _fail(path, f"top level is {type(document).__name__}, expected an object")
    return document


def check_envelope(path: Path, envelope: dict[str, Any]) -> None:
    """Assert the documented envelope of one fixture."""
    missing = [key for key in ENVELOPE_KEYS if key not in envelope]
    if missing:
        _fail(path, f"envelope is missing {missing}; expected all of {list(ENVELOPE_KEYS)}")

    if not isinstance(envelope["placeholder"], bool):
        _fail(path, f"placeholder is {envelope['placeholder']!r}, expected a bool")

    surface = envelope["surface"]
    if surface not in SURFACES:
        _fail(path, f"surface is {surface!r}, expected one of {list(SURFACES)}")

    model_id = envelope["model_id"]
    if model_id is not None and not isinstance(model_id, str):
        _fail(path, f"model_id is {model_id!r}, expected a str or null")

    if not isinstance(envelope["source"], str) or not envelope["source"].strip():
        _fail(path, "source is empty; it must name where the payload came from")

    # The directory is ``pack_a`` and the field is ``a``; the prefix is the
    # folder convention, not part of the pack identity.
    expected_pack = envelope["pack"]
    if path.parent.name != f"pack_{expected_pack}":
        _fail(
            path,
            f"pack is {expected_pack!r} but the containing directory is "
            f"{path.parent.name!r}; expected {f'pack_{expected_pack}'!r}",
        )

    # The placeholder label and the recording timestamp move together: a
    # placeholder has no recording, a recording is not a placeholder.
    if envelope["placeholder"] is True and envelope["recorded_at"] is not None:
        _fail(
            path,
            f"placeholder is true but recorded_at is {envelope['recorded_at']!r}; "
            "a placeholder has never been recorded",
        )
    if envelope["placeholder"] is False and envelope["recorded_at"] is None:
        _fail(
            path,
            "placeholder is false but recorded_at is null; a recorded fixture must "
            "carry the timestamp of the response it was recorded from",
        )


def check_payload(path: Path, surface: str, payload: Any) -> None:
    """Validate one payload against the schema for its surface."""
    schema, is_list = SCHEMA_BY_SURFACE[surface]

    if is_list:
        if not isinstance(payload, list):
            _fail(path, f"{surface} payload is {type(payload).__name__}, expected a list")
        if not payload:
            _fail(path, f"{surface} payload is an empty list")
        for index, element in enumerate(payload):
            if not isinstance(element, dict):
                _fail(
                    path,
                    f"{surface} payload[{index}] is {type(element).__name__}, expected an object",
                )
            try:
                schema.model_validate(element)
            except Exception as exc:  # pydantic.ValidationError, reported verbatim
                _fail(path, f"{surface} payload[{index}] does not validate as {schema.__name__}:\n{exc}")
        return

    if not isinstance(payload, dict):
        _fail(path, f"{surface} payload is {type(payload).__name__}, expected an object")
    try:
        schema.model_validate(payload)
    except Exception as exc:
        _fail(path, f"{surface} payload does not validate as {schema.__name__}:\n{exc}")


def check_entry_price_law(path: Path, payload: list[dict[str, Any]]) -> None:
    """Every Stage E entry keeps the price law, in the data as well as the type.

    ``price_hint_idr`` and ``price_suggestion_idr`` are integer rupiah or null.
    Floats are rejected on the raw JSON, because pydantic
    would coerce ``4500000.0`` to ``4500000`` and hide a unit error. The
    seller's own fields — ``price_idr`` and ``price_via_chat`` — must be absent
    entirely: they belong to Review, and a model that returns them has started
    deciding a price.
    """
    for index, entry in enumerate(payload):
        for field in ENTRY_PRICE_FIELDS:
            if field not in entry:
                _fail(path, f"entries payload[{index}] is missing {field!r}; null is a value, not an omission")
            value = entry[field]
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int):
                _fail(
                    path,
                    f"entries payload[{index}].{field} is {value!r} "
                    f"({type(value).__name__}); IDR is an integer or null, never a float",
                )
            if value < 0:
                _fail(path, f"entries payload[{index}].{field} is negative ({value})")

        for field in SELLER_ONLY_ENTRY_FIELDS:
            if field in entry:
                _fail(
                    path,
                    f"entries payload[{index}] carries {field!r}. That field is the seller's "
                    "review decision, not Stage E output: the model never decides a price and "
                    "never flags an item as via-chat. Remove it from the fixture.",
                )


def check_mask_geometry(path: Path, payload: list[dict[str, Any]]) -> None:
    """Masks carry their own box and dimensions, and the set must show it.

    A segmentation mask is not necessarily full-frame. If every mask in a
    fixture file shared one
    ``(width, height)`` pair, an implementation that wrongly aligned masks
    against the source photo's dimensions would pass this gate by accident and
    fail only in front of a judge. So the set must contain at least two distinct
    geometries.
    """
    if len(payload) < 2:
        _fail(
            path,
            f"masks payload has {len(payload)} mask(s); at least 2 are needed to show that "
            "masks do not all share the photo's dimensions",
        )

    geometries: set[tuple[int, int]] = set()
    for index, mask in enumerate(payload):
        concept = mask["concept"]
        if not isinstance(concept, str) or not concept.strip():
            _fail(path, f"masks payload[{index}].concept is {concept!r}, expected a phrase")
        if "," in concept:
            _fail(
                path,
                f"masks payload[{index}].concept is {concept!r}; a SAM request takes exactly "
                "one short concept phrase and commas are not a multi-label interface. "
                "One request per concept, never a joined list.",
            )

        width = mask["width"]
        height = mask["height"]
        if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
            _fail(path, f"masks payload[{index}].width is {width!r}, expected a positive int")
        if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
            _fail(path, f"masks payload[{index}].height is {height!r}, expected a positive int")
        geometries.add((width, height))

        bbox = mask["bbox"]
        if not isinstance(bbox, list) or len(bbox) != 4:
            _fail(path, f"masks payload[{index}].bbox is {bbox!r}, expected 4 numbers")
        for position, coordinate in enumerate(bbox):
            if isinstance(coordinate, bool) or not isinstance(coordinate, int):
                _fail(
                    path,
                    f"masks payload[{index}].bbox[{position}] is {coordinate!r}, expected an int",
                )
            if coordinate < 0:
                _fail(path, f"masks payload[{index}].bbox[{position}] is negative ({coordinate})")

    if len(geometries) < 2:
        _fail(
            path,
            f"all {len(payload)} masks share the geometry {sorted(geometries)[0]}; a fixture set "
            "where every mask is full-frame would let a pipeline that assumes the photo's "
            "dimensions pass by accident",
        )


def validate_fixture(path: Path) -> dict[str, Any]:
    """Validate one fixture end to end and return its envelope.

    The single entry point for every check above: envelope, payload schema, and
    the surface-specific invariants. Exposed as a plain function so a corrupt
    copy of a fixture can be pushed through the exact same logic.
    """
    path = Path(path)
    envelope = load_fixture(path)
    check_envelope(path, envelope)

    surface = envelope["surface"]
    payload = envelope["payload"]
    check_payload(path, surface, payload)

    if surface == "entries":
        check_entry_price_law(path, payload)
    elif surface == "masks":
        check_mask_geometry(path, payload)

    return envelope


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


FIXTURE_FILES = fixture_files()


def _fixture_id(path: Path) -> str:
    """Stable, readable test id: ``pack_a/photo1_masks.json``."""
    return str(path.relative_to(FIXTURES_DIR))


def test_fixture_directory_is_not_empty() -> None:
    """A silently empty glob must fail, not pass vacuously."""
    assert FIXTURES_DIR.is_dir(), f"fixtures directory is missing: {FIXTURES_DIR}"
    assert FIXTURE_FILES, f"no *.json fixture found under {FIXTURES_DIR}"


@pytest.mark.parametrize("pack", PACKS)
def test_pack_carries_every_surface(pack: str) -> None:
    """Each of the three packs exists and covers all five surfaces."""
    pack_dir = FIXTURES_DIR / f"pack_{pack}"
    assert pack_dir.is_dir(), f"missing pack directory: {pack_dir}"

    files = fixture_files(pack_dir)
    assert files, f"pack_{pack} contains no fixtures"

    present = {load_fixture(path)["surface"] for path in files}
    missing = [surface for surface in SURFACES if surface not in present]
    assert not missing, f"pack_{pack} has no fixture for {missing}"


# --------------------------------------------------------------------------
# Per-file validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", FIXTURE_FILES, ids=_fixture_id)
def test_fixture_matches_schema(path: Path) -> None:
    """Envelope, payload schema, price law and mask geometry for one file."""
    validate_fixture(path)


# --------------------------------------------------------------------------
# Counts
# --------------------------------------------------------------------------


def test_fixture_counts_match_the_sample_packs(capsys: pytest.CaptureFixture[str]) -> None:
    """Report the totals and pin the product count to each pack's range.

    "Products" means ``ProductEntry`` records across the pack's ``entries``
    files: Pack A 5–8, Pack B 6–10, Pack C 8–12. The ranges come from
    the sample-pack definitions, so a fixture pack that drifts outside them is
    no longer a stand-in for the photo it claims to represent.
    """
    assert FIXTURE_FILES, f"no *.json fixture found under {FIXTURES_DIR}"

    totals: dict[str, dict[str, int]] = {}
    for path in FIXTURE_FILES:
        envelope = validate_fixture(path)
        pack = envelope["pack"]
        surface = envelope["surface"]
        payload = envelope["payload"]
        count = len(payload) if isinstance(payload, list) else 1
        pack_counts = totals.setdefault(pack, {surface: 0 for surface in SURFACES})
        pack_counts[surface] += count

    print(f"fixture files: {len(FIXTURE_FILES)} under {FIXTURES_DIR}")
    for pack in PACKS:
        counts = totals.get(pack, {})
        line = "  ".join(f"{surface}={counts.get(surface, 0)}" for surface in SURFACES)
        print(f"pack {pack}: products={counts.get('entries', 0)}  {line}")
    print(f"packs: {sorted(totals)}")

    assert sorted(totals) == list(PACKS), f"packs found {sorted(totals)}, expected {list(PACKS)}"

    for pack in PACKS:
        counts = totals[pack]
        low, high = PRODUCT_COUNT_BY_PACK[pack]
        products = counts.get("entries", 0)
        assert low <= products <= high, (
            f"pack_{pack} has {products} ProductEntry records; "
            f"the pack specifies {low}–{high}"
        )
        for surface in SURFACES:
            assert counts.get(surface, 0) > 0, f"pack_{pack} has no {surface} payload"


# --------------------------------------------------------------------------
# Recorded-set invariant
# --------------------------------------------------------------------------


def _recorded_problems(envelope: dict[str, Any]) -> list[str]:
    """Every reason one envelope is not a recorded response; empty means good.

    The caller prefixes the file name, so the aggregate failure reads as one
    line per offending file instead of one line per violation. Judging the
    envelope alone also keeps this independent of the payload checks: a fixture
    can be schema-perfect and still be an invention.
    """
    problems: list[str] = []

    if envelope.get("placeholder") is not False:
        problems.append(
            f"placeholder is {envelope.get('placeholder')!r}, expected false "
            "(the committed set is recorded)"
        )

    recorded_at = envelope.get("recorded_at")
    if not isinstance(recorded_at, str) or not recorded_at.strip():
        problems.append(
            f"recorded_at is {recorded_at!r}, expected the ISO-8601 timestamp of the "
            "response this file was recorded from"
        )
    else:
        # ``fromisoformat`` learned the ``Z`` suffix in 3.11; stripping it keeps
        # the check honest on any interpreter rather than relying on that.
        candidate = recorded_at[:-1] if recorded_at.endswith(("Z", "z")) else recorded_at
        try:
            datetime.fromisoformat(candidate)
        except ValueError as exc:
            problems.append(f"recorded_at {recorded_at!r} is not ISO-8601: {exc}")

    source = envelope.get("source")
    if not isinstance(source, str) or not source.strip():
        problems.append(f"source is {source!r}, expected the origin of the recording")
    elif "hand-written placeholder" in source.lower():
        problems.append(
            f"source still says {source!r}; a recorded fixture names the capture it "
            "came from, not a placeholder"
        )

    surface = envelope.get("surface")
    if surface not in SURFACES:
        problems.append(f"surface is {surface!r}, expected one of {list(SURFACES)}")
    elif surface not in MODEL_IDS_BY_SURFACE:
        problems.append(f"no allowed model_id set is defined for surface {surface!r}")
    else:
        allowed = MODEL_IDS_BY_SURFACE[surface]
        model_id = envelope.get("model_id")
        if model_id is not None and not isinstance(model_id, str):
            problems.append(f"model_id is {model_id!r}, expected a str or null")
        elif model_id not in allowed:
            problems.append(
                f"model_id is {model_id!r}, expected one of "
                f"{sorted(str(value) for value in allowed)} for surface {surface!r}"
            )

    return problems


def test_all_fixtures_are_recorded() -> None:
    """The committed fixture set is recorded, not hand-written.

    ``META_MOCK=true`` replays these files, so a placeholder among them turns
    every mock-green run into a test of our imagination rather than of Meta's
    contract. One test pins the whole inventory at once: the
    file set (3 packs × 9 files), ``placeholder: false``, a parseable ISO-8601
    ``recorded_at``, a ``source`` that does not admit to being hand-written, and
    a ``model_id`` the surface can actually come from.

    Violations are collected before the assertion, so a single run names the
    entire backlog — the point is to see every file that reverted, not the first
    one alphabetically.
    """
    actual = {path.relative_to(FIXTURES_DIR).as_posix() for path in FIXTURE_FILES}
    missing = sorted(EXPECTED_FIXTURE_NAMES - actual)
    extra = sorted(actual - EXPECTED_FIXTURE_NAMES)
    per_pack = {
        pack: sum(1 for name in actual if name.startswith(f"pack_{pack}/")) for pack in PACKS
    }
    assert not missing and not extra, (
        f"fixture inventory is {len(actual)} files, expected {len(EXPECTED_FIXTURE_NAMES)} "
        f"(3 packs × {len(FIXTURE_NAMES_PER_PACK)}); per-pack counts {per_pack}, "
        f"expected {len(FIXTURE_NAMES_PER_PACK)} each\n"
        f"missing: {missing}\n"
        f"extra: {extra}"
    )

    problems: list[str] = []
    for name in sorted(EXPECTED_FIXTURE_NAMES):
        path = FIXTURES_DIR / name
        try:
            envelope = load_fixture(path)
        except AssertionError as exc:  # unreadable JSON: reported, not fatal
            problems.append(str(exc))
            continue
        issues = _recorded_problems(envelope)
        if issues:
            problems.append(f"{name}: " + "; ".join(issues))

    assert not problems, (
        f"{len(problems)} of {len(EXPECTED_FIXTURE_NAMES)} fixtures are not recorded "
        "responses; the committed set must come from live Meta responses:\n"
        + "\n".join(problems)
    )
