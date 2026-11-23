#!/usr/bin/env python3
"""
alpha_snapshot.py — cek Binance Alpha secara MANUAL (misalnya dari iPad saat istirahat).

Setiap kali dijalankan, daftar token Binance Alpha dibandingkan dengan RUN SEBELUMNYA
(berapa pun jedanya: 2 jam, semalam, dst.) lalu dikirim SATU pesan ringkasan ke Telegram:

  🚨 Listing baru sejak cek terakhir
  📈 Token yang volumenya sejak cek terakhir >= ALPHA_VOL_MULT x normal
  ⚪️ Token yang keluar dari Alpha (delist, atau pindah ke spot)
  📒 Alert sebelumnya: harga sekarang vs harga saat alert

Setiap listing baru / lonjakan volume DICATAT dengan harga saat kamu melihatnya, dan
run-run berikutnya mencatat harganya lagi. Mode "report" merangkum hasilnya. Jadi kamu
bisa mengukur: kalau beli saat jam istirahat, hasilnya biasanya bagaimana?

Read-only: tidak pernah order, tidak butuh API key Binance. Rahasia yang dipakai hanya
TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.

Pemakaian:
  python alpha_snapshot.py            # snapshot + kirim ringkasan ke Telegram
  python alpha_snapshot.py --report   # kirim laporan hasil alert ke Telegram

Environment variable (kosong = default):
  ALPHA_DATA_DIR   folder data (state + log), default ./alpha_data
  ALPHA_MIN_LIQ    likuiditas minimal (USD), default 50000
  ALPHA_MIN_VOL    volume 24 jam minimal (USD), default 0
  ALPHA_MAX_MCAP   market cap maksimal (USD), 0 = tanpa batas, default 0
  ALPHA_VOL_MULT   ambang lonjakan volume, x normal, default 5
  ALPHA_MIN_SPIKE_USD  volume minimal (USD) yang benar-benar terjadi selama jeda
                   supaya dihitung lonjakan, default 10000 (buang token yang nyaris mati)
  ALPHA_TOP_N      maks token per bagian di pesan, default 5
  TIMEZONE         zona waktu pesan, default Asia/Tokyo
  ALPHA_DRY_RUN    "1" = jangan kirim Telegram, cetak saja
"""

import argparse
import csv
import html
import os
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone

import alpha_watch as aw

TG_LIMIT = 3900          # batas aman di bawah 4096 karakter Telegram
OBS_MAX_HOURS = 48       # berapa lama harga sebuah alert terus dicatat
MIN_VOL_WINDOW_MIN = 30  # di bawah ini lonjakan volume tidak dihitung (terlalu berisik)
D24_RANGE = (18, 36)     # observasi yang dihitung sebagai "~24 jam"

CSV_FIELDS = ["time", "kind", "symbol", "name", "token_id", "signal",
              "price0", "liq0", "holders0", "mcap0", "chg24h0",
              "ret_next", "hrs_next", "ret_24h", "hrs_24h",
              "max_ret", "min_ret", "status"]


# --------------------------------------------------------------------------
# config + waktu
# --------------------------------------------------------------------------

def _tz():
    name = (os.environ.get("TIMEZONE") or "").strip() or "Asia/Tokyo"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - zona tidak dikenal / tzdata tidak ada
        return timezone(timedelta(hours=9), "JST")


def fmt_time(ts):
    return datetime.fromtimestamp(ts, _tz()).strftime("%Y-%m-%d %H:%M")


def fmt_ago(minutes):
    m = int(round(minutes))
    if m < 60:
        return f"{m}m"
    if m < 1440:
        return f"{m // 60}j {m % 60}m"
    return f"{m // 1440}h {(m % 1440) // 60}j"


def build_cfg():
    data_dir = (os.environ.get("ALPHA_DATA_DIR") or "").strip() or os.path.join(
        aw.SCRIPT_DIR, "alpha_data")
    return {
        "data_dir": data_dir,
        "state": os.path.join(data_dir, "state.json"),
        "log": os.path.join(data_dir, "alerts_log.json"),
        "csv": os.path.join(data_dir, "alerts.csv"),
        "digest": os.path.join(data_dir, "last_digest.txt"),
        "min_liq": aw.getenv_float("ALPHA_MIN_LIQ", 50000),
        "min_vol": aw.getenv_float("ALPHA_MIN_VOL", 0),
        "max_mcap": aw.getenv_float("ALPHA_MAX_MCAP", 0),
        "vol_mult": aw.getenv_float("ALPHA_VOL_MULT", 5),
        "min_spike_usd": aw.getenv_float("ALPHA_MIN_SPIKE_USD", 10000),
        "top_n": max(1, int(aw.getenv_float("ALPHA_TOP_N", 5))),
        "tg_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "tg_chat": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
        "dry_run": os.environ.get("ALPHA_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "ya"),
    }


# --------------------------------------------------------------------------
# logika
# --------------------------------------------------------------------------

def passes(t, cfg):
    """Token yang layak ditampilkan: belum delist, likuid, dan (opsional) masih kecil."""
    if t.get("delisted"):
        return False
    if t["liq"] < cfg["min_liq"] or t["volume"] < cfg["min_vol"]:
        return False
    if cfg["max_mcap"] > 0 and t["mcap"] > cfg["max_mcap"]:
        return False
    return True


def vol_multiple(ref_vol, now_vol, window_min):
    """
    Perkiraan: volume yang terjadi selama `window_min` terakhir = berapa x volume normal
    untuk rentang sepanjang itu, dihitung dari pertumbuhan volume 24 jam (rolling).
    Kalau jedanya >= 24 jam, rentang 24 jam-nya sudah tidak tumpang tindih, jadi
    dipakai perbandingan langsung volume 24 jam sekarang vs sebelumnya.
    """
    if ref_vol <= 0:
        return 0.0
    if window_min >= 1440:
        return now_vol / ref_vol
    g = (now_vol - ref_vol) / ref_vol
    return g * 1440.0 / window_min + 1.0


def window_volume_usd(ref_vol, now_vol, window_min):
    """
    Perkiraan volume (USD) yang benar-benar terjadi selama jeda: pertumbuhan volume
    24 jam + bagian volume lama yang "keluar" dari jendela 24 jam. Dipakai sebagai
    lantai absolut supaya token dengan volume harian ~$100 tidak dianggap meledak.
    """
    if window_min >= 1440:
        return now_vol
    return max(0.0, now_vol - ref_vol) + ref_vol * window_min / 1440.0


def explorer_link(t):
    """(label, url) untuk verifikasi kontrak on-chain."""
    contract = t.get("contract") or ""
    chain = str(t.get("chain") or "").strip().lower()
    if contract:
        if t.get("bsc"):
            return "BscScan", f"https://bscscan.com/token/{contract}"
        if chain in ("1", "eth", "ethereum"):
            return "Etherscan", f"https://etherscan.io/token/{contract}"
        return "DexScreener", aw.dexscreener_link(contract)
    return "DexScreener", aw.dexscreener_link(t["symbol"])


def make_record(kind, t, now, signal):
    if t["price"] <= 0:
        return None
    return {
        "ts": int(now), "time": fmt_time(now), "kind": kind,
        "token_id": t["id"], "symbol": t["symbol"], "name": t["name"], "signal": signal,
        "price0": t["price"], "liq0": round(t["liq"]), "holders0": t["holders"],
        "mcap0": round(t["mcap"]), "chg24h0": t["change"],
        "obs": [], "ret_next": None, "hrs_next": None, "ret_24h": None, "hrs_24h": None,
        "max_ret": None, "min_ret": None, "status": "open",
    }


def update_outcomes(log, curr, now):
    """
    Run dijalankan manual dengan jeda tidak tentu, jadi hasil dicatat apa adanya:
    setiap run mencatat [jam sejak alert, return %] untuk alert yang masih terbuka.
      ret_next : run pertama >= 1 jam setelah alert (dan berapa jam jedanya)
      ret_24h  : observasi paling dekat ke 24 jam dalam rentang 18-36 jam
      max/min  : puncak / titik terendah DARI RUN-RUN YANG ADA (bukan intraday)
    Token yang keluar dari Alpha ditandai 'gone' (bisa delist, bisa pindah ke spot).
    """
    for r in log:
        if r.get("status") != "open":
            continue
        hrs = (now - r["ts"]) / 3600.0
        if hrs < 1:
            continue
        t = curr.get(r["token_id"])
        live = t is not None and not t["delisted"] and t["price"] > 0
        if not live:
            r["status"] = "gone"
            continue
        if hrs <= OBS_MAX_HOURS:
            ret = round((t["price"] / r["price0"] - 1) * 100, 2)
            r["obs"].append([round(hrs, 2), ret])
            if r["ret_next"] is None:
                r["ret_next"], r["hrs_next"] = ret, round(hrs, 1)
            rets = [o[1] for o in r["obs"]]
            r["max_ret"], r["min_ret"] = max(rets), min(rets)
            lo, hi = D24_RANGE
            near = [o for o in r["obs"] if lo <= o[0] <= hi]
            if near:
                best = min(near, key=lambda o: abs(o[0] - 24))
                r["ret_24h"], r["hrs_24h"] = best[1], best[0]
        if hrs > OBS_MAX_HOURS:
            r["status"] = "done"


def export_csv(path, log):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in log:
            w.writerow(r)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# pesan
# --------------------------------------------------------------------------

def tok_line(t, tag=""):
    label, url = explorer_link(t)
    sym = html.escape(t["symbol"])
    name = f" ({html.escape(t['name'])})" if t["name"] and t["name"] != t["symbol"] else ""
    tag = f" {tag}" if tag else ""
    return (f"• <b>{sym}</b>{tag}{name} — {aw.fmt_price(t['price'])} ({t['change']:+.1f}% 24j)\n"
            f"   liq {aw.fmt_usd(t['liq'])} • holders {t['holders'] or '?'} • "
            f"mcap {aw.fmt_usd(t['mcap'])} • <a href=\"{url}\">{label}</a>")


def scoreboard(log, now, limit=6):
    """Alert yang masih terbuka dan baru saja dicek ulang: return sekarang vs saat alert."""
    rows = []
    for r in log:
        if r.get("status") != "open" or not r.get("obs"):
            continue
        hrs, ret = r["obs"][-1]
        if abs((now - r["ts"]) / 3600.0 - hrs) > 0.05:   # bukan observasi run ini
            continue
        rows.append((r["ts"], r, ret, hrs))
    if not rows:
        return []
    rets = [x[2] for x in rows]
    up = sum(1 for x in rets if x > 0)
    out = [f"• {html.escape(r['symbol'])} ({r['kind']}, {fmt_ago(h * 60)} lalu): <b>{ret:+.1f}%</b>"
           for _, r, ret, h in sorted(rows, key=lambda x: -x[0])[:limit]]
    if len(rows) > limit:
        out.append(f"…+{len(rows) - limit} lainnya")
    out.append(f"median {statistics.median(rets):+.1f}% • naik {up}/{len(rets)}")
    return out


def clip(text):
    if len(text) <= TG_LIMIT:
        return text
    cut = text[:TG_LIMIT].rsplit("\n", 1)[0]
    return cut + "\n…(dipotong, lihat last_digest.txt)"


def build_digest(cfg, now, elapsed_min, curr, new, vol, gone, log,
                 vol_window=None, vol_ok=True):
    n = cfg["top_n"]
    vol_window = elapsed_min if vol_window is None else vol_window
    filt = f"liq ≥ {aw.fmt_usd(cfg['min_liq'])}"
    if cfg["max_mcap"] > 0:
        filt += f" • mcap ≤ {aw.fmt_usd(cfg['max_mcap'])}"
    window = "24j vs sebelumnya" if vol_window >= 1440 else f"{fmt_ago(vol_window)} terakhir"
    lines = [f"🛰 <b>ALPHA SNAPSHOT</b> — {fmt_time(now)}",
             f"cek terakhir {fmt_ago(elapsed_min)} lalu • {len(curr)} token • {filt}"]
    if new:
        lines += ["", f"🚨 <b>LISTING BARU ({len(new)})</b>"]
        lines += [tok_line(t) for t in new[:n]]
        if len(new) > n:
            lines.append(f"…+{len(new) - n} lainnya")
    if vol:
        lines += ["", f"📈 <b>VOLUME ≥{cfg['vol_mult']:g}x NORMAL ({window}, "
                      f"min {aw.fmt_usd(cfg['min_spike_usd'])})</b>"]
        lines += [tok_line(t, f"{m:.1f}x · ~{aw.fmt_usd(usd)}") for m, t, usd in vol[:n]]
        if len(vol) > n:
            lines.append(f"…+{len(vol) - n} lainnya")
    if gone:
        names = ", ".join(html.escape(t["symbol"]) for t in gone[:10])
        more = f" …+{len(gone) - 10}" if len(gone) > 10 else ""
        lines += ["", f"⚪️ Keluar dari Alpha ({len(gone)}): {names}{more}"]
    if not vol_ok:
        lines += ["", f"ℹ️ Lonjakan volume belum dihitung: baru {fmt_ago(vol_window)} sejak patokan "
                      f"volume (minimal {MIN_VOL_WINDOW_MIN}m). Listing baru tetap dicek."]
    if not (new or vol or gone) and vol_ok:
        lines += ["", "😴 Tidak ada listing baru atau lonjakan volume sejak cek terakhir."]
    elif not (new or gone) and not vol_ok:
        lines += ["😴 Tidak ada listing baru sejak cek terakhir."]
    board = scoreboard(log, now)
    if board:
        lines += ["", "📒 <b>Alert sebelumnya</b> (harga sekarang vs saat alert)"] + board
    return clip("\n".join(lines))


def _stats(xs):
    if not xs:
        return "-"
    up = sum(1 for x in xs if x > 0) / len(xs) * 100
    return f"rata2 {statistics.mean(xs):+.1f}% • median {statistics.median(xs):+.1f}% • naik {up:.0f}%"


def build_report(log):
    if not log:
        return "📊 Belum ada alert yang tercatat."
    lines = [f"📊 <b>LAPORAN ALERT ALPHA</b> ({len(log)} alert, "
             f"{log[0]['time']} s/d {log[-1]['time']})"]
    for kind in ("NEW", "VOL", "ALL"):
        rs = [r for r in log if kind == "ALL" or r["kind"] == kind]
        if not rs:
            continue
        nxt = [r["ret_next"] for r in rs if r.get("ret_next") is not None]
        gap = [r["hrs_next"] for r in rs if r.get("hrs_next") is not None]
        d24 = [r["ret_24h"] for r in rs if r.get("ret_24h") is not None]
        mx = [r["max_ret"] for r in rs if r.get("max_ret") is not None]
        mn = [r["min_ret"] for r in rs if r.get("min_ret") is not None]
        gone = sum(1 for r in rs if r.get("status") == "gone")
        lines += ["", f"<b>[{kind}]</b> {len(rs)} alert"]
        jeda = f", jeda rata2 {statistics.mean(gap):.1f}j" if gap else ""
        lines.append(f"  cek berikutnya (n={len(nxt)}{jeda}): {_stats(nxt)}")
        lines.append(f"  ~24 jam (n={len(d24)}): {_stats(d24)}")
        if mx:
            lines.append(f"  puncak tercatat rata2 {statistics.mean(mx):+.1f}% • "
                         f"terendah rata2 {statistics.mean(mn):+.1f}%")
        if gone:
            lines.append(f"  keluar dari Alpha: {gone} (delist atau pindah ke spot)")
    n24 = sum(1 for r in log if r.get("ret_24h") is not None)
    if n24 < 30:
        lines += ["", f"⚠️ Baru {n24} alert yang punya hasil ~24 jam. "
                      "Sampel sekecil ini belum bisa dijadikan kesimpulan."]
    lines += ["", "Catatan: puncak/terendah dihitung dari run yang ada (jam istirahat), "
                  "bukan pergerakan per menit."]
    return clip("\n".join(lines))


# --------------------------------------------------------------------------
# kirim
# --------------------------------------------------------------------------

def deliver(cfg, text):
    os.makedirs(cfg["data_dir"], exist_ok=True)
    with open(cfg["digest"], "w", encoding="utf-8") as f:
        f.write(text + "\n")
    if cfg["dry_run"] or not (cfg["tg_token"] and cfg["tg_chat"]):
        aw.safe_print(text)
        if not cfg["dry_run"]:
            aw.safe_print("[note] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID kosong — pesan hanya dicetak.")
        return
    aw.send_telegram(text, cfg["tg_token"], cfg["tg_chat"])


# --------------------------------------------------------------------------
# satu snapshot
# --------------------------------------------------------------------------

def run_snapshot(cfg, now=None, tokens=None):
    now = time.time() if now is None else now
    raw = aw.fetch_tokens() if tokens is None else tokens
    curr = {}
    for t in raw:
        n = aw.normalize(t)
        if n["id"]:
            curr[n["id"]] = n
    if not curr:
        raise RuntimeError("feed Binance Alpha mengembalikan 0 token")

    os.makedirs(cfg["data_dir"], exist_ok=True)
    state = aw.load_json(cfg["state"], {})
    prev = state.get("snapshot") or {}
    last = state.get("last_run")

    if prev and len(curr) < aw.PARTIAL_FEED_RATIO * len(prev):
        deliver(cfg, f"⚠️ <b>ALPHA SNAPSHOT</b> — {fmt_time(now)}\n"
                     f"Feed cuma balikin {len(curr)} token (sebelumnya {len(prev)}). "
                     "Kelihatannya data setengah, run ini dilewati. Coba jalankan lagi.")
        return "partial"

    def vols(snapshot):
        return {tid: t.get("volume", 0) for tid, t in snapshot.items()}

    if not prev or not last:
        aw.save_json(cfg["state"], {"last_run": now, "snapshot": curr,
                                    "vol_ref_ts": now, "vol_ref": vols(curr)})
        deliver(cfg, f"🛰 <b>ALPHA SNAPSHOT</b> — {fmt_time(now)}\n"
                     f"Data awal tersimpan: {len(curr)} token. Mulai run berikutnya, "
                     "pesan ini berisi listing baru dan lonjakan volume sejak cek terakhir.")
        return "baseline"

    elapsed = max(1.0, (now - last) / 60.0)
    # Patokan volume terpisah dari snapshot: kalau tombol ditekan lagi terlalu cepat,
    # patokan lama tetap dipakai, jadi pengukuran volume tidak rusak.
    vol_ref = state.get("vol_ref") or vols(prev)
    vol_ref_ts = state.get("vol_ref_ts") or last
    vol_window = max(1.0, (now - vol_ref_ts) / 60.0)
    vol_ok = vol_window >= MIN_VOL_WINDOW_MIN

    new = [t for tid, t in curr.items() if tid not in prev and passes(t, cfg)]
    new.sort(key=lambda t: -t["liq"])

    vol = []
    if vol_ok:
        for tid, t in curr.items():
            p = prev.get(tid)
            if not p or p.get("delisted") or not passes(t, cfg) or tid not in vol_ref:
                continue
            m = vol_multiple(vol_ref[tid], t["volume"], vol_window)
            usd = window_volume_usd(vol_ref[tid], t["volume"], vol_window)
            if m >= cfg["vol_mult"] and usd >= cfg["min_spike_usd"]:
                vol.append((m, t, usd))
    vol.sort(key=lambda x: -x[0])

    # pakai filter yang sama, supaya token receh yang tak pernah ditampilkan tidak ikut
    gone = [p for tid, p in prev.items()
            if passes(p, cfg) and (tid not in curr or curr[tid]["delisted"])]

    log = aw.load_json(cfg["log"], [])
    update_outcomes(log, curr, now)
    window = "24j" if vol_window >= 1440 else fmt_ago(vol_window)
    for t in new:
        rec = make_record("NEW", t, now, "listing baru")
        if rec:
            log.append(rec)
    for m, t, usd in vol:
        rec = make_record("VOL", t, now, f"vol ~{m:.1f}x ~{aw.fmt_usd(usd)} / {window}")
        if rec:
            log.append(rec)
    aw.save_json(cfg["log"], log)
    export_csv(cfg["csv"], log)

    text = build_digest(cfg, now, elapsed, curr, new, vol, gone, log,
                        vol_window=vol_window, vol_ok=vol_ok)
    if vol_ok:   # patokan volume hanya diperbarui kalau jedanya cukup
        vol_ref, vol_ref_ts = vols(curr), now
    aw.save_json(cfg["state"], {"last_run": now, "snapshot": curr,
                                "vol_ref_ts": vol_ref_ts, "vol_ref": vol_ref})
    deliver(cfg, text)
    return "ok"


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="Binance Alpha snapshot (manual)")
    ap.add_argument("--report", action="store_true", help="kirim laporan hasil alert")
    args = ap.parse_args(argv)
    cfg = build_cfg()
    try:
        if args.report:
            deliver(cfg, build_report(aw.load_json(cfg["log"], [])))
        else:
            run_snapshot(cfg)
    except Exception as e:  # noqa: BLE001
        msg = f"❌ <b>ALPHA SNAPSHOT gagal</b> — {fmt_time(time.time())}\n{html.escape(str(e))[:500]}"
        aw.safe_print(msg)
        try:
            if not cfg["dry_run"] and cfg["tg_token"] and cfg["tg_chat"]:
                aw.send_telegram(msg, cfg["tg_token"], cfg["tg_chat"])
        except Exception:  # noqa: BLE001
            pass
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
