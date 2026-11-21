"""
Confluence Scanner: "mesin confluence lintas-kategori".
Deteksi pair yang kena LEBIH DARI SATU jenis sinyal sekaligus, dari tiga
kategori yang masing-masing punya scanner sendiri di repo ini:

1. Volume Spike  -> definisi & ambang sama dengan scanner.py         (default candle 1h)
2. Reversal      -> definisi & ambang sama dengan reversal_scanner.py (default candle 4h)
                    bullish engulfing di support + konfirmasi volume
3. Akumulasi     -> definisi & ambang sama dengan accumulation_scanner.py (default candle 4h)
                    flat + volume naik + dekat base (syarat inti saja, tanpa skor)

Cara kerja singkat:
1. Ambil universe pair yang SAMA dengan scanner lain (pair USDT aktif, buang
   stablecoin & leveraged token, saring volume 24 jam).
2. Untuk tiap pair, ambil candle per timeframe yang dibutuhkan SEKALI saja
   (1h & 4h; kalau dua kategori pakai timeframe sama, candle-nya dipakai
   bersama), lalu evaluasi ketiga kategori dalam satu lintasan.
3. Hitung pair kena berapa kategori. Alert hanya pair yang kena
   >= CONFLUENCE_MIN_CATEGORIES (default 2), dengan penanda "🎯 MULTI-SIGNAL".
4. Urutkan dari jumlah kategori terbanyak, lalu volume 24 jam terbesar, lalu
   kirim ke Telegram.

Scanner ini TIDAK mengubah tiga scanner lain: konfigurasi & fungsi deteksinya
hanya di-import (dibaca), jadi kalau ambang scanner lain diubah lewat env,
scanner ini ikut memakai ambang yang sama.
Semua pengaturan dibaca dari environment variable (lihat README.md).
"""

from __future__ import annotations

import html
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import accumulation_scanner as accum   # konfigurasi & daftar blokir scanner akumulasi
import reversal_scanner as reversal    # konfigurasi & evaluate() scanner reversal
import scanner as base                 # helper bersama + konfigurasi volume spike
import signals
from signals import _env_bool, _env_int


# ---------------------------------------------------------------------------
# Konfigurasi (semua bisa diubah lewat environment variable / GitHub Variables)
# ---------------------------------------------------------------------------
# Ambang tiap kategori TIDAK punya env sendiri: ikut env scanner aslinya
# (SPIKE_MULTIPLIER, SWING_LOOKBACK, ACCUM_WINDOW, dst.).

MIN_CATEGORIES = _env_int("CONFLUENCE_MIN_CATEGORIES", 2)            # minimal kategori untuk alert
MAX_ALERTS = _env_int("CONFLUENCE_MAX_ALERTS", base.MAX_ALERTS)      # maks pair per notifikasi
# CONFLUENCE_SEND_IF_EMPTY lebih diutamakan; kalau kosong ikut SEND_IF_EMPTY
SEND_IF_EMPTY = _env_bool("CONFLUENCE_SEND_IF_EMPTY", base.SEND_IF_EMPTY)

LABEL = "🎯 MULTI-SIGNAL"

# Nama kategori (urutan ini juga urutan tampil di alert)
CAT_SPIKE = "Volume Spike"
CAT_REVERSAL = "Reversal"
CAT_ACCUM = "Akumulasi"
CATEGORIES = (CAT_SPIKE, CAT_REVERSAL, CAT_ACCUM)

# Timeframe tiap kategori (ikut scanner aslinya)
SPIKE_INTERVAL = base.INTERVAL
REVERSAL_INTERVAL = reversal.INTERVAL
ACCUM_INTERVAL = accum.INTERVAL

# Jumlah candle CLOSE yang dibutuhkan tiap kategori
SPIKE_NEEDED = base.LOOKBACK + 1
REVERSAL_NEEDED = max(reversal.SWING_LOOKBACK, reversal.VOLUME_LOOKBACK, 2) + 1
ACCUM_NEEDED = max(accum.WINDOW, accum.BASE_LOOKBACK)


def candle_limits() -> dict[str, int]:
    """Jumlah candle yang di-request per timeframe. Kategori dengan timeframe
    sama berbagi satu request (ambil yang terbanyak). +1 untuk candle yang
    mungkin masih berjalan."""
    limits: dict[str, int] = {}
    for interval, needed in (
        (SPIKE_INTERVAL, SPIKE_NEEDED),
        (REVERSAL_INTERVAL, REVERSAL_NEEDED),
        (ACCUM_INTERVAL, ACCUM_NEEDED),
    ):
        limits[interval] = max(limits.get(interval, 0), needed + 1)
    return limits


LIMITS = candle_limits()


# ---------------------------------------------------------------------------
# Deteksi
# ---------------------------------------------------------------------------

@dataclass
class Hit:
    """Pair yang kena satu atau lebih kategori."""
    symbol: str
    close_price: float
    volume_24h: float
    # Kategori yang nyala -> ringkasan angka utamanya (untuk ditampilkan)
    categories: dict[str, str] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.categories)

    def names(self) -> list[str]:
        """Nama kategori yang nyala, urut sesuai CATEGORIES."""
        return [c for c in CATEGORIES if c in self.categories]


def evaluate_categories(symbol: str, candles: dict[str, list]) -> dict[str, str]:
    """
    Evaluasi ketiga kategori untuk satu pair. `candles` = {interval: candle close}.
    Return {nama kategori: ringkasan} untuk kategori yang nyala.
    """
    result: dict[str, str] = {}

    # 1) Volume spike (sama persis dengan trigger scanner.py)
    spike = signals.check_volume_spike(
        candles[SPIKE_INTERVAL],
        base.SPIKE_MULTIPLIER, base.LOOKBACK, base.MIN_CANDLE_QUOTE_VOLUME,
    )
    if spike is not None:
        result[CAT_SPIKE] = f"spike {spike.ratio:.1f}x ({SPIKE_INTERVAL})"

    # 2) Reversal (sama persis dengan trigger reversal_scanner.py)
    rev = reversal.evaluate(candles[REVERSAL_INTERVAL])
    if rev is not None:
        _support, distance_pct, ratio = rev
        result[CAT_REVERSAL] = (
            f"engulfing di support {distance_pct:+.1f}%, vol {ratio:.1f}x ({REVERSAL_INTERVAL})"
        )

    # 3) Akumulasi (syarat inti accumulation_scanner.py). Token saham/ETF/emas
    #    yang diblokir di scanner akumulasi juga tidak dihitung di sini.
    if not accum.is_blocked(symbol):
        acc = signals.check_accumulation(
            candles[ACCUM_INTERVAL],
            accum.WINDOW, accum.MAX_RANGE_PCT, accum.VOL_RISE_FACTOR,
            accum.BASE_LOOKBACK, accum.MAX_POSITION,
        )
        if acc is not None:
            result[CAT_ACCUM] = (
                f"vol naik {acc.volume_rise:.2f}x, rentang {acc.range_pct:.1f}%, "
                f"posisi {acc.position * 100:.0f}% ({ACCUM_INTERVAL})"
            )

    return result


def check_symbol(symbol: str, volume_24h: float) -> Hit | None:
    """Ambil candle per timeframe (sekali saja per timeframe) lalu evaluasi.
    Return Hit kalau minimal satu kategori nyala, None kalau tidak."""
    candles: dict[str, list] = {}
    for interval, limit in LIMITS.items():
        klines = base.binance_get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": limit},
        )
        candles[interval] = signals.only_closed(klines)

    cats = evaluate_categories(symbol, candles)
    if not cats:
        return None

    # Harga terbaru diambil dari candle close yang paling baru (biasanya 1h)
    freshest = max(
        (c for c in candles.values() if c),
        key=lambda c: int(c[-1][signals.K_CLOSE_TIME]),
        default=None,
    )
    close_price = float(freshest[-1][signals.K_CLOSE]) if freshest else 0.0
    return Hit(symbol=symbol, close_price=close_price, volume_24h=volume_24h, categories=cats)


def scan() -> tuple[list[Hit], int, int, dict[str, int]]:
    """
    Jalankan scan ke semua pair (universe sama dengan scanner lain).
    Return: (daftar multi-signal terurut, jumlah pair yang discan, jumlah error,
             jumlah pair per kategori untuk info header)
    """
    symbols = base.get_usdt_symbols()
    volumes_24h = base.get_24h_quote_volumes()

    targets = [s for s in symbols if volumes_24h.get(s, 0) >= base.MIN_24H_QUOTE_VOLUME]
    print(f"[INFO] {len(symbols)} pair USDT aktif, {len(targets)} lolos filter "
          f"volume 24 jam >= {base.MIN_24H_QUOTE_VOLUME:,.0f} USDT")
    print(f"[INFO] Candle per pair: " + ", ".join(f"{iv} x {n}" for iv, n in LIMITS.items()))

    hits: list[Hit] = []
    errors = 0

    with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol, s, volumes_24h.get(s, 0)): s for s in targets}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result:
                    hits.append(result)
            except Exception as exc:  # satu pair gagal jangan menghentikan scan
                errors += 1
                print(f"[WARN] {symbol}: {exc}")

    # Statistik per kategori (termasuk pair yang cuma kena 1 kategori)
    per_category = {c: sum(1 for h in hits if c in h.categories) for c in CATEGORIES}
    print("[INFO] Pair per kategori: " + ", ".join(f"{c} {n}" for c, n in per_category.items()))

    # Alert hanya pair yang kena >= MIN_CATEGORIES kategori
    multi = [h for h in hits if h.count >= MIN_CATEGORIES]
    # Urutkan: kategori terbanyak dulu, lalu volume 24 jam terbesar
    multi.sort(key=lambda h: (h.count, h.volume_24h), reverse=True)
    return multi, len(targets), errors, per_category


# ---------------------------------------------------------------------------
# Format pesan Telegram
# ---------------------------------------------------------------------------

def build_messages(
    hits: list[Hit], scanned: int, errors: int, per_category: dict[str, int]
) -> list[str]:
    """Susun pesan Telegram (HTML), dipecah kalau melebihi batas 4096 karakter."""
    shown = hits[:MAX_ALERTS]
    now = base.local_time(int(time.time() * 1000))
    triple = sum(1 for h in hits if h.count == len(CATEGORIES))

    header = (
        f"{LABEL} <b>Confluence Lintas-Kategori</b>\n"
        f"🕒 Scan: {html.escape(now)}\n"
        f"⚙️ Kategori: {CAT_SPIKE} ({html.escape(SPIKE_INTERVAL)}) · "
        f"{CAT_REVERSAL} ({html.escape(REVERSAL_INTERVAL)}) · "
        f"{CAT_ACCUM} ({html.escape(ACCUM_INTERVAL)}) | alert ≥ {MIN_CATEGORIES} kategori\n"
        f"📊 Per kategori: "
        + " · ".join(f"{c} {n}" for c, n in per_category.items())
        + "\n"
        f"🔎 Discan: {scanned} pair | Multi-signal: {len(hits)}"
        + (f" | 3 kategori: {triple}" if triple else "")
        + (f" | Error: {errors}" if errors else "")
        + "\n"
    )

    if not shown:
        return [header + "\nTidak ada pair yang kena lebih dari satu kategori."]

    blocks = []
    for i, h in enumerate(shown, 1):
        link = f"https://www.binance.com/en/trade/{h.symbol[:-4]}_USDT?type=spot"
        names = h.names()
        details = "\n".join(f"   • {c}: {html.escape(h.categories[c])}" for c in names)
        blocks.append(
            f"{i}. {LABEL} <a href=\"{link}\"><b>{html.escape(h.symbol)}</b></a> "
            f"— <b>{h.count}/{len(CATEGORIES)}</b>\n"
            f"   <b>{html.escape(' + '.join(names))}</b>\n"
            f"{details}\n"
            f"   Harga: ${base.fmt_price(h.close_price)} | "
            f"24j: {base.fmt_usd(h.volume_24h)} USDT\n"
        )

    footer = ""
    if len(hits) > len(shown):
        footer = f"\n…dan {len(hits) - len(shown)} pair lainnya."

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
        hits, scanned, errors, per_category = scan()
    except Exception as exc:
        print(f"[ERROR] Scan gagal: {exc}")
        return 1

    print(f"[INFO] Selesai dalam {time.time() - started:.1f} detik. "
          f"Multi-signal: {len(hits)}, error: {errors}")

    if not hits and not SEND_IF_EMPTY:
        print("[INFO] Tidak ada pair multi-signal, tidak mengirim notifikasi.")
        return 0

    messages = build_messages(hits, scanned, errors, per_category)

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
