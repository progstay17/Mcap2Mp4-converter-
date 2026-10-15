#!/usr/bin/env bash
# Bangun exe + installer Windows dari Linux (Wine + Python Windows 3.12 + NSIS). Dipakai membuat rilis ini.
#
# Prasyarat (sekali): apt install wine64 nsis; Python Windows (python-build-standalone, x86_64-pc-windows-msvc, install_only)
# diekstrak ke $WINPY; di Wine: python -m pip install pyinstaller "PySide6-Essentials>=6.8,<6.10" python-vlc zstandard lz4 openpyxl
# third_party/vlc disiapkan lewat build/prepare_vlc.py <folder libVLC Windows>.
#
#   WINPY=/path/python-windows WINEPREFIX=/path/prefix bash build/build_windows.sh [versi]
set -euo pipefail
cd "$(dirname "$0")/.."
: "${WINPY:?set WINPY ke folder Python Windows}"; : "${WINEPREFIX:?set WINEPREFIX}"
export WINEDEBUG=-all
VER="${1:-$(python3 -c 'import sys; sys.path.insert(0,"src"); import mcap2mp4; print(mcap2mp4.__version__)')}"
[ -f third_party/vlc/libvlc.dll ] || { echo "third_party/vlc kosong: jalankan build/prepare_vlc.py dulu" >&2; exit 2; }
rm -rf dist build/work
# stdin/stdout harus pipa agar Wine memberi handle valid
wine "$WINPY/python.exe" -u -m PyInstaller build/mcap2mp4.spec --noconfirm --distpath dist --workpath build/work 2>&1 < /dev/null | cat
mkdir -p dist/installer
makensis -V1 -DVERSION="$VER" build/installer.nsi
echo "Hasil: dist/installer/MCAP2MP4-Setup-$VER.exe"
# Varian uji 64-bit untuk Wine 64-bit (stub NSIS rilis = x86): jalankan dengan  wine test-amd64-Setup.exe /S /D=C:\\MCAP2MP4_test
makensis -V1 -DVERSION="$VER" -DTARGET_AMD64 '-DOUTFILE=..\dist\installer\test-amd64-Setup.exe' build/installer.nsi
