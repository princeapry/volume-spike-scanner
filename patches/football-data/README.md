# Patch lokal: football-data (sports-skills)

Modifikasi lokal untuk skill `football-data` (machina-sports/sports-skills **v0.41.1**).
Disimpan di sini karena dua file aslinya ada di luar repo dan bisa ketimpa update.

| File | Isi |
|---|---|
| `connector-understat.patch` | Diff unified dari `sports_skills/football/_connector.py` (asli → hasil patch) |
| `apply_understat_patch.py` | Script buat pasang ulang patch-nya (lebih tahan dibanding `.patch` kalau nomor baris upstream bergeser) |
| `SKILL.md` | `SKILL.md` versi modifikasi |

## Apa yang diubah & kenapa

### 1. `_connector.py`: error Understat yang jujur
**Masalah (2026-11-23):** Understat sedang maintenance. Semua URL-nya balikin halaman HTML
"We will come back soon" dengan status HTTP 200. Kode asli gagal mem-parse halaman itu sebagai JSON,
lalu diam-diam mengembalikan `None`, dan `get_event_xg` jadinya bilang **"Match not found on Understat"**.
Pesan itu menyesatkan, seolah format tahun musimnya salah. Format tahunnya sebenarnya benar:
`2026` = musim 2026-27.

**Perubahan:**
- `_note_understat_failure()` mencatat penyebab gagalnya: maintenance, HTML lain, atau error koneksi.
  `_understat_api` dan `_understat_html` sekarang memanggil fungsi ini.
- `_understat_html` juga mengenali halaman maintenance yang dikirim dengan HTTP 200.
- `_find_understat_match_id` tidak lagi menyimpan fetch yang gagal ke cache sebagai "musim kosong"
  (sebelumnya disimpan 1 jam).
- `get_event_xg` sekarang mengembalikan `message: "Understat sedang maintenance ..."` plus
  `understat_status: "unavailable"`.
- `get_event_players_statistics` tetap mengembalikan statistik ESPN, ditambah field `xg_message` dan `understat_status`.
- "Match not found on Understat" hanya muncul kalau Understat jalan normal tapi match-nya memang tidak ketemu.

### 2. `SKILL.md`: override setup lokal
- CLI dipanggil dari venv khusus `~/.venvs/sports-skills/Scripts/sports-skills.exe`.
  Jangan pernah `pip install` global.
- Wajib `PYTHONUTF8=1`, karena tanpa itu `get_missing_players` crash dengan error `'charmap' codec` pada nama beraksen.
- Error dan respons kosong harus ditampilkan apa adanya.

## Cara pasang ulang (kalau ketimpa update)

Upgrade `pip` di venv akan menimpa `_connector.py`. `npx skills update` bisa menimpa `SKILL.md`.

**`_connector.py`** (dari folder ini):
```bash
~/.venvs/sports-skills/Scripts/python.exe -I apply_understat_patch.py
```
- Path default-nya file di venv. Bisa juga kasih path lain sebagai argumen.
- Script-nya bikin backup `_connector.py.bak-<tanggal>` sebelum mengubah apa pun.
- Aman dijalankan dua kali: kalau sudah terpasang, hasilnya `already patched`.
- Kalau kode upstream berubah, script-nya berhenti dengan `AssertionError` dan tidak menulis apa-apa.
  Kalau itu terjadi, sesuaikan potongan teks di script-nya.

Alternatifnya, kalau versinya masih 0.41.1:
```bash
cd ~/.venvs/sports-skills/Lib/site-packages && patch -p1 < /path/to/connector-understat.patch
```

**`SKILL.md`:**
```bash
cp SKILL.md ~/.agents/skills/football-data/SKILL.md   # ~/.claude/skills/football-data adalah symlink ke sini
```
Kalau upstream mengubah `SKILL.md`, jangan langsung timpa. Bandingkan dulu dan gabungkan bagian "LOCAL OVERRIDE" secara manual.

**Cek hasilnya:**
```bash
PYTHONUTF8=1 ~/.venvs/sports-skills/Scripts/sports-skills.exe football get_event_xg --event_id=401879301
```
Selama Understat masih maintenance, `message` harus berisi "Understat sedang maintenance".

## Catatan
ClubElo (`api.clubelo.com`) juga sedang mati di sisi server per 2026-11-23: koneksi TCP ditolak, baik dari jaringan lokal maupun dari luar.
Bagian itu tidak dipatch, karena tool-nya sudah jujur memakai fallback `local-elo`.
