"""
Volume Spike Scanner untuk semua pair USDT di Binance Spot.

Cara kerja singkat:
1. Ambil daftar semua pair USDT yang aktif diperdagangkan (exchangeInfo).
2. Saring pair yang terlalu sepi berdasarkan volume 24 jam (ticker/24hr).
3. Untuk tiap pair, ambil data candle (klines) lalu bandingkan volume
   candle terakhir yang SUDAH CLOSE dengan rata-rata volume N candle sebelumnya.
4. Kalau rasionya >= ambang batas (misal 3x), anggap sebagai "spike".
5. Kirim ringkasan semua spike ke Telegram.

Hanya memakai endpoint market data publik Binance -> tidak butuh API key.
Semua pengaturan dibaca dari environment variable (lihat README.md).
"""

from __future__ import annotations

import html
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests


# ---------------------------------------------------------------------------
# Konfigurasi (semua bisa diubah lewat environment variable)
# ---------------------------------------------------------------------------

def _env_float(name: str, default: float) -> float:
    """Baca env var sebagai float, pakai default kalau kosong/tidak ada."""
    value = os.getenv(name, "").strip()
    return float(value) if value else default


def _env_int(name: str, default: int) -> int:
    """Baca env var sebagai int, pakai default kalau kosong/tidak ada."""
    value = os.getenv(name, "").strip()
    return int(value) if value else default


def _env_str(name: str, default: str) -> str:
    """Baca env var string. Nilai kosong ("") juga dianggap tidak diisi,
    karena GitHub Actions mengirim string kosong untuk Variable yang belum dibuat."""
    return os.getenv(name, "").strip() or default


def _env_bool(name: str, default: bool) -> bool:
    """Baca env var sebagai boolean (true/1/yes/ya dianggap True)."""
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "y", "ya")


# Token bot & chat ID Telegram (WAJIB diisi kecuali DRY_RUN=true)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# Base URL Binance. Default pakai data-api.binance.vision (khusus market data
# publik) karena api.binance.com memblokir IP dari Amerika Serikat (HTTP 451),
# sedangkan runner GitHub Actions umumnya berlokasi di AS.
BINANCE_BASE_URLS = [
    url.strip().rstrip("/")
    for url in _env_str(
        "BINANCE_BASE_URL",
        "https://data-api.binance.vision,https://api.binance.com",
    ).split(",")
    if url.strip()
]

INTERVAL = _env_str("INTERVAL", "1h")                     # timeframe candle
LOOKBACK = _env_int("LOOKBACK", 20)                       # jumlah candle untuk rata-rata
SPIKE_MULTIPLIER = _env_float("SPIKE_MULTIPLIER", 3.0)    # ambang spike (x rata-rata)
MIN_CANDLE_QUOTE_VOLUME = _env_float("MIN_CANDLE_QUOTE_VOLUME", 50_000)  # min volume USDT candle spike
MIN_24H_QUOTE_VOLUME = _env_float("MIN_24H_QUOTE_VOLUME", 500_000)       # min volume USDT 24 jam
MAX_ALERTS = _env_int("MAX_ALERTS", 30)                   # maks pair yang dikirim per run
MAX_WORKERS = _env_int("MAX_WORKERS", 8)                  # jumlah request paralel
EXCLUDE_LEVERAGED = _env_bool("EXCLUDE_LEVERAGED", True)  # buang token UP/DOWN/BULL/BEAR
EXCLUDE_STABLES = _env_bool("EXCLUDE_STABLES", True)      # buang pair stablecoin (USDCUSDT, dll)
SEND_IF_EMPTY = _env_bool("SEND_IF_EMPTY", False)         # kirim pesan walau tidak ada spike
DRY_RUN = _env_bool("DRY_RUN", False)                     # true = cetak ke console saja
TIMEZONE = _env_str("TIMEZONE", "Asia/Jakarta")           # zona waktu di pesan Telegram

# Daftar stablecoin / aset yang dipatok ke fiat (pair-nya ke USDT hampir
# tidak pernah bergerak, jadi tidak menarik untuk discan).
STABLE_ASSETS = {
    "USDC", "FDUSD", "TUSD", "BUSD", "USDP", "DAI", "USD1", "USDE", "PYUSD",
    "EUR", "EURI", "AEUR", "GBP", "TRY", "BRL", "XUSD", "BFUSD", "RLUSD",
    "PAXG",  # emas, juga cenderung stabil
}

# Timeout & retry untuk request HTTP
REQUEST_TIMEOUT = 15
MAX_RETRIES = 4

session = requests.Session()
session.headers.update({"User-Agent": "volume-spike-scanner/1.0"})


# ---------------------------------------------------------------------------
# Helper HTTP ke Binance
# ---------------------------------------------------------------------------

def binance_get(path: str, params: dict | None = None):
    """
    GET ke endpoint publik Binance dengan retry.
    - Coba setiap base URL secara berurutan (fallback).
    - Kalau kena rate limit (429/418), tunggu sesuai header Retry-After.
    - Kalau error 5xx / jaringan, retry dengan jeda yang makin lama.
    """
    last_error: Exception | None = None

    for base_url in BINANCE_BASE_URLS:
        url = f"{base_url}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

                # Rate limit: hormati Retry-After supaya IP tidak di-ban
                if resp.status_code in (418, 429):
                    wait = int(resp.headers.get("Retry-After", "10"))
                    print(f"[WARN] Rate limit ({resp.status_code}), tunggu {wait} detik...")
                    time.sleep(wait)
                    continue

                # 451 = lokasi diblokir -> langsung pindah ke base URL berikutnya
                if resp.status_code in (403, 451):
                    last_error = RuntimeError(f"{url} -> HTTP {resp.status_code} (diblokir)")
                    break

                # Error di sisi server -> retry
                if resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}")

                resp.raise_for_status()
                return resp.json()

            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 15))

    raise RuntimeError(f"Gagal request ke Binance {path}: {last_error}")


# ---------------------------------------------------------------------------
# Ambil daftar pair
# ---------------------------------------------------------------------------

def is_leveraged_token(base_asset: str) -> bool:
    """Deteksi leveraged token lama Binance, contoh: BTCUP, ETHDOWN, BNBBULL."""
    return base_asset.endswith(("UP", "DOWN", "BULL", "BEAR")) and len(base_asset) > 4


def get_usdt_symbols() -> list[str]:
    """Ambil semua pair spot dengan quote USDT yang statusnya TRADING."""
    data = binance_get("/api/v3/exchangeInfo", {"permissions": "SPOT"})
    symbols = []
    for s in data.get("symbols", []):
        if s.get("quoteAsset") != "USDT" or s.get("status") != "TRADING":
            continue
        if not s.get("isSpotTradingAllowed", True):
            continue
        base = s.get("baseAsset", "")
        if EXCLUDE_STABLES and base in STABLE_ASSETS:
            continue
        if EXCLUDE_LEVERAGED and is_leveraged_token(base):
            continue
        symbols.append(s["symbol"])
    return symbols


def get_24h_quote_volumes() -> dict[str, float]:
    """Ambil volume 24 jam (dalam USDT) untuk semua pair sekaligus (1 request)."""
    data = binance_get("/api/v3/ticker/24hr", {"type": "MINI"})
    return {t["symbol"]: float(t.get("quoteVolume", 0) or 0) for t in data}


# ---------------------------------------------------------------------------
# Deteksi spike
# ---------------------------------------------------------------------------

@dataclass
class Spike:
    """Data hasil deteksi spike untuk satu pair."""
    symbol: str
    ratio: float            # volume candle / rata-rata
    quote_volume: float     # volume candle spike dalam USDT
    avg_quote_volume: float # rata-rata volume N candle sebelumnya (USDT)
    price_change_pct: float # perubahan harga candle spike (close vs open)
    close_price: float
    candle_open_time: int   # timestamp ms open candle spike
    volume_24h: float


def check_symbol(symbol: str, volume_24h: float) -> Spike | None:
    """
    Cek satu pair. Return objek Spike kalau terdeteksi spike, None kalau tidak.

    Format satu kline dari Binance:
    [0 openTime, 1 open, 2 high, 3 low, 4 close, 5 volume(base),
     6 closeTime, 7 quoteVolume(USDT), 8 trades, ...]
    """
    # +2: LOOKBACK candle untuk rata-rata + 1 candle yang dicek + 1 candle
    # yang mungkin masih berjalan (belum close)
    klines = binance_get(
        "/api/v3/klines",
        {"symbol": symbol, "interval": INTERVAL, "limit": LOOKBACK + 2},
    )

    # Buang candle terakhir kalau belum close (closeTime masih di masa depan)
    now_ms = int(time.time() * 1000)
    closed = [k for k in klines if int(k[6]) < now_ms]

    # Butuh minimal LOOKBACK candle historis + 1 candle yang dicek
    if len(closed) < LOOKBACK + 1:
        return None

    current = closed[-1]                    # candle terakhir yang sudah close
    history = closed[-(LOOKBACK + 1):-1]    # N candle sebelumnya

    # Pakai quote volume (USDT) supaya bisa dibandingkan antar pair
    current_qv = float(current[7])
    avg_qv = sum(float(k[7]) for k in history) / len(history)

    if avg_qv <= 0:
        return None

    ratio = current_qv / avg_qv

    # Syarat spike: rasio di atas ambang DAN nilai volumenya cukup besar
    # (supaya pair super sepi yang naik dari 100 -> 500 USDT tidak ikut)
    if ratio < SPIKE_MULTIPLIER or current_qv < MIN_CANDLE_QUOTE_VOLUME:
        return None

    open_price = float(current[1])
    close_price = float(current[4])
    change_pct = ((close_price - open_price) / open_price * 100) if open_price else 0.0

    return Spike(
        symbol=symbol,
        ratio=ratio,
        quote_volume=current_qv,
        avg_quote_volume=avg_qv,
        price_change_pct=change_pct,
        close_price=close_price,
        candle_open_time=int(current[0]),
        volume_24h=volume_24h,
    )


def scan() -> tuple[list[Spike], int, int]:
    """
    Jalankan scan ke semua pair.
    Return: (daftar spike terurut, jumlah pair yang discan, jumlah error)
    """
    symbols = get_usdt_symbols()
    volumes_24h = get_24h_quote_volumes()

    # Saring pair yang volume 24 jamnya terlalu kecil (hemat request & kurangi noise)
    targets = [s for s in symbols if volumes_24h.get(s, 0) >= MIN_24H_QUOTE_VOLUME]
    print(f"[INFO] {len(symbols)} pair USDT aktif, {len(targets)} lolos filter "
          f"volume 24 jam >= {MIN_24H_QUOTE_VOLUME:,.0f} USDT")

    spikes: list[Spike] = []
    errors = 0

    # Request paralel supaya cepat. Bobot klines = 2/request, limit Binance
    # 6000/menit, jadi ratusan pair masih sangat aman.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(check_symbol, s, volumes_24h.get(s, 0)): s for s in targets}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                result = future.result()
                if result:
                    spikes.append(result)
            except Exception as exc:  # satu pair gagal jangan sampai menghentikan scan
                errors += 1
                print(f"[WARN] {symbol}: {exc}")

    # Urutkan dari rasio spike terbesar
    spikes.sort(key=lambda x: x.ratio, reverse=True)
    return spikes, len(targets), errors


# ---------------------------------------------------------------------------
# Format pesan & kirim ke Telegram
# ---------------------------------------------------------------------------

def fmt_usd(value: float) -> str:
    """Format angka besar jadi ringkas: 1.2K, 3.4M, 5.6B."""
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= size:
            return f"{value / size:.2f}{unit}"
    return f"{value:.2f}"


def fmt_price(value: float) -> str:
    """Format harga: koin murah butuh lebih banyak angka di belakang koma."""
    if value >= 1:
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return f"{value:.8f}".rstrip("0").rstrip(".")


def local_time(ms: int) -> str:
    """Ubah timestamp ms (UTC) ke zona waktu lokal sesuai env TIMEZONE."""
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = timezone.utc
    return datetime.fromtimestamp(ms / 1000, tz).strftime("%d %b %Y %H:%M %Z")


def build_messages(spikes: list[Spike], scanned: int, errors: int) -> list[str]:
    """
    Susun pesan Telegram (format HTML). Pesan dipecah kalau terlalu panjang
    karena batas Telegram 4096 karakter per pesan.
    """
    shown = spikes[:MAX_ALERTS]
    candle_time = local_time(shown[0].candle_open_time) if shown else local_time(int(time.time() * 1000))

    header = (
        f"🚨 <b>Volume Spike Binance ({html.escape(INTERVAL)})</b>\n"
        f"🕒 Candle: {html.escape(candle_time)}\n"
        f"⚙️ Ambang: ≥ {SPIKE_MULTIPLIER:g}x rata-rata {LOOKBACK} candle\n"
        f"🔎 Discan: {scanned} pair | Spike: {len(spikes)}"
        + (f" | Error: {errors}" if errors else "")
        + "\n"
    )

    if not shown:
        return [header + "\nTidak ada spike volume pada candle ini."]

    blocks = []
    for i, s in enumerate(shown, 1):
        arrow = "🟢" if s.price_change_pct >= 0 else "🔴"
        link = f"https://www.binance.com/en/trade/{s.symbol[:-4]}_USDT?type=spot"
        blocks.append(
            f"{i}. {arrow} <a href=\"{link}\"><b>{html.escape(s.symbol)}</b></a> "
            f"— <b>{s.ratio:.1f}x</b>\n"
            f"   Vol: {fmt_usd(s.quote_volume)} (avg {fmt_usd(s.avg_quote_volume)}) USDT\n"
            f"   Harga: {fmt_price(s.close_price)} ({s.price_change_pct:+.2f}%) | "
            f"24j: {fmt_usd(s.volume_24h)}\n"
        )

    footer = ""
    if len(spikes) > len(shown):
        footer = f"\n…dan {len(spikes) - len(shown)} pair lainnya."

    # Gabungkan blok ke dalam beberapa pesan, masing-masing < 4000 karakter
    messages, current = [], header + "\n"
    for block in blocks:
        if len(current) + len(block) > 4000:
            messages.append(current)
            current = ""
        current += block + "\n"
    current += footer
    messages.append(current)
    return messages


def send_telegram(text: str) -> None:
    """Kirim satu pesan ke Telegram lewat Bot API."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(1, 4):
        resp = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        if resp.ok:
            return
        # Telegram juga punya rate limit -> tunggu sesuai retry_after
        if resp.status_code == 429:
            wait = resp.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(wait)
            continue
        raise RuntimeError(f"Telegram error {resp.status_code}: {resp.text}")
    raise RuntimeError("Gagal kirim ke Telegram setelah beberapa kali percobaan")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    # Paksa output console ke UTF-8 supaya emoji tidak error di Windows
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if not DRY_RUN and (not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID):
        print("[ERROR] TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID wajib diisi "
              "(atau set DRY_RUN=true untuk tes tanpa Telegram).")
        return 1

    started = time.time()
    try:
        spikes, scanned, errors = scan()
    except Exception as exc:
        print(f"[ERROR] Scan gagal: {exc}")
        return 1

    print(f"[INFO] Selesai dalam {time.time() - started:.1f} detik. "
          f"Spike: {len(spikes)}, error: {errors}")

    # Tidak ada spike & tidak diminta kirim pesan kosong -> selesai
    if not spikes and not SEND_IF_EMPTY:
        print("[INFO] Tidak ada spike, tidak mengirim notifikasi.")
        return 0

    messages = build_messages(spikes, scanned, errors)

    if DRY_RUN:
        print("\n===== DRY RUN (pesan tidak dikirim) =====\n")
        for msg in messages:
            print(msg)
            print("-" * 40)
        return 0

    try:
        for msg in messages:
            send_telegram(msg)
            time.sleep(1)  # jeda kecil antar pesan
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"[INFO] {len(messages)} pesan terkirim ke Telegram.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
