# 📈 Binance Volume Spike Scanner

Scanner otomatis yang memantau **semua pair USDT di Binance Spot**. Kalau volume sebuah pair naik tajam dibanding rata-rata beberapa candle terakhir, scanner mengirim notifikasi ke **Telegram**. Scanner dijalankan **tiap jam lewat GitHub Actions**, jadi tidak perlu server sendiri.

> 🆕 Repo ini juga berisi scanner kedua, **Reversal Scanner** (`reversal_scanner.py`), untuk mendeteksi setup *bullish engulfing di support + volume* di timeframe 4 jam. Lihat bagian [🔄 Reversal Scanner](#-reversal-scanner-bullish-engulfing-di-support).
>
> 🟢 Ada juga scanner ketiga, **Accumulation Scanner** (`accumulation_scanner.py`), untuk mendeteksi pair yang harganya *flat di dekat dasar sementara volumenya naik* (timeframe 4 jam). Lihat bagian [🟢 Accumulation Scanner](#-accumulation-scanner-flat--volume-naik).
>
> 🎯 Scanner keempat, **Confluence Scanner** (`confluence_scanner.py`), mencari pair yang kena **lebih dari satu kategori sinyal sekaligus** (Volume Spike, Reversal, Akumulasi). Lihat bagian [🎯 Confluence Scanner](#-confluence-scanner-multi-signal-lintas-kategori).
>
> ⭐ Setiap pair di alert volume spike & reversal scanner sekarang punya **skor confluence 0–3**. Pair dengan skor ≥ 2 diberi tanda **🔥 CONFLUENCE**. Lihat bagian [⭐ Skor Confluence](#-skor-confluence).

- Hanya pakai endpoint **publik** Binance, jadi **tidak perlu API key** akun Binance.
- Gratis: GitHub Actions gratis tanpa batas menit untuk repo **public**. Repo private dapat 2.000 menit/bulan, dan scanner ini cuma butuh sekitar 1 menit per run (±720 menit/bulan).

---

## 🧠 Cara Kerja

1. Ambil daftar semua pair `xxxUSDT` yang statusnya `TRADING` (`/api/v3/exchangeInfo`).
2. Buang pair stablecoin (USDCUSDT, FDUSDUSDT, dll.) dan leveraged token (xxxUP/xxxDOWN).
3. Buang pair yang volume 24 jamnya di bawah `MIN_24H_QUOTE_VOLUME` (`/api/v3/ticker/24hr`).
4. Untuk tiap pair, ambil candle (`/api/v3/klines`) lalu bandingkan:

   ```
   rasio = volume candle terakhir yang SUDAH CLOSE
           ÷ rata-rata volume LOOKBACK candle sebelumnya
   ```

   Volume dihitung dalam **USDT** (quote volume) supaya bisa dibandingkan antar pair.
5. Pair dianggap **spike** kalau:
   - `rasio >= SPIKE_MULTIPLIER`, **dan**
   - volume candle tersebut `>= MIN_CANDLE_QUOTE_VOLUME` USDT (supaya pair sepi yang naik dari 100 ke 500 USDT tidak ikut).
6. Tiap spike diberi [skor confluence](#-skor-confluence) 0–3. Semua spike diurutkan dari skor tertinggi, lalu rasio terbesar, kemudian dikirim ke Telegram.

Contoh pesan:

```
🚨 Volume Spike Binance (1h)
🕒 Candle: 19 Nov 2026 14:00 WIB
⚙️ Ambang: ≥ 3x rata-rata 20 candle
⭐ Skor: Vol ≥ 3x avg 20 · Engulfing@support ≤ 2% · Buy ≥ 60% | 🔥 CONFLUENCE = skor ≥ 2
🔎 Discan: 232 pair | Spike: 2 | 🔥 Confluence: 1

1. 🟢 ABCUSDT — 3.6x 🔥 CONFLUENCE
   ⭐⭐ 2/3 — Vol spike 3.6x · Buy 71%
   Vol: 812.40K (avg 225.67K) USDT
   Harga: 1.2034 (+4.10%) | 24j: 4.12M

2. 🔴 XYZUSDT — 8.4x
   ⭐ 1/3 — Vol spike 8.4x
   Vol: 2.31M (avg 275.10K) USDT
   Harga: 0.4213 (-6.85%) | 24j: 9.80M
```

🟢 berarti candle spike ditutup hijau (harga naik), 🔴 berarti ditutup merah. Nama pair di pesan bisa diklik dan langsung membuka halaman trading Binance.

---

## 📁 Struktur File

```
volume-spike-scanner/
├── scanner.py                         # Scanner volume spike
├── reversal_scanner.py                # Scanner bullish engulfing di support
├── accumulation_scanner.py            # Scanner akumulasi (flat + volume naik)
├── confluence_scanner.py              # Scanner multi-signal lintas kategori
├── signals.py                         # Logika deteksi sinyal + skor confluence (dipakai semua scanner)
├── alpha_snapshot.py                  # Cek Binance Alpha manual (listing baru + volume) + catatan hasil
├── alpha_watch.py                     # Fungsi bersama Binance Alpha (feed, format, Telegram)
├── requirements.txt                   # Dependency Python
├── .github/workflows/volume-spike.yml # Jadwal volume spike (tiap jam)
├── .github/workflows/reversal.yml     # Jadwal reversal scanner (tiap 4 jam)
├── .github/workflows/accumulation.yml # Jadwal accumulation scanner (tiap 4 jam)
├── .github/workflows/confluence.yml   # Jadwal confluence scanner (tiap jam)
├── .github/workflows/alpha-snapshot.yml # Alpha snapshot (MANUAL, tanpa jadwal)
├── .gitignore
└── README.md
```

---

## 🚀 Setup Lengkap

### Langkah 1: Buat Bot Telegram

1. Buka Telegram, cari **@BotFather**, lalu klik **Start**.
2. Kirim `/newbot`.
3. Isi nama bot (bebas, misal `Volume Spike Alert`).
4. Isi username bot. Harus diakhiri `bot`, misal `volspike_aku_bot`.
5. BotFather akan memberi **token** seperti ini:
   ```
   7123456789:AAHk3x...panjang...
   ```
   Simpan token ini. Inilah `TELEGRAM_BOT_TOKEN`. **Jangan disebar ke siapa pun.**

### Langkah 2: Dapatkan Chat ID

**Opsi A: kirim ke chat pribadi**

1. Cari bot kamu di Telegram (pakai username tadi), klik **Start**, lalu kirim pesan apa saja, misal `halo`.
2. Buka URL ini di browser (ganti `<TOKEN>` dengan token bot):
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. Cari bagian `"chat":{"id":123456789,...}`. Angka itulah `TELEGRAM_CHAT_ID`.

**Opsi B: kirim ke grup**

1. Tambahkan bot ke grup, lalu kirim pesan apa saja di grup.
2. Buka URL `getUpdates` yang sama seperti di atas.
3. Chat ID grup diawali tanda minus, misal `-1001234567890`.

**Opsi C: kirim ke channel**

1. Jadikan bot sebagai **admin** di channel.
2. Pakai `@username_channel` sebagai chat ID (untuk channel publik), atau ID numerik `-100...`.

> Kalau `getUpdates` menampilkan `"result":[]`, kirim pesan lagi ke bot lalu refresh halamannya.

### Langkah 3: Upload ke GitHub

1. Buat repository baru di [github.com/new](https://github.com/new). Public atau private sama-sama bisa.
2. Upload semua file di folder ini. Pastikan folder `.github/workflows/` ikut terupload.

   Lewat terminal:
   ```bash
   git init
   git add .
   git commit -m "Volume spike scanner"
   git branch -M main
   git remote add origin https://github.com/USERNAME/NAMA-REPO.git
   git push -u origin main
   ```

   > Kalau upload lewat web (drag & drop), folder `.github` kadang tidak ikut karena diawali titik. Kalau begitu, buat file-nya manual: **Add file → Create new file**, lalu ketik nama `.github/workflows/volume-spike.yml` dan paste isinya.

### Langkah 4: Simpan Token & Chat ID sebagai Secrets

1. Buka repo di GitHub, lalu masuk ke **Settings → Secrets and variables → Actions**.
2. Di tab **Secrets**, klik **New repository secret** dan buat dua secret:

   | Name                 | Value                        |
   |----------------------|------------------------------|
   | `TELEGRAM_BOT_TOKEN` | token dari BotFather         |
   | `TELEGRAM_CHAT_ID`   | chat ID dari langkah 2       |

   Secrets dienkripsi dan tidak akan tampil di log.

### Langkah 5: (Opsional) Atur Parameter lewat Variables

Masih di halaman yang sama, buka tab **Variables** lalu klik **New repository variable**. Semua variable ini opsional. Kalau tidak dibuat, scanner memakai nilai default.

| Variable                  | Default        | Keterangan |
|---------------------------|----------------|------------|
| `SPIKE_MULTIPLIER`        | `3`            | Ambang spike: volume candle ≥ N kali rata-rata |
| `LOOKBACK`                | `20`           | Jumlah candle sebelumnya untuk menghitung rata-rata |
| `INTERVAL`                | `1h`           | Timeframe candle (`15m`, `1h`, `4h`, dll.) |
| `MIN_CANDLE_QUOTE_VOLUME` | `50000`        | Volume minimal (USDT) candle spike |
| `MIN_24H_QUOTE_VOLUME`    | `500000`       | Pair dengan volume 24 jam di bawah ini tidak discan |
| `MAX_ALERTS`              | `30`           | Maksimal pair yang ditampilkan per notifikasi |
| `SEND_IF_EMPTY`           | `false`        | `true` = tetap kirim pesan walau tidak ada spike (berguna untuk cek bot masih hidup) |
| `TIMEZONE`                | `Asia/Jakarta` | Zona waktu di pesan (`Asia/Makassar`, `Asia/Jayapura`, `Asia/Tokyo`, dll.) |
| `BINANCE_BASE_URL`        | `https://data-api.binance.vision,https://api.binance.com` | Base URL API, dipisah koma, dicoba berurutan |

Selain itu ada beberapa setting yang hanya bisa diubah lewat environment variable di file workflow atau saat dijalankan lokal:

| Env                 | Default | Keterangan |
|---------------------|---------|------------|
| `EXCLUDE_STABLES`   | `true`  | Buang pair stablecoin |
| `EXCLUDE_LEVERAGED` | `true`  | Buang leveraged token |
| `MAX_WORKERS`       | `8`     | Jumlah request paralel ke Binance |
| `DRY_RUN`           | `false` | `true` = hasil hanya dicetak ke console, tidak dikirim ke Telegram |

**Tips tuning:**
- Notifikasi terlalu banyak? Naikkan `SPIKE_MULTIPLIER` ke `4` atau `5`, atau naikkan `MIN_CANDLE_QUOTE_VOLUME` ke `200000`.
- Notifikasi terlalu jarang? Turunkan `SPIKE_MULTIPLIER` ke `2` atau `2.5`.

### Langkah 6: Tes Jalankan Manual

1. Buka tab **Actions** di repo.
2. Kalau muncul tombol hijau *"I understand my workflows, go ahead and enable them"*, klik tombol itu.
3. Pilih workflow **Volume Spike Scanner** di sidebar kiri.
4. Klik **Run workflow → Run workflow**.
5. Tunggu sekitar 1 menit, lalu klik run tersebut untuk melihat log.

> Kalau tidak ada spike, bot **tidak akan mengirim pesan** (ini normal). Untuk memastikan bot berfungsi, buat variable `SEND_IF_EMPTY` = `true`, jalankan manual sekali, lalu hapus atau set kembali ke `false`.

Setelah ini scanner akan berjalan otomatis **setiap jam di menit ke-2** (misal 14:02, 15:02, …).

---

## 🔄 Reversal Scanner (Bullish Engulfing di Support)

Scanner kedua yang terpisah (`reversal_scanner.py`, workflow `reversal.yml`). Universe pair-nya **sama** dengan volume spike scanner (pair USDT aktif, tanpa stablecoin & leveraged token, lolos filter `MIN_24H_QUOTE_VOLUME`). Secret Telegram-nya juga sama, jadi **tidak perlu setup tambahan**.

### Syarat setup

Dicek di candle terakhir yang **sudah close** (default 4h). Alert hanya dikirim kalau **ketiga** syarat terpenuhi:

1. **Bullish engulfing**: candle sebelumnya merah (`close < open`), candle sekarang hijau (`close > open`), `open sekarang ≤ close sebelumnya`, dan `close sekarang ≥ open sebelumnya`.
2. **Dekat support**: dicari *swing low* di `SWING_LOOKBACK` candle sebelum candle sekarang. Swing low = candle yang low-nya paling rendah dibanding `SWING_WINDOW` candle di kiri **dan** kanannya. Low candle sekarang harus berjarak ≤ `SUPPORT_DISTANCE_PCT` % (di atas maupun di bawah) dari salah satu swing low itu. Yang ditampilkan adalah swing low terdekat.
3. **Konfirmasi volume**: volume candle sekarang (USDT) > `VOLUME_MULTIPLIER` × rata-rata `VOLUME_LOOKBACK` candle sebelumnya.

Hasil diurutkan dari [skor confluence](#-skor-confluence) tertinggi, lalu rasio volume terbesar. Kalau tidak ada setup, bot **tidak mengirim pesan**, kecuali `REVERSAL_SEND_IF_EMPTY=true`.

Contoh pesan:

```
🔄 Bullish Engulfing di Support (4h)
🕒 Candle: 20 Nov 2026 15:00 WIB
⚙️ Support ≤ 2% dari swing low (30 candle, window 3) | Vol > 1.5x avg 20
⭐ Skor: Vol ≥ 3x avg 20 · Engulfing@support ≤ 2% · Buy ≥ 60% | 🔥 CONFLUENCE = skor ≥ 2
🔎 Discan: 240 pair | Setup: 1 | 🔥 Confluence: 1

1. 🟢 KMNOUSDT — Vol 1.6x 🔥 CONFLUENCE
   ⭐⭐ 2/3 — Engulfing@support (-1.6%) · Buy 64%
   Support: $0.02472 (low -1.62%)
   Harga: $0.02464 | 24j: 887.29K USDT
```

`low -1.62%` berarti low candle engulfing sempat turun 1,62% di bawah swing low (menyapu support), lalu ditutup naik. Nama pair bisa diklik untuk membuka halaman trading Binance.

### Jadwal

Workflow `reversal.yml` jalan **tiap 4 jam di menit ke-3 UTC** (00:03, 04:03, 08:03, … UTC = 07:03, 11:03, 15:03, … WIB), tepat setelah candle 4h Binance tutup. Bisa juga dijalankan manual: tab **Actions → Reversal Scanner (Bullish Engulfing) → Run workflow**.

> Kalau `REVERSAL_INTERVAL` diganti ke timeframe lain, ubah juga baris `cron` di `.github/workflows/reversal.yml` supaya jadwalnya cocok dengan tutup candle.

### Pengaturan (GitHub Variables)

Semua opsional. Namanya sengaja dibedakan dari variable volume spike scanner supaya bisa diatur sendiri-sendiri.

| Variable                 | Default | Keterangan |
|--------------------------|---------|------------|
| `REVERSAL_INTERVAL`      | `4h`    | Timeframe candle (`1h`, `4h`, `1d`, dll.) |
| `SWING_LOOKBACK`         | `30`    | Jumlah candle (sebelum candle sekarang) untuk mencari swing low |
| `SWING_WINDOW`           | `3`     | Jumlah candle kiri & kanan pembanding swing low |
| `SUPPORT_DISTANCE_PCT`   | `2`     | Jarak maksimal low candle sekarang ke swing low (%) |
| `VOLUME_MULTIPLIER`      | `1.5`   | Volume candle sekarang harus > N × rata-rata |
| `VOLUME_LOOKBACK`        | `20`    | Jumlah candle untuk rata-rata volume |
| `REVERSAL_MAX_ALERTS`    | ikut `MAX_ALERTS` (`30`) | Maksimal pair per notifikasi |
| `REVERSAL_SEND_IF_EMPTY` | ikut `SEND_IF_EMPTY` (`false`) | `true` = tetap kirim pesan walau tidak ada setup |

Variable bersama yang juga dipakai scanner ini: `MIN_24H_QUOTE_VOLUME`, `TIMEZONE`, `BINANCE_BASE_URL` (lihat tabel di Langkah 5). `DRY_RUN`, `EXCLUDE_STABLES`, `EXCLUDE_LEVERAGED`, `MAX_WORKERS` juga berlaku.

**Tips tuning:**
- Terlalu banyak alert? Turunkan `SUPPORT_DISTANCE_PCT` ke `1`, atau naikkan `VOLUME_MULTIPLIER` ke `2`.
- Terlalu jarang? Naikkan `SWING_LOOKBACK` ke `50` (lebih banyak level support), atau turunkan `SWING_WINDOW` ke `2`.

### Jalankan lokal

```powershell
$env:DRY_RUN = "true"
$env:REVERSAL_SEND_IF_EMPTY = "true"
python reversal_scanner.py
```

> `reversal_scanner.py` meng-import helper dari `scanner.py` dan `signals.py`, jadi ketiga file harus berada di folder yang sama.

---

## ⭐ Skor Confluence

Info tambahan di **setiap pair** yang muncul di alert, di **kedua** scanner. Skor ini **tidak mengubah trigger** masing-masing scanner (pair yang muncul tetap sama persis seperti sebelumnya), hanya menambah satu baris info dan mengubah urutan. Kedua scanner tetap jalan sendiri-sendiri dengan jadwalnya masing-masing.

Pada candle yang sama dengan candle alert (1h untuk volume spike, 4h untuk reversal), dicek tiga syarat. Definisinya ada di `signals.py` dan dipakai bersama oleh kedua scanner:

| Syarat | Nyala kalau |
|---|---|
| **Vol spike** | volume candle (USDT) ≥ `CONF_SPIKE_MULTIPLIER` × rata-rata `CONF_VOLUME_LOOKBACK` candle sebelumnya, **dan** ≥ `CONF_MIN_CANDLE_QUOTE_VOLUME` USDT |
| **Engulfing@support** | candle bullish engulfing **dan** low-nya berjarak ≤ `CONF_SUPPORT_DISTANCE_PCT` % dari swing low terdekat (dicari di `CONF_SWING_LOOKBACK` candle sebelumnya, window `CONF_SWING_WINDOW`) |
| **Buy** | buy pressure = taker buy quote volume ÷ total quote volume ≥ `CONF_BUY_PRESSURE_PCT` % |

- Skor = jumlah syarat yang nyala (0–3), ditampilkan sebagai ⭐ per syarat, lengkap dengan nama syarat yang nyala (skor 0 ditampilkan `▫️ 0/3`).
- Skor ≥ `CONF_MIN_SCORE` diberi tanda **🔥 CONFLUENCE** di baris nama pair, dan jumlahnya ditulis di header.
- Alert diurutkan dari skor tertinggi. Kalau skornya sama, urutannya tetap seperti sebelumnya (rasio volume terbesar).
- *Taker buy* = transaksi market buy (pembeli "makan" order jual). Buy pressure 60% artinya 60% nilai transaksi di candle itu adalah pembeli agresif.

> Ambang skor sengaja punya variable sendiri (awalan `CONF_`), terpisah dari trigger. Jadi skornya punya arti yang sama di kedua scanner, dan tidak ikut berubah saat trigger diubah. Karena itu pair di volume spike scanner biasanya otomatis dapat ⭐ "Vol spike", dan pair di reversal scanner biasanya otomatis dapat ⭐ "Engulfing@support" (selama ambang `CONF_` tidak lebih ketat dari trigger-nya).

### Pengaturan (GitHub Variables)

Semua opsional, dan berlaku untuk **kedua** scanner (sudah dikirim di kedua workflow).

| Variable                       | Default | Keterangan |
|--------------------------------|---------|------------|
| `CONF_SPIKE_MULTIPLIER`        | `3`     | Vol spike: volume ≥ N × rata-rata |
| `CONF_VOLUME_LOOKBACK`         | `20`    | Vol spike: jumlah candle untuk rata-rata |
| `CONF_MIN_CANDLE_QUOTE_VOLUME` | `50000` | Vol spike: volume candle minimal (USDT) |
| `CONF_SWING_LOOKBACK`          | `30`    | Engulfing: jumlah candle untuk cari swing low |
| `CONF_SWING_WINDOW`            | `3`     | Engulfing: jumlah candle kiri & kanan pembanding swing low |
| `CONF_SUPPORT_DISTANCE_PCT`    | `2`     | Engulfing: jarak maksimal low ke swing low (%) |
| `CONF_BUY_PRESSURE_PCT`        | `60`    | Buy pressure minimal, dalam persen (`60` = 60%) |
| `CONF_MIN_SCORE`               | `2`     | Skor minimal untuk tanda 🔥 CONFLUENCE |

---

## 🟢 Accumulation Scanner (Flat + Volume Naik)

Scanner ketiga yang terpisah (`accumulation_scanner.py`, workflow `accumulation.yml`). Idenya: kalau harga bergerak datar di dekat dasar tapi volumenya pelan-pelan naik, bisa jadi ada pihak yang sedang mengumpulkan (akumulasi) sebelum harga bergerak. Universe pair-nya **sama** dengan scanner lain, **ditambah** blokir token saham, ETF, dan komoditas (lihat di bawah). Secret Telegram-nya juga sama, jadi **tidak perlu setup tambahan**.

### Daftar blokir saham, ETF & komoditas

Token saham/ETF (contoh `AMZNBUSDT`, `SPYBUSDT`) dan token emas (`XAUTUSDT`, `PAXGUSDT`) jarang bergerak lebih dari 20% seminggu, jadi hampir selalu lolos syarat "flat" dan membanjiri alert. Karena itu, **khusus scanner ini**, aset tersebut dibuang dari universe. Scanner lain tetap memindainya.

- Daftarnya ada di `accumulation_scanner.py` (`BLOCKED_STOCK_TICKERS`, `BLOCKED_ETF_TICKERS`, `BLOCKED_OTHER_TICKERS`, `BLOCKED_COMMODITY_ASSETS`), ditulis sebagai ticker aslinya (AMZN, TSLA, SPY, …).
- Binance menamai token saham/ETF dengan format **ticker + `B`** (AMZN → `AMZNB`, BRK.B → `BRKBB`), jadi yang dicocokkan adalah `ticker + "B"`. Ticker-nya sengaja tidak dicocokkan apa adanya karena ada yang bentrok dengan crypto asli (misal `BB` = BounceBit, `QNT` = Quant). Crypto yang kebetulan berakhiran B (ARB, BNB, SHIB, TRB, dll.) tidak ikut terblokir.
- Daftar diverifikasi dari data `exchangeInfo` Binance per 21 Nov 2026 (113 token saham/ETF + 2 token emas).
- Kalau Binance menambah token saham baru, tambahkan lewat variable `ACCUM_EXTRA_BLOCKLIST` (nama base asset persis, dipisah koma, misal `NEWSTOCKB,KAU`) tanpa perlu ubah kode.

### Syarat akumulasi

Dicek pada candle yang **sudah close** (default 4h). Alert hanya dikirim kalau **ketiga** syarat terpenuhi. Definisinya ada di `signals.check_accumulation()`:

1. **Flat**: dari `ACCUM_WINDOW` candle terakhir (default 42 = 7 hari), `(high tertinggi − low terendah) ÷ low terendah ≤ ACCUM_MAX_RANGE_PCT` %.
2. **Volume naik**: `ACCUM_WINDOW` candle itu dibagi 2 paruh sama panjang. Rata-rata volume (USDT) paruh terbaru harus `≥ ACCUM_VOL_RISE_FACTOR` × rata-rata paruh lama.
3. **Dekat base**: dari `ACCUM_BASE_LOOKBACK` candle terakhir (default 180 = 30 hari), posisi harga close sekarang di range high–low itu harus `≤ ACCUM_MAX_POSITION`. Posisi `0` = tepat di low terendah, `1` = tepat di high tertinggi.

Pair yang candle-nya belum cukup (misal baru listing kurang dari 30 hari) dilewati. Kalau tidak ada pair yang lolos, bot **tidak mengirim pesan**, kecuali `ACCUM_SEND_IF_EMPTY=true` (atau `SEND_IF_EMPTY=true`).

### Skor /3: akumulasi + kompresi + OI

Setiap pair yang **sudah lolos** ketiga syarat di atas diberi skor 1–3, satu ⭐ per faktor yang nyala. Skor ini **bukan syarat wajib**: pair dengan skor 1 tetap dikirim. Hasil diurutkan dari skor tertinggi, lalu rasio kenaikan volume tertinggi.

| Faktor | Nyala kalau |
|---|---|
| ⭐ **Akumulasi** | selalu nyala (syarat inti di atas sudah lolos) |
| ⭐ **Kompresi** (coil ngencang) | volatilitas menyempit: `ACCUM_WINDOW` candle dibagi 2 paruh, rata-rata `(high − low) ÷ close` per candle di paruh **terbaru** `≤ COMPRESS_RATIO` × paruh **lama**. Definisi: `signals.check_compression()` |
| ⭐ **OI building** | dari Binance Futures: Open Interest terbaru `≥ OI_RISE_FACTOR` × OI di awal window, **dan** funding rate terakhir `≤ FUNDING_MAX`. Definisi: `signals.check_oi_building()` |

**Kompresi vs flat**: flat melihat rentang harga *total* selama window, sedangkan kompresi melihat apakah ukuran candle *makin lama makin kecil*.

**Detail faktor OI:**
- Riwayat OI diambil dari endpoint `openInterestHist` (period `ACCUM_INTERVAL`, `ACCUM_WINDOW` titik = 42 × 4h = 7 hari), memakai OI dalam **jumlah kontrak** supaya tidak ikut naik-turun karena harga.
- Funding rate yang dipakai adalah funding terakhir yang sudah dibayar (`/fapi/v1/fundingRate`). Funding negatif dianggap sehat.
- **Hanya dicek untuk pair yang sudah lolos syarat inti**, jadi request ke Futures API cuma beberapa per run.
- Simbol perp dicocokkan otomatis, termasuk koin yang di futures pakai awalan `1000` (spot `PEPEUSDT` → perp `1000PEPEUSDT`).
- Pair yang **tidak punya perpetual futures** tidak dapat ⭐ OI dan ditandai `OI: tidak ada perp`. Ini tidak dihitung error.
- Kalau Futures API tidak bisa diakses, semua pair tetap dikirim tanpa ⭐ OI, dan header pesan menampilkan `⚠️ Data futures tidak tersedia`.

> ⚠️ **Catatan GitHub Actions:** `fapi.binance.com` memblokir IP Amerika Serikat (HTTP 451), dan runner GitHub Actions umumnya ada di AS. Berbeda dengan data spot, Binance tidak menyediakan mirror publik untuk data futures. Jadi saat jalan di GitHub Actions, faktor OI kemungkinan besar **selalu dilewati** (skor maksimal efektif 2/3). Scanner tetap jalan normal. Dari komputer di Indonesia/Asia faktor OI berfungsi. Kalau punya proxy/server sendiri di luar AS, arahkan lewat variable `FUTURES_BASE_URL`.

Contoh pesan:

```
🟢 AKUMULASI Binance (4h)
🕒 Candle: 21 Nov 2026 15:00 WIB
⚙️ Flat ≤ 20% (42 candle / 7 hari) | Vol naik ≥ 1.3x | Posisi ≤ 60% range 30 hari
⭐ Skor /3: akumulasi + kompresi (volatilitas ≤ 0.7x) + OI (≥ 1.2x & funding ≤ 0.03%)
🔎 Discan: 246 pair | Akumulasi: 2 | ⭐⭐+: 1

1. 🟢 AKUMULASI SUSHIUSDT — Vol 1.70x
   ⭐⭐⭐ 3/3 — akumulasi · kompresi (coil ngencang) · OI building
   Volatilitas: 0.62x | OI 7 hari: +35% | Funding: +0.0100%
   Rentang: 18.2% | Posisi range 30 hari: 8%
   Range 30 hari: $0.137 – $0.2193
   Harga: $0.1433 | 24j: 948.77K USDT

2. 🟢 AKUMULASI HYPERUSDT — Vol 2.76x
   ⭐ 1/3 — akumulasi
   Volatilitas: 1.27x | OI 7 hari: -7% | Funding: -0.0180%
   Rentang: 12.2% | Posisi range 30 hari: 7%
   Range 30 hari: $0.0394 – $0.07
   Harga: $0.0416 | 24j: 1.18M USDT
```

- **Vol 1.70x**: rata-rata volume paruh terbaru 1,7× paruh lama.
- **Volatilitas 0.62x**: ukuran candle rata-rata paruh terbaru tinggal 62% dari paruh lama (menyempit). Di atas 1 berarti justru melebar.
- **OI 7 hari +35%**: open interest naik 35% dibanding 7 hari lalu. **Funding +0.0100%**: funding terakhir 0,01% (masih di bawah batas 0,03%).
- **Rentang 18.2%**: selama 7 hari terakhir harga hanya bergerak dalam rentang 18,2%.
- **Posisi range 30 hari 8%**: harga sekarang ada di 8% bagian bawah range 30 hari.
- Baris "Volatilitas | OI | Funding" selalu ditampilkan, walaupun faktornya tidak nyala.

### Jadwal

Workflow `accumulation.yml` jalan **tiap 4 jam di menit ke-5 UTC** (00:05, 04:05, … UTC = 07:05, 11:05, … WIB), dua menit setelah reversal scanner supaya tidak barengan. Bisa juga dijalankan manual: tab **Actions → Accumulation Scanner → Run workflow**.

> Kalau `ACCUM_INTERVAL` diganti, ubah juga baris `cron` di `.github/workflows/accumulation.yml`. Ingat juga bahwa `ACCUM_WINDOW` dan `ACCUM_BASE_LOOKBACK` dihitung dalam **jumlah candle**, jadi durasinya ikut berubah (label "7 hari / 30 hari" di pesan otomatis menyesuaikan).

### Pengaturan (GitHub Variables)

Semua opsional.

| Variable                | Default | Keterangan |
|-------------------------|---------|------------|
| `ACCUM_INTERVAL`        | `4h`    | Timeframe candle |
| `ACCUM_WINDOW`          | `42`    | Jumlah candle untuk cek flat & kenaikan volume (42 × 4h = 7 hari) |
| `ACCUM_MAX_RANGE_PCT`   | `20`    | Rentang harga maksimal di window (%) |
| `ACCUM_VOL_RISE_FACTOR` | `1.3`   | Volume paruh terbaru harus ≥ N × paruh lama |
| `ACCUM_BASE_LOOKBACK`   | `180`   | Jumlah candle untuk range base (180 × 4h = 30 hari), maks 999 |
| `ACCUM_MAX_POSITION`    | `0.6`   | Posisi maksimal di range base (`0`–`1`). `0.4` = hanya 40% bagian bawah |
| `ACCUM_MAX_ALERTS`      | ikut `MAX_ALERTS` (`30`) | Maksimal pair per notifikasi |
| `ACCUM_SEND_IF_EMPTY`   | ikut `SEND_IF_EMPTY` (`false`) | `true` = tetap kirim pesan walau tidak ada pair |
| `ACCUM_EXTRA_BLOCKLIST` | (kosong) | Base asset tambahan yang diblokir, dipisah koma (misal `NEWSTOCKB,KAU`) |
| `COMPRESS_RATIO`        | `0.7`   | Skor kompresi: volatilitas paruh terbaru harus ≤ N × paruh lama (makin kecil makin ketat) |
| `OI_RISE_FACTOR`        | `1.2`   | Skor OI: OI terbaru harus ≥ N × OI di awal window |
| `FUNDING_MAX`           | `0.0003` | Skor OI: funding rate terakhir harus ≤ nilai ini (`0.0003` = 0,03%) |
| `FUTURES_BASE_URL`      | `https://fapi.binance.com` | Base URL Binance Futures (untuk proxy, lihat catatan GitHub Actions) |

Variable bersama yang juga dipakai: `MIN_24H_QUOTE_VOLUME`, `TIMEZONE`, `BINANCE_BASE_URL`. `DRY_RUN`, `EXCLUDE_STABLES`, `EXCLUDE_LEVERAGED`, `MAX_WORKERS` juga berlaku.

**Tips tuning:**
- Terlalu banyak alert? Turunkan `ACCUM_MAX_POSITION` ke `0.4`, `ACCUM_MAX_RANGE_PCT` ke `15`, atau naikkan `ACCUM_VOL_RISE_FACTOR` ke `1.5`.
- Masih ada token saham/ETF yang muncul? Kemungkinan token baru listing. Tambahkan ke `ACCUM_EXTRA_BLOCKLIST`.

### Jalankan lokal

```powershell
$env:DRY_RUN = "true"
$env:ACCUM_SEND_IF_EMPTY = "true"
python accumulation_scanner.py
```

---

## 🎯 Confluence Scanner (Multi-Signal Lintas Kategori)

Scanner keempat (`confluence_scanner.py`, workflow `confluence.yml`). Tugasnya mencari pair yang kena **lebih dari satu jenis sinyal sekaligus**. Universe pair-nya sama dengan scanner lain, dan secret Telegram-nya juga sama.

### Kategori

Untuk tiap pair, ketiga kategori dievaluasi dalam satu lintasan:

| Kategori | Timeframe | Definisi |
|---|---|---|
| **Volume Spike** | `INTERVAL` (1h) | sama persis dengan trigger `scanner.py` |
| **Reversal** | `REVERSAL_INTERVAL` (4h) | sama persis dengan trigger `reversal_scanner.py` (bullish engulfing di support + konfirmasi volume) |
| **Akumulasi** | `ACCUM_INTERVAL` (4h) | syarat inti `accumulation_scanner.py` (flat + volume naik + dekat base). Token saham/ETF/emas yang diblokir di scanner akumulasi juga tidak dihitung di kategori ini. Skor kompresi/OI tidak dipakai di sini. |

- Konfigurasi dan fungsi deteksinya **di-import langsung** dari ketiga scanner dan `signals.py`, jadi ambangnya otomatis sama. Kalau misalnya `SPIKE_MULTIPLIER` diubah, kategori Volume Spike di sini ikut berubah. Variable-variable itu sudah dikirim juga di `confluence.yml`.
- Candle diambil **sekali per timeframe** per pair (default 1h × 22 dan 4h × 181). Reversal dan Akumulasi sama-sama 4h, jadi memakai candle yang sama.
- Alert hanya untuk pair yang kena `≥ CONFLUENCE_MIN_CATEGORIES` kategori (default 2), dengan penanda **🎯 MULTI-SIGNAL**.
- Urutan: pair yang kena 3 kategori paling atas, lalu volume 24 jam terbesar.
- Kalau tidak ada pair multi-signal, bot **tidak mengirim pesan**, kecuali `CONFLUENCE_SEND_IF_EMPTY=true` (atau `SEND_IF_EMPTY=true`).

Contoh pesan:

```
🎯 MULTI-SIGNAL Confluence Lintas-Kategori
🕒 Scan: 21 Nov 2026 19:53 WIB
⚙️ Kategori: Volume Spike (1h) · Reversal (4h) · Akumulasi (4h) | alert ≥ 2 kategori
📊 Per kategori: Volume Spike 4 · Reversal 1 · Akumulasi 6
🔎 Discan: 295 pair | Multi-signal: 1

1. 🎯 MULTI-SIGNAL SAPIENUSDT — 2/3
   Volume Spike + Akumulasi
   • Volume Spike: spike 5.8x (1h)
   • Akumulasi: vol naik 1.72x, rentang 18.5%, posisi 39% (4h)
   Harga: $0.04463 | 24j: 2.98M USDT
```

Baris "📊 Per kategori" menunjukkan berapa pair yang kena tiap kategori (termasuk yang cuma kena satu), supaya kelihatan kalau scanner jalan normal walau tidak ada overlap.

### Jadwal

Workflow `confluence.yml` jalan **tiap jam di menit ke-7 UTC**, supaya overlap dengan volume spike 1h yang masih fresh ikut tertangkap. Bisa juga dijalankan manual: tab **Actions → Confluence Scanner (Multi-Signal) → Run workflow**. Mau lebih jarang? Ubah baris `cron` di `.github/workflows/confluence.yml` (misal `"7 */4 * * *"` = tiap 4 jam).

> Kategori Reversal dan Akumulasi memakai candle 4h, jadi hasilnya baru berubah tiap 4 jam. Akibatnya, pair yang kena **Reversal + Akumulasi** (tanpa Volume Spike) bisa muncul berulang di beberapa run berturut-turut sampai candle 4h berikutnya close.

### Pengaturan (GitHub Variables)

| Variable                    | Default | Keterangan |
|-----------------------------|---------|------------|
| `CONFLUENCE_MIN_CATEGORIES` | `2`     | Minimal jumlah kategori untuk alert (`3` = hanya yang kena semua) |
| `CONFLUENCE_MAX_ALERTS`     | ikut `MAX_ALERTS` (`30`) | Maksimal pair per notifikasi |
| `CONFLUENCE_SEND_IF_EMPTY`  | ikut `SEND_IF_EMPTY` (`false`) | `true` = tetap kirim pesan walau tidak ada pair |

Ambang tiap kategori diatur lewat variable scanner aslinya (lihat tabel di bagian masing-masing scanner).

### Jalankan lokal

```powershell
$env:DRY_RUN = "true"
$env:CONFLUENCE_SEND_IF_EMPTY = "true"
python confluence_scanner.py
```

> `confluence_scanner.py` meng-import `scanner.py`, `reversal_scanner.py`, `accumulation_scanner.py`, dan `signals.py`, jadi semua file harus berada di folder yang sama.

---

## 🛰 Alpha Snapshot (Manual, dari iPad)

Cek token **Binance Alpha** kapan pun kamu sempat, misalnya saat jam istirahat, tanpa laptop. Sengaja **tanpa jadwal otomatis**: kamu sendiri yang menekan tombol.

**Cara menjalankan (iPad/HP):** buka repo ini di Safari atau app GitHub → tab **Actions** → **Alpha Snapshot (manual)** → **Run workflow** → pilih mode → **Run workflow**. Sekitar 1 menit kemudian pesan masuk ke Telegram.

| Mode | Isi pesan |
|---|---|
| `snapshot` | 🚨 Listing baru sejak cek terakhir · 📈 Token dengan volume ≥ N× normal sejak cek terakhir · ⚪️ Token yang keluar dari Alpha · 📒 Alert sebelumnya: harga sekarang vs saat alert |
| `report` | Rangkuman hasil semua alert: return di cek berikutnya, ~24 jam, puncak dan titik terendah yang tercatat |

**Run pertama** hanya menyimpan data awal. Perbandingan baru dimulai di run kedua.

**Lonjakan volume** dihitung dari pertumbuhan volume 24 jam sejak cek terakhir, lalu dikonversi menjadi "berapa kali volume normal untuk rentang waktu sepanjang itu". Kalau jedanya ≥ 24 jam, yang dibandingkan langsung volume 24 jam sekarang vs sebelumnya.

**Catatan hasil.** Setiap listing baru/lonjakan volume dicatat dengan harga **saat kamu melihatnya**, lalu harganya dicatat lagi di setiap run berikutnya selama 48 jam. Karena run-nya manual, "puncak" dan "terendah" adalah harga di run-run itu, bukan pergerakan per menit. Itu justru mencerminkan kondisi nyata: kamu hanya bisa bertindak saat istirahat. Token yang keluar dari Alpha ditandai `gone` (bisa delist, bisa juga pindah ke spot).

**Data disimpan** di branch `alpha-data` (satu commit, ditimpa setiap run): `alerts.csv` (bisa dibuka langsung di GitHub), `last_digest.txt`, `alerts_log.json`, `state.json`. Repo ini **public**, jadi data itu juga bisa dilihat orang lain.

Variable opsional (tab **Variables**, kosong = default):

| Variable | Default | Keterangan |
|---|---|---|
| `ALPHA_MIN_LIQ` | `50000` | Likuiditas minimal (USD). Token di bawahnya diabaikan |
| `ALPHA_MIN_VOL` | `0` | Volume 24 jam minimal (USD) |
| `ALPHA_MAX_MCAP` | `0` (tanpa batas) | Market cap maksimal, misal `50000000` untuk fokus ke token kecil |
| `ALPHA_VOL_MULT` | `3` | Ambang lonjakan volume (× normal) |
| `ALPHA_TOP_N` | `5` | Maks token per bagian di pesan |
| `ALPHA_TIMEZONE` | `Asia/Tokyo` | Zona waktu di pesan |

Memakai secret yang sama dengan scanner lain (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`). Read-only: tidak pernah order dan tidak butuh API key Binance. Endpoint Binance Alpha (`www.binance.com/bapi/...`) sudah dites bisa diakses dari runner GitHub (AS).

---

## 💻 Menjalankan di Komputer Sendiri (Opsional)

Butuh Python 3.9 atau lebih baru. Download dari [python.org](https://www.python.org/downloads/) dan centang **"Add Python to PATH"** saat install.

```powershell
# Masuk ke folder project
cd volume-spike-scanner

# (Opsional) buat virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Mac/Linux

# Install dependency
pip install -r requirements.txt

# Tes tanpa Telegram (hasil dicetak ke layar)
$env:DRY_RUN = "true"
$env:SEND_IF_EMPTY = "true"
python scanner.py
```

Untuk kirim ke Telegram dari lokal (PowerShell):

```powershell
$env:DRY_RUN = "false"
$env:TELEGRAM_BOT_TOKEN = "7123456789:AAH..."
$env:TELEGRAM_CHAT_ID = "123456789"
python scanner.py
```

Mac/Linux: ganti `$env:NAMA = "nilai"` dengan `export NAMA="nilai"`.

---

## ❓ Troubleshooting

| Masalah | Penyebab & solusi |
|---|---|
| Log: `HTTP 451 (diblokir)` | `api.binance.com` memblokir IP Amerika Serikat, dan runner GitHub ada di AS. Karena itu default-nya memakai `data-api.binance.vision`. Pastikan variable `BINANCE_BASE_URL` tidak diisi `api.binance.com` saja. |
| Log: `Telegram error 401` | Token bot salah. Cek ulang secret `TELEGRAM_BOT_TOKEN`. |
| Log: `Telegram error 400: chat not found` | Chat ID salah, atau kamu belum pernah klik **Start** di bot. Untuk grup/channel, pastikan bot sudah ditambahkan (dan dijadikan admin untuk channel). |
| Workflow tidak jalan otomatis | Jadwal GitHub Actions bisa telat 5–30 menit saat server ramai. Selain itu, di repo **public**, workflow terjadwal **otomatis dinonaktifkan setelah 60 hari tanpa aktivitas** (commit). Aktifkan lagi di tab Actions, atau buat commit kecil. |
| Tidak ada pesan sama sekali | Bisa jadi memang tidak ada spike. Cek log di tab Actions: baris `Spike: 0` berarti scanner berjalan normal. |
| `ZoneInfoNotFoundError` di Windows | Jalankan `pip install -r requirements.txt` (paket `tzdata` dibutuhkan di Windows). |

---

## 📝 Catatan

- Scanner mengecek **candle terakhir yang sudah close**. Candle yang masih berjalan diabaikan supaya hasilnya tidak berubah-ubah.
- Satu run memakai sekitar 2 × jumlah pair *request weight* Binance. Untuk ±230 pair itu sekitar 500 dari batas 6.000/menit, jadi aman.
- Volume spike, pola bullish engulfing, skor confluence, dan pola akumulasi **bukan sinyal beli/jual**. Spike bisa terjadi karena pump, dump, listing baru, berita, atau whale. Selalu cek chart dan konteksnya sendiri sebelum mengambil keputusan. DYOR.
