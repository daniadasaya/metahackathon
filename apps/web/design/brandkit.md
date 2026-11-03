# Katalog Kilat — Brandkit "Lightbox Pasar"

Status: **LOCKED — flat surfaces.** This is the single design specification for `apps/web`. Implement from the tokens and
geometry below; there are no reference images. Implemented across apps/web/src/styles/tokens.css, apps/web/src/styles/components.css, and the five application screens.

Direction in one line: a rounded 2×2 contact-sheet mark with a flash spark, on a warm alabaster / obsidian slate
palette, with flat, hairline-bordered components (no offset shadows, native dim-on-press).

## 1. Logo

The grid is the seller's messy photo set tidied into a contact sheet; the spark is "kilat", the studio flash that lifts
one cell; the emerald dot is "verified". Three soft tiles plus one hard star.

### 1.1 Geometry (viewBox `0 0 96 96`, 8px grid)

| Element | Spec |
| :--- | :--- |
| Tile | 44 × 44, corner radius 22% of tile width = 9.68 → `rx="9.7"` |
| Gutter | 8 (at 24px render: tile 11px, gutter 2px) |
| Tile positions | top-left `(0,0)`, bottom-left `(0,52)`, bottom-right `(52,52)` |
| Spark cell | top-right `(52,0)–(96,44)`, centre `(74,22)`; tips touch the cell edges |
| Spark path | four tips `(74,0) (96,22) (74,44) (52,22)`, quadratic edges whose control points sit 4 units off the centre along the diagonals → sharply concave sides |
| Verified dot | `r=8` at `(90,90)`, 3px ring in the surface colour; **hidden below 32px render size** |
| Clear space | 1/2 tile (22 units) on all sides |
| Minimum size | 16px (mark only). Dot appears from 32px; wordmark lockup from 120px wide |

```svg
<svg viewBox="0 0 96 96" xmlns="http://www.w3.org/2000/svg" overflow="visible" role="img" aria-label="Katalog Kilat">
  <rect x="0"  y="0"  width="44" height="44" rx="9.7" fill="TILE"/>
  <rect x="0"  y="52" width="44" height="44" rx="9.7" fill="TILE"/>
  <rect x="52" y="52" width="44" height="44" rx="9.7" fill="TILE"/>
  <path d="M74 0 Q78 18 96 22 Q78 26 74 44 Q70 26 52 22 Q70 18 74 0 Z" fill="SPARK"/>
  <circle cx="90" cy="90" r="8" fill="DOT" stroke="RING" stroke-width="3"/>
</svg>
```

The spark keeps **sharp tips** (no stroke-linejoin rounding, no corner radius). The contrast between four soft shapes
and one hard star is the "flash" moment; rounding the star would make it a generic sparkle.

### 1.2 Fill tokens

One tangerine and one emerald per theme, used identically in the app and in standalone files (favicon, OG image, app
tile). There is no second value.

| Part | Light canvas `#FAF7F2` | Dark canvas `#121316` | Token |
| :--- | :--- | :--- | :--- |
| 3 tiles | `#B34300` | `#FF8533` | `--accent-secondary` |
| Spark | `#F5A524` | `#F5A524` | `--accent-primary` |
| Verified dot | `#0B7A52` | `#34D399` | `--price-verified` |
| Dot ring | `#FAF7F2` | `#121316` | `--bg-canvas` |
| Wordmark | `#1B1A17` | `#FAF7F2` | `--text-primary` |

- The in-app `<Logo>` component fills from tokens only. Standalone SVG files cannot read CSS
  variables, so they carry the same hex values from this table, one file per theme.
- The spark is intentionally fill-only on light (`#F5A524` on `#FAF7F2` is 1.91:1). Logotypes are exempt from WCAG 1.4.11
  and the star is identified by shape, not by colour against the page. Where the spark must hold contrast on a light
  page (OG card), place the mark on the obsidian app tile (spark 9.10:1).
- On the obsidian app tile, tangerine `#FF8533` tiles are 7.66:1 and the spark 9.10:1. Tile and spark are only 1.19:1
  apart from each other, so the gutter and silhouette — not the hue — separate them; do not close the gutter.

### 1.3 Lockup and wordmark

- Wordmark: **Outfit ExtraBold (800)**, tracking −1.5%, text `Katalog Kilat` (title case, one space).
- Horizontal lockup: mark height = cap-height × 1.55; gap = 1/2 tile; wordmark vertically centred on the mark. A stacked
  two-line variant is for square slots only; the header uses a single line.
- 48px header: 20px mark (no dot), 8px gap, 15px wordmark (Outfit 800, `base`) beside it (fits at 360px).
- App tile: obsidian `#121316` squircle (radius 22%), mark centred at 62% of tile width, 2px solid bottom edge in `#000`
  at 40% (the standalone app icon, not UI chrome). On dark pages the tile gets a 1px `--border-strong` ring.

## 2. Colour tokens (19 per theme)

Exact names from the token list below. HEX + OKLCH. Ratios are WCAG 2.x, computed against the token's primary surface:
**Light: `--bg-surface #FFFFFF`; Dark: `--bg-surface #1B1D22`**. Text must be ≥ 4.5:1; UI boundaries and large fills ≥ 3:1.
"FILL" = never text or a 1px line on that theme.

### Light (Warm Alabaster)

| Token | HEX | OKLCH | Ratio / role |
| :--- | :--- | :--- | :--- |
| `--bg-canvas` | `#FAF7F2` | `oklch(97.7% 0.007 81)` | page; text-primary on it 16.28 |
| `--bg-surface` | `#FFFFFF` | `oklch(100% 0 0)` | card; text-primary 17.40 |
| `--bg-elevated` | `#FFFFFF` | `oklch(100% 0 0)` | sheet / popover (edge + shadow carry elevation) |
| `--bg-subtle` | `#F1EBE0` | `oklch(94.2% 0.016 83)` | chip / skeleton; text-primary 14.67 |
| `--text-primary` | `#1B1A17` | `oklch(21.8% 0.006 92)` | 17.40 |
| `--text-secondary` | `#4C4943` | `oklch(40.6% 0.011 85)` | 8.97 |
| `--text-muted` | `#615D55` | `oklch(48.0% 0.014 85)` | 6.55 (5.52 on subtle) |
| `--border-subtle` | `#E6DFD1` | `oklch(90.5% 0.020 85)` | decorative divider, 1.24 — never the only boundary of a control |
| `--border-strong` | `#857D6D` | `oklch(59.2% 0.026 85)` | inputs, secondary-button border; 4.08 (3.81 on canvas) |
| `--accent-primary` | `#F5A524` | `oklch(78.2% 0.158 72)` | **FILL** (1.91 on canvas) — buttons, active chip, spark |
| `--accent-primary-fg` | `#1B1A17` | `oklch(21.8% 0.006 92)` | label on saffron 8.53 (**white on saffron fails**) |
| `--accent-secondary` | `#B34300` | `oklch(53.1% 0.158 43)` | text-safe tangerine: links, tiles, selected borders; 5.65 (5.28 canvas, 4.76 subtle) |
| `--status-success` | `#0B7A52` | `oklch(51.3% 0.111 161)` | 5.36 (5.01 canvas, 4.52 subtle) |
| `--status-warning` | `#8F5B00` | `oklch(51.6% 0.111 71)` | 5.73 |
| `--status-danger` | `#C0301F` | `oklch(53.3% 0.183 31)` | 5.70 |
| `--price-verified` | `#0B7A52` | `oklch(51.3% 0.111 161)` | 5.36 |
| `--price-ai-hint` | `#67635B` | `oklch(50.1% 0.013 85)` | 5.98 — visibly quieter than `--text-secondary` (8.97) |
| `--gate-pass` | `#0B7A52` | `oklch(51.3% 0.111 161)` | 5.36 |
| `--gate-fallback` | `#A04A00` | `oklch(51.0% 0.134 51)` | 6.04 |

### Dark (Obsidian Slate)

| Token | HEX | OKLCH | Ratio / role |
| :--- | :--- | :--- | :--- |
| `--bg-canvas` | `#121316` | `oklch(18.7% 0.006 271)` | page; text-primary on it 17.38 |
| `--bg-surface` | `#1B1D22` | `oklch(23.1% 0.010 268)` | card; text-primary 15.78 |
| `--bg-elevated` | `#25282F` | `oklch(27.7% 0.014 267)` | sheet / popover |
| `--bg-subtle` | `#2C3038` | `oklch(30.9% 0.015 264)` | chip / skeleton; text-primary 12.38 |
| `--text-primary` | `#FAF7F2` | `oklch(97.7% 0.007 81)` | 15.78 |
| `--text-secondary` | `#C7C8CC` | `oklch(83.3% 0.006 275)` | 10.09 |
| `--text-muted` | `#9A9DA5` | `oklch(69.6% 0.012 269)` | 6.21 (4.88 on subtle) |
| `--border-subtle` | `#2A2D34` | `oklch(29.7% 0.013 267)` | decorative divider |
| `--border-strong` | `#6D727D` | `oklch(55.2% 0.018 266)` | inputs, secondary-button border; 3.50 (3.85 canvas, 3.06 elevated) |
| `--accent-primary` | `#F5A524` | `oklch(78.2% 0.158 72)` | 8.26 — text-safe on dark |
| `--accent-primary-fg` | `#121316` | `oklch(18.7% 0.006 271)` | label on saffron 9.10 |
| `--accent-secondary` | `#FF8533` | `oklch(74.1% 0.173 50)` | 6.95 — tiles, links |
| `--status-success` | `#34D399` | `oklch(77.3% 0.153 163)` | 8.77 |
| `--status-warning` | `#F5A524` | `oklch(78.2% 0.158 72)` | 8.26 |
| `--status-danger` | `#FF7A66` | `oklch(73.1% 0.166 30)` | 6.61 |
| `--price-verified` | `#34D399` | `oklch(77.3% 0.153 163)` | 8.77 |
| `--price-ai-hint` | `#9A9DA5` | `oklch(69.6% 0.012 269)` | 6.21 — quieter than `--text-secondary` (10.09) |
| `--gate-pass` | `#34D399` | `oklch(77.3% 0.153 163)` | 8.77 |
| `--gate-fallback` | `#FF8533` | `oklch(74.1% 0.173 50)` | 6.95 |

Rules:

- No component colours text with `--accent-primary` in light; text-level accents use `--accent-secondary`.
- Colour is never the sole signal — price and gate states carry an icon and a label.
- There are no brand-only colours outside these tables. Every hex used anywhere (components, SVG files) appears above.

## 3. Typography

Self-hosted WOFF2 via `@fontsource-variable/*` (Latin subset; same-origin, so it works offline). Three packages,
each needing one design-log line (see the checklist in §5): `@fontsource-variable/outfit`,
`@fontsource-variable/plus-jakarta-sans`, `@fontsource-variable/jetbrains-mono`. Inter is not used.

| Role | Font | Where |
| :--- | :--- | :--- |
| Display | **Outfit** 700–800, tracking −0.02em | wordmark, screen/section titles, button labels |
| UI sans | **Plus Jakarta Sans** 400/500/600/700 | body, inputs, chips, helper text |
| Mono | **JetBrains Mono** 500/700 | money, SKU, CSV preview |

Money and SKU: `font-variant-numeric: tabular-nums slashed-zero; font-feature-settings: "tnum" 1, "zero" 1;`. Body copy
does not set `slashed-zero`. `Rp150.000` on the Review Card is JetBrains Mono 700 so the figure is the loudest thing on
the card. The `Rp…` string always comes from the one formatter, `lib/format.ts`.

### One type scale (compacted to fintech density) — content width 328px at 16px gutters, identical on every screen

| Step | Size / line | Weight | Tracking | Use |
| :--- | :--- | :--- | :--- | :--- |
| `xs` | 11 / 14 | 600 | +0.02em | chips, pills, SKU, captions |
| `sm` | 13 / 18 | 500 | 0 | helper text (`Saran AI: …`), labels, ghost/compact buttons |
| `base` | 15 / 22 | 500 | 0 | body (root font size); wordmark (Outfit 800). Text inputs stay 16px (below) |
| `lg` | 16 / 22 | 700 | −0.015em | product name, Outfit |
| `xl` | 18 / 24 | 800 | −0.02em | section title, Outfit |
| `2xl` | 22 / 28 | 800 | −0.02em | screen title, Outfit; `max-w-[18ch]` on Upload keeps it ≤ 2 lines at 360 |
| `price` | 17 / 22 | 700 | 0 | price input value, JetBrains Mono |
| `price-sm` | 15 / 20 | 700 | 0 | Preview tile price (two per row), JetBrains Mono |

Text inputs keep a computed 16px font size (the iOS focus-zoom floor) even though `base` is 15px. Primary button label is
15 / 20 Outfit 700, secondary and success 14 / 20, ghost/compact 13 / 18. Stage-tile letters are 17 / 22; sample-pack tags are 13 / 18 Outfit 700 and pack names 15 / 22, so the pack card reads as body copy.
(Outfit 800). Header mark 20px. Negative tracking applies to `lg`, `xl`, `2xl` only. Spacing rhythm: 4 / 8 / 12 / 16 / 20 / 24
only (Tailwind's spacing unit is pinned to 4px, independent of the 15px root); section gaps 16–24, intra-group 4–8, card
padding 12–16. Optical exceptions, kept on purpose: 2px text insets under inputs (`px-0.5`), a 6px icon gap in the contact line, and 2.5px chevron strokes. Every font size in `components.css` and the TSX reads this table; there is no
`text-[Npx]` / `leading-[N]` literal. Screen titles are one size on all five screens; no screen has its own scale.

### Desktop staging (≥ 768px)

Additive layer; below 768px none of it applies, and the mobile path is unchanged. At 768px and wider the app is shown
as an iPhone mockup on a blank canvas: `--bg-canvas` fills the whole viewport (Warm Alabaster in light, Obsidian Slate in
dark), with no glows, no motif and no card. The phone is centred, `aspect-ratio: 433 / 882`, `height: min(882px, 100dvh − 48px)`,
with no transform and no scaling. Its bezel and Dynamic Island come from the Magic UI iPhone geometry (MIT, paths verbatim),
and they are coloured with the device tokens in `tokens.css`, not with brand tokens. The screen rectangle is 4.907% left,
2.182% top, 89.953% wide, 95.635% tall, with an elliptical corner radius of `14.31% / 6.61%`.

The app scrolls inside the screen, and the window does not scroll. The screen clips and paints its contents (`contain: paint`),
so the fixed toast and the skip link stay inside it. The header stays sticky under a faux status strip. That strip is
`--kk-status-inset` tall, which is 5.93% of the phone height. It holds 9:41 and signal, Wi‑Fi and battery glyphs, all
`aria-hidden`, and it is opaque canvas, so scrolled content passes under it. The island sits inside the strip's band.
The status-inset variable feeds three things: the strip height, the scroller clearance, and the header's sticky top.
Below 768px the variable is `0px`, and the strip and frame are hidden.

Devices stay in a single shell at every width, so crossing 768px never remounts the app. The shadow is the one soft lift the
phone carries (see §4).

## 4. Flat components

| Aspect | Spec |
| :--- | :--- |
| Radius | `--r-sm 10px` (inner photo crop, inputs) · `--r-md 16px` (cards) · `--r-lg 22px` (sheets) · buttons 12px, icon buttons 10px · chips 999px |
| Border | 1px `--border-subtle` hairline on decorative surfaces (cards, pending stage tiles). Controls take `--border-strong`, because a control's boundary must reach 3:1 against its surface (WCAG 1.4.11): chips, the locale and theme controls, inputs 1.5px, secondary button 1px. Status outlines (failed tile 2px, price states) keep their weight |
| **Surfaces & press** | Flat: no offset (`0 Npx 0`) shadow anywhere. Press feedback is a native dim, `opacity: .75` over 120ms (`.kk-tactile:active`), no movement, removed transition under reduced motion. Soft diffuse shadows only where elevation is real: sticky bar (`0 -8px 24px -12px`, 14% `--text-primary`), toast (`0 8px 24px -12px`, 18%), desktop phone frame (`0 24px 64px -24px`, 22%, ≥ 768px only) |
| Primary button | 48px, label 15 / 20, `--accent-primary` fill, `--accent-primary-fg` label in Outfit 700, flat fill, 1.5px transparent edge. Disabled: `--bg-subtle` fill, `--text-secondary` label (7.56:1 light / 7.92:1 dark), 1.5px `--border-strong` edge (4.08:1 light / 3.50:1 dark against the bar), still a visible button. Upload uses the native `disabled` attribute; Review and Export use `aria-disabled="true"` so they stay focusable and can name the blocker |
| Secondary / success button | 44px, label 14 / 20, `--bg-surface` fill (success: `--status-success`), 1px `--border-strong`, flat. Ghost / compact: 44px, label 13 / 18 |
| Chip | 32px pill, full-width variant 38px (tap area ≥ 44px via a `::after` overlay); selected = `--accent-primary` fill + `--accent-primary-fg` label + check |
| Focus ring | 3px `--text-primary`, offset 2px |
| Motion | `transform` and `opacity` only, ≤ 160ms; `prefers-reduced-motion` removes all |
| Tap targets | ≥ 44px hit area everywhere (a visual 32–36px control gets a `::after` overlay), 48px for primary actions. Text inputs 44px, list rows 52–60px, stage tiles 40px, price input 44px, locale segments 44px tall with a 36px visual, theme select 44px, sticky bar padding 8px, switch 42×24 with a 44px hit area |

### Review Card anatomy (360px, card width 328px)

Card: 16px radius, 1px `--border-subtle`, flat, 16px padding, 12px gaps between blocks. Top row: a 72×72 photo (10px crop radius) beside the product name (a 2-row textarea, Outfit 16 / 700) and the gate pill with the SKU. Price: a 44px `--r-sm` input in JetBrains Mono 700 17, pre-filled ONLY from `price_hint_idr`. Below it, the helper line (`Saran AI: … — periksa lagi`, 13 / 18, `--price-ai-hint`) and the full-width 38px "Harga via chat" chip (its tap area is ≥ 44px through a `::after` overlay). Then the in-stock and included switches, the optional merge selector, and the "Detail produk" disclosure.

```
┌ 16px radius, 1px --border-subtle, flat, 16px padding ──────────────┐
│ ┌────────────┐  Kaos Polos Katun Hitam  (name, 2 rows)            │
│ │ 72×72      │  ✓ Lolos cek (--gate-pass)   KK-0001               │
│ └────────────┘                                                    │
│ ┌──────────────────────────────────────┐                          │
│ │ Rp150.000                            │  44px input, mono 700 17 │  pre-filled ONLY from price_hint_idr
│ └──────────────────────────────────────┘                          │
│ Saran AI: Rp165.000 — periksa lagi       13/18, --price-ai-hint   │  price_suggestion_idr; never in the input
│ ( Harga via chat )                       full-width 38px chip     │  price_via_chat
│ ─ Stok tersedia ........................... [switch]              │
│ ─ Masukkan ke katalog ..................... [switch]              │
└───────────────────────────────────────────────────────────────────┘
```


The mark lives once in the sticky app header (20px mark + Outfit 800 15px wordmark), never on each card. The step rail sits under the header, scrolls with the page, and names the five steps (§4 Step rail).

States — the Price Law applies in full:

- **Confirmed / hint accepted:** input border `--price-verified` 1.5px + check disc.
- **No price, not via chat:** input border `--status-warning` 1.5px, helper "Belum ada harga"; the export CTA in the
  summary bar stays disabled and reads `{n} produk siap · {m} belum ada harga`.
- **`price_via_chat`:** the price field is not rendered; the full-width chip is the only control on that row and reads selected (`--bg-subtle`). The card counts as ready. No `Rp0` is shown or stored.
- **Gate fallback:** pill in `--gate-fallback`, "Foto standar" with a layers icon. Never hidden.
- `Saran AI: Rp… — periksa lagi` never enters the input value and never enters the POST body as a price.
- All user-visible strings come from the `id`/`en` dictionaries; the copy shown here is `id` illustration only.

### Stage A–E strip (360px)

A full-width stepper: five 40×40 chunky tiles spread edge to edge of the card, with the connector rules flexing to fill the gaps (min 6px, 4px tall); the done-state summary row sits under a hairline `--border-subtle` rule. 11px tile radius, stage letter in Outfit 800 17px.

| State | Fill | Content |
| :--- | :--- | :--- |
| Done | `--status-success` / `--gate-pass` | check icon (colour is not the only signal) |
| Active | `--accent-primary` | letter in `--accent-primary-fg`; saffron fill alone marks the active stage (no pulse, no edge) |
| Pending | `--bg-subtle` | letter in `--text-muted`, 1px `--border-subtle` |
| Failed | `--status-danger` outline | retry icon; the retry button is per item |

The active stage's name appears as a caption under the row; connectors are 4px rules in the same state colours.

### Progress: done state

When a job finishes, the Progress screen adds a grid of the tidied photos under the stage strip: three square columns with 8px gaps, each photo from `clean_url` with its SKU in JetBrains Mono beneath it. The grid is read-only and uses the `ProductPhoto` fallback, so a missing file shows a token placeholder, never a broken image. The Review action sits in the sticky bar below it.

### Export files

Each export file is a row: a card at least 56px tall with a 40px icon tile and a 12px radius (the button radius). Before the export, the tile is `--bg-subtle` with its icon and the download control is a disabled 44px icon button. After the export, the tile turns `--status-success` with a check, and the download is a 44px success button (`--status-success` fill, `--bg-surface` icon). The file name sits under the label in JetBrains Mono.

### Upload fit budget (360×800, empty state, measured)

Measured in a real browser on 2026-11-03 at 360×800, light, empty state, with the sample packs as thumbnails: header 49 (48 + 1px rule) · step rail 37 · title 28 · lead 44 (two lines) · drop zone 148 · sample section 389 (heading, hint, 2 × 2 photo cards at 3:2) · caption `<details>` 46 · sticky bar 91 (CTA 48 + helper line). The page is 924px tall at 360×800: the sticky bar sits on the fold and the sample grid scrolls beneath it, with no void between the caption and the bar. A resume card measures 154px and sits above the drop zone. Re-measure in a browser at 360×800, using the same selectors (`header`, `.kk-rail`, `h1`, `.kk-drop`, `section[aria-labelledby="samples-title"]`, `details.kk-details`, `.kk-bar`), after any change to this screen.

### Step rail (all widths)

Five named steps on one row, directly under the header, scrolling with the page (not sticky, so the header keeps one row): `Foto · Proses · Tinjau · Pratinjau · Ekspor` (`EN`: `Photos · Process · Review · Preview · Export`). Each step is a 3px bar over an 11 / 14 label, 62px wide at 360px. Done steps use `--accent-secondary` and a screen-reader word "selesai" / "done"; the current step uses `--accent-primary` and bold ink text with `aria-current="step"`; waiting steps use `--border-subtle` and read "belum" / "to do". Labels never share a row, so they cannot collide.

### Header controls

Lockup (wordmark is one line, `whitespace-nowrap`, 93px wide), then two controls, all 44px tall and never wrapping at 360px (header 49px, right group ends at 344px):

- **Language:** a two-option segmented control. Visible codes `ID` / `EN` are the accessible names' first words (`ID · Bahasa Indonesia`), so the label matches what is shown (WCAG 2.5.3). Active segment is `--text-primary` fill with `--bg-canvas` label.
- **Theme:** a labelled native `<select>` with the active mode's icon and name (`Sistem` / `Terang` / `Gelap`, or `System` / `Light` / `Dark`). Keeps all three states, persists in `kk:theme`, and `system` follows the OS live.

### Disabled and enabled primary action

The disabled primary keeps the label readable: `--bg-subtle` fill, `--text-secondary` label (7.56:1 light, 7.92:1 dark), 1.5px transparent edge. It reads as a button that is not ready yet, not a broken bar. The enabled primary is `--accent-primary` with `--accent-primary-fg` (8.53:1 light, 9.10:1 dark).

### Sample pack tiles

Four photo cards in a 2 × 2 grid. Each shows a real product photo from `public/samples/thumbs/{a..d}.jpg` (640 × 480, cropped from the first photo of each pack, static and local). The card is a 3:2 image with a 10px (`--r-sm`) corner, a 1px `--border-subtle` edge, a 13 / 18 bold tag, and a 15 / 22 pack name under it. Selected: a 2px `--accent-secondary` outline offset 2px around the photo and a check disc in the top right; the tag turns `--accent-secondary`, and the name goes to `--text-primary` bold.

### Drop zone and resume card

The drop zone is one dashed `--border-strong` card, 120px tall, with an icon tile (saffron until photos are picked), a 18 / 24 Outfit title and a 13 / 18 hint. Once photos exist it collapses to a single row with a solid border. The resume card holds two actions only, "Lanjutkan" (secondary) and "Mulai baru" (ghost), on a `--bg-subtle` surface.

### Toast

Errors show inside the sticky bar, directly above the primary action, as a `role="alert"` card with a 1.5px `--status-danger` edge. It never covers the action. It dismisses itself after 6 seconds and has a close button. The card carries the one soft lift the spec allows for floating layers (see §4).


## 5. Implementation checklist

Status as of the owner-directed redesign. Items marked **open** are real gaps, not done work.

1. Design log: this direction plus one line each for the three `@fontsource-variable/*` packages. **Open:** the design-log entry is recorded by whoever owns the project record; this file does not carry it.
2. `public/brand/`: SVG files drawn from §1.1 using the §1.2 hex values — light mark, dark mark, app tile, favicon, OG. **Open:** not drawn. The app renders the mark from `Logo.tsx` tokens, so the header does not depend on these files.
3. `src/components/Logo.tsx`: tokens-only `<Logo>`; `src/styles/tokens.css`: §2 copied verbatim (`:root` / `[data-theme="dark"]`). **Done:** `tokens.css` is unchanged by the redesign.
4. A grep for `#[0-9a-fA-F]{3,6}` in `src/components/**` must return nothing, including the Logo component. **Done:** zero matches after the redesign.
5. Both locales complete: `id` and `en` have identical key sets, and every `t('…')` key in the code resolves. The compile-time check in `lib/locale/check.ts` enforces the key sets. **Done.**
6. Browser evidence at 360×800 and at the desktop staging, both themes, both locales, for all five screens. **Done:** captured to `apps/web/.qa/shots/` (git-ignored). The harness scripts were removed after capture; the screenshots and run logs stay.

