# Status M9 (paket exe + installer)
Hasil: dist/installer/MCAP2MP4-Setup-0.3.0.exe (NSIS, ~45 MB; terpasang ~172 MB). Isi: MCAP2MP4.exe (GUI), mcap2mp4-cli.exe, exiftool, libVLC 3.0.11 x64 (subset plugin, 30 MB),
PySide6 6.9.x, Python 3.12. Wizard 3 bahasa (id/en/zh), pintasan Start Menu (+ opsional Desktop), entri Tambah/Hapus Program, copot bersih (data pengguna di %APPDATA% dipertahankan).

Cara bangun ulang: `WINPY=... WINEPREFIX=... bash build/build_windows.sh` (lihat header skrip). libVLC disiapkan oleh `build/prepare_vlc.py <folder>`;
`build/fetch_vlc.py` mengunduh VLC resmi (tidak bisa dijalankan di sandbox: videolan.org tidak terjangkau).

Diverifikasi di Wine 9 (Linux) dengan exe hasil PyInstaller dan installer varian 64-bit (-DTARGET_AMD64; skrip sama, hanya stub NSIS):
 - pasang senyap /S ke C:\MCAP2MP4_test: berkas, kunci registri Uninstall, pintasan Start Menu ada.
 - exe terpasang: `--selftest-player` (libVLC bawaan memutar 2 kamera 3,5 dtk, 119 frame, 1 trek audio ipcm, snapshot PNG) lulus; `--selftest` GUI (konversi nyata, verifikasi penuh) lulus;
   CLI `--session-mode merged --verify full` lulus; `mcap2mp4-cli --version` = 0.3.0.
 - copot senyap: folder, registri, pintasan hilang.
 - libVLC Windows 3.0.11 memutar HEVC + audio ipcm/sowt + MP4 tanpa audio (diuji di Wine lewat python-vlc Windows).
Belum/tidak bisa diuji di sini: Windows asli (SmartScreen akan memperingatkan: exe TIDAK ditandatangani), RDP/VPS tanpa GPU, installer x86 rilis dijalankan (hanya varian 64-bit yang
dijalankan di Wine; isi sama), font (di Wine tanpa font teks GUI tampil kotak-kotak: bukan masalah di Windows yang punya Segoe UI), dekode hardware d3d11va/dxva2, kinerja 2x 1080p30 (benchmark di Windows).
Pihak ketiga: libVLC dari build RSATom/WebChimera.js v0.5.2 (VLC 3.0.11, LGPL-2.1+); ganti dengan VLC resmi 3.0.x bila ingin versi lebih baru (README_VLC.txt).
