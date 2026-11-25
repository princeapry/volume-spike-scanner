"""State antar-run untuk mencegah alert berulang (dedup).

Kenapa ada file ini
-------------------
GitHub Actions menjalankan setiap run di mesin baru yang bersih, jadi scanner
tidak ingat apa pun dari run sebelumnya. Akibatnya pair yang sama bisa dikirim
berulang kali: kalau ASTERUSDT spike dan tetap memenuhi syarat selama 5 jam,
scanner 1 jam akan mengirim pair itu 5 kali.

Modul ini menyimpan "kapan terakhir sebuah pair dialertkan" ke sebuah file JSON
yang di-checkout dari branch state sebelum run dan disimpan kembali sesudahnya
(lihat workflow .yml masing-masing scanner).

Cara pakai di scanner
---------------------
    import alert_state

    results, scanned, errors = scan()
    results, suppressed = alert_state.filter_new("volume-spike", results)

Pengaturan (environment variable)
---------------------------------
ALERT_COOLDOWN_HOURS : jeda minimum sebelum pair yang sama boleh dialertkan
                       lagi oleh scanner yang sama. Kalau kosong, dipakai
                       default per scanner (DEFAULT_COOLDOWN_HOURS di bawah):
                         volume-spike 2 jam, confluence 6 jam,
                         reversal 6 jam, accumulation 24 jam.
                       Set 0 untuk MEMATIKAN dedup (perilaku lama).
                       Di workflow, nilainya diambil dari Variables
                       ALERT_COOLDOWN_HOURS_<SCANNER> (mis.
                       ALERT_COOLDOWN_HOURS_VOLUME_SPIKE), lalu jatuh ke
                       ALERT_COOLDOWN_HOURS global, lalu ke default di atas.
STATE_DIR            : folder tempat file state berada. Default "scanner_state".

Prinsip penting: fail-open
--------------------------
Kalau file state rusak, tidak bisa dibaca, atau tidak bisa ditulis, modul ini
TIDAK PERNAH menelan alert. Lebih baik kamu dapat alert dobel daripada kehilangan
alert karena urusan state.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

# Default jeda per scanner. Scanner yang cepat berubah (volume spike, cron
# per jam) pakai jeda pendek; scanner yang sinyalnya lambat (akumulasi) pakai
# jeda panjang supaya pair yang sama tidak muncul tiap run.
DEFAULT_COOLDOWN_HOURS: dict[str, float] = {
    "volume-spike": 2.0,
    "confluence": 6.0,
    "reversal": 6.0,
    "accumulation": 24.0,
}
FALLBACK_COOLDOWN_HOURS = 6.0  # untuk nama scanner yang tidak ada di tabel


def cooldown_hours(scanner: str) -> float:
    """Jeda dedup (jam) untuk scanner ini. 0 atau negatif = dedup mati.

    ALERT_COOLDOWN_HOURS (kalau diisi) menimpa default per scanner.
    """
    default = DEFAULT_COOLDOWN_HOURS.get(scanner, FALLBACK_COOLDOWN_HOURS)
    raw = os.getenv("ALERT_COOLDOWN_HOURS", "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print(f"[WARN] ALERT_COOLDOWN_HOURS='{raw}' bukan angka, pakai default {default:g} jam.")
        return default

STATE_DIR = os.getenv("STATE_DIR", "scanner_state").strip() or "scanner_state"

# Entri yang lebih tua dari ini dibuang supaya file state tidak membengkak.
PRUNE_DAYS = 30


def _state_path(scanner: str) -> Path:
    return Path(STATE_DIR) / f"{scanner}.json"


def load(scanner: str) -> dict[str, float]:
    """Baca state scanner. Kembalikan dict {symbol: epoch_detik_terakhir_alert}."""
    path = _state_path(scanner)
    try:
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:
        print(f"[WARN] State '{path}' tidak terbaca ({exc}). Dedup dilewati run ini.")
        return {}

    if not isinstance(data, dict):
        print(f"[WARN] Isi state '{path}' bukan objek JSON. Dedup dilewati run ini.")
        return {}

    clean: dict[str, float] = {}
    for symbol, ts in data.items():
        try:
            clean[str(symbol)] = float(ts)
        except (TypeError, ValueError):
            continue
    return clean


def save(scanner: str, state: dict[str, float], now: float | None = None) -> None:
    """Tulis state scanner, sambil membuang entri yang sudah sangat tua."""
    now = time.time() if now is None else now
    cutoff = now - PRUNE_DAYS * 86400
    pruned = {s: ts for s, ts in state.items() if ts >= cutoff}

    path = _state_path(scanner)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(pruned, fh, indent=1, sort_keys=True)
        tmp.replace(path)
    except Exception as exc:
        print(f"[WARN] State '{path}' gagal disimpan ({exc}). Run berikutnya mulai dari kosong.")


def filter_new(scanner: str, items: list, now: float | None = None) -> tuple[list, list]:
    """Buang item yang pair-nya sudah dialertkan dalam jeda cooldown scanner ini.

    `items` adalah list objek hasil scan yang punya atribut `.symbol`
    (Spike / Setup / Hit — keempat scanner memakai nama atribut yang sama).

    Kembalikan (items_yang_lolos, daftar_symbol_yang_ditahan).
    """
    hours = cooldown_hours(scanner)
    if hours <= 0 or not items:
        return items, []

    now = time.time() if now is None else now
    window = hours * 3600
    state = load(scanner)

    kept: list = []
    suppressed: list[str] = []

    for item in items:
        symbol = getattr(item, "symbol", None)
        if not symbol:
            # Tidak ada symbol untuk dijadikan kunci -> jangan ditahan.
            kept.append(item)
            continue
        last = state.get(symbol)
        if last is not None and (now - last) < window:
            suppressed.append(symbol)
            continue
        kept.append(item)
        state[symbol] = now

    if suppressed:
        print(f"[INFO] Dedup: {len(suppressed)} pair ditahan "
              f"(sudah dialertkan < {hours:g} jam lalu): "
              f"{', '.join(suppressed[:10])}"
              f"{' ...' if len(suppressed) > 10 else ''}")

    if kept:
        save(scanner, state, now=now)

    return kept, suppressed
