"""
Backtest historis sinyal scanner (sekali jalan, dijalankan manual).

Untuk tiap jenis sinyal (volume spike, reversal, akumulasi), script ini
"memutar ulang" candle historis Binance Spot secara walk-forward:

1. Ambil universe pair yang sama dengan scanner live (pair USDT aktif, buang
   stablecoin & leveraged token, buang token saham/ETF/emas, saring volume 24 jam).
2. Ambil candle historis (volume spike 1h; reversal & akumulasi 4h) untuk
   periode backtest + candle "pemanasan" di depannya.
3. Jalan maju per candle. Di candle ke-i, fungsi deteksi HANYA diberi candle
   sampai i (potongan dengan panjang sama seperti yang diambil scanner live),
   jadi tidak ada data masa depan yang ikut (tanpa look-ahead bias).
4. Kalau sinyal nyala di candle i, harga entry = close candle i, lalu diukur
   gerakan 24 jam & 7 hari SETELAHNYA: perubahan %, max naik %, max turun %.
5. Ringkasan per sinyal ditulis ke backtest_report.md dan ditampilkan di layar.
   Detail tiap kejadian disimpan di backtest_events.csv.

Logika deteksi diambil langsung dari kode live (signals.py, reversal_scanner.py,
accumulation_scanner.py) dengan parameter/env var yang sama, jadi definisinya
persis sama dengan scanner.

Cara pakai:
    python backtest.py              # default 90 hari
    python backtest.py --days 30    # lebih cepat, 1 bulan
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

import accumulation_scanner as accum
import reversal_scanner as reversal
import scanner as base
import signals

REPORT_FILE = "backtest_report.md"
EVENTS_FILE = "backtest_events.csv"

# Cooldown untuk versi "dedup": kejadian berikutnya pada coin yang sama
# baru dihitung lagi kalau sudah lewat sekian jam dari kejadian sebelumnya.
DEDUP_COOLDOWN_HOURS = 24


# ---------------------------------------------------------------------------
# Definisi sinyal yang dibacktest
# ---------------------------------------------------------------------------
# Tiap sinyal: nama, timeframe, jumlah candle yang dibutuhkan detektor
# (= panjang potongan data yang diberikan, sama seperti limit di scanner live),
# dan fungsi deteksi yang menerima list candle close (urut lama -> baru).

@dataclass(frozen=True)
class SignalDef:
    name: str
    interval: str
    needed: int
    detect: callable


SIGNAL_DEFS = [
    SignalDef(
        name="Volume spike",
        interval=base.INTERVAL,  # default 1h
        needed=base.LOOKBACK + 1,
        detect=lambda c: signals.check_volume_spike(
            c, base.SPIKE_MULTIPLIER, base.LOOKBACK, base.MIN_CANDLE_QUOTE_VOLUME
        ),
    ),
    SignalDef(
        name="Reversal",
        interval=reversal.INTERVAL,  # default 4h
        needed=max(reversal.SWING_LOOKBACK, reversal.VOLUME_LOOKBACK, 2) + 1,
        detect=reversal.evaluate,  # trigger live persis: engulfing@support + volume
    ),
    SignalDef(
        name="Akumulasi",
        interval=accum.INTERVAL,  # default 4h
        needed=max(accum.WINDOW, accum.BASE_LOOKBACK),
        detect=lambda c: signals.check_accumulation(
            c, accum.WINDOW, accum.MAX_RANGE_PCT, accum.VOL_RISE_FACTOR,
            accum.BASE_LOOKBACK, accum.MAX_POSITION,
        ),
    ),
]

HORIZONS = (("24j", 24), ("7h", 24 * 7))  # label, jam


# ---------------------------------------------------------------------------
# Ambil data historis
# ---------------------------------------------------------------------------

def fetch_klines(symbol: str, interval: str, start_ms: int) -> list[list[float]]:
    """Ambil semua candle sejak start_ms (paginasi 1000 per request),
    buang candle yang belum close, dan ubah semua kolom jadi float
    supaya perhitungan cepat (fungsi signals.py memanggil float() lagi, aman)."""
    rows: list = []
    start = start_ms
    while True:
        batch = base.binance_get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "startTime": start, "limit": 1000},
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start = int(batch[-1][signals.K_OPEN_TIME]) + 1
    rows = signals.only_closed(rows)
    return [[float(x) for x in k[:11]] for k in rows]


def get_universe() -> list[str]:
    """Universe sama dengan scanner: pair USDT aktif tanpa stablecoin/leveraged,
    tanpa token saham/ETF/emas, dan volume 24 jam >= MIN_24H_QUOTE_VOLUME."""
    symbols = [s for s in base.get_usdt_symbols() if not accum.is_blocked(s)]
    vols = base.get_24h_quote_volumes()
    return [s for s in symbols if vols.get(s, 0) >= base.MIN_24H_QUOTE_VOLUME]


# ---------------------------------------------------------------------------
# Hitung forward return
# ---------------------------------------------------------------------------

def forward(candles: list, i: int, n: int) -> tuple[float, float, float] | None:
    """Gerakan n candle SETELAH candle i, relatif ke close candle i (entry).
    Return (perubahan %, max naik %, max turun %), None kalau datanya belum cukup."""
    if i + n >= len(candles):
        return None
    entry = candles[i][signals.K_CLOSE]
    if entry <= 0:
        return None
    after = candles[i + 1:i + n + 1]
    change = (after[-1][signals.K_CLOSE] / entry - 1) * 100
    max_up = (max(k[signals.K_HIGH] for k in after) / entry - 1) * 100
    max_down = (min(k[signals.K_LOW] for k in after) / entry - 1) * 100
    return change, max_up, max_down


@dataclass
class Event:
    signal: str
    symbol: str
    time_ms: int           # close time candle sinyal (titik T)
    price: float           # close candle sinyal (entry)
    fwd: dict = field(default_factory=dict)  # label horizon -> (chg, up, down) atau None


# ---------------------------------------------------------------------------
# Walk-forward per pair
# ---------------------------------------------------------------------------

def backtest_symbol(symbol: str, period_start_ms: int) -> tuple[list[Event], dict]:
    """Jalankan ketiga sinyal untuk satu pair.
    Return (daftar kejadian, baseline) dengan baseline = forward return SEMUA
    candle di periode (per interval), untuk pembanding."""
    events: list[Event] = []
    baseline: dict[str, dict[str, list]] = {}

    # Ambil data per interval sekali saja (reversal & akumulasi sama-sama 4h)
    max_needed: dict[str, int] = {}
    for sd in SIGNAL_DEFS:
        max_needed[sd.interval] = max(max_needed.get(sd.interval, 0), sd.needed)

    data: dict[str, list] = {}
    for interval, needed in max_needed.items():
        hours = accum.interval_hours(interval) or 1
        warmup_ms = int((needed + 2) * hours * 3600 * 1000)
        data[interval] = fetch_klines(symbol, interval, period_start_ms - warmup_ms)

    for interval, candles in data.items():
        hours = accum.interval_hours(interval) or 1
        steps = {label: round(h / hours) for label, h in HORIZONS}
        base_rows = baseline.setdefault(interval, {label: [] for label, _ in HORIZONS})

        defs = [sd for sd in SIGNAL_DEFS if sd.interval == interval]
        for i, k in enumerate(candles):
            if k[signals.K_OPEN_TIME] < period_start_ms:
                continue  # masih candle pemanasan, belum masuk periode backtest

            fwd = {label: forward(candles, i, n) for label, n in steps.items()}
            for label, f in fwd.items():
                if f is not None:
                    base_rows[label].append(f)

            for sd in defs:
                # HANYA candle sampai i (tanpa data masa depan), panjang sama
                # dengan yang diambil scanner live
                window = candles[max(0, i + 1 - sd.needed):i + 1]
                if sd.detect(window) is None:
                    continue
                events.append(Event(
                    signal=sd.name,
                    symbol=symbol,
                    time_ms=int(k[signals.K_CLOSE_TIME]),
                    price=k[signals.K_CLOSE],
                    fwd=fwd,
                ))
    return events, baseline


# ---------------------------------------------------------------------------
# Ringkasan
# ---------------------------------------------------------------------------

def dedup(events: list[Event]) -> list[Event]:
    """Satu kejadian per coin per DEDUP_COOLDOWN_HOURS (yang paling awal dipakai)."""
    cooldown = DEDUP_COOLDOWN_HOURS * 3600 * 1000
    last: dict[tuple[str, str], int] = {}
    result = []
    for e in sorted(events, key=lambda x: x.time_ms):
        key = (e.signal, e.symbol)
        if key in last and e.time_ms - last[key] < cooldown:
            continue
        last[key] = e.time_ms
        result.append(e)
    return result


def summarize(rows: list[tuple[float, float, float]]) -> dict | None:
    """Statistik dari list (chg, up, down)."""
    if not rows:
        return None
    chg = [r[0] for r in rows]
    return {
        "n": len(rows),
        "avg": statistics.fmean(chg),
        "median": statistics.median(chg),
        "win": sum(1 for c in chg if c > 0) / len(chg) * 100,
        "up": statistics.fmean(r[1] for r in rows),
        "down": statistics.fmean(r[2] for r in rows),
    }


def build_table(label: str, groups: list[tuple[str, str, int, list]]) -> list[str]:
    """Tabel markdown untuk satu horizon.
    groups = [(nama baris, interval, jumlah kejadian total, list forward)]."""
    lines = [
        f"| Sinyal | TF | Kejadian | Ada data {label} | Avg return | Median | "
        f"Win rate | Avg max naik | Avg max turun |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for name, interval, total, rows in groups:
        s = summarize(rows)
        if s is None:
            lines.append(f"| {name} | {interval} | {total:,} | 0 | - | - | - | - | - |")
            continue
        lines.append(
            f"| {name} | {interval} | {total:,} | {s['n']:,} | {s['avg']:+.2f}% | "
            f"{s['median']:+.2f}% | {s['win']:.1f}% | {s['up']:+.2f}% | {s['down']:+.2f}% |"
        )
    return lines


def build_report(events, baseline, days, n_symbols, errors, started, period_start_ms) -> str:
    now = datetime.now(timezone.utc)
    start = datetime.fromtimestamp(period_start_ms / 1000, timezone.utc)
    deduped = dedup(events)

    out = [
        "# Backtest Sinyal Scanner",
        "",
        f"- Dibuat: {now:%Y-%m-%d %H:%M} UTC (durasi {time.time() - started:.0f} detik)",
        f"- Periode sinyal: {start:%Y-%m-%d} s/d {now:%Y-%m-%d} ({days} hari)",
        f"- Universe: {n_symbols} pair USDT (filter sama dengan scanner) | error: {errors}",
        f"- Volume spike: ≥ {base.SPIKE_MULTIPLIER:g}x avg {base.LOOKBACK} candle "
        f"& ≥ {base.MIN_CANDLE_QUOTE_VOLUME:,.0f} USDT ({base.INTERVAL})",
        f"- Reversal: engulfing ≤ {reversal.SUPPORT_DISTANCE_PCT:g}% dari swing low "
        f"({reversal.SWING_LOOKBACK} candle, window {reversal.SWING_WINDOW}) & "
        f"vol > {reversal.VOLUME_MULTIPLIER:g}x avg {reversal.VOLUME_LOOKBACK} ({reversal.INTERVAL})",
        f"- Akumulasi: flat ≤ {accum.MAX_RANGE_PCT:g}% / {accum.WINDOW} candle, "
        f"vol naik ≥ {accum.VOL_RISE_FACTOR:g}x, posisi ≤ {accum.MAX_POSITION * 100:g}% "
        f"range {accum.BASE_LOOKBACK} candle ({accum.INTERVAL})",
        "",
        "> ⚠️ **Disclaimer:** angka di bawah adalah *forward return* sinyal mentah — "
        "perubahan harga dari close candle sinyal ke 24 jam / 7 hari sesudahnya. "
        "Ini **BUKAN PnL trading beneran**: tanpa fee, tanpa slippage, tanpa stop loss/"
        "take profit, dan mengasumsikan entry tepat di harga close. Universe memakai "
        "daftar pair & volume 24 jam **hari ini**, jadi coin yang sudah delisting tidak "
        "ikut (survivorship bias). Hasil masa lalu tidak menjamin hasil ke depan.",
        "",
        "Cara baca: *Kejadian* = jumlah candle yang memicu sinyal. *Ada data* = kejadian "
        "yang sudah punya cukup candle sesudahnya (sinyal di ujung periode belum bisa "
        "diukur 7 hari). *Win rate* = % kejadian yang harganya naik di akhir horizon. "
        f"Baris *dedup* = maksimal 1 kejadian per coin per {DEDUP_COOLDOWN_HOURS} jam "
        "(sinyal yang nyala beruntun dihitung sekali). *Baseline* = rata-rata semua "
        "candle semua coin di periode yang sama, sebagai pembanding: sinyal baru "
        "berguna kalau hasilnya lebih baik dari baseline.",
        "",
    ]

    for label, _hours in HORIZONS:
        title = "24 jam" if label == "24j" else "7 hari"
        groups = []
        for sd in SIGNAL_DEFS:
            ev = [e for e in events if e.signal == sd.name]
            dd = [e for e in deduped if e.signal == sd.name]
            groups.append((sd.name, sd.interval, len(ev),
                           [e.fwd[label] for e in ev if e.fwd.get(label)]))
            groups.append((f"{sd.name} (dedup)", sd.interval, len(dd),
                           [e.fwd[label] for e in dd if e.fwd.get(label)]))
        for interval, rows in baseline.items():
            groups.append(("Baseline (semua candle)", interval, len(rows[label]), rows[label]))
        out.append(f"## Horizon {title}")
        out.append("")
        out.extend(build_table(label, groups))
        out.append("")

    # Contoh kejadian terbaik & terburuk 7 hari (dedup) per sinyal
    out.append("## Kejadian ekstrem 7 hari (dedup)")
    out.append("")
    for sd in SIGNAL_DEFS:
        dd = [e for e in deduped if e.signal == sd.name and e.fwd.get("7h")]
        if not dd:
            continue
        dd.sort(key=lambda e: e.fwd["7h"][0])
        pick = [("terburuk", e) for e in dd[:3]] + [("terbaik", e) for e in dd[-3:][::-1]]
        out.append(f"**{sd.name}**")
        out.append("")
        for tag, e in pick:
            t = datetime.fromtimestamp(e.time_ms / 1000, timezone.utc)
            out.append(f"- {tag}: {e.symbol} @ {t:%Y-%m-%d %H:%M} UTC → "
                       f"{e.fwd['7h'][0]:+.1f}% (max naik {e.fwd['7h'][1]:+.1f}%, "
                       f"max turun {e.fwd['7h'][2]:+.1f}%)")
        out.append("")

    out.append(f"Detail semua kejadian: `{EVENTS_FILE}`")
    return "\n".join(out) + "\n"


def write_events_csv(events: list[Event]) -> None:
    with open(EVENTS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        header = ["signal", "symbol", "time_utc", "price"]
        for label, _ in HORIZONS:
            header += [f"chg_{label}", f"maxup_{label}", f"maxdown_{label}"]
        w.writerow(header)
        for e in sorted(events, key=lambda x: (x.signal, x.time_ms)):
            t = datetime.fromtimestamp(e.time_ms / 1000, timezone.utc)
            row = [e.signal, e.symbol, f"{t:%Y-%m-%d %H:%M}", f"{e.price:g}"]
            for label, _ in HORIZONS:
                f_ = e.fwd.get(label)
                row += [f"{v:.3f}" for v in f_] if f_ else ["", "", ""]
            w.writerow(row)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Backtest historis sinyal scanner")
    parser.add_argument("--days", type=int, default=90, help="panjang periode sinyal (hari)")
    parser.add_argument("--limit", type=int, default=0,
                        help="batasi jumlah pair (0 = semua), untuk tes cepat")
    args = parser.parse_args()

    started = time.time()
    period_start_ms = int((time.time() - args.days * 86400) * 1000)

    symbols = get_universe()
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"[INFO] Backtest {args.days} hari, {len(symbols)} pair...")

    events: list[Event] = []
    baseline: dict[str, dict[str, list]] = {}
    errors = 0
    done = 0

    with ThreadPoolExecutor(max_workers=base.MAX_WORKERS) as pool:
        futures = {pool.submit(backtest_symbol, s, period_start_ms): s for s in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            done += 1
            try:
                ev, bl = future.result()
                events.extend(ev)
                for interval, rows in bl.items():
                    target = baseline.setdefault(interval, {label: [] for label, _ in HORIZONS})
                    for label, vals in rows.items():
                        target[label].extend(vals)
            except Exception as exc:  # satu pair gagal jangan menghentikan backtest
                errors += 1
                print(f"[WARN] {symbol}: {exc}")
            if done % 50 == 0 or done == len(symbols):
                print(f"[INFO] {done}/{len(symbols)} pair selesai "
                      f"({time.time() - started:.0f} detik, {len(events)} kejadian)")

    report = build_report(events, baseline, args.days, len(symbols), errors,
                          started, period_start_ms)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    write_events_csv(events)

    print()
    print(report)
    print(f"[INFO] Report disimpan ke {REPORT_FILE}, detail ke {EVENTS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
