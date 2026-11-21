"""
Logika deteksi sinyal BERSAMA untuk scanner.py dan reversal_scanner.py.

Semua fungsi deteksi di sini adalah fungsi murni (tidak request ke internet),
input-nya daftar candle (kline) Binance yang SUDAH CLOSE, urut lama -> baru.
Dengan begitu kedua scanner memakai definisi sinyal yang persis sama.

Isi file:
1. Helper baca environment variable (dipakai juga oleh kedua scanner).
2. Tiga sinyal dasar:
   a. Volume spike           -> check_volume_spike()
   b. Bullish engulfing di support -> check_engulfing_at_support()
   c. Buy pressure (taker buy) -> check_buy_pressure()
3. Skor confluence 0-3 (berapa sinyal yang nyala) + format teks untuk alert.

Format satu kline Binance:
[0 openTime, 1 open, 2 high, 3 low, 4 close, 5 volume(base),
 6 closeTime, 7 quoteVolume(USDT), 8 jumlah trade,
 9 takerBuyBaseVolume, 10 takerBuyQuoteVolume(USDT), 11 ignore]
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Helper environment variable
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


# ---------------------------------------------------------------------------
# Helper candle
# ---------------------------------------------------------------------------

# Index kolom kline Binance (supaya kode lebih mudah dibaca)
K_OPEN_TIME = 0
K_OPEN = 1
K_HIGH = 2
K_LOW = 3
K_CLOSE = 4
K_CLOSE_TIME = 6
K_QUOTE_VOLUME = 7
K_TAKER_BUY_QUOTE = 10


def only_closed(klines: list, now_ms: int | None = None) -> list:
    """Buang candle yang belum close (closeTime masih di masa depan)."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return [k for k in klines if int(k[K_CLOSE_TIME]) < now_ms]


# ---------------------------------------------------------------------------
# Sinyal 1: Volume spike
# ---------------------------------------------------------------------------

@dataclass
class VolumeSpike:
    """Hasil sinyal volume spike."""
    ratio: float             # volume candle terakhir / rata-rata
    quote_volume: float      # volume candle terakhir (USDT)
    avg_quote_volume: float  # rata-rata volume `lookback` candle sebelumnya (USDT)


def volume_stats(closed: list, lookback: int) -> tuple[float, float] | None:
    """
    Hitung (rasio, rata-rata) volume candle terakhir dibanding rata-rata
    `lookback` candle sebelumnya. Pakai quote volume (USDT) supaya bisa
    dibandingkan antar pair. None kalau data kurang / rata-rata nol.
    """
    if lookback <= 0 or len(closed) < lookback + 1:
        return None
    history = closed[-(lookback + 1):-1]
    avg_qv = sum(float(k[K_QUOTE_VOLUME]) for k in history) / len(history)
    if avg_qv <= 0:
        return None
    return float(closed[-1][K_QUOTE_VOLUME]) / avg_qv, avg_qv


def check_volume_spike(
    closed: list,
    multiplier: float,
    lookback: int,
    min_quote_volume: float = 0.0,
) -> VolumeSpike | None:
    """
    Volume spike = volume candle terakhir >= `multiplier` x rata-rata
    `lookback` candle sebelumnya DAN nilainya >= `min_quote_volume` USDT
    (supaya pair super sepi yang naik dari 100 -> 500 USDT tidak ikut).
    Return VolumeSpike kalau nyala, None kalau tidak.
    """
    stats = volume_stats(closed, lookback)
    if stats is None:
        return None
    ratio, avg_qv = stats
    current_qv = float(closed[-1][K_QUOTE_VOLUME])
    if ratio < multiplier or current_qv < min_quote_volume:
        return None
    return VolumeSpike(ratio=ratio, quote_volume=current_qv, avg_quote_volume=avg_qv)


# ---------------------------------------------------------------------------
# Sinyal 2: Bullish engulfing di support
# ---------------------------------------------------------------------------

@dataclass
class EngulfingAtSupport:
    """Hasil sinyal bullish engulfing di support."""
    support: float       # harga swing low terdekat
    distance_pct: float  # jarak low candle terakhir ke support (%), minus = di bawah support


def is_bullish_engulfing(prev: list, cur: list) -> bool:
    """Candle sebelumnya merah, candle sekarang hijau dan badannya menelan
    badan candle sebelumnya."""
    p_open, p_close = float(prev[K_OPEN]), float(prev[K_CLOSE])
    c_open, c_close = float(cur[K_OPEN]), float(cur[K_CLOSE])
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
    if window <= 0:
        return []
    lows = [float(k[K_LOW]) for k in candles]
    result = []
    for i in range(window, len(lows) - window):
        left = lows[i - window:i]
        right = lows[i + 1:i + 1 + window]
        if lows[i] < min(left) and lows[i] <= min(right):
            result.append(lows[i])
    return result


def check_engulfing_at_support(
    closed: list,
    swing_lookback: int,
    swing_window: int,
    max_distance_pct: float,
) -> EngulfingAtSupport | None:
    """
    Candle terakhir = bullish engulfing DAN low-nya berjarak <= `max_distance_pct` %
    (di atas maupun di bawah) dari swing low terdekat. Swing low dicari di
    `swing_lookback` candle SEBELUM candle terakhir, jadi candle engulfing tidak
    menjadi support-nya sendiri. Return EngulfingAtSupport kalau nyala.
    """
    if len(closed) < max(swing_lookback, 1) + 1:
        return None

    cur, prev = closed[-1], closed[-2]

    # Bullish engulfing dulu (paling murah dicek)
    if not is_bullish_engulfing(prev, cur):
        return None

    swing_area = closed[-(swing_lookback + 1):-1]
    swings = find_swing_lows(swing_area, swing_window)
    if not swings:
        return None

    cur_low = float(cur[K_LOW])
    # Pilih swing low yang paling dekat dengan low candle sekarang
    support = min(swings, key=lambda s: abs(cur_low - s))
    if support <= 0:
        return None
    distance_pct = (cur_low - support) / support * 100
    if abs(distance_pct) > max_distance_pct:
        return None

    return EngulfingAtSupport(support=support, distance_pct=distance_pct)


# ---------------------------------------------------------------------------
# Sinyal 3: Buy pressure
# ---------------------------------------------------------------------------

def buy_pressure(candle: list) -> float | None:
    """
    Rasio taker buy quote volume / total quote volume satu candle (0..1).
    Taker buy = pembeli yang "makan" order jual (market buy), jadi rasio
    tinggi berarti pembeli agresif mendominasi candle itu.
    None kalau datanya tidak ada atau volume nol.
    """
    if len(candle) <= K_TAKER_BUY_QUOTE:
        return None
    total_qv = float(candle[K_QUOTE_VOLUME])
    if total_qv <= 0:
        return None
    return float(candle[K_TAKER_BUY_QUOTE]) / total_qv


def check_buy_pressure(closed: list, min_ratio: float) -> float | None:
    """Buy pressure candle terakhir >= `min_ratio` (0..1). Return rasionya kalau nyala."""
    if not closed:
        return None
    ratio = buy_pressure(closed[-1])
    if ratio is None or ratio < min_ratio:
        return None
    return ratio


# ---------------------------------------------------------------------------
# Skor confluence
# ---------------------------------------------------------------------------

CONFLUENCE_TAG = "🔥 CONFLUENCE"


@dataclass(frozen=True)
class ConfluenceConfig:
    """Ambang ketiga sinyal untuk skor confluence. Sengaja punya env var
    sendiri (awalan CONF_) dan dibaca di kedua scanner, supaya arti skornya
    sama di kedua alert dan tidak ikut berubah saat trigger utama diubah."""
    spike_multiplier: float         # volume spike: >= N x rata-rata
    volume_lookback: int            # volume spike: jumlah candle rata-rata
    min_candle_quote_volume: float  # volume spike: volume candle minimal (USDT)
    swing_lookback: int             # engulfing: candle untuk cari swing low
    swing_window: int               # engulfing: tetangga kiri/kanan swing low
    support_distance_pct: float     # engulfing: jarak maks ke swing low (%)
    buy_pressure_min: float         # buy pressure minimal (0..1)
    min_score: int                  # skor minimal untuk penanda 🔥 CONFLUENCE

    @classmethod
    def from_env(cls) -> "ConfluenceConfig":
        return cls(
            spike_multiplier=_env_float("CONF_SPIKE_MULTIPLIER", 3.0),
            volume_lookback=_env_int("CONF_VOLUME_LOOKBACK", 20),
            min_candle_quote_volume=_env_float("CONF_MIN_CANDLE_QUOTE_VOLUME", 50_000),
            swing_lookback=_env_int("CONF_SWING_LOOKBACK", 30),
            swing_window=_env_int("CONF_SWING_WINDOW", 3),
            support_distance_pct=_env_float("CONF_SUPPORT_DISTANCE_PCT", 2.0),
            # Ditulis dalam persen di env (60 = 60%), disimpan sebagai rasio 0..1
            buy_pressure_min=_env_float("CONF_BUY_PRESSURE_PCT", 60.0) / 100,
            min_score=_env_int("CONF_MIN_SCORE", 2),
        )

    @property
    def candles_needed(self) -> int:
        """Jumlah candle close minimal supaya ketiga sinyal bisa dievaluasi."""
        return max(self.volume_lookback, self.swing_lookback, 1) + 1


@dataclass
class Confluence:
    """Hasil evaluasi ketiga sinyal pada candle terakhir satu pair."""
    volume_spike: VolumeSpike | None
    engulfing: EngulfingAtSupport | None
    buy_pressure: float | None  # rasio buy pressure (selalu diisi kalau datanya ada)
    buy_ok: bool                # True kalau buy pressure >= ambang

    @property
    def score(self) -> int:
        return int(self.volume_spike is not None) + int(self.engulfing is not None) + int(self.buy_ok)

    def labels(self) -> list[str]:
        """Nama sinyal yang nyala, untuk ditampilkan di alert."""
        result = []
        if self.volume_spike is not None:
            result.append(f"Vol spike {self.volume_spike.ratio:.1f}x")
        if self.engulfing is not None:
            result.append(f"Engulfing@support ({self.engulfing.distance_pct:+.1f}%)")
        if self.buy_ok and self.buy_pressure is not None:
            result.append(f"Buy {self.buy_pressure * 100:.0f}%")
        return result


def evaluate_confluence(closed: list, cfg: ConfluenceConfig) -> Confluence:
    """Evaluasi ketiga sinyal pada candle terakhir di `closed`."""
    bp = buy_pressure(closed[-1]) if closed else None
    return Confluence(
        volume_spike=check_volume_spike(
            closed, cfg.spike_multiplier, cfg.volume_lookback, cfg.min_candle_quote_volume
        ),
        engulfing=check_engulfing_at_support(
            closed, cfg.swing_lookback, cfg.swing_window, cfg.support_distance_pct
        ),
        buy_pressure=bp,
        buy_ok=bp is not None and bp >= cfg.buy_pressure_min,
    )


def is_hot(conf: Confluence, cfg: ConfluenceConfig) -> bool:
    """True kalau skor cukup tinggi untuk diberi penanda 🔥 CONFLUENCE."""
    return conf.score >= cfg.min_score


def confluence_line(conf: Confluence) -> str:
    """Satu baris skor untuk alert, contoh: '⭐⭐ 2/3 — Vol spike 4.2x · Buy 68%'."""
    labels = conf.labels()
    stars = "⭐" * conf.score if conf.score else "▫️"
    detail = " · ".join(labels) if labels else "belum ada syarat yang nyala"
    return f"{stars} {conf.score}/3 — {detail}"


def confluence_legend(cfg: ConfluenceConfig) -> str:
    """Penjelasan singkat ambang skor, untuk header pesan."""
    return (
        f"⭐ Skor: Vol ≥ {cfg.spike_multiplier:g}x avg {cfg.volume_lookback} · "
        f"Engulfing@support ≤ {cfg.support_distance_pct:g}% · "
        f"Buy ≥ {cfg.buy_pressure_min * 100:g}% | "
        f"{CONFLUENCE_TAG} = skor ≥ {cfg.min_score}"
    )


# ---------------------------------------------------------------------------
# Sinyal 4: Akumulasi (harga flat + volume naik + dekat base)
# ---------------------------------------------------------------------------
# Dipakai accumulation_scanner.py. Tidak dipakai di skor confluence.

@dataclass
class Accumulation:
    """Hasil sinyal akumulasi untuk satu pair."""
    volume_rise: float  # rata-rata volume paruh terbaru / paruh lama
    range_pct: float    # rentang harga window: (high max - low min) / low min x 100
    position: float     # posisi close terakhir di range base (0 = di low, 1 = di high)
    base_low: float     # low terendah di base lookback
    base_high: float    # high tertinggi di base lookback


def price_range_pct(candles: list) -> float | None:
    """Rentang harga sekumpulan candle dalam persen:
    (high tertinggi - low terendah) / low terendah x 100."""
    if not candles:
        return None
    high = max(float(k[K_HIGH]) for k in candles)
    low = min(float(k[K_LOW]) for k in candles)
    if low <= 0:
        return None
    return (high - low) / low * 100


def volume_rise_ratio(candles: list) -> float | None:
    """
    Bagi candle jadi 2 paruh sama panjang, lalu hitung
    rata-rata quote volume paruh TERBARU / rata-rata paruh LAMA.
    Kalau jumlah candle ganjil, candle paling lama dibuang supaya paruhnya sama.
    """
    half = len(candles) // 2
    if half == 0:
        return None
    old, new = candles[-2 * half:-half], candles[-half:]
    avg_old = sum(float(k[K_QUOTE_VOLUME]) for k in old) / half
    avg_new = sum(float(k[K_QUOTE_VOLUME]) for k in new) / half
    if avg_old <= 0:
        return None
    return avg_new / avg_old


def range_position(candles: list) -> tuple[float, float, float] | None:
    """
    Posisi close candle terakhir di dalam range high-low sekumpulan candle.
    Return (posisi 0..1, low, high). 0 = di low terendah, 1 = di high tertinggi.
    """
    if not candles:
        return None
    high = max(float(k[K_HIGH]) for k in candles)
    low = min(float(k[K_LOW]) for k in candles)
    if high <= low:
        return None
    close = float(candles[-1][K_CLOSE])
    return (close - low) / (high - low), low, high


def check_accumulation(
    closed: list,
    window: int,
    max_range_pct: float,
    vol_rise_factor: float,
    base_lookback: int,
    max_position: float,
) -> Accumulation | None:
    """
    Akumulasi = ketiga syarat ini terpenuhi pada candle close terbaru:
    1. Flat: rentang harga `window` candle terakhir <= `max_range_pct` %.
    2. Volume naik: rata-rata volume paruh terbaru `window` >= `vol_rise_factor`
       x rata-rata paruh lama.
    3. Dekat base: posisi close di range `base_lookback` candle terakhir
       <= `max_position` (0..1).
    Pair yang candle-nya belum cukup (misal listing baru) tidak dihitung.
    """
    if window < 2 or len(closed) < max(window, base_lookback):
        return None
    recent = closed[-window:]

    # 1) Flat
    rng = price_range_pct(recent)
    if rng is None or rng > max_range_pct:
        return None

    # 2) Volume naik
    rise = volume_rise_ratio(recent)
    if rise is None or rise < vol_rise_factor:
        return None

    # 3) Dekat base
    pos = range_position(closed[-base_lookback:])
    if pos is None:
        return None
    position, base_low, base_high = pos
    if position > max_position:
        return None

    return Accumulation(
        volume_rise=rise,
        range_pct=rng,
        position=position,
        base_low=base_low,
        base_high=base_high,
    )


# ---------------------------------------------------------------------------
# Sinyal 5: Kompresi (volatilitas menyempit)
# ---------------------------------------------------------------------------
# Beda dengan "flat": flat melihat rentang harga TOTAL di window, sedangkan
# kompresi melihat apakah ukuran candle makin lama makin KECIL (coil ngencang).
# Dipakai accumulation_scanner.py sebagai skor tambahan, bukan syarat wajib.

def avg_relative_range(candles: list) -> float | None:
    """Rata-rata (high - low) / close per candle. Candle dengan close <= 0 dilewati."""
    values = [
        (float(k[K_HIGH]) - float(k[K_LOW])) / float(k[K_CLOSE])
        for k in candles
        if float(k[K_CLOSE]) > 0
    ]
    if not values:
        return None
    return sum(values) / len(values)


def compression_ratio(candles: list) -> float | None:
    """
    Bagi candle jadi 2 paruh sama panjang, lalu hitung
    rata-rata range relatif paruh TERBARU / rata-rata paruh LAMA.
    < 1 berarti volatilitas menyempit. Kalau jumlah candle ganjil,
    candle paling lama dibuang supaya paruhnya sama.
    """
    half = len(candles) // 2
    if half == 0:
        return None
    old = avg_relative_range(candles[-2 * half:-half])
    new = avg_relative_range(candles[-half:])
    if old is None or new is None or old <= 0:
        return None
    return new / old


def check_compression(candles: list, max_ratio: float) -> float | None:
    """Kompresi = compression_ratio <= `max_ratio`. Return rasionya kalau nyala."""
    ratio = compression_ratio(candles)
    if ratio is None or ratio > max_ratio:
        return None
    return ratio
