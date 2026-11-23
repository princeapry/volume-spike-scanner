"""
Screening sekali-jalan: koin top 1000 market cap yang BARU cross & close di
atas EMA60 harian (dijalankan manual, bukan workflow).

Cara kerja:
1. Ambil top 1000 koin by market cap dari CoinGecko (/coins/markets,
   250 koin per halaman x 4 halaman, tanpa API key).
2. Cocokkan simbol CoinGecko ke pair <SIMBOL>USDT yang aktif di Binance Spot.
   Koin yang tidak punya pair USDT di Binance di-skip (jumlahnya dilaporkan).
3. Untuk tiap pair, ambil candle harian (1d) dari Binance, buang candle hari
   ini yang masih berjalan, lalu hitung EMA60 dari close harian.
4. Sinyal "fresh cross": candle harian terakhir yang SUDAH CLOSE punya
   close > EMA60, DAN candle sebelumnya close <= EMA60.
5. Hasil diurutkan by market cap, dicetak ke layar dan disimpan ke
   ema_cross_screen.md.

Cara pakai:
    python screen_ema.py
    python screen_ema.py --top 500 --ema 60
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

import scanner as base  # helper request Binance (retry, fallback URL, rate limit)
import signals

COINGECKO_URL = "https://api.coingecko.com/api/v3/coins/markets"
PER_PAGE = 250              # maksimum per halaman CoinGecko
REPORT_FILE = "ema_cross_screen.md"
KLINE_LIMIT = 1000          # candle harian sebanyak mungkin supaya EMA sudah "stabil"


# ---------------------------------------------------------------------------
# CoinGecko
# ---------------------------------------------------------------------------

def coingecko_get(params: dict) -> list:
    """GET ke CoinGecko dengan retry. API gratis tanpa key rate limit-nya ketat,
    jadi kalau kena 429 kita tunggu lalu coba lagi."""
    last_error: Exception | None = None
    for attempt in range(1, 6):
        try:
            resp = requests.get(COINGECKO_URL, params=params, timeout=30,
                                headers={"Accept": "application/json"})
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "0") or 0) or 20 * attempt
                print(f"[WARN] CoinGecko rate limit, tunggu {wait} detik...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            time.sleep(5 * attempt)
    raise RuntimeError(f"Gagal request ke CoinGecko: {last_error}")


def get_top_coins(top: int) -> list[dict]:
    """Top `top` koin by market cap (urut dari terbesar)."""
    coins: list[dict] = []
    pages = (top + PER_PAGE - 1) // PER_PAGE
    for page in range(1, pages + 1):
        batch = coingecko_get({
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": PER_PAGE,
            "page": page,
        })
        coins.extend(batch)
        print(f"[INFO] CoinGecko halaman {page}/{pages}: {len(batch)} koin")
        if len(batch) < PER_PAGE:
            break
        time.sleep(3)  # jeda antar halaman supaya tidak kena rate limit
    return coins[:top]


# ---------------------------------------------------------------------------
# Binance
# ---------------------------------------------------------------------------

def get_binance_usdt_pairs() -> set[str]:
    """Semua pair spot dengan quote USDT yang statusnya TRADING."""
    data = base.binance_get("/api/v3/exchangeInfo", {"permissions": "SPOT"})
    return {
        s["symbol"]
        for s in data.get("symbols", [])
        if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
    }


def ema_series(values: list[float], period: int) -> list[float | None]:
    """EMA standar: diawali SMA `period` nilai pertama, lalu
    EMA = close x k + EMA_sebelumnya x (1 - k), dengan k = 2 / (period + 1).
    Index sebelum EMA pertama terbentuk berisi None."""
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    result[period - 1] = ema
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
        result[i] = ema
    return result


@dataclass
class Cross:
    rank: int
    name: str
    symbol: str       # simbol koin (BTC)
    pair: str         # pair Binance (BTCUSDT)
    close: float      # close candle harian terakhir yang sudah close
    ema: float        # EMA pada candle itu
    above_pct: float  # % close di atas EMA
    candle_date: str  # tanggal candle (UTC)
    days_data: int    # jumlah candle harian yang dipakai menghitung EMA


def check_pair(coin: dict, pair: str, period: int) -> Cross | None | str:
    """Cek satu pair. Return Cross kalau fresh cross, None kalau tidak,
    atau string "short" kalau data candle belum cukup untuk EMA."""
    klines = base.binance_get(
        "/api/v3/klines", {"symbol": pair, "interval": "1d", "limit": KLINE_LIMIT}
    )
    # Buang candle hari ini yang masih berjalan
    closed = signals.only_closed(klines)
    # Butuh EMA di candle terakhir DAN candle sebelumnya
    if len(closed) < period + 1:
        return "short"

    closes = [float(k[signals.K_CLOSE]) for k in closed]
    ema = ema_series(closes, period)

    last_close, last_ema = closes[-1], ema[-1]
    prev_close, prev_ema = closes[-2], ema[-2]
    if not (last_close > last_ema and prev_close <= prev_ema):
        return None

    date = datetime.fromtimestamp(int(closed[-1][signals.K_OPEN_TIME]) / 1000, timezone.utc)
    return Cross(
        rank=coin.get("market_cap_rank") or 0,
        name=coin.get("name", ""),
        symbol=coin["symbol"].upper(),
        pair=pair,
        close=last_close,
        ema=last_ema,
        above_pct=(last_close / last_ema - 1) * 100,
        candle_date=f"{date:%Y-%m-%d}",
        days_data=len(closed),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Screening fresh cross di atas EMA harian")
    parser.add_argument("--top", type=int, default=1000, help="jumlah koin top market cap")
    parser.add_argument("--ema", type=int, default=60, help="periode EMA")
    args = parser.parse_args()
    started = time.time()

    coins = get_top_coins(args.top)
    pairs = get_binance_usdt_pairs()

    # Cocokkan ke pair Binance. Kalau ada beberapa koin dengan simbol sama
    # (misal token tiruan), hanya yang market cap-nya terbesar yang dipakai,
    # karena pair Binance hampir pasti merujuk ke koin yang besar itu.
    targets: list[tuple[dict, str]] = []
    used_pairs: set[str] = set()
    not_on_binance: list[dict] = []
    duplicates = 0
    for coin in coins:
        pair = coin["symbol"].upper() + "USDT"
        if pair not in pairs:
            not_on_binance.append(coin)
        elif pair in used_pairs:
            duplicates += 1
        else:
            used_pairs.add(pair)
            targets.append((coin, pair))

    print(f"[INFO] {len(coins)} koin dari CoinGecko | {len(targets)} punya pair USDT "
          f"di Binance | {len(not_on_binance)} di-skip (tidak ada di Binance)"
          + (f" | {duplicates} simbol duplikat di-skip" if duplicates else ""))

    crosses: list[Cross] = []
    short_data: list[str] = []
    errors = 0
    with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:
        futures = {pool.submit(check_pair, c, p, args.ema): p for c, p in targets}
        for future in as_completed(futures):
            pair = futures[future]
            try:
                result = future.result()
                if isinstance(result, Cross):
                    crosses.append(result)
                elif result == "short":
                    short_data.append(pair)
            except Exception as exc:  # satu pair gagal jangan menghentikan screening
                errors += 1
                print(f"[WARN] {pair}: {exc}")

    # Urutkan by market cap (rank kecil = market cap besar)
    crosses.sort(key=lambda x: x.rank or 10**9)

    now = datetime.now(timezone.utc)
    candle_date = crosses[0].candle_date if crosses else "-"
    lines = [
        f"# Fresh Cross & Close di Atas EMA{args.ema} Harian",
        "",
        f"- Dibuat: {now:%Y-%m-%d %H:%M} UTC (durasi {time.time() - started:.0f} detik)",
        f"- Candle yang dicek: candle harian terakhir yang sudah close ({candle_date}, UTC)",
        f"- Syarat: close > EMA{args.ema} DAN close candle sebelumnya ≤ EMA{args.ema}",
        f"- Universe: top {len(coins)} CoinGecko → {len(targets)} punya pair USDT di Binance",
        f"- Di-skip: {len(not_on_binance)} tidak ada di Binance"
        + (f", {duplicates} simbol duplikat" if duplicates else "")
        + (f", {len(short_data)} data harian < {args.ema + 1} candle (listing baru)"
           if short_data else "")
        + (f", {errors} error" if errors else ""),
        f"- Hasil: **{len(crosses)} koin**",
        "",
        f"| Rank | Simbol | Nama | Harga (USDT) | EMA{args.ema} | % di atas EMA |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for c in crosses:
        lines.append(
            f"| {c.rank} | {c.symbol} | {c.name} | {base.fmt_price(c.close)} | "
            f"{base.fmt_price(c.ema)} | {c.above_pct:+.2f}% |"
        )
    if not crosses:
        lines.append("| - | - | Tidak ada koin yang baru cross | - | - | - |")
    if short_data:
        lines += ["", f"Data belum cukup untuk EMA{args.ema}: {', '.join(sorted(short_data))}"]

    report = "\n".join(lines) + "\n"
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    print()
    print(report)
    print(f"[INFO] Disimpan ke {REPORT_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
