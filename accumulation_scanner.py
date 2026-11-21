"""
Accumulation Scanner: deteksi pair yang sedang "diakumulasi", yaitu harga
bergerak datar (flat) di dekat dasar (base) sementara volume pelan-pelan naik.
Target: semua pair USDT di Binance Spot.

Cara kerja singkat:
1. Ambil universe pair yang SAMA dengan scanner.py (pair USDT aktif, buang
   stablecoin & leveraged token, saring volume 24 jam).
2. Untuk tiap pair, ambil candle (default 4h) yang SUDAH CLOSE. Alert hanya
   kalau KETIGA syarat ini terpenuhi (definisi di signals.check_accumulation):
   a. Flat: dari ACCUM_WINDOW candle terakhir,
      (high tertinggi - low terendah) / low terendah <= ACCUM_MAX_RANGE_PCT %.
   b. Volume naik: ACCUM_WINDOW candle dibagi 2 paruh; rata-rata volume paruh
      terbaru >= ACCUM_VOL_RISE_FACTOR x rata-rata paruh lama.
   c. Dekat base: posisi close terakhir di range high-low ACCUM_BASE_LOOKBACK
      candle terakhir <= ACCUM_MAX_POSITION (0 = di low, 1 = di high).
3. Untuk tiap pair yang lolos, hitung skor 1-2 (info tambahan, BUKAN syarat):
   ⭐ 1/2 = akumulasi saja, ⭐⭐ 2/2 = akumulasi + kompresi (volatilitas
   menyempit: rata-rata (high-low)/close paruh terbaru <= COMPRESS_RATIO x
   paruh lama). Definisi kompresi di signals.check_compression.
4. Urutkan ⭐⭐ di atas, lalu rasio kenaikan volume tertinggi, lalu kirim
   ke Telegram.

Scanner ini berdiri sendiri (jadwal & workflow sendiri). Helper umum (request
Binance, daftar pair, format angka, kirim Telegram) di-import dari scanner.py
dan logika deteksinya dari signals.py, jadi tidak ada kode ganda.
Semua pengaturan dibaca dari environment variable (lihat README.md).
"""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import scanner as base  # helper bersama; aman di-import karena main() tidak ikut jalan
import signals
from signals import _env_bool, _env_float, _env_int, _env_str


# ---------------------------------------------------------------------------
# Konfigurasi (semua bisa diubah lewat environment variable / GitHub Variables)
# ---------------------------------------------------------------------------
# Semua nama env diberi awalan ACCUM_ supaya tidak bentrok dengan scanner lain.

INTERVAL = _env_str("ACCUM_INTERVAL", "4h")                     # timeframe candle
WINDOW = _env_int("ACCUM_WINDOW", 42)                           # candle untuk cek flat & volume (42 x 4h = 7 hari)
MAX_RANGE_PCT = _env_float("ACCUM_MAX_RANGE_PCT", 20.0)         # rentang harga maks di WINDOW (%)
VOL_RISE_FACTOR = _env_float("ACCUM_VOL_RISE_FACTOR", 1.3)      # volume paruh baru >= N x paruh lama
BASE_LOOKBACK = _env_int("ACCUM_BASE_LOOKBACK", 180)            # candle untuk range base (180 x 4h = 30 hari)
MAX_POSITION = _env_float("ACCUM_MAX_POSITION", 0.6)            # posisi maks di range base (0..1)
# Skor kompresi: volatilitas paruh terbaru WINDOW harus <= N x paruh lama
COMPRESS_RATIO = _env_float("COMPRESS_RATIO", 0.7)
MAX_ALERTS = _env_int("ACCUM_MAX_ALERTS", base.MAX_ALERTS)      # maks pair per notifikasi
# ACCUM_SEND_IF_EMPTY lebih diutamakan; kalau kosong ikut SEND_IF_EMPTY
SEND_IF_EMPTY = _env_bool("ACCUM_SEND_IF_EMPTY", base.SEND_IF_EMPTY)

LABEL = "🟢 AKUMULASI"


# ---------------------------------------------------------------------------
# Daftar blokir: token saham, ETF, dan komoditas
# ---------------------------------------------------------------------------
# Aset seperti ini memang jarang bergerak > 20% seminggu, jadi hampir selalu
# lolos syarat "flat" dan membanjiri alert. Khusus scanner ini, aset tersebut
# dibuang dari universe (scanner lain tidak terpengaruh).
#
# Binance menamai token saham/ETF dengan format TICKER + "B", contoh:
# AMZN -> AMZNB, SPY -> SPYB, BRK.B -> BRKBB. Jadi yang dicocokkan adalah
# base asset == ticker + "B". Ticker-nya SENGAJA tidak dicocokkan apa adanya,
# karena beberapa bentrok dengan crypto asli (BB = BounceBit, QNT = Quant).
# Daftar diverifikasi dari exchangeInfo Binance per 21 Nov 2026 (113 token).

# Saham (AS & luar AS)
BLOCKED_STOCK_TICKERS = {
    "AAOI", "AAPL", "ADBE", "ALAB", "AMAT", "AMC", "AMD", "AMZN", "APP", "ARM",
    "ASML", "ASTS", "AVGO", "AXTI", "BABA", "BB", "BE", "BMNR", "BNC", "BRKB",
    "CBRS", "COHR", "COIN", "CRCL", "CRDO", "CRM", "CRWD", "CRWV", "CYPH", "DELL",
    "DJT", "FLNC", "FWDI", "GEV", "GLW", "GME", "GOOGL", "GPRO", "GS", "HIMS",
    "HOOD", "HPE", "IBM", "INTC", "IREN", "JPM", "LITE", "LLY", "MDB", "MELI",
    "META", "MRNA", "MRVL", "MSFT", "MSTR", "MU", "NBIS", "NFLX", "NOK", "NU",
    "NVDA", "ORCL", "PDD", "PENG", "PLTR", "PYPL", "QCOM", "RDDT", "RKLB", "SKHY",
    "SMCI", "SNDK", "STX", "TSLA", "TSM", "USAR", "VST", "WDC", "WEN", "ZM",
}

# ETF (indeks, negara, sektor, leveraged/inverse)
BLOCKED_ETF_TICKERS = {
    "EWJ", "EWT", "EWY", "EWZ", "EWZS", "IWM", "QQQ", "SPY", "SMH",
    "SOXL", "SOXS", "SQQQ", "TQQQ", "TMF", "UVXY", "KORU", "MUU", "DRAM",
}

# Token saham/ETF lain di Binance yang jenis persisnya kurang jelas, tapi
# terverifikasi satu keluarga dengan token saham di atas (bukan crypto)
BLOCKED_OTHER_TICKERS = {
    "AGPU", "ANT", "BOT", "BSP", "INTW", "KSTR", "MVLL", "QNT", "SECZ", "SHAZ",
    "SKDD", "SKUU", "SNXX", "SPCX", "USDE",
}

# Komoditas (token emas): dicocokkan PERSIS dengan base asset
BLOCKED_COMMODITY_ASSETS = {"XAUT", "PAXG"}

# Tambahan dari env, dipisah koma, dicocokkan PERSIS dengan base asset.
# Contoh: ACCUM_EXTRA_BLOCKLIST=NEWSTOCKB,KAU
EXTRA_BLOCKED_ASSETS = {
    a.strip().upper()
    for a in _env_str("ACCUM_EXTRA_BLOCKLIST", "").split(",")
    if a.strip()
}

BLOCKED_ASSETS = (
    {t + "B" for t in BLOCKED_STOCK_TICKERS | BLOCKED_ETF_TICKERS | BLOCKED_OTHER_TICKERS}
    | BLOCKED_COMMODITY_ASSETS
    | EXTRA_BLOCKED_ASSETS
)


def is_blocked(symbol: str) -> bool:
    """True kalau pair (misal AMZNBUSDT) termasuk token saham/ETF/komoditas."""
    return symbol.removesuffix("USDT") in BLOCKED_ASSETS


def interval_hours(interval: str) -> float | None:
    """Ubah string interval Binance (15m, 4h, 1d, 1w) jadi jam. None kalau tidak dikenal."""
    units = {"m": 1 / 60, "h": 1, "d": 24, "w": 168}
    try:
        return float(interval[:-1]) * units[interval[-1]]
    except (KeyError, ValueError, IndexError):
        return None


def span_label(candles: int) -> str:
    """Label durasi untuk sejumlah candle, misal 180 candle 4h -> '30 hari'."""
    hours = interval_hours(INTERVAL)
    if not hours:
        return f"{candles} candle"
    days = candles * hours / 24
    return f"{days:g} hari" if days >= 1 else f"{candles * hours:g} jam"


# ---------------------------------------------------------------------------
# Deteksi
# ---------------------------------------------------------------------------

@dataclass
class Setup:
    """Data hasil deteksi akumulasi untuk satu pair."""
    symbol: str
    signal: signals.Accumulation
    close_price: float
    candle_open_time: int   # timestamp ms open candle close terakhir
    volume_24h: float
    volatility_ratio: float | None  # volatilitas paruh terbaru / paruh lama (info)
    compressed: bool                # True kalau volatility_ratio <= COMPRESS_RATIO

    @property
    def score(self) -> int:
        """1 = akumulasi saja, 2 = akumulasi + kompresi."""
        return 2 if self.compressed else 1


def score_line(s: Setup) -> str:
    """Baris skor untuk alert, contoh: '⭐⭐ 2/2 — coil ngencang (volatilitas 0.62x)'."""
    stars = "⭐" * s.score
    note = "coil ngencang" if s.compressed else "akumulasi"
    vol = f" (volatilitas {s.volatility_ratio:.2f}x)" if s.volatility_ratio is not None else ""
    return f"{stars} {s.score}/2 — {note}{vol}"


def check_symbol(symbol: str, volume_24h: float) -> Setup | None:
    """Ambil candle satu pair lalu evaluasi. None kalau bukan akumulasi."""
    # +1 untuk candle yang mungkin masih berjalan (belum close)
    limit = max(WINDOW, BASE_LOOKBACK) + 1
    klines = base.binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": INTERVAL, "limit": limit},
    )
    closed = signals.only_closed(klines)

    found = signals.check_accumulation(
        closed, WINDOW, MAX_RANGE_PCT, VOL_RISE_FACTOR, BASE_LOOKBACK, MAX_POSITION
    )
    if found is None:
        return None

    # Skor kompresi (info tambahan, tidak menggugurkan alert). Rasio tetap
    # dihitung walau tidak nyala, supaya bisa ditampilkan di alert.
    window = closed[-WINDOW:]
    vol_ratio = signals.compression_ratio(window)
    compressed = signals.check_compression(window, COMPRESS_RATIO) is not None

    cur = closed[-1]
    return Setup(
        symbol=symbol,
        signal=found,
        close_price=float(cur[signals.K_CLOSE]),
        candle_open_time=int(cur[signals.K_OPEN_TIME]),
        volume_24h=volume_24h,
        volatility_ratio=vol_ratio,
        compressed=compressed,
    )


def scan() -> tuple[list[Setup], int, int]:
    """
    Jalankan scan ke semua pair (universe sama dengan scanner.py).
    Return: (daftar setup terurut, jumlah pair yang discan, jumlah error)
    """
    all_symbols = base.get_usdt_symbols()
    volumes_24h = base.get_24h_quote_volumes()

    # Buang token saham/ETF/komoditas (khusus scanner ini)
    symbols = [s for s in all_symbols if not is_blocked(s)]
    print(f"[INFO] {len(all_symbols) - len(symbols)} pair saham/ETF/komoditas diblokir")

    targets = [s for s in symbols if volumes_24h.get(s, 0) >= base.MIN_24H_QUOTE_VOLUME]
    print(f"[INFO] {len(symbols)} pair USDT aktif, {len(targets)} lolos filter "
          f"volume 24 jam >= {base.MIN_24H_QUOTE_VOLUME:,.0f} USDT")

    setups: list[Setup] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol, s, volumes_24h.get(s, 0)): s for s in targets}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result:
                    setups.append(result)
            except Exception as exc:  # satu pair gagal jangan menghentikan scan
                errors += 1
                print(f"[WARN] {symbol}: {exc}")

    # Urutkan ⭐⭐ (kompresi) di atas, lalu rasio kenaikan volume tertinggi
    setups.sort(key=lambda x: (x.score, x.signal.volume_rise), reverse=True)
    return setups, len(targets), errors


# ---------------------------------------------------------------------------
# Format pesan Telegram
# ---------------------------------------------------------------------------

def build_messages(setups: list[Setup], scanned: int, errors: int) -> list[str]:
    """Susun pesan Telegram (HTML), dipecah kalau melebihi batas 4096 karakter."""
    shown = setups[:MAX_ALERTS]
    candle_time = base.local_time(
        shown[0].candle_open_time if shown else int(time.time() * 1000)
    )
    base_span = span_label(BASE_LOOKBACK)
    coiled = sum(1 for s in setups if s.compressed)

    header = (
        f"{LABEL} <b>Binance ({html.escape(INTERVAL)})</b>\n"
        f"🕒 Candle: {html.escape(candle_time)}\n"
        f"⚙️ Flat ≤ {MAX_RANGE_PCT:g}% ({WINDOW} candle / {span_label(WINDOW)}) | "
        f"Vol naik ≥ {VOL_RISE_FACTOR:g}x | "
        f"Posisi ≤ {MAX_POSITION * 100:g}% range {base_span}\n"
        f"⭐ Skor: ⭐ akumulasi | ⭐⭐ + kompresi (volatilitas ≤ {COMPRESS_RATIO:g}x paruh lama)\n"
        f"🔎 Discan: {scanned} pair | Akumulasi: {len(setups)}"
        + (f" | ⭐⭐: {coiled}" if coiled else "")
        + (f" | Error: {errors}" if errors else "")
        + "\n"
    )

    if not shown:
        return [header + "\nTidak ada pair akumulasi pada candle ini."]

    blocks = []
    for i, s in enumerate(shown, 1):
        a = s.signal
        link = f"https://www.binance.com/en/trade/{s.symbol[:-4]}_USDT?type=spot"
        blocks.append(
            f"{i}. {LABEL} <a href=\"{link}\"><b>{html.escape(s.symbol)}</b></a> "
            f"— Vol <b>{a.volume_rise:.2f}x</b>\n"
            f"   {html.escape(score_line(s))}\n"
            f"   Rentang: {a.range_pct:.1f}% | Posisi range {base_span}: "
            f"{a.position * 100:.0f}%\n"
            f"   Range {base_span}: ${base.fmt_price(a.base_low)} – ${base.fmt_price(a.base_high)}\n"
            f"   Harga: ${base.fmt_price(s.close_price)} | "
            f"24j: {base.fmt_usd(s.volume_24h)} USDT\n"
        )

    footer = ""
    if len(setups) > len(shown):
        footer = f"\n…dan {len(setups) - len(shown)} pair lainnya."

    messages, current = [], header + "\n"
    for block in blocks:
        if len(current) + len(block) > 4000:
            messages.append(current)
            current = ""
        current += block + "\n"
    current += footer
    messages.append(current)
    return messages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Paksa output console ke UTF-8 supaya emoji tidak error di Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not base.DRY_RUN and (not base.TELEGRAM_BOT_TOKEN or not base.TELEGRAM_CHAT_ID):
        print("[ERROR] TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID wajib diisi "
              "(atau set DRY_RUN=true untuk tes tanpa Telegram).")
        return 1

    started = time.time()
    try:
        setups, scanned, errors = scan()
    except Exception as exc:
        print(f"[ERROR] Scan gagal: {exc}")
        return 1

    print(f"[INFO] Selesai dalam {time.time() - started:.1f} detik. "
          f"Akumulasi: {len(setups)}, error: {errors}")

    if not setups and not SEND_IF_EMPTY:
        print("[INFO] Tidak ada pair akumulasi, tidak mengirim notifikasi.")
        return 0

    messages = build_messages(setups, scanned, errors)

    if base.DRY_RUN:
        print("\n===== DRY RUN (pesan tidak dikirim) =====\n")
        for msg in messages:
            print(msg)
            print("-" * 40)
        return 0

    try:
        for msg in messages:
            base.send_telegram(msg)
            time.sleep(1)  # jeda kecil antar pesan
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"[INFO] {len(messages)} pesan terkirim ke Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
