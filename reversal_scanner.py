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
3. Untuk tiap setup, hitung skor confluence 0-3 (volume spike, bullish
   engulfing di support, buy pressure) sebagai info tambahan. Skor TIDAK
   mengubah trigger di atas, hanya urutan & tampilan alert.
4. Kirim hasilnya ke Telegram.

File ini sengaja terpisah dari scanner.py dan punya jadwal sendiri. Helper
umum (request Binance, daftar pair, format angka, kirim Telegram) di-import
dari scanner.py, sedangkan logika deteksi sinyal ada di signals.py supaya
kedua scanner memakai definisi yang sama.
Semua pengaturan dibaca dari environment variable (lihat README.md).
"""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import scanner as base  # helper bersama; aman di-import karena main() tidak ikut jalan
import alert_state
import signals
from signals import _env_bool, _env_float, _env_int, _env_str


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

# Ambang skor confluence (env CONF_*, sama dengan scanner.py). Hanya info
# tambahan di alert, TIDAK mengubah trigger reversal.
CONF = signals.ConfluenceConfig.from_env()


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
    confluence: signals.Confluence  # skor 0-3 (info tambahan, bukan trigger)


def evaluate(closed: list) -> tuple[float, float, float] | None:
    """
    Cek candle terakhir di `closed` (semua candle sudah close, urut lama->baru).
    Return (support, jarak %, rasio volume) kalau ketiga syarat terpenuhi,
    None kalau tidak. Definisi pola & swing low ada di signals.py.
    """
    needed = max(SWING_LOOKBACK, VOLUME_LOOKBACK, 2) + 1
    if len(closed) < needed:
        return None

    # 1) + 3) Bullish engulfing yang low-nya dekat swing low (support)
    found = signals.check_engulfing_at_support(
        closed, SWING_LOOKBACK, SWING_WINDOW, SUPPORT_DISTANCE_PCT
    )
    if found is None:
        return None

    # 2) Konfirmasi volume: harus LEBIH DARI VOLUME_MULTIPLIER x rata-rata
    stats = signals.volume_stats(closed, VOLUME_LOOKBACK)
    if stats is None:
        return None
    ratio, _avg = stats
    if ratio <= VOLUME_MULTIPLIER:
        return None

    return found.support, found.distance_pct, ratio


def check_symbol(symbol: str, volume_24h: float) -> Setup | None:
    """Ambil candle satu pair lalu evaluasi. None kalau tidak ada setup."""
    # Ambil cukup candle untuk trigger reversal DAN skor confluence,
    # +1 untuk candle yang mungkin masih berjalan
    limit = max(max(SWING_LOOKBACK, VOLUME_LOOKBACK, 2) + 1, CONF.candles_needed) + 1
    klines = base.binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": INTERVAL, "limit": limit},
    )

    # Buang candle yang belum close (closeTime masih di masa depan)
    closed = signals.only_closed(klines)

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
        confluence=signals.evaluate_confluence(closed, CONF),
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

    # Urutkan dari skor confluence tertinggi, lalu konfirmasi volume terkuat
    setups.sort(key=lambda x: (x.confluence.score, x.volume_ratio), reverse=True)
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
    hot = sum(1 for s in setups if signals.is_hot(s.confluence, CONF))

    header = (
        f"🔄 <b>Bullish Engulfing di Support ({html.escape(INTERVAL)})</b>\n"
        f"🕒 Candle: {html.escape(candle_time)}\n"
        f"⚙️ Support ≤ {SUPPORT_DISTANCE_PCT:g}% dari swing low "
        f"({SWING_LOOKBACK} candle, window {SWING_WINDOW}) | "
        f"Vol > {VOLUME_MULTIPLIER:g}x avg {VOLUME_LOOKBACK}\n"
        f"{html.escape(signals.confluence_legend(CONF))}\n"
        f"🔎 Discan: {scanned} pair | Setup: {len(setups)}"
        + (f" | 🔥 Confluence: {hot}" if hot else "")
        + (f" | Error: {errors}" if errors else "")
        + "\n"
    )

    if not shown:
        return [header + "\nTidak ada setup pada candle ini."]

    blocks = []
    for i, s in enumerate(shown, 1):
        link = f"https://www.binance.com/en/trade/{s.symbol[:-4]}_USDT?type=spot"
        tag = f" <b>{signals.CONFLUENCE_TAG}</b>" if signals.is_hot(s.confluence, CONF) else ""
        blocks.append(
            f"{i}. 🟢 <a href=\"{link}\"><b>{html.escape(s.symbol)}</b></a> "
            f"— Vol <b>{s.volume_ratio:.1f}x</b>{tag}\n"
            f"   {html.escape(signals.confluence_line(s.confluence))}\n"
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

    # Buang pair yang sudah dialertkan dalam ALERT_COOLDOWN_HOURS terakhir.
    # Set ALERT_COOLDOWN_HOURS=0 untuk mematikan dedup.
    setups, _ = alert_state.filter_new("reversal", setups)

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
