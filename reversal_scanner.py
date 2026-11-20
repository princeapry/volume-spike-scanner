"""
Reversal Scanner: deteksi setup "bullish engulfing di support + volume"
untuk semua pair USDT di Binance Spot.

Cara kerja singkat:
1. Ambil universe pair yang SAMA dengan scanner.py (pair USDT aktif, buang
   stablecoin & leveraged token, saring volume 24 jam).
2. Untuk tiap pair, ambil candle (default 4h) dan cek candle terakhir yang
   SUDAH CLOSE. Alert hanya kalau KETIGA syarat ini terpenuhi:
   a. Bullish engulfing: candle sebelumnya merah, candle sekarang hijau,
      open sekarang <= close sebelumnya, close sekarang >= open sebelumnya.
   b. Dekat support: low candle sekarang berjarak <= SUPPORT_DISTANCE_PCT %
      dari salah satu swing low dalam SWING_LOOKBACK candle terakhir.
   c. Konfirmasi volume: volume candle sekarang > VOLUME_MULTIPLIER x
      rata-rata VOLUME_LOOKBACK candle sebelumnya.
3. Kirim hasilnya ke Telegram.

File ini sengaja terpisah dari scanner.py. Helper umum (request Binance,
daftar pair, format angka, kirim Telegram) di-import dari scanner.py supaya
tidak ada kode ganda, tapi scanner.py sendiri tidak diubah.
Semua pengaturan dibaca dari environment variable (lihat README.md).
"""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import scanner as base  # helper bersama; aman di-import karena main() tidak ikut jalan
from scanner import _env_bool, _env_float, _env_int, _env_str


# ---------------------------------------------------------------------------
# Konfigurasi (semua bisa diubah lewat environment variable / GitHub Variables)
# ---------------------------------------------------------------------------
# Nama env sengaja diberi awalan REVERSAL_ / nama khusus supaya tidak bentrok
# dengan Variable milik scanner.py (misal INTERVAL=1h untuk volume spike).

INTERVAL = _env_str("REVERSAL_INTERVAL", "4h")              # timeframe candle
SWING_LOOKBACK = _env_int("SWING_LOOKBACK", 30)             # jumlah candle untuk cari swing low
SWING_WINDOW = _env_int("SWING_WINDOW", 3)                  # jumlah candle kiri & kanan pembanding swing low
SUPPORT_DISTANCE_PCT = _env_float("SUPPORT_DISTANCE_PCT", 2.0)  # jarak maks low sekarang ke swing low (%)
VOLUME_MULTIPLIER = _env_float("VOLUME_MULTIPLIER", 1.5)    # volume sekarang harus > N x rata-rata
VOLUME_LOOKBACK = _env_int("VOLUME_LOOKBACK", 20)           # jumlah candle untuk rata-rata volume
MAX_ALERTS = _env_int("REVERSAL_MAX_ALERTS", base.MAX_ALERTS)  # maks pair per notifikasi
# REVERSAL_SEND_IF_EMPTY lebih diutamakan; kalau kosong ikut SEND_IF_EMPTY
SEND_IF_EMPTY = _env_bool("REVERSAL_SEND_IF_EMPTY", base.SEND_IF_EMPTY)


# ---------------------------------------------------------------------------
# Logika deteksi (fungsi murni, mudah dites tanpa internet)
# ---------------------------------------------------------------------------

@dataclass
class Setup:
    """Data hasil deteksi setup reversal untuk satu pair."""
    symbol: str
    support: float          # harga swing low terdekat
    distance_pct: float     # jarak low candle sekarang ke support (%), minus = di bawah support
    volume_ratio: float     # volume candle sekarang / rata-rata
    close_price: float      # harga close candle engulfing (harga terakhir)
    candle_open_time: int   # timestamp ms open candle engulfing
    volume_24h: float


def is_bullish_engulfing(prev: list, cur: list) -> bool:
    """Candle sebelumnya merah, candle sekarang hijau dan badannya menelan
    badan candle sebelumnya."""
    p_open, p_close = float(prev[1]), float(prev[4])
    c_open, c_close = float(cur[1]), float(cur[4])
    return (
        p_close < p_open          # sebelumnya merah
        and c_close > c_open      # sekarang hijau
        and c_open <= p_close     # open sekarang <= close sebelumnya
        and c_close >= p_open     # close sekarang >= open sebelumnya
    )


def find_swing_lows(candles: list, window: int) -> list[float]:
    """
    Cari swing low: candle yang low-nya paling rendah dibanding `window`
    candle di kiri DAN `window` candle di kanannya.
    Candle di ujung (tidak punya cukup tetangga) tidak dihitung.
    Pembanding kiri pakai "<" dan kanan pakai "<=" supaya dua low yang
    persis sama tidak tercatat dua kali.
    """
    lows = [float(k[3]) for k in candles]
    result = []
    for i in range(window, len(lows) - window):
        left = lows[i - window:i]
        right = lows[i + 1:i + 1 + window]
        if lows[i] < min(left) and lows[i] <= min(right):
            result.append(lows[i])
    return result


def evaluate(closed: list) -> tuple[float, float, float] | None:
    """
    Cek candle terakhir di `closed` (semua candle sudah close, urut lama->baru).
    Return (support, jarak %, rasio volume) kalau ketiga syarat terpenuhi,
    None kalau tidak.

    Format kline Binance:
    [0 openTime, 1 open, 2 high, 3 low, 4 close, 5 volume(base),
     6 closeTime, 7 quoteVolume(USDT), ...]
    """
    needed = max(SWING_LOOKBACK, VOLUME_LOOKBACK, 2) + 1
    if len(closed) < needed:
        return None

    cur, prev = closed[-1], closed[-2]

    # 1) Bullish engulfing (syarat paling murah dicek duluan)
    if not is_bullish_engulfing(prev, cur):
        return None

    # 2) Konfirmasi volume (pakai quote volume / USDT)
    history = closed[-(VOLUME_LOOKBACK + 1):-1]
    avg_qv = sum(float(k[7]) for k in history) / len(history)
    if avg_qv <= 0:
        return None
    ratio = float(cur[7]) / avg_qv
    if ratio <= VOLUME_MULTIPLIER:
        return None

    # 3) Dekat support. Swing low dicari di SWING_LOOKBACK candle SEBELUM
    #    candle sekarang, jadi candle engulfing tidak menjadi support-nya sendiri.
    swing_area = closed[-(SWING_LOOKBACK + 1):-1]
    swings = find_swing_lows(swing_area, SWING_WINDOW)
    if not swings:
        return None

    cur_low = float(cur[3])
    # Pilih swing low yang paling dekat dengan low candle sekarang
    support = min(swings, key=lambda s: abs(cur_low - s))
    distance_pct = (cur_low - support) / support * 100
    if abs(distance_pct) > SUPPORT_DISTANCE_PCT:
        return None

    return support, distance_pct, ratio


def check_symbol(symbol: str, volume_24h: float) -> Setup | None:
    """Ambil candle satu pair lalu evaluasi. None kalau tidak ada setup."""
    # +2: candle yang dicek + 1 candle yang mungkin masih berjalan
    limit = max(SWING_LOOKBACK, VOLUME_LOOKBACK, 2) + 2
    klines = base.binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": INTERVAL, "limit": limit},
    )

    # Buang candle yang belum close (closeTime masih di masa depan)
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if int(k[6]) < now_ms]

    found = evaluate(closed)
    if not found:
        return None

    support, distance_pct, ratio = found
    cur = closed[-1]
    return Setup(
        symbol=symbol,
        support=support,
        distance_pct=distance_pct,
        volume_ratio=ratio,
        close_price=float(cur[4]),
        candle_open_time=int(cur[0]),
        volume_24h=volume_24h,
    )


def scan() -> tuple[list[Setup], int, int]:
    """
    Jalankan scan ke semua pair (universe sama dengan scanner.py).
    Return: (daftar setup terurut, jumlah pair yang discan, jumlah error)
    """
    symbols = base.get_usdt_symbols()
    volumes_24h = base.get_24h_quote_volumes()

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

    # Urutkan dari konfirmasi volume terkuat
    setups.sort(key=lambda x: x.volume_ratio, reverse=True)
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

    header = (
        f"🔄 <b>Bullish Engulfing di Support ({html.escape(INTERVAL)})</b>\n"
        f"🕒 Candle: {html.escape(candle_time)}\n"
        f"⚙️ Support ≤ {SUPPORT_DISTANCE_PCT:g}% dari swing low "
        f"({SWING_LOOKBACK} candle, window {SWING_WINDOW}) | "
        f"Vol > {VOLUME_MULTIPLIER:g}x avg {VOLUME_LOOKBACK}\n"
        f"🔎 Discan: {scanned} pair | Setup: {len(setups)}"
        + (f" | Error: {errors}" if errors else "")
        + "\n"
    )

    if not shown:
        return [header + "\nTidak ada setup pada candle ini."]

    blocks = []
    for i, s in enumerate(shown, 1):
        link = f"https://www.binance.com/en/trade/{s.symbol[:-4]}_USDT?type=spot"
        blocks.append(
            f"{i}. 🟢 <a href=\"{link}\"><b>{html.escape(s.symbol)}</b></a> "
            f"— Vol <b>{s.volume_ratio:.1f}x</b>\n"
            f"   Support: ${base.fmt_price(s.support)} "
            f"(low {s.distance_pct:+.2f}%)\n"
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
          f"Setup: {len(setups)}, error: {errors}")

    if not setups and not SEND_IF_EMPTY:
        print("[INFO] Tidak ada setup, tidak mengirim notifikasi.")
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
