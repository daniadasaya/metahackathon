# Fixtures — `apps/api/tests/fixtures/`

These files are **recorded live Meta Model API responses**. Every file carries `"placeholder": false` and the ISO-8601
`recorded_at` of the response it was recorded from, and `source` names the
capture it came from — so no fixture can be mistaken for a hand-written
placeholder.

## Envelope

Every `*.json` file in this directory is an object with the same top level:

```json
{
  "placeholder": false,
  "recorded_at": "2026-10-28T00:13:01.739645Z",
  "pack": "a",
  "surface": "concepts",
  "source": "Live P1 discover_concepts on apps/web/public/samples/a/photo1.jpg ...",
  "model_id": "muse-spark-1.3-contributor",
  "payload": {}
}
```

- `placeholder` is always `false`. A recording pairs `false` with a real
  timestamp; a `true`/`null` pair would be a placeholder and fails
  `test_all_fixtures_are_recorded`.
- `recorded_at` is the timestamp of the live call (per-call capture
  timestamps for Pack A concepts/masks) or of the live job run the payload
  was frozen from (the job's `updated_at`).
- `surface` is one of `concepts`, `masks`, `entries`, `fidelity`, `health`.
- `model_id` is the model the surface was recorded from:
  `muse-spark-1.3-contributor` for `concepts`, `entries` and `fidelity`;
  `sam-3.1` for `masks`; `null` for `health`, which is a local liveness
  probe rather than a model call.
- `payload` is the part that validates against `apps/api/app/schemas.py`.
  The label lives at the envelope level because the schemas use
  `extra="forbid"` — a label inside a `ProductEntry` or a `Concept` would
  make the fixture invalid.

## Provenance: who recorded what, from which run

| Pack | `concepts` (per photo) | `masks` (per photo) | `entries` (per photo) | `fidelity` | `health` |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `a` | Live P1 `discover_concepts` on `samples/a/photo{1,2}.jpg` (+ `captions.txt`), captured **2026-10-28** (4+4 phrases). These two files exist only as this capture. | Live P2 `segment_concept`, one request per P1 phrase, captured **2026-10-28** (29 + 7 records). | Stage E subset (4+4 SKUs) of the live **`job-pack-a-v3`** run (window `2026-10-26T18:46:32Z` → `18:52:17Z`), frozen via `samples/a/golden/products.json`. | P4 verdicts for **KK-0004** (pass) / **KK-0006** (fail) from `job-pack-a-v3`. | Live `health_probe()` timestamp; payload is the mock contract (see below). |
| `b` | Live Stage A from **`job-f6dce0c0ee01`**/concepts.json (done, 15 products, 0 errors; window `2026-10-27T21:34:17Z` → `21:39:31Z`). | Same job's `masks/photo_{0,1}.json`, filtered to paired-concept records (13 + 11). | Stage E subset (7+2 SKUs) of the same job's `entries.json`, split by `source_photo_index`. | P4 verdicts for **KK-0010** (pass) / **KK-0001** (fail) from the same job. | Same as pack `a`. |
| `c` | Live Stage A from **`job-3d538290960c`**/concepts.json (done, 19 products, 0 errors; window `2026-10-27T21:39:52Z` → `21:46:42Z`). | Same job's `masks/photo_{0,1}.json`, full sets (22 + 37 records) including the `foreground`-labelled retry records. | Stage E subset (5+5 SKUs) of the same job's `entries.json`, split by `source_photo_index`. | P4 verdicts for **KK-0013** (pass) / **KK-0008** (fail) from the same job. | Same as pack `a`. |

The three `health.json` files carry the UTC timestamp of one real live
`health_probe()` (`client.models.list()`) from the 2026-10-28 capture. The
payload stays exactly `{"ok": true, "mock": true}` — that is the contract
`META_MOCK=true` must serve to the UI; the live branch returns `mock: false`.
`recorded_at` therefore dates the liveness proof, not a response body.

All six previously-frozen payloads were superseded: concepts, masks,
entries and fidelity in one pack all come from the same live run.

## Inventory

3 packs × 9 files = 27 files. Per pack:

- `photo1_concepts.json`, `photo2_concepts.json`
- `photo1_masks.json`, `photo2_masks.json` (every concept's masks for that photo)
- `photo1_entries.json`, `photo2_entries.json`
- `fidelity_pass.json`, `fidelity_fail.json` (both gate outcomes)
- `health.json` (identical payload in all packs, per-pack files keep the
  "every pack carries every surface" check trivial)

Product totals: A 8 (range 5–8), B 9 (range 6–10), C 10 (range 8–12) —
pinned by `test_fixture_counts_match_the_sample_packs`.

## What was filtered

- **Masks.** No mask records are filtered: `masks` fixtures carry the full
  live per-photo record sets in capture order, including records labelled with
  the P2 `"foreground {phrase}"` retry phrase. The 24 Pack C retry records
  dropped by an earlier filter were restored verbatim from the surviving
  `job-3d538290960c` capture (`masks/photo_{0,1}.json`: photo1 back to 22,
  photo2 back to 37), so the mock retry sequence `segment_concept(photo,
  phrase)` → `segment_concept(photo, f"foreground {phrase}")` reaches them
  through the adapter's exact concept matching. `bbox`, `width`, `height`,
  `revision`, `score` and `mask_png_base64` are untouched on every record.
  Entries/concepts/fidelity/health filtering is unchanged.
- **Entries.** Per-photo subsets of the live runs (A 8 of 16, B 9 of 15,
  C 10 of 19), gate-passed/intact items first. Nothing was re-priced,
  re-categorised or re-worded: values are copied verbatim from the live
  Stage E output.

## Price law

Entries carry only `price_hint_idr` / `price_suggestion_idr` (integer rupiah
or null). `price_idr` and `price_via_chat` are seller review decisions and are
absent — a model that returned them would fail `test_fixtures.py`
(the Price Law: only seller-written prices may pre-fill; AI guesses never export).

## Mock-green still proves nothing about the live API

`META_MOCK=true` replays these files; a green mock run proves the wiring, not
the contract. These fixtures are evidence because they were
captured from live responses — the envelope dates and names each capture —
not because the suite passes on them.
