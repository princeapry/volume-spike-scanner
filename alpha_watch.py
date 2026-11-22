#!/usr/bin/env python3
"""
binance-alpha-watch v4 — watcher for Binance Alpha early-stage tokens.

Polls Binance Alpha's PUBLIC market-data endpoint (no API key needed),
and alerts on:
  - NEW LISTING   : a token id that wasn't in the list last poll
  - VOLUME SPIKE  : the last ~window minutes carried >= ALPHA_VOL_MULT x the
                    token's average volume for a window that size (estimated
                    from how much its rolling 24h volume grew vs ~window ago)
  - PRICE MOVE    : |24h change| crossed up through a threshold
  - DELISTED      : a token dropped off the list or flipped fullyDelisted

Every NEW / VOL / MOVE alert is also LOGGED (alerts_log.json + alerts.csv)
with the price at alert time, and the watcher later fills in the price
1h and 24h afterwards plus the 24h peak and trough. Run --report to see
how each alert type actually performed. Use that before risking money.

Each alert shows liquidity, holders, market cap, FDV and Binance's score.
When the feed carries the token's contract + chain (most BSC tokens do),
the alert links it on BscScan; otherwise it adds a DexScreener search.

READ-ONLY. Never places an order, never needs a Binance key. The only
secret is a Telegram bot token for sending alerts.

Usage:
  python alpha_watch.py --loop            # run forever (the normal mode)
  python alpha_watch.py --once            # single poll
  python alpha_watch.py --once --dry-run  # print instead of sending to Telegram
  python alpha_watch.py --report          # how past alerts performed
  python alpha_watch.py --print-schema    # dump the raw fields of one token

Settings are read from environment variables, and from config.env in the
skill folder (loaded automatically; real environment variables win):
  TELEGRAM_BOT_TOKEN    bot token from @BotFather
  TELEGRAM_CHAT_ID      your chat / channel id
  ALPHA_POLL_SECONDS    loop interval, default 60
  ALPHA_VOL_MULT        volume-spike threshold, x average, default 5
  ALPHA_VOL_WINDOW_MIN  volume-spike window in minutes, default 60
  ALPHA_MIN_SPIKE_USD   min USD actually traded in that window to count, default 10000
  ALPHA_COOLDOWN_MIN    min minutes between repeat VOL/MOVE alerts per token, default 60
  ALPHA_PRICE_MOVE_PCT  price-move threshold %, default 30
  ALPHA_MIN_VOL         ignore tokens below this 24h volume, default 0
  ALPHA_MIN_LIQ         ignore tokens below this liquidity, default 0
"""

import argparse
import csv
import html
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

TOKEN_LIST_URL = (
    "https://www.binance.com/bapi/defi/v1/public/"
    "wallet-direct/buw/wallet/cex/alpha/all/token/list"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Skill layout keeps the script in <skill>/scripts/; a flat repo keeps it at the root.
SKILL_DIR = (os.environ.get("ALPHA_HOME")
             or (os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR) == "scripts"
                 else SCRIPT_DIR))

# Field names confirmed from the live feed (2026-11); alternates kept for safety.
KEY_ID = ["alphaId", "tokenId", "id", "alpha_id"]
KEY_SYMBOL = ["symbol", "tokenSymbol"]
KEY_NAME = ["name", "tokenName"]
KEY_PRICE = ["price", "lastPrice", "priceUsd"]
KEY_CHANGE = ["percentChange24h", "priceChangePercent", "change24h"]
KEY_VOLUME = ["volume24h", "volume", "quoteVolume"]
KEY_LIQ = ["liquidity", "liq"]
KEY_HOLDERS = ["holders", "holderCount"]
KEY_MCAP = ["marketCap", "mcap"]
KEY_FDV = ["fdv", "fullyDilutedValuation"]
KEY_SCORE = ["score"]
KEY_LISTTIME = ["listingTime", "onlineTime"]
KEY_LISTCEX = ["listingCex"]
KEY_DELISTED = ["fullyDelisted"]
KEY_CHAIN = ["chainId", "chain", "chainName", "network"]
KEY_CONTRACT = ["contractAddress", "contract_address", "address", "tokenAddress"]

BSC_TOKENS = {"56", "0x38", "bsc", "bnb", "bnbchain", "bnb smart chain",
              "binance-smart-chain", "binance smart chain", "bnbsmartchain"}

HIST_STEP_SEC = 300               # volume history sampled every ~5 minutes
LOG_MAX_BYTES = 5 * 1024 * 1024   # rotate watch.log past 5 MB
PARTIAL_FEED_RATIO = 0.8          # fewer tokens than this x last poll = bad fetch

CSV_FIELDS = ["time", "kind", "symbol", "name", "token_id", "signal",
              "price0", "liq0", "holders0", "mcap0", "chg24h0",
              "ret_1h", "status_1h", "ret_24h", "status_24h",
              "max_ret_24h", "min_ret_24h", "polls", "mode"]

_LOG_PATH = None
_LOCK_HANDLE = None


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def set_log_path(path):
    global _LOG_PATH
    _LOG_PATH = path


def _append_log(line):
    if not _LOG_PATH:
        return
    try:
        if os.path.exists(_LOG_PATH) and os.path.getsize(_LOG_PATH) > LOG_MAX_BYTES:
            os.replace(_LOG_PATH, _LOG_PATH + ".1")
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def safe_print(s):
    """Print without crashing: survives consoles that can't encode emoji
    (Windows cp1252) and a missing console (pythonw / Task Scheduler).
    Also appends to the log file when one is set."""
    out = sys.stdout
    if out is not None:
        try:
            out.write(s + "\n")
        except UnicodeEncodeError:
            enc = getattr(out, "encoding", None) or "utf-8"
            try:
                out.write(s.encode(enc, "replace").decode(enc, "replace") + "\n")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        try:
            out.flush()
        except Exception:  # noqa: BLE001
            pass
    _append_log(s)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

def load_config_env(path):
    """Load KEY=VALUE lines from config.env. Existing environment wins."""
    loaded = []
    try:
        with open(path, encoding="utf-8-sig") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, val = line.split("=", 1)
                key, val = key.strip(), val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
                    loaded.append(key)
    except FileNotFoundError:
        pass
    return loaded


def getenv_float(name, default):
    try:
        val = os.environ.get(name, "")
        return float(val) if str(val).strip() else float(default)
    except (TypeError, ValueError):
        return float(default)


def build_cfg():
    return {
        "poll_seconds": max(15, int(getenv_float("ALPHA_POLL_SECONDS", 60))),
        "vol_mult": getenv_float("ALPHA_VOL_MULT", 5),
        "vol_window_min": max(10, int(getenv_float("ALPHA_VOL_WINDOW_MIN", 60))),
        "min_spike_usd": getenv_float("ALPHA_MIN_SPIKE_USD", 10000),
        "cooldown_min": getenv_float("ALPHA_COOLDOWN_MIN", 60),
        "price_move_pct": getenv_float("ALPHA_PRICE_MOVE_PCT", 30),
        "min_vol": getenv_float("ALPHA_MIN_VOL", 0),
        "min_liq": getenv_float("ALPHA_MIN_LIQ", 0),
        "state_file": os.environ.get("ALPHA_STATE_FILE")
        or os.path.join(SCRIPT_DIR, "state.json"),
        "alerts_log": os.path.join(SKILL_DIR, "alerts_log.json"),
        "alerts_csv": os.path.join(SKILL_DIR, "alerts.csv"),
        "log_file": os.path.join(SKILL_DIR, "watch.log"),
        "lock_file": os.path.join(SKILL_DIR, "watch.lock"),
        "tg_token": os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
        "tg_chat": os.environ.get("TELEGRAM_CHAT_ID", "").strip(),
    }


# --------------------------------------------------------------------------
# single-instance lock (released automatically when the process exits)
# --------------------------------------------------------------------------

def acquire_lock(path):
    """Return an open handle if we got the lock, None if another watcher
    already holds it. Uses OS file locks, never signals another process."""
    try:
        f = open(path, "a+b")
    except OSError:
        return None
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def pick(d, keys, default=None):
    for k in keys:
        if k in d and d[k] not in (None, "", "null"):
            return d[k]
    return default


def to_float(v, default=0.0):
    if v is None:
        return default
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return default


def is_bsc(chain):
    if chain is None:
        return False
    return str(chain).strip().lower() in BSC_TOKENS


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def fmt_time(ts):
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def now_local():
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------
# feed
# --------------------------------------------------------------------------

def http_get_json(url, timeout=20, retries=1):
    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw)
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(2)
    raise RuntimeError(f"fetch failed for {url}: {last_err}")


def fetch_tokens():
    payload = http_get_json(TOKEN_LIST_URL)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        if isinstance(data, dict):
            for k in ("list", "tokens", "rows", "items"):
                if isinstance(data.get(k), list):
                    return data[k]
            for v in data.values():
                if isinstance(v, list):
                    return v
            return []
        if isinstance(data, list):
            return data
    return []


def normalize(tok):
    tid = pick(tok, KEY_ID)
    sym = pick(tok, KEY_SYMBOL, default="?")
    chain = pick(tok, KEY_CHAIN)
    contract = pick(tok, KEY_CONTRACT)
    return {
        "id": str(tid) if tid is not None else f"sym:{sym}",
        "symbol": str(sym),
        "name": str(pick(tok, KEY_NAME, default="")),
        "chain": str(chain) if chain is not None else "",
        "bsc": is_bsc(chain),
        "contract": str(contract) if contract else "",
        "price": to_float(pick(tok, KEY_PRICE)),
        "change": to_float(pick(tok, KEY_CHANGE)),
        "volume": to_float(pick(tok, KEY_VOLUME)),
        "liq": to_float(pick(tok, KEY_LIQ)),
        "holders": int(to_float(pick(tok, KEY_HOLDERS))),
        "mcap": to_float(pick(tok, KEY_MCAP)),
        "fdv": to_float(pick(tok, KEY_FDV)),
        "score": to_float(pick(tok, KEY_SCORE)),
        "list_time": to_float(pick(tok, KEY_LISTTIME)),
        "cex": bool(pick(tok, KEY_LISTCEX, default=False)),
        "delisted": bool(pick(tok, KEY_DELISTED, default=False)),
    }


# --------------------------------------------------------------------------
# state: snapshot + volume history + cooldowns
# --------------------------------------------------------------------------

def load_state(path):
    raw = load_json(path, {})
    if isinstance(raw, dict) and raw.get("_v") == 4:
        return {"snapshot": raw.get("snapshot") or {},
                "vol_hist": raw.get("vol_hist") or {},
                "cooldown": raw.get("cooldown") or {}}
    # v1-v3 state files were the snapshot itself
    return {"snapshot": raw if isinstance(raw, dict) else {},
            "vol_hist": {}, "cooldown": {}}


def update_vol_history(hist, curr, now, window_min):
    """Sample each token's 24h volume every ~5 min; keep a bit more than the
    window so there's always a reference point ~window minutes old."""
    keep_sec = window_min * 60 + 2 * HIST_STEP_SEC
    out = {}
    for tid, t in curr.items():
        h = [e for e in (hist.get(tid) or []) if now - e[0] <= keep_sec]
        if not h or now - h[-1][0] >= HIST_STEP_SEC - 30:
            h.append([int(now), round(t["volume"], 2)])
        out[tid] = h
    return out


def reference_volumes(hist, now, window_min):
    """For each token: the newest sampled 24h volume that is at least
    window minutes old. Tokens without enough history get no reference."""
    w = window_min * 60
    ref = {}
    for tid, h in hist.items():
        old = [v for ts, v in h if now - ts >= w]
        if old:
            ref[tid] = old[-1]
    return ref


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def detect(prev, curr, cfg, ref_vol=None, now=None, cooldown=None):
    """
    prev / curr: {id: normalized token}. ref_vol: {id: 24h volume ~window ago}
    (None = use the previous poll's volume). cooldown: {"id:KIND": ts}.
    Returns alerts, highest priority first.
    """
    alerts = []
    prev = prev or {}
    now = time.time() if now is None else now
    window = cfg.get("vol_window_min", 60)
    cd_sec = cfg.get("cooldown_min", 60) * 60

    def cooling(tid, kind):
        if not cooldown:
            return False
        ts = cooldown.get(f"{tid}:{kind}")
        return ts is not None and now - ts < cd_sec

    for tid, c in curr.items():
        p = prev.get(tid)

        if p is None:
            if (not c["delisted"] and c["volume"] >= cfg["min_vol"]
                    and c["liq"] >= cfg["min_liq"]):
                alerts.append({"kind": "NEW", "tok": c})
            continue

        # DELIST only on a genuine False -> True flip; already-delisted
        # tokens never trigger anything else.
        if c["delisted"]:
            if p.get("delisted") is False:
                alerts.append({"kind": "DELIST", "tok": c})
            continue

        if c["volume"] < cfg["min_vol"] or c["liq"] < cfg["min_liq"]:
            continue

        # VOLUME SPIKE. If the rolling 24h volume V grew by fraction g over the
        # last W minutes, the volume traded in those W minutes is roughly
        # g*V + V*W/1440, i.e. about (g*1440/W + 1) x a typical W-minute slice.
        ref = ref_vol.get(tid) if ref_vol is not None else p.get("volume")
        if ref and ref > 0 and not cooling(tid, "VOL"):
            g = (c["volume"] - ref) / ref
            mult = g * 1440.0 / window + 1.0
            # USD actually traded in the window; filters near-dead tokens where a
            # $17 trade on $94 daily volume would otherwise read as "5x".
            usd = max(0.0, c["volume"] - ref) + ref * window / 1440.0
            if mult >= cfg["vol_mult"] and usd >= cfg.get("min_spike_usd", 10000):
                alerts.append({"kind": "VOL", "tok": c, "growth": g * 100, "usd": usd,
                               "mult": mult, "prev_vol": ref, "window": window})

        if (abs(c["change"]) >= cfg["price_move_pct"] > abs(p.get("change", 0))
                and not cooling(tid, "MOVE")):
            alerts.append({"kind": "MOVE", "tok": c})

    for tid, p in prev.items():
        if tid not in curr and not p.get("delisted"):
            alerts.append({"kind": "DELIST", "tok": p})

    order = {"NEW": 0, "VOL": 1, "MOVE": 2, "DELIST": 3}
    alerts.sort(key=lambda a: order.get(a["kind"], 9))
    return alerts


# --------------------------------------------------------------------------
# alert log + outcome tracking
# --------------------------------------------------------------------------

def make_record(a, now, mode):
    t = a["tok"]
    if t["price"] <= 0:
        return None
    if a["kind"] == "VOL":
        sig = f"vol ~{a['mult']:.1f}x avg/{a['window']}m"
    elif a["kind"] == "MOVE":
        sig = f"24h {t['change']:+.1f}%"
    else:
        sig = "new listing"
    return {
        "ts": int(now), "time": fmt_time(now), "kind": a["kind"],
        "token_id": t["id"], "symbol": t["symbol"], "name": t["name"],
        "signal": sig, "price0": t["price"], "liq0": round(t["liq"], 2),
        "holders0": t["holders"], "mcap0": round(t["mcap"], 2),
        "chg24h0": t["change"], "mode": mode,
        "price_1h": None, "ret_1h": None, "status_1h": None,
        "price_24h": None, "ret_24h": None, "status_24h": None,
        "max_price": t["price"], "min_price": t["price"], "polls": 0,
        "max_ret_24h": None, "min_ret_24h": None, "done": False,
    }


CHECKPOINTS = (("1h", 3600, 900), ("24h", 86400, 3600))  # label, seconds, tolerance


def update_outcomes(log, curr, now):
    """Fill 1h / 24h prices and track the 24h peak/trough. A checkpoint the
    watcher wasn't running for is marked 'missed' (not guessed); one whose
    token has disappeared or been delisted is marked 'gone'."""
    changed = False
    for r in log:
        if r.get("done"):
            continue
        age = now - r["ts"]
        t = curr.get(r["token_id"])
        live = t is not None and not t["delisted"] and t["price"] > 0
        price = t["price"] if live else None

        if price is not None and age <= 86400:
            r["max_price"] = max(r["max_price"], price)
            r["min_price"] = min(r["min_price"], price)
            r["polls"] += 1
            changed = True

        for label, secs, tol in CHECKPOINTS:
            if r.get(f"status_{label}") is not None or age < secs:
                continue
            if price is not None and age <= secs + tol:
                r[f"price_{label}"] = price
                r[f"ret_{label}"] = round((price / r["price0"] - 1) * 100, 2)
                r[f"status_{label}"] = "ok"
                changed = True
            elif age > secs + tol:
                r[f"status_{label}"] = "gone" if not live else "missed"
                changed = True

        if r.get("status_24h") is not None:
            r["max_ret_24h"] = round((r["max_price"] / r["price0"] - 1) * 100, 2)
            r["min_ret_24h"] = round((r["min_price"] / r["price0"] - 1) * 100, 2)
            r["done"] = True
            changed = True
    return changed


def export_csv(path, log):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in log:
            w.writerow(r)
    os.replace(tmp, path)


def _stats(xs):
    if not xs:
        return "-"
    win = sum(1 for x in xs if x > 0) / len(xs) * 100
    return (f"avg {statistics.mean(xs):+.1f}%  median {statistics.median(xs):+.1f}%  "
            f"naik {win:.0f}%")


def build_report(log):
    if not log:
        return "Belum ada alert yang tercatat."
    lines = [f"Laporan alert ({len(log)} tercatat, "
             f"{fmt_time(log[0]['ts'])} s/d {fmt_time(log[-1]['ts'])})", ""]
    for kind in ("NEW", "VOL", "MOVE", "ALL"):
        rs = [r for r in log if kind == "ALL" or r["kind"] == kind]
        if not rs:
            continue
        r1 = [r["ret_1h"] for r in rs if r.get("status_1h") == "ok"]
        r24 = [r["ret_24h"] for r in rs if r.get("status_24h") == "ok"]
        pk = [r["max_ret_24h"] for r in rs if r.get("max_ret_24h") is not None]
        tr = [r["min_ret_24h"] for r in rs if r.get("min_ret_24h") is not None]
        lines.append(f"[{kind}] {len(rs)} alert")
        lines.append(f"  +1 jam  (n={len(r1)}): {_stats(r1)}")
        lines.append(f"  +24 jam (n={len(r24)}): {_stats(r24)}")
        if pk:
            lines.append(f"  puncak 24 jam rata-rata {statistics.mean(pk):+.1f}%  |  "
                         f"titik terendah rata-rata {statistics.mean(tr):+.1f}%")
        lines.append("")
    n24 = sum(1 for r in log if r.get("status_24h") == "ok")
    if n24 < 30:
        lines.append(f"⚠️ Baru {n24} alert yang punya hasil 24 jam. "
                     "Sampel sekecil ini belum bisa dijadikan kesimpulan.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# formatting + telegram
# --------------------------------------------------------------------------

def fmt_price(x):
    if x == 0:
        return "0"
    if abs(x) >= 1:
        return f"{x:,.4f}".rstrip("0").rstrip(".")
    return f"{x:.8f}".rstrip("0").rstrip(".")


def fmt_usd(x):
    if not x:
        return "?"
    for unit, div in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= div:
            return f"${x/div:.2f}{unit}"
    return f"${x:,.0f}"


def dexscreener_link(symbol):
    return "https://dexscreener.com/search?q=" + urllib.parse.quote(symbol)


def format_alert(a):
    t = a["tok"]
    sym = html.escape(t["symbol"])
    name = f' ({html.escape(t["name"])})' if t["name"] else ""
    head = {
        "NEW": f"🚨 <b>NEW ALPHA LISTING</b> — {sym}{name}",
        "VOL": f"📈 <b>VOLUME SPIKE</b> — {sym}{name}",
        "MOVE": f"⚡ <b>PRICE MOVE</b> — {sym}{name}",
        "DELIST": f"⚪️ <b>DELISTED FROM ALPHA</b> — {sym}{name}",
    }[a["kind"]]

    lines = [head]
    if a["kind"] != "DELIST":
        lines.append(f"price {fmt_price(t['price'])}  |  24h {t['change']:+.2f}%")
        lines.append(
            f"liq {fmt_usd(t['liq'])} • holders {t['holders'] or '?'} "
            f"• mcap {fmt_usd(t['mcap'])} • FDV {fmt_usd(t['fdv'])}")
        extra = []
        if t["score"]:
            extra.append(f"score {int(t['score'])}")
        extra.append("CEX-listed: " + ("yes" if t["cex"] else "no"))
        lines.append(" | ".join(extra))
    if a["kind"] == "VOL":
        lines.append(f"↑ last {a['window']}m ≈ {a['mult']:.1f}x normal volume, "
                     f"~{fmt_usd(a.get('usd', 0))} traded "
                     f"(24h vol was {fmt_usd(a['prev_vol'])})")

    if t["bsc"] and t["contract"]:
        lines.append(f'<a href="https://bscscan.com/token/{t["contract"]}">verify on BscScan</a>')
    else:
        lines.append(f'🔎 <a href="{dexscreener_link(t["symbol"])}">find contract on DexScreener</a>')
    return "\n".join(lines)


def send_telegram(text, token, chat_id):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    if not body.get("ok"):
        raise RuntimeError(f"telegram error: {body.get('description', body)}")


# --------------------------------------------------------------------------
# one poll
# --------------------------------------------------------------------------

def poll_once(cfg, dry_run, now=None, tokens=None):
    now = time.time() if now is None else now
    raw = fetch_tokens() if tokens is None else tokens
    curr = {}
    for t in raw:
        n = normalize(t)
        if n["id"]:
            curr[n["id"]] = n
    if not curr:
        raise RuntimeError("feed returned 0 tokens")

    state = load_state(cfg["state_file"])
    prev = state["snapshot"]
    if prev and len(curr) < PARTIAL_FEED_RATIO * len(prev):
        safe_print(f"[{now_local()}] [warn] feed returned {len(curr)} tokens vs "
                   f"{len(prev)} last poll — looks partial, skipping this poll.")
        return 0

    window = cfg["vol_window_min"]
    hist = update_vol_history(state["vol_hist"], curr, now, window)
    ref = reference_volumes(hist, now, window)
    cd_sec = cfg["cooldown_min"] * 60
    cooldown = {k: v for k, v in state["cooldown"].items() if now - v < cd_sec}

    first_run = not prev
    alerts = [] if first_run else detect(prev, curr, cfg, ref_vol=ref,
                                         now=now, cooldown=cooldown)
    for a in alerts:
        if a["kind"] in ("VOL", "MOVE"):
            cooldown[f"{a['tok']['id']}:{a['kind']}"] = now

    save_json(cfg["state_file"], {"_v": 4, "snapshot": curr,
                                  "vol_hist": hist, "cooldown": cooldown})

    # log alerts and fill in outcomes of earlier ones
    telegram_on = bool(cfg["tg_token"] and cfg["tg_chat"]) and not dry_run
    log = load_json(cfg["alerts_log"], [])
    added = False
    for a in alerts:
        if a["kind"] in ("NEW", "VOL", "MOVE"):
            rec = make_record(a, now, "live" if telegram_on else "print")
            if rec:
                log.append(rec)
                added = True
    if update_outcomes(log, curr, now) or added:
        save_json(cfg["alerts_log"], log)
        try:
            export_csv(cfg["alerts_csv"], log)
        except OSError as e:  # e.g. alerts.csv open in Excel
            safe_print(f"[warn] couldn't write alerts.csv ({e}); "
                       "alerts_log.json is still up to date.")

    if first_run:
        safe_print(f"[{now_local()}] baseline saved: {len(curr)} Alpha tokens. "
                   f"No alerts on first run.")
        return 0

    sent = 0
    for a in alerts:
        text = format_alert(a)
        if telegram_on:
            try:
                send_telegram(text, cfg["tg_token"], cfg["tg_chat"])
            except Exception as e:  # noqa: BLE001
                safe_print(f"[warn] telegram send failed: {e}")
                continue
        else:
            safe_print("---\n" + text.replace("<b>", "").replace("</b>", ""))
        sent += 1

    safe_print(f"[{now_local()}] {len(curr)} tokens, {len(alerts)} alerts"
               f"{'' if telegram_on else ' (print mode)'}.")
    return sent


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    global _LOCK_HANDLE
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    ap = argparse.ArgumentParser(description="Binance Alpha token watcher")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--loop", action="store_true", help="poll forever")
    ap.add_argument("--dry-run", action="store_true",
                    help="print alerts instead of sending to Telegram")
    ap.add_argument("--report", action="store_true",
                    help="show how past alerts performed and exit")
    ap.add_argument("--print-schema", action="store_true",
                    help="dump the raw fields of the first token and exit")
    args = ap.parse_args(argv)

    load_config_env(os.path.join(SKILL_DIR, "config.env"))
    cfg = build_cfg()

    if args.print_schema:
        toks = fetch_tokens()
        if not toks:
            safe_print("no tokens returned")
            return 1
        safe_print(json.dumps(toks[0], indent=2, ensure_ascii=False))
        return 0

    if args.report:
        safe_print(build_report(load_json(cfg["alerts_log"], [])))
        return 0

    if os.environ.get("ALPHA_NO_LOCK") != "1":
        _LOCK_HANDLE = acquire_lock(cfg["lock_file"])
        if _LOCK_HANDLE is None:
            safe_print("[stop] Another alpha_watch is already running from this folder "
                       "(watch.lock is held). Stop it first, or use --report to read results.")
            return 2

    if not (cfg["tg_token"] and cfg["tg_chat"]) and not args.dry_run:
        safe_print("[note] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
                   "printing alerts instead of sending them.")

    if args.loop:
        set_log_path(cfg["log_file"])
        safe_print(f"[{now_local()}] watching Alpha every {cfg['poll_seconds']}s | "
                   f"vol spike >= {cfg['vol_mult']:g}x over {cfg['vol_window_min']}m | "
                   f"move >= {cfg['price_move_pct']:g}% | min liq {fmt_usd(cfg['min_liq'])} | "
                   f"cooldown {cfg['cooldown_min']:g}m. Ctrl-C to stop.")
        while True:
            try:
                poll_once(cfg, args.dry_run)
            except Exception as e:  # noqa: BLE001
                safe_print(f"[{now_local()}] [warn] poll failed: {e}")
            time.sleep(cfg["poll_seconds"])
    else:
        try:
            poll_once(cfg, args.dry_run)
        except Exception as e:  # noqa: BLE001
            safe_print(f"[{now_local()}] [error] poll failed: {e}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
