"""Re-apply the honest-Understat-error patch to sports_skills/football/_connector.py.

Usage:  python -I apply_understat_patch.py [path/to/_connector.py]
Default path: ~/.venvs/sports-skills/Lib/site-packages/sports_skills/football/_connector.py
Safe to run twice (skips if already patched). Fails loudly if upstream code changed.
"""
import sys, pathlib, shutil, datetime
default = pathlib.Path.home() / ".venvs/sports-skills/Lib/site-packages/sports_skills/football/_connector.py"
p = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else default
s = p.read_text(encoding="utf-8")
if "_UNDERSTAT_STATUS_KEY" in s:
    print(f"already patched: {p}"); sys.exit(0)
bak = p.with_name(p.name + ".bak-" + datetime.date.today().isoformat())
if not bak.exists():
    shutil.copy2(p, bak); print(f"backup: {bak}")
def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, (n, old[:80])
    s = s.replace(old, new)

# 1) status helpers, placed before _understat_html
rep('def _understat_html(url):\n', '''_UNDERSTAT_STATUS_KEY = "ustat_status"


def _note_understat_failure(raw=None, err=None):
    """Remember WHY Understat failed so callers can report it honestly."""
    if err:
        reason = f"Understat tidak bisa diakses: {err.get('message', err)}"
    else:
        text = (raw or b"").decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
        low = text.lower()
        if "come back soon" in low or "maintenance" in low:
            reason = (
                "Understat sedang maintenance (situs menampilkan halaman "
                "'We will come back soon', bukan data). Coba lagi nanti."
            )
        elif "<html" in low or "<!doctype" in low:
            reason = (
                "Understat mengembalikan halaman HTML, bukan data JSON "
                "(kemungkinan maintenance atau diblokir)."
            )
        else:
            reason = "Understat mengembalikan respons yang tidak bisa dibaca sebagai JSON."
    logger.warning(reason)
    _cache_set(_UNDERSTAT_STATUS_KEY, reason, ttl=60)
    return reason


def _understat_failure_reason():
    """Return the latest Understat failure reason (or None if none recently)."""
    return _cache_get(_UNDERSTAT_STATUS_KEY) or None


def _is_understat_maintenance_html(text):
    low = (text or "").lower()
    return "come back soon" in low and "errors.css" in low


def _understat_html(url):
''')

# 2) _understat_html: network error + maintenance page served as 200
rep('''        logger.debug("Understat HTML failed for %s: %s", url, err.get("message", ""))
        _cache_set(cache_key, "", ttl=60)
        return None
    try:
        html = raw.decode()
        _cache_set(cache_key, html, ttl=600)''', '''        logger.debug("Understat HTML failed for %s: %s", url, err.get("message", ""))
        _note_understat_failure(err=err)
        _cache_set(cache_key, "", ttl=60)
        return None
    try:
        html = raw.decode()
        if _is_understat_maintenance_html(html):
            _note_understat_failure(raw=html)
            _cache_set(cache_key, "", ttl=60)
            return None
        _cache_set(cache_key, html, ttl=600)''')

# 3) _understat_api: network error + non-JSON body
rep('''        logger.debug("Understat API failed for %s: %s", path, err.get("message", ""))
        _cache_set(cache_key, "", ttl=60)
        return None
    try:
        data = json.loads(raw.decode())
        _cache_set(cache_key, data, ttl=ttl)
        return data
    except (json.JSONDecodeError, ValueError):
        _cache_set(cache_key, "", ttl=60)
        return None''', '''        logger.debug("Understat API failed for %s: %s", path, err.get("message", ""))
        _note_understat_failure(err=err)
        _cache_set(cache_key, "", ttl=60)
        return None
    try:
        data = json.loads(raw.decode())
        _cache_set(cache_key, data, ttl=ttl)
        return data
    except (json.JSONDecodeError, ValueError):
        _note_understat_failure(raw=raw)
        _cache_set(cache_key, "", ttl=60)
        return None''')

# 4) don't cache a failed fetch as an empty season for an hour
rep('''        matches = league_data.get("dates", []) if league_data else []
        _cache_set(season_key, matches, ttl=3600)''', '''        if league_data is None:
            # Fetch failed (maintenance/network) - don't cache as "no matches"
            return None
        matches = league_data.get("dates", [])
        _cache_set(season_key, matches, ttl=3600)''')

# 5) get_event_xg: honest messages
rep('''    understat_id = _find_understat_match_id(match_ctx)
    if not understat_id:
        return {
            "event_id": event_id,
            "teams": [],
            "shots": [],
            "message": "Match not found on Understat",
        }
    udata = _get_understat_match(understat_id)
    if not udata:''', '''    understat_id = _find_understat_match_id(match_ctx)
    if not understat_id:
        down = _understat_failure_reason()
        return {
            "event_id": event_id,
            "teams": [],
            "shots": [],
            "message": down or "Match not found on Understat",
            "understat_status": "unavailable" if down else "ok",
        }
    udata = _get_understat_match(understat_id)
    down = _understat_failure_reason()
    if down and udata and not (udata["shots"].get("h") or udata["shots"].get("a")):
        return {
            "event_id": event_id,
            "teams": [],
            "shots": [],
            "message": down,
            "understat_status": "unavailable",
        }
    if not udata:''')

# 6) players statistics: say why xG is missing
rep('''                        _merge_understat_player_xg(teams, uteams)
            return {"event_id": event_id, "teams": teams}''', '''                        _merge_understat_player_xg(teams, uteams)
                down = _understat_failure_reason()
                if down:
                    return {
                        "event_id": event_id,
                        "teams": teams,
                        "xg_message": down,
                        "understat_status": "unavailable",
                    }
            return {"event_id": event_id, "teams": teams}''')
p.write_bytes(s.encode("utf-8"))  # keep LF line endings
print(f"patched: {p}")
