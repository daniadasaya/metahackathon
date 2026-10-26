# Katalog Kilat

Katalog Kilat mengubah foto produk berantakan menjadi katalog Meta siap jual.

---

## Status

Wadah container melayani API sekaligus aplikasi web (*Single Page Application*) hasil kompilasi statis pada `127.0.0.1:8180` dengan seluruh fitur siap pakai dan 258 pengujian otomatis luring lulus. Kontrak skema data produk kanonik berbasis pydantic v2 telah terkunci di `apps/api/app/schemas.py`, dipublikasikan penuh ke `apps/api/openapi.json` (9 rute, 28 komponen skema), dan disinkronkan ke artefak klien `apps/web/src/lib/types.ts` dengan asersi waktu kompilasi di `apps/web/src/lib/types.check.ts`. Seluruh 27 berkas fixture mock untuk paket sampel di `apps/api/tests/fixtures/` dibekukan dari respons live Meta Model API (konsep, mask segmentasi termasuk frasa coba-ulang, entri katalog, dan fidelitas), serta dikunci oleh pengujian regresi otomatis. Sembilan rute API aktif melayani pemeriksaan kesehatan, pembuatan dan pemantauan pekerjaan, ekspor berkas, coba-ulang serta pengalihan pembersihan per-SKU, dan penyajian publik. Antarmuka web Layar 1–2 (unggah foto, takarir umum & per foto, pemilihan sampel, pemantauan progres pipa 5 tahap, dan pemulihan error mandiri) serta Layar 3–4 (tinjauan produk, penegakan hukum harga ketat, pemblokiran ekspor ber-SKU, persistensi penyegaran browser, serta pratonton toko kisi 2 kolom) beroperasi dengan perutean TanStack Router, densitas fintech ringkas, permukaan datar profesional bergaris batas *hairline* tanpa bayangan offset keras, pita tahap selebar penuh kartu, dukungan dwibahasa (ID bawaan / EN), tiga keadaan tema (*system*, *light*, *dark*), serta interaksi langsung ke titik akhir riil di mana ketiadaan backend memunculkan pemberitahuan berlabel eksplisit. Modul pengekspor katalog (`apps/api/app/exporter.py`) beserta rute ekspor dan penyajian publik melayani empat berkas keluaran nyata (umpan CSV Meta Commerce 11 kolom dengan kolom `additional_image_link`, berkas ZIP gambar produk yang mengemas seluruh sudut foto bersama CSV di root, paket takarir Instagram dengan slugifikasi tagar, dan daftar harga WhatsApp dengan pemformatan tebal/miring), rute publik `/p/{job_id}/{sku}.jpg` (citra berlatar putih kualitas 88) dan `/p/{job_id}/{sku}` (halaman rincian produk HTML yang disajikan server dengan galeri multi-sudut dan tautan langsung `wa.me`), layar ekspor (`ExportScreen.tsx`) dengan persistensi data penjual, serta penegakan hukum harga ketat (larangan transmisi saran harga AI, penolakan ekspor produk tanpa harga dengan status 400). Mode Demo Aman (*Demo Safe Mode*) dan bundel keluaran contoh (*golden outputs*) terpasang di `apps/web/public/samples/{a,b,c,d}/golden/` dan beroperasi penuh di sisi klien dengan nol ketergantungan jaringan maupun panggilan API; mencakup 4 paket sampel A, B, C, dan D (paket D menguji fitur penggabungan multi-foto per SKU dengan sudut foto gabungan dan kolom `additional_image_link` terisi) serta ekspor golden yang dirender ulang melalui modul pengekspor riil dengan kategori kanonis. Pipa pemrosesan 5 tahap (Tahap A: Konsep dengan `muse-spark-1.3`, Tahap B: Segmentasi dengan `sam-3.1`, Tahap C: Pemotongan dengan Pillow, Tahap D: Pembersihan Citra dengan `muse-image-1.0`, Tahap E: Ekstraksi Katalog dengan `muse-spark-1.3`) teruji pada alur live penuh. Tahap D mengoperasikan gerbang kesetiaan (*fidelity gate*) dua tahap berbasis acuan dasar dua panel `crops/{sku}-ref.png` oleh `muse-spark-1.3` (pra-gerbang menolak potongan cacat sebelum pemanggilan Muse Image; pasca-gerbang memeriksa tiga boolean kecocokan produk, warna, dan logo/teks citra bersih terhadap acuan dua panel tersebut, dengan penolakan pada salah satu gerbang mengalihkan SKU secara otomatis ke cadangan komposit deterministik Pillow, serta divalidasi ulang saat ekspor oleh `validate_image_gate`). Tahap E berjalan di atas `POST /v1/responses` dengan Search Grounding `web_search` dan Structured Output `json_schema`, mengunci taksonomi kategori kanonis 14 nilai bahasa Indonesia (`Handphone & Tablet`, `Aksesoris HP`, dll.) pada skema kawat serta normalizer deterministik sembari menjamin hasil pencarian web tidak pernah mengisi petunjuk harga (`price_hint_idr`). Penjual pada layar Tinjauan dapat menggabungkan foto sudut tambahan ke dalam satu SKU induk, memisahkan, atau mempromosikan foto sudut menjadi foto utama dengan galeri multi-foto interaktif.

---

## Cara Kerjanya

1. **Tahap A — Konsep (`muse-spark-1.3`)**: Menemukan konsep produk dari foto masukan melalui inferensi visi.
2. **Tahap B — Segmentasi (`sam-3.1`)**: Menghasilkan mask segmentasi untuk setiap instance produk per konsep.
3. **Tahap C — Pemotongan (*Cut-out*) (Pillow)**: Memotong objek berdasarkan mask dan menerapkan bantalan kanvas secara deterministik tanpa model.
4. **Tahap D — Pembersihan Citra (`muse-image-1.0`)**: Menghasilkan foto studio 1:1 berlatar bersih dengan pengawasan gerbang kesetiaan (*fidelity gate*) dua tahap berbasis acuan dasar dua panel `crops/{sku}-ref.png` oleh `muse-spark-1.3` (pra-gerbang menolak potongan cacat sebelum pemanggilan Muse Image; pasca-gerbang mengevaluasi tiga boolean: `same_product`, `color_match`, `logo_text_match`), serta pengalihan otomatis ke cadangan komposit deterministik Pillow.
5. **Tahap E — Ekstraksi Katalog (`muse-spark-1.3`)**: Menghasilkan data terstruktur produk melalui panggilan `POST /v1/responses` dengan Search Grounding `web_search` dan format terstruktur `json_schema`, mengunci kategori ke dalam taksonomi kanonis 14 nilai bahasa Indonesia (`Handphone & Tablet`, `Aksesoris HP`, dll.), deskripsi dwibahasa (ID/EN), atribut, dan saran harga non-mengikat (`price_suggestion_idr`) tanpa pernah mengisi petunjuk harga (`price_hint_idr`) dari hasil pencarian web.

---

## Keluaran

1. **Katalog Meta Commerce (CSV)**: Berkas umpan (*feed*) katalog 11 kolom kanonis sesuai spesifikasi Meta Commerce Manager, mencakup tautan multi-foto sudut pada kolom `additional_image_link`.
2. **Paket Foto Produk (ZIP)**: Kumpulan citra studio 1:1 berlatar bersih bersama seluruh foto sudut tambahan yang digabungkan dan berkas CSV katalog di root arsip.
3. **Paket Takarir Instagram (TXT)**: Draf takarir (*caption*) promosi siap pakai untuk setiap produk dengan slugifikasi tagar.
4. **Daftar Harga WhatsApp (TXT)**: Ringkasan daftar harga produk dengan format tebal/miring WhatsApp dan tautan pemesanan langsung `wa.me`.

Selain keempat berkas unduhan di atas, sistem menyediakan halaman web publik `/p/{job_id}/{sku}` yang menyajikan galeri foto multi-sudut serta tombol pemesanan WhatsApp langsung untuk setiap produk aktif.

---

## Tumpukan Teknologi

| Lapisan | Pilihan Teknologi | Keterangan |
| :--- | :--- | :--- |
| Web | Vite + React + TypeScript + TanStack Router/Query + Tailwind | Aplikasi web satu halaman (*Single Page Application*) dengan tipe ketat |
| API | Python 3.12 + FastAPI + pydantic v2 | Layanan REST lokal dengan validasi skema data |
| Model | Meta Model API (`muse-spark-1.3-contributor`, `sam-3.1`, `muse-image-1.0`) | Penemuan konsep, segmentasi instansi, penyuntingan citra, inspeksi gerbang kesetiaan, serta ekstraksi katalog melalui `POST /v1/responses` dengan Search Grounding `web_search` dan Structured Output `json_schema` |
| Deployment | Docker tunggal (*Multi-stage*) | Penyatuan layanan API dan aset statis web dalam satu wadah container |

---

## Menjalankan Secara Lokal

Sebelum menjalankan, salin berkas konfigurasi lingkungan (`.env` diabaikan oleh git melalui `.gitignore`):

```bash
cp .env.example .env
```

Menjalankan lewat container:

```bash
docker compose -p katalogkilat up -d --build
curl http://127.0.0.1:8180/api/health
curl http://127.0.0.1:8180/review
```

Titik akhir kesehatan mengembalikan respons `{"ok":true,"mock":false}` saat berkas `.env` menetapkan `META_MOCK=false`, atau `{"ok":true,"mock":true}` sebagai cadangan bawaan `${META_MOCK:-true}` ketika berkas `.env` tidak ada. Rute aplikasi web seperti `http://127.0.0.1:8180/review` menyajikan berkas shell `index.html` dengan `<div id="root">`.

Pengembangan langsung tanpa container (dua proses mandiri):

```bash
# Antarmuka web Vite (port 4100)
npm --prefix apps/web run dev

# API FastAPI dengan uvicorn (port 8100)
uvicorn app.main:app --app-dir apps/api --port 8100 --reload
```

Port `4100` untuk antarmuka web, `8100` untuk API tanpa container, dan `8180` untuk container produksi gabungan. Pengukuran gerbang teknis selalu dilakukan pada container di port `8180`.

---

## Struktur Repositori

```text
metahackathon/
├── apps/               # Kode sumber aplikasi: web (Vite) dan api (FastAPI)
├── docs/               # Dokumentasi prompt final untuk Meta Model API
├── .dockerignore       # Pengecualian berkas build Docker
├── .env.example        # Contoh konfigurasi variabel lingkungan
├── .gitignore          # Pola pengecualian Git
├── docker-compose.yml  # Konfigurasi container layanan terpadu
└── README.md           # Ringkasan publik proyek dan panduan pengenalan
```

---

## Dokumen

| Dokumen | Keterangan |
| :--- | :--- |
| `docs/prompts-final.md` | Versi final prompt teruji untuk Meta Model API |
| `apps/api/openapi.json` | Spesifikasi OpenAPI untuk 9 rute dan 28 komponen skema data REST API |
| `apps/api/tests/fixtures/README.md` | Dokumentasi rekaman fixture live Meta Model API untuk pengujian luring |
| `apps/web/design/brandkit.md` | Panduan identitas visual, token warna, tipografi, dan geometri antarmuka |

---

## Lisensi

Dikembangkan untuk Meta Global AI Developer Hackathon 2026.

---

Dibangun untuk **Meta Global AI Developer Hackathon 2026**.
