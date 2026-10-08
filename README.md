# MCAP2MP4: paket dasar pembangunan

Konversi `.mcap` -> `.mp4` (remux H.265, tanpa re-encode) untuk Windows, dengan metadata lengkap dan laporan batch.
Spesifikasi lengkap: `docs/PRD_MCAP_ke_MP4.md` (v0.2). Catatan porting: `docs/PORTING_NOTES.md`.

## Isi paket
| Folder | Isi |
|---|---|
| `docs/` | PRD dan catatan porting dari notebook |
| `reference/legacy/` | Notebook `mcap_drive_batch.ipynb` dan 4 skrip asli (`mcap_to_mp4.py`, `extractor.py`, `ros2_video.py`, `worker.py`), sha256 terverifikasi. **Hanya acuan, jangan diimpor** |
| `third_party/exiftool/` | ExifTool (`exiftool.exe` + `exiftool_files`) apa adanya; ExifToolGUI tidak disertakan |
| `src/mcap2mp4/` | Kerangka aplikasi. **Sudah berfungsi:** `settings`, `i18n`, `naming`, `cli` (parser argumen). Sisanya stub bernomor FR |
| `i18n/` | Bahasa awal: `id.json`, `en.json`, `zh.json` |
| `config/` | `settings.default.json` (sesuai PRD §7) |
| `tests/` | `test_naming.py` |
| `build/` | `mcap2mp4.spec` (PyInstaller) dan `installer.iss` (Inno Setup), **belum diuji** |
| `samples/` | Tempat file `.mcap` contoh untuk uji |

## Mulai
```
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m unittest discover tests
python -m mcap2mp4 --help
```
Pengemasan: `pyinstaller build/mcap2mp4.spec`, lalu kompilasi `build/installer.iss` dengan Inno Setup.

## Langkah berikutnya (M1)
Implementasikan `mcap_reader`, `source_adapters`, `mp4_writer` (streaming, co64 opsional, `stts` per-frame), `sync`, `verify`, `manifest`, `worker`, dengan memindahkan logika dari `reference/legacy/` sesuai tabel di `docs/PORTING_NOTES.md`. Uji pada dua file contoh di `samples/`, plus file sintetis >4 GB untuk co64.

## Status (v0.2.0 — GUI + installer)

- **GUI** (`MCAP2MP4.exe`, PySide6): antrean, tabel status/progres/kecepatan/ETA, Jeda/Lanjut/Batal bersih,
  pemilih lokasi output (folder / di samping sumber / tanya saat mulai), penampil metadata (pohon, filter, salin,
  buka lokasi), laporan CSV/JSON/XLSX/HTML, bahasa Indonesia/English/简体中文 langsung tanpa restart.
- **CLI** (`mcap2mp4-cli.exe`): fitur setara; kode keluar 0/1/2/64.
- **Inti**: remux H.265 (Foxglove protobuf & ROS2 CDR) streaming tanpa re-encode, co64/largesize otomatis (>4 GB),
  durasi per-frame dari timestamp, sinkron antar-channel (strict/warn/trim), verifikasi quick/full, resume lewat
  `manifest.jsonl`, sidecar JSON + ExifTool.
- Belum: B-frame, codec selain H.265, `after_success`, `faststart`, ekspor IMU/audio (P2).

### Build
```
pip install -r requirements-dev.txt
pyinstaller build/mcap2mp4.spec --noconfirm        # -> dist/MCAP2MP4/ (GUI + CLI)
makensis -DVERSION=0.2.0 build/installer.nsi        # -> dist/installer/MCAP2MP4-Setup-0.2.0.exe
```
Installer: NSIS (wizard Indonesia/English/简体中文, Program Files, pintasan Start Menu/Desktop, entri Add/Remove Programs,
pemasangan senyap `/S`, pencopotan hanya menghapus berkas milik aplikasi; pengaturan di `%APPDATA%\MCAP2MP4` dipertahankan).
PySide6 di-pin `<6.10` (6.10+ butuh `icuuc.dll`). Workflow `build-windows` membangun semuanya di runner Windows.

### Tes
`python -m unittest discover tests` (butuh ffmpeg+libx265) dan `python tests/gui_smoke.py` (GUI headless).
`MCAP2MP4.exe --selftest <src> <out> <png>` menjalankan konversi nyata secara headless pada paket terpasang.
