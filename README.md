# 📈 Binance Volume Spike Scanner

Scanner otomatis yang memantau **semua pair USDT di Binance Spot**. Kalau volume sebuah pair naik tajam dibanding rata-rata beberapa candle terakhir, scanner mengirim notifikasi ke **Telegram**. Scanner dijalankan **tiap jam lewat GitHub Actions**, jadi tidak perlu server sendiri.

> 🆕 Repo ini juga berisi scanner kedua, **Reversal Scanner** (`reversal_scanner.py`), untuk mendeteksi setup *bullish engulfing di support + volume* di timeframe 4 jam. Lihat bagian [🔄 Reversal Scanner](#-reversal-scanner-bullish-engulfing-di-support).
>
> ⭐ Setiap pair di alert kedua scanner sekarang punya **skor confluence 0–3**. Pair dengan skor ≥ 2 diberi tanda **🔥 CONFLUENCE**. Lihat bagian [⭐ Skor Confluence](#-skor-confluence).

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
├── signals.py                         # Logika deteksi sinyal + skor confluence (dipakai kedua scanner)
├── requirements.txt                   # Dependency Python
├── .github/workflows/volume-spike.yml # Jadwal volume spike (tiap jam)
├── .github/workflows/reversal.yml     # Jadwal reversal scanner (tiap 4 jam)
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
- Volume spike, pola bullish engulfing, dan skor confluence **bukan sinyal beli/jual**. Spike bisa terjadi karena pump, dump, listing baru, berita, atau whale. Selalu cek chart dan konteksnya sendiri sebelum mengambil keputusan. DYOR.
