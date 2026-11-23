"""
Screening bullish divergence RSI (dijalankan manual, bisa diulang kapan saja).

Tujuan: cari koin yang harganya membuat low LEBIH RENDAH tapi RSI-nya membuat
low LEBIH TINGGI (bullish divergence), sebagai kandidat untuk dianalisa manual.

Cara kerja:
1. Universe sama dengan scanner lain: pair USDT aktif di Binance Spot, buang
   stablecoin & leveraged token, buang token saham/ETF/emas, saring volume 24 jam.
2. Untuk tiap pair, ambil candle (default 4h) yang SUDAH CLOSE, hitung RSI 14
   (metode Wilder) dari close.
3. Ambil DIV_LOOKBACK candle terakhir (default 30), bagi 2 paruh sama panjang,
   lalu cari candle dengan low terendah di tiap paruh.
4. Bullish divergence = low paruh TERBARU < low paruh LAMA,
   TAPI RSI di candle low terbaru > RSI di candle low lama.
5. Hasil diurutkan dari selisih RSI terbesar, dicetak ke layar, dan disimpan
   ke divergence_screen.md (ditimpa tiap run).

Pengaturan lewat environment variable (semua opsional):
    DIV_TF          timeframe candle (default 4h)
    DIV_RSI_PERIOD  periode RSI (default 14)
    DIV_LOOKBACK    jumlah candle yang dibagi 2 paruh (default 30)
    MIN_24H_QUOTE_VOLUME  filter likuiditas, sama dengan scanner (default 500000)
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

import accumulation_scanner as accum  # blocklist saham/ETF/emas
import scanner as base                # request Binance, universe, format angka
import signals
from signals import _env_int, _env_str

TIMEFRAME = _env_str("DIV_TF", "4h")
RSI_PERIOD = _env_int("DIV_RSI_PERIOD", 14)
LOOKBACK = _env_int("DIV_LOOKBACK", 30)
REPORT_FILE = "divergence_screen.md"

# Candle tambahan sebelum lookback supaya RSI Wilder sudah "stabil"
# (RSI butuh pemanasan; makin banyak data makin dekat dengan nilai di chart).
RSI_WARMUP = 200


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

def rsi_series(closes: list[float], period: int) -> list[float | None]:
    """RSI Wilder (sama dengan RSI default TradingView/Binance).
    Rata-rata gain/loss awal = rata-rata sederhana `period` perubahan pertama,
    selanjutnya dihaluskan: avg = (avg_sebelumnya x (period-1) + nilai) / period.
    Index yang RSI-nya belum terbentuk berisi None."""
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result

    def to_rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        return 100 - 100 / (1 + gain / loss)

    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(c, 0) for c in changes[:period]) / period
    avg_loss = sum(max(-c, 0) for c in changes[:period]) / period
    result[period] = to_rsi(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        c = changes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(c, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-c, 0)) / period
        result[i] = to_rsi(avg_gain, avg_loss)
    return result


# ---------------------------------------------------------------------------
# Deteksi divergence
# ---------------------------------------------------------------------------

@dataclass
class Divergence:
    symbol: str
    price: float          # close candle terakhir yang sudah close
    old_low: float
    old_rsi: float
    old_time: int         # open time candle low lama (ms)
    new_low: float
    new_rsi: float
    new_time: int         # open time candle low baru (ms)
    rsi_now: float        # RSI candle terakhir

    @property
    def low_change_pct(self) -> float:
        return (self.new_low / self.old_low - 1) * 100

    @property
    def rsi_diff(self) -> float:
        return self.new_rsi - self.old_rsi


def find_divergence(closed: list, lookback: int, period: int) -> tuple | None:
    """Fungsi murni (tanpa internet). Input candle close urut lama -> baru.
    Return (idx_low_lama, idx_low_baru, rsi list) kalau bullish divergence."""
    half = lookback // 2
    if half < 2 or len(closed) < 2 * half + period + 1:
        return None
    closes = [float(k[signals.K_CLOSE]) for k in closed]
    lows = [float(k[signals.K_LOW]) for k in closed]
    rsi = rsi_series(closes, period)

    n = len(closed)
    old_range = range(n - 2 * half, n - half)   # paruh lama
    new_range = range(n - half, n)              # paruh terbaru
    i_old = min(old_range, key=lambda i: lows[i])
    i_new = min(new_range, key=lambda i: lows[i])
    if rsi[i_old] is None or rsi[i_new] is None:
        return None

    # Harga: low baru lebih rendah. RSI: lebih tinggi di low baru.
    if lows[i_new] < lows[i_old] and rsi[i_new] > rsi[i_old]:
        return i_old, i_new, rsi
    return None


def check_symbol(symbol: str) -> Divergence | None:
    """Ambil candle satu pair lalu cek divergence."""
    limit = min(LOOKBACK + RSI_WARMUP + 1, 1000)  # +1 untuk candle yang masih berjalan
    klines = base.binance_get(
        "/api/v3/klines", {"symbol": symbol, "interval": TIMEFRAME, "limit": limit}
    )
    closed = signals.only_closed(klines)
    found = find_divergence(closed, LOOKBACK, RSI_PERIOD)
    if found is None:
        return None
    i_old, i_new, rsi = found
    return Divergence(
        symbol=symbol,
        price=float(closed[-1][signals.K_CLOSE]),
        old_low=float(closed[i_old][signals.K_LOW]),
        old_rsi=rsi[i_old],
        old_time=int(closed[i_old][signals.K_OPEN_TIME]),
        new_low=float(closed[i_new][signals.K_LOW]),
        new_rsi=rsi[i_new],
        new_time=int(closed[i_new][signals.K_OPEN_TIME]),
        rsi_now=rsi[-1],
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fmt_time(ms: int) -> str:
    """Waktu open candle dalam zona waktu TIMEZONE (sama dengan alert Telegram)."""
    return base.local_time(ms)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    started = time.time()

    # Universe sama dengan scanner lain
    all_symbols = base.get_usdt_symbols()
    symbols = [s for s in all_symbols if not accum.is_blocked(s)]
    vols = base.get_24h_quote_volumes()
    targets = [s for s in symbols if vols.get(s, 0) >= base.MIN_24H_QUOTE_VOLUME]
    print(f"[INFO] {len(all_symbols)} pair USDT aktif, {len(all_symbols) - len(symbols)} "
          f"saham/ETF/emas dibuang, {len(targets)} lolos filter volume 24 jam "
          f">= {base.MIN_24H_QUOTE_VOLUME:,.0f} USDT")

    results: list[Divergence] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol, s): s for s in targets}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                r = future.result()
                if r:
                    results.append(r)
            except Exception as exc:  # satu pair gagal jangan menghentikan screening
                errors += 1
                print(f"[WARN] {symbol}: {exc}")

    # Urutkan dari selisih RSI terbesar (divergence paling jelas di atas)
    results.sort(key=lambda d: d.rsi_diff, reverse=True)

    now = datetime.now(timezone.utc)
    lines = [
        f"# Bullish Divergence RSI ({TIMEFRAME})",
        "",
        f"- Dibuat: {fmt_time(int(now.timestamp() * 1000))} "
        f"(durasi {time.time() - started:.0f} detik)",
        f"- Syarat: dari {LOOKBACK} candle {TIMEFRAME} terakhir yang sudah close (2 paruh "
        f"x {LOOKBACK // 2}), low paruh baru < low paruh lama TAPI RSI{RSI_PERIOD} "
        f"di low baru > RSI di low lama",
        f"- Universe: {len(targets)} pair USDT (filter sama dengan scanner)"
        + (f" | error: {errors}" if errors else ""),
        f"- Hasil: **{len(results)} kandidat** (urut dari selisih RSI terbesar)",
        "",
        "> Ini hanya daftar kandidat untuk dianalisa manual, bukan sinyal beli. "
        "Divergence bisa gagal, terutama kalau tren turun masih kuat.",
        "",
        "| # | Pair | Harga | Low lama (waktu) | Low baru (waktu) | Δ low | "
        "RSI lama → baru | RSI kini | Link |",
        "|---:|---|---:|---|---|---:|---|---:|---|",
    ]
    for i, d in enumerate(results, 1):
        link = f"https://www.binance.com/en/trade/{d.symbol[:-4]}_USDT?type=spot"
        lines.append(
            f"| {i} | {d.symbol} | {base.fmt_price(d.price)} | "
            f"{base.fmt_price(d.old_low)} ({fmt_time(d.old_time)}) | "
            f"{base.fmt_price(d.new_low)} ({fmt_time(d.new_time)}) | "
            f"{d.low_change_pct:+.2f}% | {d.old_rsi:.1f} → {d.new_rsi:.1f} "
            f"(+{d.rsi_diff:.1f}) | {d.rsi_now:.1f} | [Binance]({link}) |"
        )
    if not results:
        lines.append("| - | Tidak ada kandidat | | | | | | | |")

    report = "\n".join(lines) + "\n"
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    print()
    print(report)
    print(f"[INFO] Disimpan ke {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
