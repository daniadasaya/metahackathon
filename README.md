# Katalog Kilat

Katalog Kilat turns messy multi-product seller photos into a ready-to-sell Meta catalog in one click.

## Status

A single production Docker container serves both the FastAPI backend and the compiled Vite + React Single Page Application (SPA) on `127.0.0.1:8180`, with all 258 offline automated tests passing. Canonical pydantic v2 product data contracts are locked in `apps/api/app/schemas.py`, published to `apps/api/openapi.json` (9 routes, 28 schema components), and synchronized to the client artifact `apps/web/src/lib/types.ts` with compile-time assertions in `apps/web/src/lib/types.check.ts`. All 27 mock fixture files under `apps/api/tests/fixtures/` are frozen from real live Meta Model API responses (concepts, segmentation masks including `foreground` retry phrases, catalog entries, and fidelity verdicts), locked by automated regression tests. Nine active API routes serve health checks, job creation and polling, catalog file exports, per-SKU cleanup retry and method toggling, and public product serving. The web interface provides Screens 1–2 (photo upload, general and per-photo captions, sample pack picker, 5-stage pipeline progress stepper, and independent per-item error recovery) and Screens 3–4 (product review, strict Price Law enforcement, multi-photo per SKU merge/unmerge/promote angle shots, SKU-named export blocking, browser refresh persistence, and 2-column store preview) built with TanStack Router, compact fintech density, flat hairline-bordered surfaces (`Lightbox Pasar` brandkit) with no hard offset shadows, a full-width stage progress ribbon, bilingual UI (`id` default / `en`), and three theme states (`system`, `light`, `dark`). The catalog exporter (`apps/api/app/exporter.py`) and public routes serve four real downloadable outputs (an 11-column Meta Commerce CSV feed with populated `additional_image_link`, a product photo ZIP packing studio shots, crop shots, merged angle shots, and the root CSV, an Instagram caption pack with slugified hashtags, and a WhatsApp price list with WhatsApp formatting and `wa.me` order links) alongside public product pages `/p/{job_id}/{sku}` and image endpoints `/p/{job_id}/{sku}.jpg`. Client-side **Demo Safe Mode** bundles four golden sample packs (`a`, `b`, `c`, `d` under `apps/web/public/samples/{a,b,c,d}/golden/`) frozen from real live Meta Model API runs — including Sample Pack D demonstrating multi-angle SKU merging across opposite-side product photos — operating 100% offline with zero `/api/` requests and instant Blob downloads.

## How It Works

Katalog Kilat orchestrates three Meta models across a five-stage pipeline:

1. **Stage A — Concept Discovery (`muse-spark-1.3-contributor`)**: Vision inference identifies distinct foreground product concepts, scene context, and verbatim visible text/price tags.
2. **Stage B — Instance Segmentation (`sam-3.1`)**: Generates instance segmentation masks per concept (one short noun phrase per request, automatic `foreground` retry on zero-mask concepts, and cross-concept IoU/containment deduplication).
3. **Stage C — Cut-out (Pillow)**: Deterministic mask alignment, largest-connected-component cleanup, and 8%-padded alpha crop extraction without a generative model.
4. **Stage D — Studio Cleanup & Two-Stage Fidelity Gate (`muse-image-1.0` + `muse-spark-1.3-contributor`)**: Produces a 1:1 studio product image guarded by a two-stage fidelity gate against a two-panel ground-truth reference (`crops/{sku}-ref.png` evaluated by `muse-spark-1.3-contributor`): a pre-gate rejects defective cut-outs before calling Muse Image, and a post-gate evaluates three booleans (`same_product`, `color_match`, `logo_text_match`), falling back automatically to a deterministic Pillow studio composite whenever an edit alters the product.
5. **Stage E — Structured Catalog Extraction (`muse-spark-1.3-contributor`)**: Extracts structured product data via `POST /v1/responses` with `web_search` Search Grounding and `json_schema` structured output, pinning categories to a canonical 14-value Indonesian taxonomy (`Handphone & Tablet`, `Aksesoris HP`, etc.), generating bilingual descriptions (`description_id` / `description_en`), attributes, and non-binding AI price suggestions (`price_suggestion_idr`) while never populating `price_hint_idr` from web search results.

## Outputs

1. **Meta Commerce Catalog Feed (CSV)**: Canonical 11-column catalog feed compliant with Meta Commerce Manager specifications, including multi-angle image URLs in `additional_image_link`.
2. **Product Photo Pack (ZIP)**: Clean 1:1 studio images, original crops, merged angle photos, and the catalog CSV at the archive root.
3. **Instagram Caption Pack (TXT)**: Ready-to-post promotional captions for each product with slugified hashtags.
4. **WhatsApp Price List (TXT)**: Formatted price list summary with WhatsApp bold/italic styling and direct `wa.me` order links.

Public product pages are served at `/p/{job_id}/{sku}` with individual studio image endpoints at `/p/{job_id}/{sku}.jpg`.

## Tech Stack

| Layer | Technology | Description |
| :--- | :--- | :--- |
| **Web UI** | React 19, TypeScript, Vite, Tailwind CSS | Single Page Application with TanStack Router and Query, Lightbox Pasar design system, bilingual support (`id`/`en`), and 3 theme states. |
| **API Backend** | FastAPI, Python 3.12, Uvicorn, Pydantic v2 | High-performance asynchronous REST backend with strict schema validation and OpenAPI generation. |
| **Imaging** | Pillow (PIL), meta-sam-parser | Non-generative image processing, mask raster decoding, connected-component filtering, alpha extraction, and deterministic compositing. |
| **AI Models** | `muse-spark-1.3-contributor`, `sam-3.1`, `muse-image-1.0` | Meta Model API orchestration for vision concept discovery, instance segmentation, generative cleanup, fidelity inspection, and search-grounded catalog extraction. |
| **Deployment** | Docker, Docker Compose | Unified multi-stage container hosting both compiled frontend static assets and FastAPI API service. |

## Running Locally

Clone the repository and copy the example environment configuration:

```bash
cp .env.example .env
```

### Production Container (Unified)

Run the unified production container hosting both API and SPA:

```bash
docker compose -p katalogkilat up -d --build
curl http://127.0.0.1:8180/api/health
curl http://127.0.0.1:8180/review
```

The `/api/health` endpoint returns `{"ok":true,"mock":false}` when `.env` sets `META_MOCK=false`, or `{"ok":true,"mock":true}` via the `${META_MOCK:-true}` fallback when `.env` is absent. The `/review` endpoint serves the SPA shell `index.html` containing `<div id="root">`.

### Bare-Metal Development

Run backend and frontend in separate terminals:

```bash
# Vite web UI (port 4100)
npm --prefix apps/web run dev

# FastAPI with uvicorn (port 8100)
uvicorn app.main:app --app-dir apps/api --port 8100 --reload
```

Port allocation summary:
- `4100`: Vite development server
- `4180`: Vite preview server
- `8100`: Bare uvicorn FastAPI development server
- `8180`: Unified production container loopback port

## Repository Structure

```text
metahackathon/
├── apps/
│   ├── api/                      # FastAPI backend service, pipeline, schemas, tests, and fixtures
│   └── web/                      # Vite + React SPA, components, routes, locales, and sample assets
├── docs/                         # Model prompts and specifications
├── .dockerignore                 # Container build exclusions
├── .env.example                  # Environment configuration template
├── .gitignore                    # Git tracking exclusions
├── docker-compose.yml            # Unified container service definition
├── LICENSE                       # Proprietary license and Hackathon Evaluation Grant
└── README.md                     # Project documentation and run instructions
```

## Documentation

| Document | Description |
| :--- | :--- |
| `docs/prompts-final.md` | Final prompt versions and execution specifications for Meta Model API calls. |
| `apps/api/openapi.json` | Committed OpenAPI contract specifying all 9 routes and 28 schema components. |
| `apps/api/tests/fixtures/README.md` | Provenance and validation documentation for the 27 live-recorded mock fixtures. |
| `apps/web/design/brandkit.md` | Lightbox Pasar design system tokens, typography, and density specifications. |
| `LICENSE` | Proprietary License & Hackathon Evaluation Grant terms. |

## License

Proprietary License with Hackathon Evaluation Grant for the Meta Global AI Developer Hackathon 2026 — see `LICENSE`.
