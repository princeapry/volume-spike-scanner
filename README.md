# 📈 Binance Volume Spike Scanner

Scanner otomatis yang memantau **semua pair USDT di Binance Spot**. Kalau volume sebuah pair naik tajam dibanding rata-rata beberapa candle terakhir, scanner mengirim notifikasi ke **Telegram**. Scanner dijalankan **tiap jam lewat GitHub Actions**, jadi tidak perlu server sendiri.

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
6. Semua spike diurutkan dari rasio terbesar lalu dikirim ke Telegram.

Contoh pesan:

```
🚨 Volume Spike Binance (1h)
🕒 Candle: 19 Nov 2026 14:00 WIB
⚙️ Ambang: ≥ 3x rata-rata 20 candle
🔎 Discan: 232 pair | Spike: 2

1. 🟢 XYZUSDT — 8.4x
   Vol: 2.31M (avg 275.10K) USDT
   Harga: 0.4213 (+6.85%) | 24j: 9.80M

2. 🔴 ABCUSDT — 3.6x
   Vol: 812.40K (avg 225.67K) USDT
   Harga: 1.2034 (-4.10%) | 24j: 4.12M
```

🟢 berarti candle spike ditutup hijau (harga naik), 🔴 berarti ditutup merah. Nama pair di pesan bisa diklik dan langsung membuka halaman trading Binance.

---

## 📁 Struktur File

```
volume-spike-scanner/
├── scanner.py                         # Script utama
├── requirements.txt                   # Dependency Python
├── .github/workflows/volume-spike.yml # Jadwal GitHub Actions (tiap jam)
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
- Volume spike **bukan sinyal beli/jual**. Spike bisa terjadi karena pump, dump, listing baru, berita, atau whale. Selalu cek chart dan konteksnya sendiri sebelum mengambil keputusan. DYOR.
