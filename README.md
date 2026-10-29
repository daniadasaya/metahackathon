# Katalog Kilat

*From messy chat photos to a ready-to-sell Meta catalog in one click.*

Social-commerce sellers across Indonesia run their entire business inside WhatsApp, Instagram, and Facebook chats. Their products live in messy multi-item photos on shop tables, display racks, and cardboard boxes, while their prices live in chat captions. Traditional e-commerce catalog tools assume clean, one-product-per-photo studio shots and manual spreadsheet entry — so most sellers never build a catalog at all.

Katalog Kilat starts from the mess sellers already have. Upload multi-product photos and chat captions, and three orchestrated Meta models (`muse-spark-1.3-contributor`, `sam-3.1`, and `muse-image-1.0`) detect every product concept, segment each physical item, generate studio-clean product imagery guarded by a strict visual fidelity gate, and extract bilingual catalog listings ready for Meta Commerce Manager, Instagram, and WhatsApp.

## Multi-Model Architecture (How It Works)

Katalog Kilat coordinates three Meta models across a five-stage pipeline where each model checks the others' work:

1. **Stage A — Vision Concept Discovery (`muse-spark-1.3-contributor`)**: Analyzes each messy multi-product photo to extract foreground product concepts (short English noun phrases), scene context, and verbatim visible text/price tags.
2. **Stage B — Streamed Instance Segmentation (`sam-3.1`)**: Runs concept segmentation (one concept per request, streamed via `meta-sam-parser`) to isolate individual product instances, with automatic `foreground` retry on zero-mask concepts and cross-concept IoU/containment deduplication.
3. **Stage C — Deterministic Cut-out (Pillow)**: Aligns instance masks, filters largest connected components, and extracts 8%-padded alpha product crops without generative distortion.
4. **Stage D — Studio Cleanup & Two-Stage Fidelity Gate (`muse-image-1.0` + `muse-spark-1.3-contributor`)**: Transforms each cut-out into a 1:1 off-white (`#F7F7F5`) studio shot while enforcing a two-stage visual audit against a two-panel ground-truth reference:
   - **Pre-Gate**: Rejects occluded or fragmented crops before spending a generative edit.
   - **Post-Gate**: Verifies `same_product`, `color_match`, and `logo_text_match`. Any edit that alters product geometry, color, or printed branding is rejected and automatically replaced with a deterministic Pillow studio composite.
5. **Stage E — Grounded Catalog Extraction (`muse-spark-1.3-contributor`)**: Uses Meta's Responses API (`POST /v1/responses`) with `web_search` Search Grounding and schema-constrained JSON output (`json_schema`) to generate product titles, bilingual descriptions (`description_id` and `description_en`), brand/condition attributes, and a canonical 14-value Indonesian category taxonomy.

## Core Engineering Guarantees

- **Strict Price Law (Zero Invented Prices)**: AI never invents an exportable price. Only prices explicitly written in the seller's captions or printed on a visible price tag (`price_hint_idr`) may pre-fill the review input. AI market estimates (`price_suggestion_idr`) are displayed strictly as non-binding helper text and can never pre-fill or export. Items without a seller-confirmed price (`price_idr`) or explicit `"price via chat"` (`price_via_chat`) flag block export and highlight the blocking SKUs.
- **Zero Ungated Generative Images**: Every exported image is guaranteed to be either a fidelity-gate-approved `muse-image-1.0` studio shot or a non-generative Pillow studio composite.
- **Multi-Angle SKU Merging**: Sellers photographing the same item from multiple angles (front, back, side) can merge secondary crops into a primary SKU, promote any angle as the primary image, or split them back during mobile review.
- **Offline Demo Safe Mode**: Four pre-bundled sample packs (Packs A–D under `apps/web/public/samples/{a,b,c,d}/golden/`, frozen from real live Meta Model API runs) execute entirely client-side with zero network requests and instant Blob exports.

## Catalog Outputs

1. **Meta Commerce Catalog Feed (`.csv`)**: Spec-compliant 11-column feed (`id,title,description,availability,condition,price,link,image_link,brand,product_type,additional_image_link`) ready for direct import into Meta Commerce Manager (Facebook Shop, Instagram Shop, and WhatsApp Catalog).
2. **Product Photo Archive (`.zip`)**: Studio 1:1 JPEGs, original crop JPEGs, merged angle photos, and the catalog CSV at the archive root.
3. **Instagram Caption Pack (`.txt`)**: Ready-to-paste promotional captions with slugified category hashtags.
4. **WhatsApp Price List (`.txt`)**: Formatted WhatsApp broadcast list (`*bold*`, `_italic_`, `"harga via chat"` support, and direct `wa.me` order links).
5. **Public Product Gallery (`/p/{job_id}/{sku}`)**: Shareable mobile product page with multi-angle photo gallery and one-tap WhatsApp ordering.

## Tech Stack

| Layer | Technology | Description |
| :--- | :--- | :--- |
| **Frontend** | React 19, TypeScript, Vite, TanStack Router & Query, Tailwind CSS | Single Page Application with Lightbox Pasar design system, mobile-first density, bilingual interface (`id`/`en`), and 3 theme states (`system`, `light`, `dark`). |
| **Backend** | Python 3.12, FastAPI, Pydantic v2, Uvicorn | High-performance asynchronous REST backend with strict schema validation and OpenAPI contract generation. |
| **Imaging** | Pillow, `meta-sam-parser` | Non-generative image processing, mask raster decoding, connected-component filtering, alpha extraction, and deterministic compositing. |
| **AI Models** | `muse-spark-1.3-contributor`, `sam-3.1`, `muse-image-1.0` | Meta Model API orchestration for vision concept discovery, instance segmentation, generative cleanup, fidelity inspection, and search-grounded catalog extraction. |
| **Packaging** | Docker, Docker Compose | Unified multi-stage container hosting both compiled frontend static assets and FastAPI backend service. |

## Quickstart

Clone the repository and set up environment configuration:

```bash
cp .env.example .env
```

Set `MODEL_API_KEY` in `.env` (or leave `META_MOCK=true` to run offline using the 27 recorded live fixtures).

### Run with Docker Compose (recommended)

```bash
docker compose -p katalogkilat up -d --build
```

Open `http://localhost:8180` in your browser (health check at `http://localhost:8180/api/health`).

### Run for local development

```bash
# Terminal 1 — FastAPI backend (port 8100)
uvicorn app.main:app --app-dir apps/api --port 8100 --reload

# Terminal 2 — Vite web app (port 4100)
npm --prefix apps/web run dev
```

### Run offline test suite

```bash
docker exec -e META_MOCK=true katalogkilat-api pytest apps/api/tests -q
```

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
| [`docs/prompts-final.md`](docs/prompts-final.md) | Final prompt versions and execution specifications for Meta Model API calls. |
| [`apps/api/openapi.json`](apps/api/openapi.json) | Committed OpenAPI contract specifying all 9 routes and 28 schema components. |
| [`apps/api/tests/fixtures/README.md`](apps/api/tests/fixtures/README.md) | Provenance and validation documentation for the 27 live-recorded mock fixtures. |
| [`apps/web/design/brandkit.md`](apps/web/design/brandkit.md) | Lightbox Pasar design system tokens, typography, and density specifications. |
| [`LICENSE`](LICENSE) | Proprietary License & Hackathon Evaluation Grant terms. |

## License

Proprietary License with Hackathon Evaluation Grant for the Meta Global AI Developer Hackathon 2026 — see [`LICENSE`](LICENSE).
