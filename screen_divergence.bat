@echo off
rem Jalankan screening bullish divergence RSI (double-click file ini).
rem Hasil juga disimpan ke divergence_screen.md di folder yang sama.
rem Ganti timeframe: hapus "rem" di baris set DIV_TF di bawah, contoh 1h atau 1d.

rem set DIV_TF=1h

rem Pindah ke folder tempat file .bat ini berada
cd /d "%~dp0"

rem Pakai Python dari virtualenv proyek kalau ada, kalau tidak pakai python sistem
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" screen_divergence.py
) else (
    python screen_divergence.py
)

echo.
pause
